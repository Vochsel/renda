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
const eventsRoute = await import("@/app/api/v1/internal/worker/events/route");
const webhookRoute = await import("@/app/api/v1/webhooks/stripe/route");
const { signTimed } = await import("@/lib/hmac");
const { checkBudget, checkRunningBudget, recordUsage, reconcileUsage, cardBillable } = await import("@/lib/billing");
const { sweepStaleJobs } = await import("@/lib/jobs");
const { normalizeEmail, claimFreeGrant } = await import("@/lib/freegrants");
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
});

type Billing = Partial<typeof schema.billing.$inferInsert>;
async function newOrg(b: Billing = {}) {
  const [org] = await testDb.insert(schema.orgs).values({ name: "org" }).returning();
  await testDb.insert(schema.billing).values({ orgId: org.id, freeSecondsRemaining: 1800, ...b });
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
  const est = (s: number, usd: number) => ({ estSeconds: [s, s * 2] as [number, number], estCostUsd: [usd, usd * 2] as [number, number] });
  const free = { hasPaymentMethod: false, subscriptionStatus: null, freeSecondsRemaining: 100, monthlyLimitUsd: null };
  const card = { ...free, hasPaymentMethod: true, subscriptionStatus: "active", freeSecondsRemaining: 0 };
  const code = (f: () => void) => { try { f(); return null; } catch (e) { return (e as ApiError).code; } };

  it("free tier: needs a balance that covers the low estimate", () => {
    expect(code(() => checkBudget(free, 0, est(60, 0.1)))).toBeNull();
    expect(code(() => checkBudget(free, 0, est(101, 0.1)))).toBe("insufficient_free_minutes");
    expect(code(() => checkBudget({ ...free, freeSecondsRemaining: 0 }, 0, est(1, 0)))).toBe("payment_required");
  });
  it("card: monthly limit (default or own) applies, free balance doesn't", () => {
    expect(code(() => checkBudget(card, 99, est(1e6, 0.5)))).toBeNull();
    expect(code(() => checkBudget(card, 99.9, est(1, 0.5)))).toBe("spend_limit");
    expect(code(() => checkBudget({ ...card, monthlyLimitUsd: 1000 }, 99.9, est(1, 0.5)))).toBeNull();
  });
  it("a failed or canceled subscription stops card billing until fixed", () => {
    for (const s of ["past_due", "unpaid", "canceled"]) expect(cardBillable({ hasPaymentMethod: true, subscriptionStatus: s })).toBe(false);
    expect(code(() => checkBudget({ ...card, subscriptionStatus: "past_due" }, 0, est(1, 0)))).toBe("payment_failed");
    // free minutes still work while the card is blocked
    expect(code(() => checkBudget({ ...card, subscriptionStatus: "past_due", freeSecondsRemaining: 50 }, 0, est(10, 0)))).toBeNull();
  });
  it("maxCostUsd refuses jobs estimated above it", () => {
    expect(code(() => checkBudget(card, 0, est(1, 2), 1))).toBe("job_over_budget");
  });
  it("running jobs stop past the free balance (with grace), the monthly limit, or maxCostUsd", () => {
    const run = (pendingSeconds: number, pendingCostUsd = 0, jobCostUsd = 0) => ({ pendingSeconds, pendingCostUsd, jobCostUsd });
    expect(checkRunningBudget(free, 0, run(120)).stop).toBe(false);
    expect(checkRunningBudget(free, 0, run(131)).stop).toBe(true);
    expect(checkRunningBudget(card, 99.5, run(1e5, 0.4)).stop).toBe(false);
    expect(checkRunningBudget(card, 99.5, run(1e5, 0.6)).stop).toBe(true);
    expect(checkRunningBudget(card, 0, run(1, 0, 3), 2).stop).toBe(true);
  });
});

describe("submit gates", () => {
  it("no card and no free minutes → 402 payment_required; too big for the balance → insufficient_free_minutes", async () => {
    expect(await codeOf(await submit((await newOrg({ freeSecondsRemaining: 0 })).key))).toBe("payment_required");
    expect(await codeOf(await submit((await newOrg({ freeSecondsRemaining: 1 })).key))).toBe("insufficient_free_minutes");
    expect(modal.submit).toHaveLength(0);
  });
  it("card holders are held to the monthly limit, which they can set", async () => {
    const { orgId, key } = await newOrg({ hasPaymentMethod: true, subscriptionStatus: "active", monthlyLimitUsd: 0.000001 });
    expect(await codeOf(await submit(key))).toBe("spend_limit");
    const put = await limitRoute.PUT(new Request("https://renda.test/api/v1/billing/limit", {
      method: "PUT", headers: { authorization: `Bearer ${key}`, "content-type": "application/json" }, body: JSON.stringify({ monthlyLimitUsd: 50 }),
    }), undefined as never);
    expect((await put.json()).monthlyLimitUsd).toBe(50);
    expect((await submit(key)).status).toBe(201);
    expect((await billingOf(orgId)).activeJobs).toBe(1);
  });
  it("maxCostUsd below the estimate → 422 and nothing runs", async () => {
    const { key } = await newOrg({ hasPaymentMethod: true });
    expect(await codeOf(await submit(key, { maxCostUsd: 0.0000001 }))).toBe("job_over_budget");
    expect(modal.submit).toHaveLength(0);
  });
  it("caps concurrent jobs; a finished job frees its slot", async () => {
    vi.stubEnv("MAX_ACTIVE_JOBS_FREE", "2");
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
    vi.stubEnv("MAX_ACTIVE_JOBS_FREE", "2");
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

describe("usage is recorded exactly once, atomically", () => {
  it("splits free/billed, never drives the balance negative, and ignores duplicates", async () => {
    const { orgId } = await newOrg({ freeSecondsRemaining: 30 });
    const [job] = await testDb.insert(schema.jobs).values({ orgId, package: {} }).returning();
    const first = await recordUsage(job.id, orgId, "0", "gpu-l4", 60);
    expect(first).toEqual({ freeSeconds: 30, billedSeconds: 30, costUsd: 0.0175 });
    expect(await recordUsage(job.id, orgId, "0", "gpu-l4", 60)).toBeNull();
    expect((await billingOf(orgId)).freeSecondsRemaining).toBe(0);
    const j = await jobOf(job.id);
    expect(j.secondsGpuL4).toBe(60);
    expect(j.costUsd).toBeCloseTo(0.0175, 6);
    expect(await recordUsage(job.id, orgId, "1", "cpu", 120)).toEqual({ freeSeconds: 0, billedSeconds: 120, costUsd: 0.03 });
  });
  it("concurrent reports of different chunks each take their own share of the free balance", async () => {
    const { orgId } = await newOrg({ freeSecondsRemaining: 100 });
    const [job] = await testDb.insert(schema.jobs).values({ orgId, package: {} }).returning();
    const parts = await Promise.all(Array.from({ length: 5 }, (_, i) => recordUsage(job.id, orgId, String(i), "cpu", 40)));
    expect(parts.reduce((s, p) => s + p!.freeSeconds, 0)).toBe(100);
    expect((await billingOf(orgId)).freeSecondsRemaining).toBe(0);
  });
  it("re-sends usage Stripe never confirmed", async () => {
    const { orgId } = await newOrg({ stripeCustomerId: `cus_${Math.random()}` });
    const [job] = await testDb.insert(schema.jobs).values({ orgId, package: {} }).returning();
    await testDb.insert(schema.usageEvents).values({ jobId: job.id, chunk: "0", orgId, hardware: "cpu", seconds: 10, billedSeconds: 10,
      createdAt: new Date(Date.now() - 10 * 60_000) });
    expect(await reconcileUsage()).toBeGreaterThanOrEqual(1);
    expect(fakeStripe.billing.meterEvents.create).toHaveBeenCalledWith(expect.objectContaining({ identifier: `${job.id}:0` }));
    const [u] = await testDb.select().from(schema.usageEvents).where(eq(schema.usageEvents.jobId, job.id));
    expect(u.reportedToStripe).toBe(true);
  });
});

describe("running jobs are stopped at the budget", () => {
  it("a free-tier job is stopped when its running time passes the balance", async () => {
    const { orgId, key } = await newOrg({ freeSecondsRemaining: 60 });
    const job = await (await submit(key)).json();
    await send({ jobId: job.id, type: "meter", chunk: 0, hardware: "cpu", elapsedSeconds: 30 });
    expect((await jobOf(job.id)).status).toBe("queued");
    await new Promise((r) => setTimeout(r, 2_600)); // past the meter write throttle
    await send({ jobId: job.id, type: "meter", chunk: 0, hardware: "cpu", elapsedSeconds: 200 });
    const j = await jobOf(job.id);
    expect(j.status).toBe("failed");
    expect(j.error).toMatch(/free render minutes ran out/);
    expect(modal.cancel).toContainEqual(expect.objectContaining({ jobId: job.id }));
    expect((await billingOf(orgId)).activeJobs).toBe(0);
    // the late `done` doesn't resurrect it
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

describe("Stripe webhook", () => {
  it("a past_due subscription pauses card renders; paying it resumes them", async () => {
    const cus = `cus_${Math.random()}`;
    const { key } = await newOrg({ stripeCustomerId: cus, hasPaymentMethod: true, subscriptionStatus: "active", freeSecondsRemaining: 0 });
    const sub = (status: string) => ({ type: "customer.subscription.updated", data: { object: { id: "sub_1", customer: cus, status } } });
    await stripeEvent(sub("past_due"));
    expect(await codeOf(await submit(key))).toBe("payment_failed");
    await stripeEvent(sub("active"));
    expect((await submit(key)).status).toBe(201);
    await stripeEvent({ type: "customer.subscription.deleted", data: { object: { id: "sub_1", customer: cus, status: "canceled" } } });
    expect(await codeOf(await submit(key))).toBe("payment_failed");
  });
  it("a detached card clears the flag (the customer is only in previous_attributes)", async () => {
    const cus = `cus_${Math.random()}`;
    const { orgId } = await newOrg({ stripeCustomerId: cus, hasPaymentMethod: true });
    await stripeEvent({ type: "payment_method.detached", data: { object: { id: "pm_1", customer: null }, previous_attributes: { customer: cus } } });
    expect((await billingOf(orgId)).hasPaymentMethod).toBe(false);
  });
});

describe("free-tier farming", () => {
  it("normalises emails for the once-per-person grant", () => {
    expect(normalizeEmail(" Ben.V+renda1@GoogleMail.com")).toBe("benv@gmail.com");
    expect(normalizeEmail("a.b+x@example.com")).toBe("a.b@example.com");
  });
  it("grants the free tier once per person", async () => {
    const { orgId } = await newOrg();
    expect(await claimFreeGrant("someone@example.com", orgId)).toBe(true);
    expect(await claimFreeGrant("Someone+2@example.com", orgId)).toBe(false);
  });
  it("rate-limits trials by IPv6 /64 and isn't fooled by parallel requests", async () => {
    expect(networkOf("2001:db8:1:2:aaaa::1")).toBe(networkOf("2001:0db8:0001:0002:ffff:1:2:3"));
    expect(networkOf("2001:db8:1:2::1")).not.toBe(networkOf("2001:db8:1:3::1"));
    expect(networkOf("::ffff:203.0.113.9")).toBe("203.0.113.9");
    const results = await Promise.allSettled(Array.from({ length: 5 }, () => createTrial("2001:db8:9:9::42")));
    expect(results.filter((r) => r.status === "fulfilled")).toHaveLength(1);
  });
});
