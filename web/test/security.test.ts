/**
 * Security tests against the real route handlers, auth and Drizzle queries, on an in-memory Postgres
 * (PGlite) built from the real migrations. R2, WorkOS and the Modal submit call are faked.
 */
import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { PGlite } from "@electric-sql/pglite";
import { drizzle } from "drizzle-orm/pglite";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";

process.env.RENDA_WORKER_SECRET = "worker-secret";
process.env.RENDA_WORKER_TOKEN = "worker-token";
process.env.MODAL_SUBMIT_URL = "https://modal.test/submit";
process.env.APP_URL = "https://renda.test";
process.env.R2_OUTPUT_TTL_SECONDS = "3600";
process.env.RATE_LIMIT_DISABLED = "1";
process.env.MAX_ACTIVE_JOBS_FREE = "100"; // fake worker: submitted jobs stay queued

const pg = new PGlite();
const testDb = drizzle(pg, { schema });
vi.mock("@/lib/db", () => ({ db: () => testDb, schema }));

// In-memory R2.
const objects = new Map<string, Buffer>();
vi.mock("@/lib/r2", () => ({
  assetKey: (sha: string) => `assets/${sha}`,
  stagingKey: (org: string, sha: string) => `staging/${org}/${sha}`,
  outputPrefix: (id: string) => `outputs/${id}/`,
  objectExists: async (k: string) => objects.has(k),
  presignPut: async (k: string) => `https://r2.test/put/${k}`,
  presignGet: async (k: string) => `https://r2.test/get/${k}`,
  getObjectText: async (k: string) => {
    const b = objects.get(k);
    if (!b) throw new Error("NoSuchKey");
    return b.toString("utf8");
  },
  sha256OfObject: async (k: string) => createHash("sha256").update(objects.get(k)!).digest("hex"),
  copyObject: async (from: string, to: string) => void objects.set(to, objects.get(from)!),
  deleteObject: async (k: string) => void objects.delete(k),
}));

vi.mock("@workos-inc/authkit-nextjs", () => ({ withAuth: vi.fn() })); // dashboard sessions; API keys don't use it

const workos = { createMagicAuth: vi.fn(async () => ({})) };
vi.mock("@/lib/workos", () => ({ workos: () => ({ userManagement: workos }) }));

const modalCalls: unknown[] = [];
vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
  if (String(url).startsWith("https://modal.test")) {
    modalCalls.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({ accepted: true, callId: "fc-1" }), { status: 200 });
  }
  throw new Error(`unexpected fetch ${url}`);
}));

const { generateApiKey } = await import("@/lib/apikeys");
const jobsRoute = await import("@/app/api/v1/jobs/route");
const jobRoute = await import("@/app/api/v1/jobs/[id]/route");
const cancelRoute = await import("@/app/api/v1/jobs/[id]/cancel/route");
const uploadsRoute = await import("@/app/api/v1/uploads/route");
const outputsRoute = await import("@/app/api/v1/outputs/route");
const usageRoute = await import("@/app/api/v1/usage/route");
const flattenRoute = await import("@/app/api/v1/packages/flatten/route");
const estimateRoute = await import("@/app/api/v1/estimate/route");
const eventsRoute = await import("@/app/api/v1/internal/worker/events/route");
const magicStart = await import("@/app/api/v1/auth/magic/start/route");
const { signTimed, signBody } = await import("@/lib/hmac");
const { clientIp, signForwardedIp, FORWARDED_IP_HEADER } = await import("@/lib/clientip");

const keys: Record<"A" | "B", string> = { A: "", B: "" };
const orgIds: Record<"A" | "B", string> = { A: "", B: "" };

beforeAll(async () => {
  const dir = join(__dirname, "..", "drizzle");
  for (const f of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) {
    for (const stmt of readFileSync(join(dir, f), "utf8").split("--> statement-breakpoint")) {
      if (stmt.trim()) await pg.exec(stmt);
    }
  }
  for (const who of ["A", "B"] as const) {
    const [org] = await testDb.insert(schema.orgs).values({ name: `org ${who}` }).returning();
    await testDb.insert(schema.billing).values({ orgId: org.id, freeSecondsRemaining: 0, creditUsd: 100 });
    const k = generateApiKey();
    await testDb.insert(schema.apiKeys).values({ orgId: org.id, name: who, prefix: k.prefix, hash: k.hash });
    keys[who] = k.key;
    orgIds[who] = org.id;
  }
});

beforeEach(() => {
  modalCalls.length = 0;
});

const H = (who: "A" | "B", extra: Record<string, string> = {}) => ({ authorization: `Bearer ${keys[who]}`, "content-type": "application/json", ...extra });
type Handler = (req: Request, ctx: never) => Promise<Response>;
type Route = { GET?: Handler; POST?: Handler };
const post = (h: Route, url: string, who: "A" | "B", body: unknown, ctx?: unknown) =>
  h.POST!(new Request(`https://renda.test${url}`, { method: "POST", headers: H(who), body: JSON.stringify(body) }), ctx as never);
const get = (h: Route, url: string, who: "A" | "B", ctx?: unknown) =>
  h.GET!(new Request(`https://renda.test${url}`, { headers: H(who) }), ctx as never);
const params = (id: string) => ({ params: Promise.resolve({ id }) });

const sha = (b: Buffer) => createHash("sha256").update(b).digest("hex");
const MODEL = Buffer.from("glTF real model bytes");
const pkgFor = (s: string) => ({ version: 1, assets: { m: { uri: `asset://${s}` } }, scene: { root: "m" } });

async function upload(who: "A" | "B", bytes: Buffer, claimedSha = sha(bytes)) {
  const res = await post(uploadsRoute, "/api/v1/uploads", who, { filename: "m.glb", size: bytes.length, sha256: claimedSha, contentType: "model/gltf-binary" });
  const body = await res.json();
  if (body.uploadUrl) objects.set(body.uploadUrl.replace("https://r2.test/put/", ""), bytes); // the client's PUT
  return body;
}

describe("uploads: proof of possession, no poisoning", () => {
  it("uploads go to the caller's staging key and are promoted only after the hash checks out", async () => {
    const s = sha(MODEL);
    const up = await upload("A", MODEL);
    expect(up.exists).toBe(false);
    expect(up.uploadUrl).toContain(`staging/${orgIds.A}/${s}`);
    expect(objects.has(`assets/${s}`)).toBe(false);
    const res = await post(jobsRoute, "/api/v1/jobs", "A", { package: pkgFor(s) });
    expect(res.status).toBe(201);
    expect(objects.get(`assets/${s}`)).toEqual(MODEL);
    expect(objects.has(`staging/${orgIds.A}/${s}`)).toBe(false);
    expect((modalCalls[0] as { inputs: Record<string, string> }).inputs[`asset://${s}`]).toBe(`https://r2.test/get/assets/${s}`);
    expect((await upload("A", MODEL)).exists).toBe(true); // verified now: no re-upload
  });

  it("another org can't use a file by knowing its hash", async () => {
    const s = sha(MODEL); // A's verified file
    const reg = await post(uploadsRoute, "/api/v1/uploads", "B", { filename: "x", size: MODEL.length, sha256: s });
    const body = await reg.json();
    expect(body.exists).toBe(false); // must upload its own copy
    const res = await post(jobsRoute, "/api/v1/jobs", "B", { package: pkgFor(s) }); // ...but didn't
    expect(res.status).toBe(422);
    expect((await res.json()).error.code).toBe("asset_missing");
  });

  it("wrong bytes under someone else's hash are rejected and never reach the shared copy", async () => {
    const victim = Buffer.from("victim's future upload");
    const s = sha(victim);
    await upload("B", Buffer.from("attacker garbage"), s); // B claims the victim's hash first
    const res = await post(jobsRoute, "/api/v1/jobs", "B", { package: pkgFor(s) });
    expect(res.status).toBe(422);
    expect((await res.json()).error.code).toBe("asset_mismatch");
    expect(objects.has(`assets/${s}`)).toBe(false);
    await upload("A", victim); // the real owner still gets the right bytes in
    expect((await post(jobsRoute, "/api/v1/jobs", "A", { package: pkgFor(s) })).status).toBe(201);
    expect(objects.get(`assets/${s}`)).toEqual(victim);
  });

  it("only accepts plain MIME types", async () => {
    const res = await post(uploadsRoute, "/api/v1/uploads", "A", { filename: "a", size: 1, sha256: "a".repeat(64), contentType: "text/html; charset=utf-8\r\nx: y" });
    expect(res.status).toBe(400);
  });
});

describe("org isolation", () => {
  let jobId = "";
  beforeAll(async () => {
    const s = sha(MODEL);
    const res = await post(jobsRoute, "/api/v1/jobs", "A", { package: pkgFor(s), idempotencyKey: "iso-1" });
    jobId = (await res.json()).id;
  });

  it("org B can't read, cancel or list org A's jobs", async () => {
    expect((await get(jobRoute, `/api/v1/jobs/${jobId}`, "A", params(jobId))).status).toBe(200);
    expect((await get(jobRoute, `/api/v1/jobs/${jobId}`, "B", params(jobId))).status).toBe(404);
    expect((await post(cancelRoute, `/api/v1/jobs/${jobId}/cancel`, "B", {}, params(jobId))).status).toBe(404);
    const list = await (await get(jobsRoute, "/api/v1/jobs", "B")).json();
    expect(list.jobs.map((j: { id: string }) => j.id)).not.toContain(jobId);
    const [row] = await testDb.select().from(schema.jobs).where((await import("drizzle-orm")).eq(schema.jobs.id, jobId));
    expect(row.status).not.toBe("canceled");
  });

  it("idempotency keys are per org", async () => {
    const res = await post(jobsRoute, "/api/v1/jobs", "B", { package: pkgFor(sha(MODEL)), idempotencyKey: "iso-1" });
    expect(res.status).toBe(422); // B's own (missing) asset, not A's job returned
  });

  it("outputs and usage are scoped to the caller", async () => {
    const { eq } = await import("drizzle-orm");
    await testDb.update(schema.jobs).set({ status: "succeeded", outputs: [{ name: "a.png", key: `outputs/${jobId}/a.png`, contentType: "image/png", bytes: 1 }] })
      .where(eq(schema.jobs.id, jobId));
    await testDb.insert(schema.usageEvents).values({ jobId, chunk: "0", orgId: orgIds.A, hardware: "gpu-l4", seconds: 42 });
    const a = await (await get(outputsRoute, "/api/v1/outputs", "A")).json();
    const b = await (await get(outputsRoute, "/api/v1/outputs", "B")).json();
    expect(a.items.length).toBeGreaterThan(0);
    expect(b.items).toEqual([]);
    const ua = await (await get(usageRoute, "/api/v1/usage", "A")).json();
    const ub = await (await get(usageRoute, "/api/v1/usage", "B")).json();
    expect(JSON.stringify(ua)).toContain("42");
    expect(JSON.stringify(ub)).not.toContain("42");
  });

  it("org B can't read org A's uploads as package layers", async () => {
    const layer = Buffer.from(JSON.stringify({ version: 1, render: { resolution: [64, 64] } }));
    const s = sha(layer);
    await upload("A", layer);
    const base = { version: 1, extends: [`asset://${s}`], scene: { root: "https://cdn.example/a.glb" } };
    const ok = await post(flattenRoute, "/api/v1/packages/flatten", "A", { package: base });
    expect(ok.status).toBe(200);
    for (const route of [flattenRoute, estimateRoute]) {
      const url = route === flattenRoute ? "/api/v1/packages/flatten" : "/api/v1/estimate";
      const res = await post(route, url, "B", { package: base });
      expect(res.status).toBe(422);
      expect((await res.json()).error.code).toBe("layer_error");
    }
  });

  it("every v1 route rejects missing or revoked keys", async () => {
    const anon = (h: Route, url: string, method: "GET" | "POST" = "GET") =>
      h[method]!(new Request(`https://renda.test${url}`, { method, headers: { "content-type": "application/json" }, body: method === "POST" ? "{}" : undefined }), params("x") as never);
    for (const [h, url, m] of [[jobsRoute, "/api/v1/jobs", "GET"], [jobsRoute, "/api/v1/jobs", "POST"], [jobRoute, "/api/v1/jobs/x", "GET"],
      [cancelRoute, "/api/v1/jobs/x/cancel", "POST"], [uploadsRoute, "/api/v1/uploads", "POST"], [outputsRoute, "/api/v1/outputs", "GET"],
      [usageRoute, "/api/v1/usage", "GET"], [flattenRoute, "/api/v1/packages/flatten", "POST"], [estimateRoute, "/api/v1/estimate", "POST"]] as const) {
      expect((await anon(h, url, m)).status, `${m} ${url}`).toBe(401);
    }
    const { eq } = await import("drizzle-orm");
    await testDb.update(schema.apiKeys).set({ revokedAt: new Date() }).where(eq(schema.apiKeys.orgId, orgIds.B));
    expect((await get(jobsRoute, "/api/v1/jobs", "B")).status).toBe(401);
    await testDb.update(schema.apiKeys).set({ revokedAt: null }).where(eq(schema.apiKeys.orgId, orgIds.B));
  });
});

describe("worker events", () => {
  const send = (body: string, headers: Record<string, string>) =>
    eventsRoute.POST(new Request("https://renda.test/api/v1/internal/worker/events", { method: "POST", headers, body }), undefined as never);
  const ev = (jobId: string) => JSON.stringify({ jobId, type: "warning", message: "hello" });

  it("accepts fresh timed signatures and rejects stale, future or forged ones", async () => {
    const [job] = await testDb.select().from(schema.jobs).limit(1);
    const body = ev(job.id);
    const now = Math.floor(Date.now() / 1000);
    const sig = (ts: number, secret = "worker-secret") => ({ "x-renda-timestamp": String(ts), "x-renda-signature": signTimed(body, ts, secret) });
    expect((await send(body, sig(now))).status).toBe(200);
    expect((await send(body, sig(now - 3600))).status).toBe(401);
    expect((await send(body, sig(now + 3600))).status).toBe(401);
    expect((await send(body, sig(now, "wrong"))).status).toBe(401);
    expect((await send(body, { "x-renda-timestamp": String(now), "x-renda-signature": signTimed(body, now - 1, "worker-secret") })).status).toBe(401);
  });

  it("legacy untimed signatures work until switched off", async () => {
    const [job] = await testDb.select().from(schema.jobs).limit(1);
    const body = ev(job.id);
    expect((await send(body, { "x-renda-signature": signBody(body, "worker-secret") })).status).toBe(200);
    process.env.RENDA_ACCEPT_UNTIMED_WORKER_EVENTS = "0";
    try {
      expect((await send(body, { "x-renda-signature": signBody(body, "worker-secret") })).status).toBe(401);
    } finally {
      delete process.env.RENDA_ACCEPT_UNTIMED_WORKER_EVENTS;
    }
  });

  it("replayed warnings are stored once", async () => {
    const [job] = await testDb.select().from(schema.jobs).limit(1);
    const body = JSON.stringify({ jobId: job.id, type: "warning", message: "only once" });
    const now = Math.floor(Date.now() / 1000);
    for (let i = 0; i < 3; i++) await send(body, { "x-renda-timestamp": String(now), "x-renda-signature": signTimed(body, now, "worker-secret") });
    const { eq } = await import("drizzle-orm");
    const [row] = await testDb.select().from(schema.jobs).where(eq(schema.jobs.id, job.id));
    expect((row.warnings as string[]).filter((w) => w === "only once")).toHaveLength(1);
  });
});

describe("rate limiting", () => {
  it("limits magic-code requests per network and per address, with Retry-After", async () => {
    process.env.RATE_LIMIT_DISABLED = "0";
    process.env.RATE_LIMIT_MAGIC_START_IP = "2/600";
    process.env.RATE_LIMIT_MAGIC_START_EMAIL = "100/600";
    try {
      const req = () => magicStart.POST(new Request("https://renda.test/api/v1/auth/magic/start", {
        method: "POST", headers: { "content-type": "application/json", "x-real-ip": "203.0.113.9" }, body: JSON.stringify({ email: "a@b.test" }),
      }), undefined as never);
      expect((await req()).status).toBe(200);
      expect((await req()).status).toBe(200);
      const third = await req();
      expect(third.status).toBe(429);
      expect(Number(third.headers.get("retry-after"))).toBeGreaterThan(0);
      expect(workos.createMagicAuth).toHaveBeenCalledTimes(2);
    } finally {
      process.env.RATE_LIMIT_DISABLED = "1";
      delete process.env.RATE_LIMIT_MAGIC_START_IP;
      delete process.env.RATE_LIMIT_MAGIC_START_EMAIL;
    }
  });

  it("stores hashed keys, never raw IPs or emails", async () => {
    const rows = await testDb.select().from(schema.rateLimits);
    expect(rows.length).toBeGreaterThan(0);
    for (const r of rows) expect(r.key).toMatch(/^[0-9a-f]{64}$/);
    expect(JSON.stringify(rows)).not.toContain("203.0.113.9");
  });
});

describe("client IP", () => {
  const req = (headers: Record<string, string>) => new Request("https://renda.test/x", { headers });
  it("trusts the forwarded header only when signed by the server", () => {
    expect(clientIp(req({ [FORWARDED_IP_HEADER]: signForwardedIp("198.51.100.7"), "x-real-ip": "10.0.0.1" }))).toBe("198.51.100.7");
    expect(clientIp(req({ [FORWARDED_IP_HEADER]: "198.51.100.7.deadbeef", "x-real-ip": "203.0.113.1" }))).toBe("203.0.113.1");
    expect(clientIp(req({ [FORWARDED_IP_HEADER]: "198.51.100.7", "x-forwarded-for": "203.0.113.2, 10.0.0.1" }))).toBe("203.0.113.2");
  });
});
