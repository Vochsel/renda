/**
 * Spend, concurrency and liveness limits: submit gates, mid-run stops, exactly-once usage, the activity-based
 * sweep, Stripe subscription state, usage reconciliation and free-tier farming. Real route handlers and
 * migrations on an in-memory Postgres (PGlite); R2, Stripe and the Modal endpoints are faked.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { PGlite } from "@electric-sql/pglite";
import { drizzle } from "drizzle-orm/pglite";
import { eq } from "drizzle-orm";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";
import type { ApiError } from "@/lib/errors";

process.env.RENDA_WORKER_SECRET = "worker-secret";
process.env.RENDA_WORKER_TOKEN = "worker-token";
process.env.RENDA_ACCEPT_UNTIMED_WORKER_EVENTS = "0";
process.env.MODAL_SUBMIT_URL = "https://modal.test/submit";
process.env.MODAL_CANCEL_URL = "https://modal.test/cancel";
process.env.APP_URL = "https://renda.test";
process.env.R2_OUTPUT_TTL_SECONDS = "3600";
process.env.FREE_SECONDS = "1800";
process.env.RATE_LIMIT_DISABLED = "1";
process.env.STRIPE_SECRET_KEY = "sk_test_fake";
process.env.STRIPE_WEBHOOK_SECRET = "whsec_fake";

const pg = new PGlite();
const testDb = drizzle(pg, { schema });
vi.mock("@/lib/db", () => ({ db: () => testDb, schema }));
vi.mock("@/lib/r2", () => ({ outputPrefix: (id: string) => `outputs/${id}/`, presignGet: async (k: string) => `https://r2.test/get/${k}` }));
vi.mock("@workos-inc/authkit-nextjs", () => ({ withAuth: vi.fn() }));
vi.mock("@/lib/workos", () => ({ workos: () => ({}) }));

const posthog = vi.hoisted(() => ({ captureError: vi.fn(), track: vi.fn(), identify: vi.fn() }));
vi.mock("@/lib/posthog", async (orig) => ({ ...(await orig<typeof import("@/lib/posthog")>()), ...posthog }));

const fakeStripe = vi.hoisted(() => ({
  webhooks: { constructEventAsync: async (raw: string) => JSON.parse(raw) },
  paymentMethods: { list: vi.fn(async () => ({ data: [] as unknown[] })) },
  billing: { meterEvents: { create: vi.fn(async () => ({})) } },
  customers: { create: vi.fn(async () => ({ id: `cus_${Math.random()}` })) },
  checkout: { sessions: { create: vi.fn(async (p: unknown) => ({ url: "https://checkout.stripe.test/s", params: p })) } },
}));
vi.mock("@/lib/stripe", () => ({ stripe: () => fakeStripe, stripeConfigured: () => true }));

const modal: { submit: unknown[]; cancel: unknown[] } = { submit: [], cancel: [] };
vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
  const body = JSON.parse(String(init?.body));
  if (url === "https://modal.test/submit") { modal.submit.push(body); return Response.json({ callId: "fc-1" }); }
  if (url === "https://modal.test/cancel") { modal.cancel.push(body); return Response.json({ ok: true }); }
  throw new Error(`unexpected fetch ${url}`);
}));

const { generateApiKey } = await import("@/lib/apikeys");
const jobsRoute = await import("@/app/api/v1/jobs/route");
const limitRoute = await import("@/app/api/v1/billing/limit/route");
const checkoutRoute = await import("@/app/api/v1/billing/checkout/route");
const eventsRoute = await import("@/app/api/v1/internal/worker/events/route");
const webhookRoute = await import("@/app/api/v1/webhooks/stripe/route");
const { signTimed } = await import("@/lib/hmac");
const { checkBudget, checkRunningBudget, recordUsage } = await import("@/lib/billing");
const { sweepStaleJobs } = await import("@/lib/jobs");
const { networkOf, createTrial } = await import("@/lib/trials");

beforeAll(async () => {
  const dir = join(__dirname, "..", "drizzle");
  for (const f of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) {
    for (const stmt of readFileSync(join(dir, f), "utf8").split("--> statement-breakpoint")) if (stmt.trim()) await pg.exec(stmt);
  }
});
beforeEach(() => {
  modal.submit.length = 0;
  modal.cancel.length = 0;
  vi.unstubAllEnvs();
  posthog.captureError.mockClear();
  fakeStripe.billing.meterEvents.create.mockClear();
  fakeStripe.checkout.sessions.create.mockClear();
});

type Billing = Partial<typeof schema.billing.$inferInsert>;
async function newOrg(b: Billing = {}) {
  const [org] = await testDb.insert(schema.orgs).values({ name: "org" }).returning();
  await testDb.insert(schema.billing).values({ orgId: org.id, freeSecondsRemaining: 0, creditUsd: 100, ...b });
  const k = generateApiKey();
  await testDb.insert(schema.apiKeys).values({ orgId: org.id, name: "k", prefix: k.prefix, hash: k.hash });
  return { orgId: org.id, key: k.key };
}
const billingOf = async (orgId: string) => (await testDb.select().from(schema.billing).where(eq(schema.billing.orgId, orgId)))[0];
const jobOf = async (id: string) => (await testDb.select().from(schema.jobs).where(eq(schema.jobs.id, id)))[0];

const PKG = { version: 1, assets: { m: { uri: "https://example.com/m.glb" } }, scene: { root: "m" } };
function submit(key: string, extra: Record<string, unknown> = {}) {
  return jobsRoute.POST(new Request("https://renda.test/api/v1/jobs", {
    method: "POST", headers: { authorization: `Bearer ${key}`, "content-type": "application/json" },
    body: JSON.stringify({ package: PKG, ...extra }),
  }), undefined as never);
}
function send(ev: Record<string, unknown>) {
  const body = JSON.stringify(ev);
  const ts = Math.floor(Date.now() / 1000);
  return eventsRoute.POST(new Request("https://renda.test/api/v1/internal/worker/events", {
    method: "POST", body,
    headers: { "content-type": "application/json", "x-renda-timestamp": String(ts), "x-renda-signature": signTimed(body, ts, "worker-secret") },
  }), undefined as never);
}
const stripeEvent = (event: unknown) => webhookRoute.POST(new Request("https://renda.test/api/v1/webhooks/stripe", {
  method: "POST", body: JSON.stringify(event), headers: { "stripe-signature": "t=1,v1=x" },
}));
const codeOf = async (r: Response) => (await r.json()).error?.code;

describe("budget checks (pure)", () => {
  const est = (usd: number) => ({ estSeconds: [60, 120] as [number, number], estCostUsd: [usd, usd * 2] as [number, number] });
  const funded = { creditUsd: 10, monthlyLimitUsd: null };
  const code = (f: () => void) => { try { f(); return null; } catch (e) { return (e as ApiError).code; } };

  it("needs prepaid credit that covers the low estimate", () => {
    expect(code(() => checkBudget(funded, 0, est(5)))).toBeNull();
    expect(code(() => checkBudget(funded, 0, est(11)))).toBe("insufficient_credit");
    expect(code(() => checkBudget({ ...funded, creditUsd: 0 }, 0, est(0)))).toBe("payment_required");
    expect(code(() => checkBudget({ ...funded, creditUsd: -0.01 }, 0, est(0)))).toBe("payment_required");
  });
  it("no monthly limit by default; a workspace's own limit applies", () => {
    expect(code(() => checkBudget({ creditUsd: 1e6, monthlyLimitUsd: null }, 1e5, est(1)))).toBeNull();
    expect(code(() => checkBudget({ creditUsd: 1e6, monthlyLimitUsd: 50 }, 49.5, est(1)))).toBe("spend_limit");
  });
  it("maxCostUsd refuses jobs estimated above it", () => {
    expect(code(() => checkBudget(funded, 0, est(2), 1))).toBe("job_over_budget");
  });
  it("running jobs stop once in-flight cost passes the credit (with grace), the monthly limit, or maxCostUsd", () => {
    const run = (pendingCostUsd: number, jobCostUsd = pendingCostUsd) => ({ pendingCostUsd, jobCostUsd });
    expect(checkRunningBudget(funded, 0, run(10.04)).stop).toBe(false);
    expect(checkRunningBudget(funded, 0, run(10.06)).stop).toBe(true);
    expect(checkRunningBudget({ creditUsd: 100, monthlyLimitUsd: 20 }, 19.5, run(0.6)).stop).toBe(true);
    expect(checkRunningBudget(funded, 0, run(1, 3), 2).stop).toBe(true);
  });
});

describe("submit gates", () => {
  it("no credit → 402 payment_required; job bigger than the credit → insufficient_credit", async () => {
    expect(await codeOf(await submit((await newOrg({ creditUsd: 0 })).key))).toBe("payment_required");
    expect(await codeOf(await submit((await newOrg({ creditUsd: 0.0000001 })).key))).toBe("insufficient_credit");
    expect(modal.submit).toHaveLength(0);
  });
  it("leftover free seconds don't let a workspace without credit render", async () => {
    expect(await codeOf(await submit((await newOrg({ creditUsd: 0, freeSecondsRemaining: 1800 })).key))).toBe("payment_required");
  });
  it("a workspace's own monthly limit is enforced, and can be changed", async () => {
    const { orgId, key } = await newOrg({ monthlyLimitUsd: 0.000001 });
    expect(await codeOf(await submit(key))).toBe("spend_limit");
    const put = await limitRoute.PUT(new Request("https://renda.test/api/v1/billing/limit", {
      method: "PUT", headers: { authorization: `Bearer ${key}`, "content-type": "application/json" }, body: JSON.stringify({ monthlyLimitUsd: 50 }),
    }), undefined as never);
    expect((await put.json()).monthlyLimitUsd).toBe(50);
    expect((await submit(key)).status).toBe(201);
    expect((await billingOf(orgId)).activeJobs).toBe(1);
  });
  it("maxCostUsd below the estimate → 422 and nothing runs", async () => {
    const { key } = await newOrg();
    expect(await codeOf(await submit(key, { maxCostUsd: 0.0000001 }))).toBe("job_over_budget");
    expect(modal.submit).toHaveLength(0);
  });
  it("caps concurrent jobs; a finished job frees its slot", async () => {
    vi.stubEnv("MAX_ACTIVE_JOBS_PAID", "2");
    const { orgId, key } = await newOrg();
    const a = await (await submit(key)).json();
    expect((await submit(key)).status).toBe(201);
    const third = await submit(key);
    expect(third.status).toBe(429);
    expect(await codeOf(third)).toBe("concurrency_limit");
    await send({ jobId: a.id, type: "done", outputs: [] });
    expect((await billingOf(orgId)).activeJobs).toBe(1);
    expect((await submit(key)).status).toBe(201);
  });
  it("parallel submits can't exceed the cap", async () => {
    vi.stubEnv("MAX_ACTIVE_JOBS_PAID", "2");
    const { orgId, key } = await newOrg();
    const res = await Promise.all(Array.from({ length: 6 }, () => submit(key)));
    expect(res.filter((r) => r.status === 201)).toHaveLength(2);
    expect((await billingOf(orgId)).activeJobs).toBe(2);
  });
  it("a failed submit to the worker releases the slot and is reported", async () => {
    const { orgId, key } = await newOrg();
    vi.stubEnv("MODAL_SUBMIT_URL", "https://modal.down/submit");
    expect(await codeOf(await submit(key))).toBe("worker_unavailable");
    expect((await billingOf(orgId)).activeJobs).toBe(0);
    expect(posthog.captureError).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ source: "worker_submit" }));
  });
});

describe("usage draws down prepaid credit exactly once, atomically", () => {
  it("charges the credit, records totals, and ignores duplicates", async () => {
    const { orgId } = await newOrg({ creditUsd: 1 });
    const [job] = await testDb.insert(schema.jobs).values({ orgId, package: {} }).returning();
    expect(await recordUsage(job.id, orgId, "0", "gpu-l4", 60)).toEqual({ freeSeconds: 0, billedSeconds: 60, costUsd: 0.035 });
    expect(await recordUsage(job.id, orgId, "0", "gpu-l4", 60)).toBeNull();
    expect((await billingOf(orgId)).creditUsd).toBeCloseTo(0.965, 6);
    const j = await jobOf(job.id);
    expect(j.secondsGpuL4).toBe(60);
    expect(j.costUsd).toBeCloseTo(0.035, 6);
  });
  it("concurrent chunk reports all land on the balance", async () => {
    const { orgId } = await newOrg({ creditUsd: 1 });
    const [job] = await testDb.insert(schema.jobs).values({ orgId, package: {} }).returning();
    await Promise.all(Array.from({ length: 5 }, (_, i) => recordUsage(job.id, orgId, String(i), "cpu", 60)));
    expect((await billingOf(orgId)).creditUsd).toBeCloseTo(1 - 5 * 0.015, 6);
  });
  it("never reports usage to Stripe meters (credit is prepaid)", async () => {
    const { orgId } = await newOrg({ stripeCustomerId: `cus_${Math.random()}` });
    const job = await (await submit((await newOrg()).key)).json();
    await send({ jobId: job.id, type: "usage", hardware: "cpu", seconds: 10, chunk: 0 });
    expect(fakeStripe.billing.meterEvents.create).not.toHaveBeenCalled();
    expect(orgId).toBeTruthy();
  });
});

describe("running jobs are stopped when credit runs out", () => {
  it("stops the job, cancels the worker call and frees the slot; a late done doesn't revive it", async () => {
    const { orgId, key } = await newOrg({ creditUsd: 0.05 });
    vi.stubEnv("MAX_ACTIVE_JOBS_PAID", "20");
    const job = await (await submit(key, { package: PKG })).json();
    expect(job.status).toBe("queued");
    await send({ jobId: job.id, type: "meter", chunk: 0, hardware: "cpu", elapsedSeconds: 30 });
    expect((await jobOf(job.id)).status).toBe("queued");
    await new Promise((r) => setTimeout(r, 2_600)); // past the meter write throttle
    await send({ jobId: job.id, type: "meter", chunk: 0, hardware: "cpu", elapsedSeconds: 600 }); // $0.15 in flight
    const j = await jobOf(job.id);
    expect(j.status).toBe("failed");
    expect(j.error).toMatch(/render credit ran out/);
    expect(modal.cancel).toContainEqual(expect.objectContaining({ jobId: job.id }));
    expect((await billingOf(orgId)).activeJobs).toBe(0);
    await send({ jobId: job.id, type: "done", outputs: [] });
    expect((await jobOf(job.id)).status).toBe("failed");
  });
});

describe("sweep", () => {
  it("fails jobs the worker went quiet on, keeps long jobs that are still heartbeating, and reports", async () => {
    const { orgId } = await newOrg();
    const old = new Date(Date.now() - 2 * 3_600_000);
    const [quiet] = await testDb.insert(schema.jobs).values({ orgId, package: {}, status: "rendering", createdAt: old, lastEventAt: new Date(Date.now() - 20 * 60_000) }).returning();
    const [alive] = await testDb.insert(schema.jobs).values({ orgId, package: {}, status: "queued", createdAt: old, lastEventAt: new Date(Date.now() - 60 * 60_000) }).returning();
    await send({ jobId: alive.id, type: "heartbeat" });
    const [ancient] = await testDb.insert(schema.jobs).values({ orgId, package: {}, status: "rendering", createdAt: new Date(Date.now() - 7 * 3_600_000), lastEventAt: new Date() }).returning();
    await testDb.update(schema.billing).set({ activeJobs: 99 }).where(eq(schema.billing.orgId, orgId)); // drifted counter
    const { failed } = await sweepStaleJobs();
    expect(failed).toEqual(expect.arrayContaining([quiet.id, ancient.id]));
    expect(failed).not.toContain(alive.id);
    expect((await jobOf(quiet.id)).error).toMatch(/no word from the render worker/);
    expect((await jobOf(ancient.id)).error).toMatch(/still running after/);
    expect(posthog.captureError).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ job_id: quiet.id, source: "sweep" }));
    expect((await billingOf(orgId)).activeJobs).toBe(1); // recounted: only `alive` is active
  });
});

describe("prepaid credit purchases", () => {
  const session = (orgId: string, id: string, cents: number, extra: Record<string, unknown> = {}) => ({
    type: "checkout.session.completed",
    data: { object: { id, mode: "payment", payment_status: "paid", amount_subtotal: cents, payment_intent: `pi_${id}`, metadata: { renda_org: orgId, renda_kind: "credit" }, ...extra } },
  });

  it("checkout opens a payment-mode session for the chosen amount, clamped to $5–$1000", async () => {
    const { key } = await newOrg({ creditUsd: 0 });
    const buy = (body: unknown) => checkoutRoute.POST(new Request("https://renda.test/api/v1/billing/checkout", {
      method: "POST", headers: { authorization: `Bearer ${key}`, "content-type": "application/json" }, body: JSON.stringify(body),
    }), undefined as never);
    const r = await buy({ amountUsd: 40 });
    expect((await r.json()).url).toBe("https://checkout.stripe.test/s");
    const params = fakeStripe.checkout.sessions.create.mock.calls[0][0] as { mode: string; line_items: { quantity: number; price_data: { unit_amount: number } }[] };
    expect(params.mode).toBe("payment");
    expect(params.line_items[0]).toMatchObject({ quantity: 40, price_data: { unit_amount: 100 } });
    expect((await buy({ amountUsd: 2 })).status).toBe(400);
    expect((await buy({})).status).toBe(200);
  });
  it("a paid session credits the balance once; replays and unpaid sessions don't", async () => {
    const { orgId } = await newOrg({ creditUsd: 0 });
    await stripeEvent(session(orgId, "cs_1", 2500));
    await stripeEvent(session(orgId, "cs_1", 2500)); // replay
    await stripeEvent(session(orgId, "cs_2", 1000, { payment_status: "unpaid" })); // e.g. bank debit pending
    expect((await billingOf(orgId)).creditUsd).toBe(25);
    await stripeEvent({ ...session(orgId, "cs_2", 1000), type: "checkout.session.async_payment_succeeded" });
    expect((await billingOf(orgId)).creditUsd).toBe(35);
  });
  it("refunds take credit back off, counting each refunded cent once", async () => {
    const { orgId } = await newOrg({ creditUsd: 0 });
    await stripeEvent(session(orgId, "cs_r", 5000));
    const refund = (cents: number) => stripeEvent({ type: "charge.refunded", data: { object: { payment_intent: "pi_cs_r", amount_refunded: cents } } });
    await refund(2000);
    await refund(2000); // replay
    expect((await billingOf(orgId)).creditUsd).toBe(30);
    await refund(5000); // refunded in full
    expect((await billingOf(orgId)).creditUsd).toBe(0);
  });
  it("credit unlocks a trial workspace's resolution cap", async () => {
    const [org] = await testDb.insert(schema.orgs).values({ name: "t", isTrial: true, trialExpiresAt: new Date(Date.now() + 86_400_000) }).returning();
    await testDb.insert(schema.billing).values({ orgId: org.id, freeSecondsRemaining: 0, creditUsd: 0 });
    const k = generateApiKey();
    await testDb.insert(schema.apiKeys).values({ orgId: org.id, name: "k", prefix: k.prefix, hash: k.hash });
    const big = { ...PKG, render: { resolution: [3840, 2160] } };
    expect(await codeOf(await submit(k.key, { package: big }))).toBe("trial_limit");
    await stripeEvent(session(org.id, "cs_t", 1000));
    expect((await submit(k.key, { package: big })).status).toBe(201);
  });
});

describe("no free render time", () => {
  it("anonymous trials start with no free seconds and no credit", async () => {
    const t = await createTrial("198.51.100.77");
    expect(t.freeSecondsLeft).toBe(0);
    const b = await billingOf(t.orgId);
    expect(b.freeSecondsRemaining).toBe(0);
    expect(b.creditUsd).toBe(0);
  });
  it("rate-limits trials by IPv6 /64 and isn't fooled by parallel requests", async () => {
    expect(networkOf("2001:db8:1:2:aaaa::1")).toBe(networkOf("2001:0db8:0001:0002:ffff:1:2:3"));
    expect(networkOf("2001:db8:1:2::1")).not.toBe(networkOf("2001:db8:1:3::1"));
    expect(networkOf("::ffff:203.0.113.9")).toBe("203.0.113.9");
    const results = await Promise.allSettled(Array.from({ length: 5 }, () => createTrial("2001:db8:9:9::42")));
    expect(results.filter((r) => r.status === "fulfilled")).toHaveLength(1);
  });
});
