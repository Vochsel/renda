/**
 * Live cost: worker `meter` heartbeats → throttled per-job snapshot → `live` on the job API. Real route handlers
 * and migrations on an in-memory Postgres (PGlite); R2 is faked.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { PGlite } from "@electric-sql/pglite";
import { drizzle } from "drizzle-orm/pglite";
import { eq } from "drizzle-orm";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";

process.env.RENDA_WORKER_SECRET = "worker-secret";
process.env.RENDA_ACCEPT_UNTIMED_WORKER_EVENTS = "0";
process.env.R2_OUTPUT_TTL_SECONDS = "3600";
process.env.RATE_LIMIT_DISABLED = "1";

const pg = new PGlite();
const testDb = drizzle(pg, { schema });
vi.mock("@/lib/db", () => ({ db: () => testDb, schema }));
vi.mock("@/lib/r2", () => ({ outputPrefix: (id: string) => `outputs/${id}/`, presignGet: async (k: string) => `https://r2.test/get/${k}` }));
vi.mock("@/lib/billing", async (orig) => ({ ...(await orig<typeof import("@/lib/billing")>()), reportMeter: async () => undefined }));

const eventsRoute = await import("@/app/api/v1/internal/worker/events/route");
const { signTimed } = await import("@/lib/hmac");
const { serializeJob } = await import("@/lib/jobs");
const { liveCost, interpolateCost, MAX_EXTRAPOLATE_MS, STALE_MS, METER_WRITE_INTERVAL_MS } = await import("@/lib/live");

let orgId = "";
beforeAll(async () => {
  const dir = join(__dirname, "..", "drizzle");
  for (const f of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) {
    for (const stmt of readFileSync(join(dir, f), "utf8").split("--> statement-breakpoint")) if (stmt.trim()) await pg.exec(stmt);
  }
  const [org] = await testDb.insert(schema.orgs).values({ name: "org" }).returning();
  await testDb.insert(schema.billing).values({ orgId: org.id, freeSecondsRemaining: 0, hasPaymentMethod: true });
  orgId = org.id;
});
afterEach(() => vi.restoreAllMocks());

async function newJob(status: "rendering" | "succeeded" = "rendering") {
  const [j] = await testDb.insert(schema.jobs).values({ orgId, package: {}, status }).returning();
  return j.id;
}
function send(ev: Record<string, unknown>, opts: { secret?: string; ts?: number } = {}) {
  const body = JSON.stringify(ev);
  const ts = opts.ts ?? Math.floor(Date.now() / 1000);
  const headers = { "content-type": "application/json", "x-renda-timestamp": String(ts), "x-renda-signature": signTimed(body, ts, opts.secret ?? "worker-secret") };
  return eventsRoute.POST(new Request("https://renda.test/api/v1/internal/worker/events", { method: "POST", headers, body }), undefined as never);
}
const meter = (jobId: string, chunk: number | string, elapsedSeconds: number, hardware = "gpu-l4") =>
  send({ jobId, type: "meter", chunk, hardware, elapsedSeconds, at: Date.now() / 1000 });
const row = async (id: string) => (await testDb.select().from(schema.jobs).where(eq(schema.jobs.id, id)))[0];

describe("live cost maths", () => {
  const billed = { secondsCpu: 2, secondsGpuL4: 0, secondsGpuL40s: 0 };
  it("adds running containers to billed seconds, at list price, and reports the current rate", () => {
    const now = 1_000_000;
    const live = { updatedAt: now, chunks: { "0": { hardware: "gpu-l4", elapsed: 30, at: now - 1_000 }, orchestrate: { hardware: "cpu", elapsed: 1, at: now } } };
    const l = liveCost(live, billed, now);
    expect(l.seconds["gpu-l4"]).toBeCloseTo(31, 5);
    expect(l.seconds.cpu).toBeCloseTo(3, 5);
    expect(l.costUsd).toBeCloseTo((31 * 0.035 + 3 * 0.015) / 60, 4);
    expect(l.activeContainers).toBe(2);
    expect(l.ratePerSecond).toBeCloseTo((0.035 + 0.015) / 60, 8);
  });
  it("caps extrapolation and drops stale containers from the rate", () => {
    const now = 1_000_000;
    const l = liveCost({ updatedAt: now, chunks: { "0": { hardware: "gpu-l4", elapsed: 10, at: now - STALE_MS - 1 } } }, billed, now);
    expect(l.seconds["gpu-l4"]).toBeCloseTo(10 + MAX_EXTRAPOLATE_MS / 1000, 5);
    expect(l.activeContainers).toBe(0);
    expect(l.ratePerSecond).toBe(0);
  });
  it("ignores finished chunks (their seconds are in the billed columns)", () => {
    const l = liveCost({ updatedAt: 1, chunks: { "0": { hardware: "gpu-l4", elapsed: 99, at: 1, final: true } } }, billed, 1);
    expect(l.seconds["gpu-l4"]).toBe(0);
  });
  it("client interpolation ticks up at the rate and is capped", () => {
    const snap = { costUsd: 0.01, ratePerSecond: 0.001, updatedAt: new Date(0).toISOString() };
    expect(interpolateCost(snap, 2_000)).toBeCloseTo(0.012, 6);
    expect(interpolateCost(snap, 3_600_000)).toBeCloseTo(0.01 + 0.001 * (MAX_EXTRAPOLATE_MS + 4_000) / 1000, 6);
  });
});

describe("meter events", () => {
  it("are HMAC-verified like every worker event", async () => {
    const id = await newJob();
    expect((await send({ jobId: id, type: "meter", chunk: 0, hardware: "gpu-l4", elapsedSeconds: 1 }, { secret: "wrong" })).status).toBe(401);
    expect((await send({ jobId: id, type: "meter", chunk: 0, hardware: "gpu-l4", elapsedSeconds: 1 }, { ts: 1 })).status).toBe(401);
    expect((await send({ jobId: id, type: "meter", chunk: 0, hardware: "tpu", elapsedSeconds: 1 })).status).toBe(400);
  });

  it("write at most once per job per interval, except a chunk's first heartbeat", async () => {
    const id = await newJob();
    let now = 10_000_000;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    expect((await (await meter(id, 0, 3)).json()).results[0]).toEqual({ ok: true });
    expect((await (await meter(id, 1, 3)).json()).results[0]).toEqual({ ok: true }); // new chunk: lands
    now += 1_000;
    expect((await (await meter(id, 0, 4)).json()).results[0]).toEqual({ throttled: true });
    now += METER_WRITE_INTERVAL_MS;
    expect((await (await meter(id, 0, 6.5)).json()).results[0]).toEqual({ ok: true });
    const live = (await row(id)).live!;
    expect(Object.keys(live.chunks).sort()).toEqual(["0", "1"]);
    expect(live.chunks["0"].elapsed).toBe(6.5);
    expect(live.updatedAt).toBe(now);
  });

  it("never touch billing; usage marks the chunk final so late heartbeats can't revive it", async () => {
    const id = await newJob();
    await meter(id, 0, 5);
    expect((await row(id)).costUsd).toBe(0);
    await send({ jobId: id, type: "usage", hardware: "gpu-l4", seconds: 6, chunk: 0 });
    const after = await row(id);
    expect(after.live!.chunks["0"].final).toBe(true);
    expect(after.secondsGpuL4).toBe(6);
    vi.spyOn(Date, "now").mockImplementation(() => after.live!.updatedAt + 60_000);
    expect((await (await meter(id, 0, 7)).json()).results[0]).toEqual({ throttled: true });
    expect((await row(id)).live!.chunks["0"].final).toBe(true);
  });

  it("are ignored once the job is finished", async () => {
    const id = await newJob("succeeded");
    expect((await (await meter(id, 0, 1)).json()).results[0]).toEqual({ ignored: true });
    expect((await row(id)).live).toBeNull();
  });
});

describe("replayed worker stream (captured from a real 96-frame turntable on Modal)", () => {
  it("live cost climbs while it runs and lands on the billed total", async () => {
    const fixture = JSON.parse(readFileSync(join(__dirname, "fixtures", "live-meter-stream.json"), "utf8")) as {
      events: { ms: number; event: Record<string, unknown> }[];
    };
    const id = await newJob();
    await testDb.update(schema.jobs).set({ status: "planning" }).where(eq(schema.jobs.id, id));
    const start = 50_000_000;
    let now = start;
    vi.spyOn(Date, "now").mockImplementation(() => now);
    const samples: number[] = [];
    for (const { ms, event } of fixture.events) {
      now = start + ms;
      const res = await send({ ...event, jobId: id }, { ts: Math.floor(now / 1000) });
      expect(res.status).toBe(200);
      const j = await serializeJob(await row(id));
      if (j.live) samples.push(j.live.costUsd);
    }
    const final = await row(id);
    expect(final.status).toBe("succeeded");
    expect(samples.length).toBeGreaterThan(50);
    // Rising overall (it can dip slightly when a chunk's billed seconds replace its extrapolated estimate).
    expect(samples.at(-1)!).toBeGreaterThan(samples[10] * 3);
    for (let i = 1; i < samples.length; i++) expect(samples[i]).toBeGreaterThanOrEqual(samples[i - 1] * 0.9);
    // The last live value is within a few percent of what was billed at list price.
    const listBilled = (final.secondsGpuL4 * 0.035 + final.secondsCpu * 0.015) / 60;
    expect(Math.abs(samples.at(-1)! - listBilled) / listBilled).toBeLessThan(0.1);
    expect((await serializeJob(final)).live).toBeUndefined();
  });
});

describe("job API", () => {
  it("returns `live` while running and drops it when finished", async () => {
    const id = await newJob();
    await meter(id, 0, 12);
    const running = await serializeJob(await row(id));
    expect(running.live).toMatchObject({ activeContainers: 1 });
    expect(running.live!.seconds["gpu-l4"]).toBeGreaterThanOrEqual(12);
    expect(running.live!.costUsd).toBeGreaterThan(0);
    await testDb.update(schema.jobs).set({ status: "succeeded" }).where(eq(schema.jobs.id, id));
    expect((await serializeJob(await row(id))).live).toBeUndefined();
  });
});
