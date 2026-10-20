"use server";

import { unstable_rethrow } from "next/navigation";
import { ZodError } from "zod";
import { requireAccount } from "@/lib/auth";
import { ApiError } from "@/lib/errors";
import { zodIssues } from "@/lib/http";
import { estimateWithPriorities, getJob, serializeJob, submitJob, type Job } from "@/lib/jobs";
import { parseJobPackage } from "@/lib/packages";
import { rateLimit } from "@/lib/ratelimit";
import { registerUpload, type UploadInput, type UploadTicket } from "@/lib/uploads";

/**
 * Playground server actions: the same libraries as /api/v1 (upload policy, validation, billing gates,
 * rate limits), authenticated by the signed-in WorkOS session instead of an API key. Errors come back as
 * values so the client can show the API's code/message/hint.
 */
export type ActionError = { code: string; message: string; hint?: string; issues?: { path: string; message: string }[] };
export type ActionResult<T> = { ok: true; data: T } | { ok: false; error: ActionError };

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
