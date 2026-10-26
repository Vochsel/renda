import { createHmac } from "node:crypto";
import { ApiError } from "./errors";
import { env, envOptional } from "./env";
import { verifyWorkerEvent, type WorkerSigResult } from "./hmac";

/**
 * Per-job worker credentials. Render containers process untrusted customer content, so they never hold
 * RENDA_WORKER_SECRET or the bucket-wide R2 key. Each job instead gets:
 *  - jobKey: HMAC-SHA256(RENDA_WORKER_SECRET, "job:" + jobId). The worker signs that job's events with it;
 *    the events route re-derives it from the event's jobId. A leaked jobKey can only speak for its own job.
 *  - storage: Cloudflare R2 temporary credentials scoped to outputs/<jobId>/ and work/<jobId>/.
 */

export function deriveJobKey(jobId: string, secret = env("RENDA_WORKER_SECRET")): string {
  return createHmac("sha256", secret).update(`job:${jobId}`).digest("hex");
}

/** Every bucket prefix a job's containers may read or write. Keep in sync with worker/renda_worker/dispatch.py. */
export const jobPrefixes = (jobId: string) => [`outputs/${jobId}/`, `work/${jobId}/`];

export type StorageGrant = {
  accessKeyId: string;
  secretAccessKey: string;
  sessionToken: string;
  bucket: string;
  endpoint: string;
  prefixes: string[];
  expiresAt: string;
};

const DEFAULT_TTL_S = 24 * 3600; // longer than any job can run (orchestrate times out at 6 h)
const MAX_TTL_S = 7 * 24 * 3600; // Cloudflare's limit

/**
 * Mint R2 temporary credentials for one job (POST /accounts/:id/r2/temp-access-credentials). Needs
 * CLOUDFLARE_API_TOKEN (Account → Workers R2 Storage: Edit); the parent is the server's own R2 key, which
 * caps what the temporary key can do. Returns null when CLOUDFLARE_API_TOKEN isn't set (local dev / tests):
 * the production worker then refuses the job, so set it before deploying that worker.
 */
export async function mintJobStorage(jobId: string, fetchImpl: typeof fetch = fetch): Promise<StorageGrant | null> {
  const token = envOptional("CLOUDFLARE_API_TOKEN");
  if (!token) return null;
  const account = env("R2_ACCOUNT_ID");
  const bucket = env("R2_BUCKET");
  const ttl = Math.min(MAX_TTL_S, Math.max(900, Number(envOptional("R2_JOB_CREDENTIAL_TTL_SECONDS")) || DEFAULT_TTL_S));
  const prefixes = jobPrefixes(jobId);
  const res = await fetchImpl(`https://api.cloudflare.com/client/v4/accounts/${account}/r2/temp-access-credentials`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
    body: JSON.stringify({
      bucket,
      parentAccessKeyId: env("R2_ACCESS_KEY_ID"),
      permission: "object-read-write",
      ttlSeconds: ttl,
      prefixes,
    }),
  });
  const body = (await res.json().catch(() => null)) as
    | { success?: boolean; result?: { accessKeyId?: string; secretAccessKey?: string; sessionToken?: string }; errors?: unknown }
    | null;
  const r = body?.result;
  if (!res.ok || !body?.success || !r?.accessKeyId || !r.secretAccessKey || !r.sessionToken) {
    throw new Error(`R2 temp credentials ${res.status}: ${JSON.stringify(body?.errors ?? body).slice(0, 300)}`);
  }
  return {
    accessKeyId: r.accessKeyId,
    secretAccessKey: r.secretAccessKey,
    sessionToken: r.sessionToken,
    bucket,
    endpoint: `https://${account}.r2.cloudflarestorage.com`,
    prefixes,
    expiresAt: new Date(Date.now() + ttl * 1000).toISOString(),
  };
}

let warnedNoToken = false;

/** Fields to add to the Modal submit body: `{ jobKey, storage? }`. */
export async function workerJobCredentials(jobId: string): Promise<{ jobKey: string; storage?: StorageGrant }> {
  const storage = await mintJobStorage(jobId);
  if (!storage && !warnedNoToken && (warnedNoToken = true)) console.warn("CLOUDFLARE_API_TOKEN unset: submitting without per-job R2 credentials (the production worker will refuse)");
  return { jobKey: deriveJobKey(jobId), ...(storage ? { storage } : {}) };
}

type RawEvent = { jobId?: unknown; type?: unknown; outputs?: unknown };

/**
 * Authenticate a batch of worker events. Every event must name the same jobId; the signature must verify
 * with that job's derived key. While RENDA_ACCEPT_MASTER_WORKER_EVENTS isn't "0", events signed with
 * the master secret (workers deployed before per-job keys) are still accepted so in-flight jobs finish.
 * Returns the parsed events; throws ApiError otherwise.
 */
export function authenticateWorkerEvents(
  raw: string,
  headers: { signature: string | null; timestamp: string | null },
  now = Date.now(),
): { events: Record<string, unknown>[]; jobId: string; signedWith: "job" | "master" } {
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    throw new ApiError("invalid_signature"); // unsigned garbage gets no more detail than a bad signature
  }
  const events = (Array.isArray(body) ? body : [body]) as RawEvent[];
  const jobId = events[0]?.jobId;
  if (!events.length || typeof jobId !== "string" || !jobId) throw new ApiError("invalid_signature");

  const master = env("RENDA_WORKER_SECRET");
  let sig: WorkerSigResult = verifyWorkerEvent(raw, headers, deriveJobKey(jobId, master), now);
  let signedWith: "job" | "master" = "job";
  if (sig === "bad" && envOptional("RENDA_ACCEPT_MASTER_WORKER_EVENTS") !== "0") {
    sig = verifyWorkerEvent(raw, headers, master, now);
    signedWith = "master";
  }
  // Untimed signatures stay accepted while older worker containers drain after a deploy; set
  // RENDA_ACCEPT_UNTIMED_WORKER_EVENTS=0 once every worker sends x-renda-timestamp.
  const legacyOk = envOptional("RENDA_ACCEPT_UNTIMED_WORKER_EVENTS") !== "0";
  if (sig === "stale") throw new ApiError("invalid_signature", "Event timestamp is too old or in the future.");
  if (sig === "bad" || (sig === "legacy" && !legacyOk)) throw new ApiError("invalid_signature");

  // Signed, so it came from a worker holding this job's key, but that worker may be running hostile content:
  // it may only speak for its own job and only point at its own outputs.
  for (const ev of events) {
    if (!ev || typeof ev.type !== "string" || ev.jobId !== jobId) {
      throw new ApiError("invalid_request", "every event in a batch needs a type and the same jobId");
    }
    if (ev.type === "done") assertOwnOutputs(jobId, ev.outputs);
  }
  return { events: events as Record<string, unknown>[], jobId, signedWith };
}

/** `done` may only list objects under outputs/<jobId>/ (web presigns these keys for the job's owner). */
export function assertOwnOutputs(jobId: string, outputs: unknown): void {
  if (!Array.isArray(outputs)) throw new ApiError("invalid_request", "done needs outputs");
  const prefix = `outputs/${jobId}/`;
  const ok = (k: unknown) => typeof k === "string" && k.startsWith(prefix) && !k.split("/").includes("..");
  for (const o of outputs as { key?: unknown; thumbKey?: unknown }[]) {
    if (!o || !ok(o.key) || (o.thumbKey !== undefined && !ok(o.thumbKey))) {
      throw new ApiError("invalid_request", `done outputs must live under ${prefix}`);
    }
  }
}
