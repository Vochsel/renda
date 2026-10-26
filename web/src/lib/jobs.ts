import { createHash } from "node:crypto";
import { captureError, track } from "./posthog";
import { and, desc, eq, inArray, lt, or, sql } from "drizzle-orm";
import { db, schema } from "./db";
import type { JobOutput } from "./db/schema";
import { appUrl, env, envOptional } from "./env";
import { ApiError } from "./errors";
import {
  acquireJobSlot, checkBudget, checkRunningBudget, getBillingRow, recordUsage, reconcileUsage, recountActiveJobs,
  releaseJobSlot, reportMeter, spentThisMonthUsd,
} from "./billing";
import { estimateHyperframes, estimatePriorities, estimateScene, type Priority } from "./estimate";
import { flattenForOrg } from "./layers";
import { parseJobPackage } from "./packages";
import { ensureVerifiedAssets } from "./assets";
import { assertWithinTrialLimits } from "./trials";
import { outputPrefix, presignGet } from "./r2";
import { costUsd as priceOf, HARDWARE, type Hardware } from "./pricing";
import { liveCost, METER_WRITE_INTERVAL_MS, type LiveCost } from "./live";
import { workerJobCredentials } from "./worker-auth";

const { jobs } = schema;
type JobRow = typeof jobs.$inferSelect;

export const TERMINAL = new Set(["succeeded", "failed", "canceled"]);
const ACTIVE = ["queued", "planning", "rendering", "encoding"] as const;
/** Calls to the Modal submit/cancel endpoints give up after this. */
const WORKER_FETCH_TIMEOUT_MS = 30_000;

export type Job = {
  id: string;
  kind: "scene" | "hyperframes";
  name: string | null;
  status: JobRow["status"];
  priority: JobRow["priority"];
  progress: number;
  engine?: string;
  hardware?: string;
  createdAt: string;
  startedAt?: string;
  finishedAt?: string;
  billedSeconds: number;
  costUsd: number;
  cached: boolean;
  warnings: string[];
  error?: string;
  estimate?: unknown;
  /** Layer files the submitted package was flattened from (layered submissions only). */
  layers?: string[];
  outputs: { name: string; url: string; contentType: string; bytes: number }[];
  /** While running: billed so far + running containers, at list price (the final bill can be lower). */
  live?: LiveCost;
};

export async function serializeJob(j: JobRow): Promise<Job> {
  const outputs = await Promise.all(
    (j.outputs ?? []).map(async (o) => ({ name: o.name, url: await presignGet(o.key, undefined, o.name), contentType: o.contentType, bytes: o.bytes })),
  );
  return {
    id: j.id,
    kind: j.kind,
    name: j.name,
    status: j.status,
    priority: j.priority,
    progress: j.progress,
    engine: j.engine ?? undefined,
    hardware: j.hardware ?? undefined,
    createdAt: j.createdAt.toISOString(),
    startedAt: j.startedAt?.toISOString(),
    finishedAt: j.finishedAt?.toISOString(),
    billedSeconds: Math.round((j.secondsCpu + j.secondsGpuL4 + j.secondsGpuL40s) * 10) / 10,
    costUsd: j.costUsd,
    cached: j.cached,
    warnings: j.warnings ?? [],
    error: j.error ?? undefined,
    estimate: j.estimate ?? undefined,
    layers: j.source?.layers,
    outputs,
    live: TERMINAL.has(j.status) ? undefined : liveCost(j.live, j, Date.now()),
  };
}

/** Stable JSON (sorted keys) so equal packages hash equally. */
export function canonicalJson(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonicalJson).join(",")}]`;
  if (v && typeof v === "object") {
    return `{${Object.keys(v).sort().filter((k) => (v as Record<string, unknown>)[k] !== undefined)
      .map((k) => `${JSON.stringify(k)}:${canonicalJson((v as Record<string, unknown>)[k])}`).join(",")}}`;
  }
  return JSON.stringify(v);
}

export function cacheKeyFor(kind: string, pkg: unknown): string {
  const engines = process.env.RENDA_ENGINE_VERSIONS ?? "v1";
  return createHash("sha256").update(`${kind}\n${engines}\n${canonicalJson(pkg)}`).digest("hex");
}

export type SubmitInput = {
  orgId: string;
  userId: string | null;
  kind: "scene" | "hyperframes";
  package: unknown;
  priority: Priority;
  idempotencyKey?: string;
  /** Refuse the job if it's estimated above this, and stop it if it runs past it. */
  maxCostUsd?: number;
  /** The submitting org; anonymous trials get resolution/priority limits. */
  org?: Pick<typeof schema.orgs.$inferSelect, "isTrial" | "claimedAt">;
};

export function estimateFor(parsed: ReturnType<typeof parseJobPackage>, priority: Priority) {
  return parsed.kind === "scene" ? estimateScene(parsed.pkg, priority) : estimateHyperframes(parsed.pkg);
}

/** estimateFor + cost/wall ranges for every priority (the /estimate API response). */
export function estimateWithPriorities(parsed: ReturnType<typeof parseJobPackage>, priority: Priority) {
  const e = estimateFor(parsed, priority);
  return parsed.kind === "scene" ? { ...e, priorities: estimatePriorities(parsed.pkg) } : e;
}

export async function submitJob(input: SubmitInput): Promise<Job> {
  if (input.idempotencyKey) {
    const [prior] = await db().select().from(jobs)
      .where(and(eq(jobs.orgId, input.orgId), eq(jobs.idempotencyKey, input.idempotencyKey)));
    if (prior) return serializeJob(prior);
  }

  // `extends` / package references → one flat package (https + this org's asset:// layers)
  const layered = input.kind === "scene" ? await flattenForOrg(input.orgId, input.package) : null;
  const source = layered && layered.layers.length
    ? { submitted: input.package, layers: layered.layers, provenance: layered.provenance }
    : null;
  const parsed = parseJobPackage(input.kind, layered ? layered.package : input.package);
  if (input.org) {
    const resolution = parsed.kind === "scene"
      ? parsed.pkg.render.resolution
      : parsed.pkg.width && parsed.pkg.height ? ([parsed.pkg.width, parsed.pkg.height] as [number, number]) : null;
    assertWithinTrialLimits(input.org, { resolution, priority: input.priority });
  }
  // Registered by this org via POST /uploads *and* bytes uploaded + hash-verified (lib/assets.ts).
  const verified = await ensureVerifiedAssets(input.orgId, parsed.uris.assetShas);

  const estimate = estimateFor(parsed, input.priority);
  const name = parsed.kind === "scene" ? parsed.pkg.name ?? null : parsed.pkg.name ?? parsed.pkg.composition ?? null;

  // Server-side result cache: only when every input is content-addressed.
  const usesHttps = parsed.kind === "scene"
    ? Object.values(parsed.pkg.assets).some((a) => a.uri.startsWith("https://"))
    : parsed.pkg.project.startsWith("https://");
  const cacheKey = usesHttps ? null : cacheKeyFor(parsed.kind, parsed.pkg);
  if (cacheKey) {
    const [hit] = await db().select().from(jobs)
      .where(and(eq(jobs.orgId, input.orgId), eq(jobs.cacheKey, cacheKey), eq(jobs.status, "succeeded")))
      .orderBy(desc(jobs.createdAt)).limit(1);
    if (hit) {
      const now = new Date();
      const [row] = await db().insert(jobs).values({
        orgId: input.orgId, createdBy: input.userId, kind: parsed.kind, priority: input.priority, package: parsed.raw, name,
        status: "succeeded", progress: 1, engine: hit.engine, hardware: hit.hardware, outputs: hit.outputs, cached: true,
        cacheKey, idempotencyKey: input.idempotencyKey, estimate, source, startedAt: now, finishedAt: now,
        warnings: [`served from cache of ${hit.id}; billed $0`],
      }).returning();
      return serializeJob(row);
    }
  }

  // Cache hits above are free; anything that renders must fit the budget and take a concurrency slot.
  const bill = await getBillingRow(input.orgId);
  checkBudget(bill, await spentThisMonthUsd(input.orgId), estimate, input.maxCostUsd);
  await acquireJobSlot(input.orgId, bill);

  let row: JobRow;
  try {
    [row] = await db().insert(jobs).values({
      orgId: input.orgId, createdBy: input.userId, kind: parsed.kind, priority: input.priority, package: parsed.raw, name,
      cacheKey, idempotencyKey: input.idempotencyKey, estimate, engine: estimate.engine, hardware: estimate.hardware,
      warnings: estimate.warnings, source, maxCostUsd: input.maxCostUsd ?? null, lastEventAt: new Date(),
    }).returning();
  } catch (e) {
    await releaseJobSlot(input.orgId);
    throw e;
  }

  const inputs: Record<string, string> = {};
  await Promise.all(verified.map(async ({ sha, key }) => { inputs[`asset://${sha}`] = await presignGet(key, 6 * 3600); }));

  try {
    const res = await fetch(env("MODAL_SUBMIT_URL"), {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${env("RENDA_WORKER_TOKEN")}` },
      signal: AbortSignal.timeout(WORKER_FETCH_TIMEOUT_MS),
      body: JSON.stringify({
        jobId: row.id,
        kind: parsed.kind,
        package: parsed.raw,
        priority: input.priority,
        inputs,
        outputPrefix: outputPrefix(row.id),
        callbackUrl: `${appUrl()}/api/v1/internal/worker/events`,
        ...(await workerJobCredentials(row.id)), // per-job signing key + scoped R2 credentials
      }),
    });
    if (!res.ok) throw new Error(`worker ${res.status}: ${(await res.text()).slice(0, 300)}`);
    const body = (await res.json().catch(() => ({}))) as { callId?: string };
    const [updated] = await db().update(jobs).set({ modalCallId: body.callId ?? null }).where(eq(jobs.id, row.id)).returning();
    return serializeJob(updated);
  } catch (e) {
    console.error("submit failed", e);
    captureError(e, { userId: input.userId, orgId: input.orgId, job_id: row.id, source: "worker_submit" });
    await finishJob(row, { status: "failed", error: "Could not reach the render backend." });
    throw new ApiError("worker_unavailable", undefined, { jobId: row.id });
  }
}

/**
 * Move a job to a terminal status, once: a no-op (null) if it already finished. Frees its concurrency slot.
 * Every terminal transition goes through here so slots can't leak.
 */
async function finishJob(job: Pick<JobRow, "id" | "orgId">, set: Partial<typeof jobs.$inferInsert> & { status: "succeeded" | "failed" | "canceled" }) {
  const [row] = await db().update(jobs).set({ finishedAt: new Date(), ...set })
    .where(and(eq(jobs.id, job.id), inArray(jobs.status, [...ACTIVE]))).returning();
  if (row) await releaseJobSlot(job.orgId);
  return row ?? null;
}

/** Best-effort cancel of the job's Modal call tree. */
async function cancelWorkerCall(jobId: string, callId: string | null) {
  const cancelUrl = envOptional("MODAL_CANCEL_URL");
  if (!cancelUrl) return;
  await fetch(cancelUrl, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${env("RENDA_WORKER_TOKEN")}` },
    signal: AbortSignal.timeout(WORKER_FETCH_TIMEOUT_MS),
    body: JSON.stringify({ jobId, callId }),
  }).catch((e) => console.warn("worker cancel", jobId, e));
}

export async function getJob(orgId: string, id: string): Promise<JobRow> {
  const [row] = await db().select().from(jobs).where(and(eq(jobs.id, id), eq(jobs.orgId, orgId)));
  if (!row) throw new ApiError("not_found", `No job ${id}.`);
  return row;
}

export async function listJobs(orgId: string, opts: { limit?: number; before?: string; status?: string } = {}) {
  const conds = [eq(jobs.orgId, orgId)];
  if (opts.before) conds.push(lt(jobs.createdAt, new Date(opts.before)));
  if (opts.status) conds.push(eq(jobs.status, opts.status as JobRow["status"]));
  return db().select().from(jobs).where(and(...conds)).orderBy(desc(jobs.createdAt)).limit(Math.min(opts.limit ?? 50, 200));
}

/** Several of an org's jobs by id in one query (other orgs' ids are simply absent). */
export async function getJobsByIds(orgId: string, ids: string[]): Promise<JobRow[]> {
  const unique = [...new Set(ids)].slice(0, 100);
  if (!unique.length) return [];
  return db().select().from(jobs).where(and(eq(jobs.orgId, orgId), inArray(jobs.id, unique)));
}

/** Jobs still running (queued → encoding), newest first — for the dashboard's live "Active jobs" strip. */
export async function listActiveJobs(orgId: string, limit = 20) {
  return db().select().from(jobs)
    .where(and(eq(jobs.orgId, orgId), inArray(jobs.status, ["queued", "planning", "rendering", "encoding"])))
    .orderBy(desc(jobs.createdAt)).limit(limit);
}

export async function cancelJob(orgId: string, id: string): Promise<JobRow> {
  const job = await getJob(orgId, id);
  if (TERMINAL.has(job.status)) throw new ApiError("conflict", `Job is already ${job.status}.`);
  await cancelWorkerCall(job.id, job.modalCallId);
  // It may have finished while we cancelled; then it keeps its real status.
  return (await finishJob(job, { status: "canceled" })) ?? getJob(orgId, id);
}

function envMinutes(name: string, fallback: number): number {
  const v = Number(envOptional(name));
  return Number.isFinite(v) && v > 0 ? v : fallback;
}

/**
 * Fail jobs the worker has gone quiet on (crashed or OOM-killed orchestrator, lost callbacks) so nothing sits in
 * "rendering" forever. A job is live while events arrive: the orchestrator heartbeats every minute, including
 * while chunks wait for GPUs, so the cutoff is silence, not age. A hard cap past the worker's own timeouts
 * catches anything else. Also recounts concurrency slots and re-sends unconfirmed usage to Stripe.
 * Run from the cron route.
 */
export async function sweepStaleJobs(now = Date.now()) {
  const quietMinutes = envMinutes("JOB_STALE_MINUTES", 15);
  const maxHours = envMinutes("JOB_MAX_HOURS", 6.5); // orchestrate's Modal timeout is 6 h
  const stale = await db().select().from(jobs).where(and(
    inArray(jobs.status, [...ACTIVE]),
    or(
      lt(sql`coalesce(${jobs.lastEventAt}, ${jobs.createdAt})`, new Date(now - quietMinutes * 60_000)),
      lt(jobs.createdAt, new Date(now - maxHours * 3_600_000)),
    ),
  ));
  const failed: string[] = [];
  for (const job of stale) {
    const quiet = (job.lastEventAt ?? job.createdAt).getTime() < now - quietMinutes * 60_000;
    const reason = quiet ? `no word from the render worker for ${quietMinutes} minutes` : `still running after ${maxHours} hours`;
    await cancelWorkerCall(job.id, job.modalCallId);
    const row = await finishJob(job, { status: "failed", error: `Timed out: ${reason}. Retry, or contact support with the job id.` });
    if (!row) continue;
    failed.push(job.id);
    // Infrastructure failures: these never produce a worker `failed` event, so report them here.
    captureError(new Error(`job swept: ${quiet ? "worker went quiet" : "exceeded max runtime"}`), {
      userId: job.createdBy, orgId: job.orgId, job_id: job.id, engine: job.engine, hardware: job.hardware, status: job.status, source: "sweep",
    });
    await trackFinished(row, "failed", { error: reason, swept: true }).catch(() => {});
  }
  await recountActiveJobs().catch((e) => captureError(e, { source: "sweep_recount" }));
  const reconciled = await reconcileUsage().catch((e) => { captureError(e, { source: "sweep_reconcile" }); return 0; });
  return { failed, reconciled };
}

// ---- worker events -------------------------------------------------------

export type WorkerOutput = {
  key: string;
  contentType: string;
  bytes: number;
  name?: string;
  pass?: string;
  width?: number;
  height?: number;
  thumbKey?: string;
  placeholder?: string;
};

/** Keep only known, well-typed fields from a worker output (the payload is HMAC-signed, but stay strict). */
export function toJobOutput(o: WorkerOutput): JobOutput {
  const out: JobOutput = { name: o.name ?? o.key.split("/").pop()!, key: o.key, contentType: o.contentType, bytes: o.bytes };
  if (typeof o.pass === "string") out.pass = o.pass;
  if (Number.isInteger(o.width) && Number.isInteger(o.height) && o.width! > 0 && o.height! > 0) {
    out.width = o.width;
    out.height = o.height;
  }
  if (typeof o.thumbKey === "string" && o.thumbKey.startsWith(o.key.slice(0, o.key.lastIndexOf("/") + 1))) out.thumbKey = o.thumbKey;
  if (typeof o.placeholder === "string" && o.placeholder.startsWith("data:image/") && o.placeholder.length <= 4096) out.placeholder = o.placeholder;
  return out;
}

export type WorkerEvent =
  | { jobId: string; type: "status"; status: JobRow["status"]; progress?: number; engine?: string; hardware?: string }
  | { jobId: string; type: "usage"; hardware: Hardware; seconds: number; chunk: number | string }
  | { jobId: string; type: "warning"; message: string }
  | { jobId: string; type: "done"; outputs: WorkerOutput[]; cached?: boolean }
  | { jobId: string; type: "failed"; error: string }
  | { jobId: string; type: "meter"; chunk: number | string; hardware: Hardware; elapsedSeconds: number; at?: number }
  | { jobId: string; type: "heartbeat" };

export async function applyWorkerEvent(ev: WorkerEvent) {
  const [job] = await db().select().from(jobs).where(eq(jobs.id, ev.jobId));
  if (!job) throw new ApiError("not_found", `No job ${ev.jobId}.`);

  switch (ev.type) {
    case "meter":
      return applyMeter(job, ev);
    case "usage": {
      // Usage is always recorded, even for canceled jobs: the compute was spent.
      if (!HARDWARE.includes(ev.hardware) || !(ev.seconds >= 0)) throw new ApiError("invalid_request", "bad usage event");
      const chunk = String(ev.chunk);
      const split = await recordUsage(job.id, job.orgId, chunk, ev.hardware, ev.seconds);
      if (!split) return { duplicate: true };
      await markChunkFinal(job.id, chunk, ev.hardware, ev.seconds);
      // A failure here is retried by the sweep (reconcileUsage): the row stays reported_to_stripe = false.
      await reportMeter(job.orgId, job.id, chunk, ev.hardware, split.billedSeconds)
        .catch((e) => captureError(e, { orgId: job.orgId, job_id: job.id, chunk, source: "stripe_meter" }));
      return { ok: true, ...split };
    }
    case "heartbeat":
      await touch(job.id);
      return { ok: true };
    case "warning":
      // Deliveries can repeat (retries, replays inside the signature window): keep each message once.
      if ((job.warnings as string[] | null)?.includes(ev.message)) return { duplicate: true };
      await db().update(jobs).set({ warnings: sql`${jobs.warnings} || ${JSON.stringify([ev.message])}::jsonb` }).where(eq(jobs.id, job.id));
      return { ok: true };
    case "status":
      if (TERMINAL.has(job.status)) return { ignored: true };
      await db().update(jobs).set({
        lastEventAt: new Date(),
        status: TERMINAL.has(ev.status) ? job.status : ev.status,
        progress: ev.progress ?? job.progress,
        engine: ev.engine ?? job.engine,
        hardware: ev.hardware ?? job.hardware,
        startedAt: job.startedAt ?? (ev.status === "queued" ? null : new Date()),
      }).where(eq(jobs.id, job.id));
      return { ok: true };
    case "done": {
      // Once a job is canceled, stopped or swept it stays that way (the customer may already have retried it).
      const outputs: JobOutput[] = ev.outputs.map(toJobOutput);
      const row = await finishJob(job, { status: "succeeded", progress: 1, outputs, cached: Boolean(ev.cached), startedAt: job.startedAt ?? new Date() });
      if (!row) return { ignored: true };
      await trackFinished(job, "succeeded", { cached: Boolean(ev.cached), outputs: outputs.length }).catch(() => {});
      return { ok: true };
    }
    case "failed":
      if (!(await finishJob(job, { status: "failed", error: ev.error }))) return { ignored: true };
      await trackFinished(job, "failed", { error: ev.error.slice(0, 300) }).catch(() => {});
      // Render failures are product errors too: one error-tracking issue per kind of failure.
      captureError(new Error(`job failed: ${ev.error.split("\n")[0].slice(0, 200)}`), {
        userId: job.createdBy, orgId: job.orgId, job_id: job.id, engine: job.engine, hardware: job.hardware, source: "worker",
      });
      return { ok: true };
  }
}

/** Billed totals come from the usage events (reported before `done`); the stopwatch is the job's own. */
async function trackFinished(job: JobRow, status: "succeeded" | "failed", extra: Record<string, unknown>) {
  const [fresh] = await db().select().from(jobs).where(eq(jobs.id, job.id)).catch(() => [job]);
  const j = fresh ?? job;
  track(`job_${status}`, { userId: j.createdBy, orgId: j.orgId }, {
    job_id: j.id,
    kind: j.kind,
    priority: j.priority,
    engine: j.engine,
    hardware: j.hardware,
    billed_seconds: Math.round((j.secondsCpu + j.secondsGpuL4 + j.secondsGpuL40s) * 10) / 10,
    cost_usd: j.costUsd,
    estimate_usd: (j.estimate as { costUsd?: number } | null)?.costUsd,
    wall_seconds: j.startedAt ? Math.round((Date.now() - j.startedAt.getTime()) / 100) / 10 : undefined,
    ...extra,
  });
}

/**
 * Live metering heartbeat (never billing). One atomic jsonb merge per accepted event; at most one write per job
 * every METER_WRITE_INTERVAL_MS, except a chunk's first heartbeat. Ignored once the job is finished or the
 * chunk has been billed.
 */
async function applyMeter(job: JobRow, ev: Extract<WorkerEvent, { type: "meter" }>) {
  if (TERMINAL.has(job.status)) return { ignored: true };
  if (!HARDWARE.includes(ev.hardware) || !(ev.elapsedSeconds >= 0) || ev.elapsedSeconds > 86_400) {
    throw new ApiError("invalid_request", "bad meter event");
  }
  const chunk = String(ev.chunk).slice(0, 64);
  const now = Date.now();
  const entry = JSON.stringify({ hardware: ev.hardware, elapsed: ev.elapsedSeconds, at: now });
  const updated = await db()
    .update(jobs)
    .set({
      lastEventAt: new Date(now),
      live: sql`jsonb_build_object(
        'updatedAt', ${now}::bigint,
        'chunks', coalesce(${jobs.live}->'chunks', '{}'::jsonb) || jsonb_build_object(${chunk}::text, ${entry}::jsonb))`,
    })
    .where(and(
      eq(jobs.id, job.id),
      sql`${jobs.status} not in ('succeeded', 'failed', 'canceled')`,
      sql`coalesce((${jobs.live}->'chunks'->${chunk}::text->>'final')::boolean, false) = false`,
      sql`(${jobs.live} is null
           or not (coalesce(${jobs.live}->'chunks', '{}'::jsonb) ? ${chunk}::text)
           or coalesce((${jobs.live}->>'updatedAt')::bigint, 0) <= ${now - METER_WRITE_INTERVAL_MS}::bigint)`,
    ))
    .returning({ id: jobs.id, live: jobs.live, costUsd: jobs.costUsd, maxCostUsd: jobs.maxCostUsd });
  if (!updated.length) return { throttled: true };
  await enforceRunningBudget(job, updated[0], now)
    .catch((e) => captureError(e, { orgId: job.orgId, job_id: job.id, source: "budget_check" }));
  return { ok: true };
}

/** In-flight (not yet billed) seconds and list-price cost of a job's running chunks. */
export function pendingUsage(live: JobRow["live"]): { seconds: number; costUsd: number } {
  let seconds = 0, cost = 0;
  for (const c of Object.values(live?.chunks ?? {})) {
    if (c.final || !(HARDWARE as string[]).includes(c.hardware)) continue;
    seconds += c.elapsed;
    cost += priceOf(c.hardware as Hardware, c.elapsed);
  }
  return { seconds, costUsd: cost };
}

/**
 * Stop a running job once it would overrun the free balance, the monthly spending limit or its own maxCostUsd.
 * Checked on each accepted meter write (at most every METER_WRITE_INTERVAL_MS per job).
 */
async function enforceRunningBudget(job: JobRow, now: Pick<JobRow, "live" | "costUsd" | "maxCostUsd">, at: number) {
  const pending = pendingUsage(now.live);
  const b = await getBillingRow(job.orgId);
  const verdict = checkRunningBudget(b, await spentThisMonthUsd(job.orgId, new Date(at)), {
    pendingSeconds: pending.seconds, pendingCostUsd: pending.costUsd, jobCostUsd: now.costUsd + pending.costUsd,
  }, now.maxCostUsd);
  if (!verdict.stop) return;
  await cancelWorkerCall(job.id, job.modalCallId);
  const row = await finishJob(job, { status: "failed", error: verdict.reason });
  if (row) await trackFinished(row, "failed", { error: verdict.reason, budget_stop: true }).catch(() => {});
}

/** Record that the worker is still alive for this job (see sweepStaleJobs). */
async function touch(jobId: string) {
  await db().update(jobs).set({ lastEventAt: new Date() }).where(and(eq(jobs.id, jobId), inArray(jobs.status, [...ACTIVE])));
}

/** A billed chunk leaves the live set: its seconds are in the job's billed columns from here on. */
async function markChunkFinal(jobId: string, chunk: string, hardware: Hardware, seconds: number) {
  const entry = JSON.stringify({ hardware, elapsed: seconds, at: Date.now(), final: true });
  await db()
    .update(jobs)
    .set({
      live: sql`jsonb_build_object(
        'updatedAt', coalesce((${jobs.live}->>'updatedAt')::bigint, 0),
        'chunks', coalesce(${jobs.live}->'chunks', '{}'::jsonb) || jsonb_build_object(${chunk}::text, ${entry}::jsonb))`,
    })
    .where(eq(jobs.id, jobId));
}
