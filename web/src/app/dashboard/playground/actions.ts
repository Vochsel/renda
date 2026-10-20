"use server";

import { unstable_rethrow } from "next/navigation";
import { ZodError } from "zod";
import { requireAccount } from "@/lib/auth";
import { ApiError } from "@/lib/errors";
import { zodIssues } from "@/lib/http";
import type { schema } from "@/lib/db";
import { cancelJob, estimateWithPriorities, getJob, getJobsByIds, listJobs, serializeJob, submitJob, type Job } from "@/lib/jobs";
import { parseJobPackage } from "@/lib/packages";
import { isPlaygroundJob, MAX_BATCH } from "@/lib/playground";
import { presignGet } from "@/lib/r2";
import { rateLimit } from "@/lib/ratelimit";
import { registerUpload, type UploadInput, type UploadTicket } from "@/lib/uploads";

/**
 * Playground server actions: the same libraries as /api/v1 (upload policy, validation, billing gates,
 * rate limits), authenticated by the signed-in WorkOS session instead of an API key. Errors come back as
 * values so the client can show the API's code/message/hint.
 */
export type ActionError = { code: string; message: string; hint?: string; issues?: { path: string; message: string }[] };
export type ActionResult<T> = { ok: true; data: T } | { ok: false; error: ActionError };

type JobRow = typeof schema.jobs.$inferSelect;

const MAX_FILES = 20;
type Priority = "economy" | "standard" | "rush";
const PRIORITIES = new Set<Priority>(["economy", "standard", "rush"]);

async function run<T>(fn: () => Promise<T>): Promise<ActionResult<T>> {
  try {
    return { ok: true, data: await fn() };
  } catch (e) {
    unstable_rethrow(e); // sign-in redirects (expired session) must reach Next, not become an error value
    if (e instanceof ApiError) return { ok: false, error: e.toBody().error as ActionError };
    if (e instanceof ZodError) return { ok: false, error: { code: "invalid_request", message: "Invalid input.", issues: zodIssues(e) } };
    console.error("playground", e);
    return { ok: false, error: { code: "internal", message: "Something went wrong. Try again." } };
  }
}

function priorityOf(p: unknown): Priority {
  if (!PRIORITIES.has(p as Priority)) throw new ApiError("invalid_request", "priority must be economy, standard or rush.");
  return p as Priority;
}

/** Register the dropped files; returns presigned PUTs for the ones this org hasn't uploaded yet. */
export async function registerUploads(files: UploadInput[]): Promise<ActionResult<UploadTicket[]>> {
  return run(async () => {
    const { user, org } = await requireAccount();
    if (!Array.isArray(files) || files.length === 0 || files.length > MAX_FILES) {
      throw new ApiError("invalid_request", `Send 1–${MAX_FILES} files.`);
    }
    await rateLimit("api_write_key", `session:${user.id}`);
    return Promise.all(files.map((f) => registerUpload(org.id, f)));
  });
}

/** Cost and wall-time ranges for every priority (estimates never read the files). */
export async function estimatePlayground(pkg: unknown, priority: Priority) {
  return run(async () => {
    await requireAccount();
    return estimateWithPriorities(parseJobPackage("scene", pkg, { allowLocal: true }), priorityOf(priority));
  });
}

/** Submit through the same path as POST /api/v1/jobs (validation, verification, trial/billing gates). */
export async function submitPlayground(pkg: unknown, priority: Priority, idempotencyKey?: string): Promise<ActionResult<Job>> {
  return run(async () => {
    const { user, org } = await requireAccount();
    await rateLimit("api_write_key", `session:${user.id}`);
    return submitJob({
      orgId: org.id,
      org,
      userId: user.id,
      kind: "scene",
      package: pkg,
      priority: priorityOf(priority),
      idempotencyKey: typeof idempotencyKey === "string" && idempotencyKey ? idempotencyKey.slice(0, 200) : undefined,
    });
  });
}

/** Poll one of this org's jobs. */
export async function getPlaygroundJob(id: string): Promise<ActionResult<Job>> {
  return run(async () => {
    const { org } = await requireAccount();
    return serializeJob(await getJob(org.id, String(id)));
  });
}

/** A job as the queue shows it: the job plus its gallery thumbnail once it has one. */
export type QueueJob = Job & { thumb?: { url: string; placeholder?: string; width?: number; height?: number } };

async function toQueueJob(row: JobRow): Promise<QueueJob> {
  const job = await serializeJob(row);
  const outs = row.outputs ?? [];
  const o = outs.find((x) => x.thumbKey && !x.pass) ?? outs.find((x) => x.thumbKey);
  if (!o?.thumbKey) return job;
  return { ...job, thumb: { url: await presignGet(o.thumbKey), placeholder: o.placeholder, width: o.width, height: o.height } };
}

const MAX_POLL = 100;

/** Poll many of this org's jobs in one request (ids from other orgs are simply not returned). */
export async function getPlaygroundJobs(ids: string[]): Promise<ActionResult<QueueJob[]>> {
  return run(async () => {
    const { org } = await requireAccount();
    if (!Array.isArray(ids) || ids.length > MAX_POLL) throw new ApiError("invalid_request", `Send up to ${MAX_POLL} job ids.`);
    const rows = await getJobsByIds(org.id, ids.map(String));
    return Promise.all(rows.map(toQueueJob));
  });
}

export async function cancelPlaygroundJob(id: string): Promise<ActionResult<QueueJob>> {
  return run(async () => {
    const { user, org } = await requireAccount();
    await rateLimit("api_write_key", `session:${user.id}`);
    return toQueueJob(await cancelJob(org.id, String(id)));
  });
}

/** This org's playground jobs from the last day, so the queue survives a refresh. */
export async function recentPlaygroundJobs(): Promise<ActionResult<QueueJob[]>> {
  return run(async () => {
    const { org } = await requireAccount();
    const since = Date.now() - 24 * 3600_000;
    const rows = (await listJobs(org.id, { limit: 100 }))
      .filter((j) => isPlaygroundJob(j.name) && j.createdAt.getTime() >= since)
      .slice(0, MAX_BATCH * 2);
    return Promise.all(rows.map(toQueueJob));
  });
}
