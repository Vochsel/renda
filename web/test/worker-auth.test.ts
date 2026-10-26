/**
 * Per-job worker credentials: derived event-signing keys, the events route's per-job verification, the
 * `done` output-prefix rule, and R2 temporary credentials. Real route + migrations on PGlite; R2 is faked.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { PGlite } from "@electric-sql/pglite";
import { drizzle } from "drizzle-orm/pglite";
import { eq } from "drizzle-orm";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";

process.env.RENDA_WORKER_SECRET = "worker-secret";
process.env.RATE_LIMIT_DISABLED = "1";

const pg = new PGlite();
const testDb = drizzle(pg, { schema });
vi.mock("@/lib/db", () => ({ db: () => testDb, schema }));
vi.mock("@/lib/r2", () => ({ outputPrefix: (id: string) => `outputs/${id}/`, presignGet: async (k: string) => `https://r2.test/get/${k}` }));
vi.mock("@/lib/billing", async (orig) => ({ ...(await orig<typeof import("@/lib/billing")>()), reportMeter: async () => undefined }));

const eventsRoute = await import("@/app/api/v1/internal/worker/events/route");
const { signTimed, signBody } = await import("@/lib/hmac");
const { deriveJobKey, mintJobStorage, workerJobCredentials, assertOwnOutputs } = await import("@/lib/worker-auth");

let orgId = "";
beforeAll(async () => {
  const dir = join(__dirname, "..", "drizzle");
  for (const f of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) {
    for (const stmt of readFileSync(join(dir, f), "utf8").split("--> statement-breakpoint")) if (stmt.trim()) await pg.exec(stmt);
  }
  const [org] = await testDb.insert(schema.orgs).values({ name: "org" }).returning();
  await testDb.insert(schema.billing).values({ orgId: org.id, freeSecondsRemaining: 0 });
  orgId = org.id;
});
afterEach(() => {
  vi.restoreAllMocks();
  for (const k of ["RENDA_ACCEPT_MASTER_WORKER_EVENTS", "CLOUDFLARE_API_TOKEN", "R2_ACCOUNT_ID", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_JOB_CREDENTIAL_TTL_SECONDS"]) {
    delete process.env[k];
  }
});

async function newJob() {
  const [j] = await testDb.insert(schema.jobs).values({ orgId, package: {}, status: "rendering" }).returning();
  return j.id;
}
function send(ev: unknown, key: string, ts = Math.floor(Date.now() / 1000)) {
  const body = JSON.stringify(ev);
  const headers = { "content-type": "application/json", "x-renda-timestamp": String(ts), "x-renda-signature": signTimed(body, ts, key) };
  return eventsRoute.POST(new Request("https://renda.test/api/v1/internal/worker/events", { method: "POST", headers, body }), undefined as never);
}
const row = async (id: string) => (await testDb.select().from(schema.jobs).where(eq(schema.jobs.id, id)))[0];
const warning = (jobId: string, message = "w") => ({ jobId, type: "warning", message });

describe("job keys", () => {
  it("are HMAC-SHA256(secret, 'job:' + jobId), matching the worker's test vector", () => {
    // worker/tests/test_containment.py derives JOB_KEY the same way
    expect(deriveJobKey("job_1", "master")).toBe("85d94b3bf5446b758d9a7701d987fc457bb52ca3df63d1ad6c0c09359016e8e1");
    expect(deriveJobKey("job_2", "master")).not.toBe(deriveJobKey("job_1", "master"));
  });
});

describe("worker events route", () => {
  it("accepts events signed with the job's own key and rejects another job's key", async () => {
    const a = await newJob();
    const b = await newJob();
    expect((await send(warning(a), deriveJobKey(a))).status).toBe(200);
    expect((await send(warning(a, "forged"), deriveJobKey(b))).status).toBe(401);
    expect((await row(a)).warnings).not.toContain("forged");
  });

  it("keeps the ±300 s window for job-key signatures", async () => {
    const a = await newJob();
    const now = Math.floor(Date.now() / 1000);
    expect((await send(warning(a), deriveJobKey(a), now - 3600)).status).toBe(401);
    expect((await send(warning(a), deriveJobKey(a), now + 3600)).status).toBe(401);
  });

  it("accepts master-secret signatures during rollout, until RENDA_ACCEPT_MASTER_WORKER_EVENTS=0", async () => {
    const a = await newJob();
    expect((await send(warning(a, "old worker"), "worker-secret")).status).toBe(200);
    process.env.RENDA_ACCEPT_MASTER_WORKER_EVENTS = "0";
    expect((await send(warning(a, "old worker 2"), "worker-secret")).status).toBe(401);
    expect((await send(warning(a, "new worker"), deriveJobKey(a))).status).toBe(200);
    // legacy untimed signatures follow the same key rules
    const body = JSON.stringify(warning(a, "untimed"));
    const untimed = (key: string) => eventsRoute.POST(new Request("https://renda.test/x", {
      method: "POST", headers: { "x-renda-signature": signBody(body, key) }, body,
    }), undefined as never);
    expect((await untimed("worker-secret")).status).toBe(401);
    expect((await untimed(deriveJobKey(a))).status).toBe(200);
  });

  it("rejects batches that mix jobs, even when signed with the first job's key", async () => {
    const a = await newJob();
    const b = await newJob();
    const res = await send([warning(a, "ok"), warning(b, "smuggled")], deriveJobKey(a));
    expect(res.status).toBe(400);
    expect((await row(b)).warnings).not.toContain("smuggled");
    expect((await row(a)).warnings).not.toContain("ok"); // nothing in a rejected batch is applied
  });

  it("rejects `done` pointing outside outputs/<jobId>/", async () => {
    const a = await newJob();
    const b = await newJob();
    const done = (key: string, thumbKey?: string) => ({ jobId: a, type: "done", cached: false,
      outputs: [{ key, contentType: "image/png", bytes: 1, ...(thumbKey ? { thumbKey } : {}) }] });
    for (const bad of [`outputs/${b}/frame.png`, `assets/${"0".repeat(64)}`, `outputs/${a}/../${b}/x.png`]) {
      expect((await send(done(bad), deriveJobKey(a))).status, bad).toBe(400);
    }
    expect((await send(done(`outputs/${a}/x.png`, `outputs/${b}/thumbs/x.webp`), deriveJobKey(a))).status).toBe(400);
    expect((await row(a)).status).toBe("rendering");
    expect((await send(done(`outputs/${a}/x.png`), deriveJobKey(a))).status).toBe(200);
    expect((await row(a)).status).toBe("succeeded");
  });

  it("accepts heartbeats", async () => {
    const a = await newJob();
    expect((await send({ jobId: a, type: "heartbeat" }, deriveJobKey(a))).status).toBe(200);
  });

  it("answers unparseable bodies like bad signatures", async () => {
    const res = await eventsRoute.POST(new Request("https://renda.test/x", { method: "POST", body: "not json" }), undefined as never);
    expect(res.status).toBe(401);
  });
});

describe("output prefix rule", () => {
  it("needs an outputs array", () => {
    expect(() => assertOwnOutputs("j", undefined)).toThrow();
    expect(() => assertOwnOutputs("j", [])).not.toThrow();
  });
});

describe("R2 temporary credentials", () => {
  it("are skipped without CLOUDFLARE_API_TOKEN (dev/tests)", async () => {
    const f = vi.fn();
    expect(await mintJobStorage("job_1", f as never)).toBeNull();
    expect(f).not.toHaveBeenCalled();
    const creds = await workerJobCredentials("job_1");
    expect(creds).toEqual({ jobKey: deriveJobKey("job_1") });
  });

  it("are minted per job, scoped to its prefixes, with the server's R2 key as parent", async () => {
    Object.assign(process.env, { CLOUDFLARE_API_TOKEN: "cf", R2_ACCOUNT_ID: "acct", R2_BUCKET: "renda", R2_ACCESS_KEY_ID: "parent" });
    const f = vi.fn(async () => new Response(JSON.stringify({ success: true, result: { accessKeyId: "ak", secretAccessKey: "sk", sessionToken: "st" } })));
    const g = await mintJobStorage("job_1", f as never);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://api.cloudflare.com/client/v4/accounts/acct/r2/temp-access-credentials");
    expect((init.headers as Record<string, string>).authorization).toBe("Bearer cf");
    expect(JSON.parse(String(init.body))).toEqual({
      bucket: "renda", parentAccessKeyId: "parent", permission: "object-read-write", ttlSeconds: 86400,
      prefixes: ["outputs/job_1/", "work/job_1/"],
    });
    expect(g).toMatchObject({ accessKeyId: "ak", secretAccessKey: "sk", sessionToken: "st", bucket: "renda",
      endpoint: "https://acct.r2.cloudflarestorage.com", prefixes: ["outputs/job_1/", "work/job_1/"] });
  });

  it("clamp the TTL to Cloudflare's 7-day limit and fail loudly on API errors", async () => {
    Object.assign(process.env, { CLOUDFLARE_API_TOKEN: "cf", R2_ACCOUNT_ID: "acct", R2_BUCKET: "renda", R2_ACCESS_KEY_ID: "parent",
      R2_JOB_CREDENTIAL_TTL_SECONDS: String(30 * 86400) });
    const ok = vi.fn(async () => new Response(JSON.stringify({ success: true, result: { accessKeyId: "a", secretAccessKey: "s", sessionToken: "t" } })));
    await mintJobStorage("j", ok as never);
    expect(JSON.parse(String((ok.mock.calls[0] as unknown as [string, RequestInit])[1].body)).ttlSeconds).toBe(604800);
    const bad = vi.fn(async () => new Response(JSON.stringify({ success: false, errors: [{ code: 10000, message: "Authentication error" }] }), { status: 403 }));
    await expect(mintJobStorage("j", bad as never)).rejects.toThrow(/403/);
  });
});
