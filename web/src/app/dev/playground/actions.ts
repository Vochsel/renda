"use server";

import type { ActionResult, QueueJob } from "@/app/dashboard/playground/actions";
import type { Job } from "@/lib/jobs";
import { estimateWithPriorities } from "@/lib/jobs";
import { parseJobPackage } from "@/lib/packages";
import type { UploadInput, UploadTicket } from "@/lib/uploads";

/** Dev-only fakes for /dev/playground: real estimator, uploads to a local no-op route, canned results. */
const guard = () => {
  if (process.env.NODE_ENV === "production") throw new Error("not found");
};
const jobs = new Map<string, { job: Job; polls: number; output: Job["outputs"][number] }>();

export async function devRegisterUploads(files: UploadInput[]): Promise<ActionResult<UploadTicket[]>> {
  guard();
  return {
    ok: true,
    data: files.map((f, i) => i === 0
      ? { assetUri: `asset://${f.sha256}`, exists: false as const, uploadUrl: "/dev/playground/upload", uploadMethod: "PUT" as const, uploadHeaders: { "content-type": f.contentType ?? "application/octet-stream" }, expiresIn: 3600 }
      : { assetUri: `asset://${f.sha256}`, exists: true as const }),
  };
}

export async function devEstimate(pkg: unknown, priority: "economy" | "standard" | "rush"): Promise<ActionResult<unknown>> {
  guard();
  try {
    return { ok: true, data: estimateWithPriorities(parseJobPackage("scene", pkg, { allowLocal: true }), priority) };
  } catch (e) {
    return { ok: false, error: { code: "invalid_package", message: (e as Error).message } };
  }
}

export async function devSubmit(pkg: unknown, priority: "economy" | "standard" | "rush"): Promise<ActionResult<Job>> {
  guard();
  const format = (pkg as { output?: { format?: string } }).output?.format ?? "png";
  const output = format === "glb"
    ? { name: "baked.glb", url: "/bakes/camera-cycles.glb", contentType: "model/gltf-binary", bytes: 1 }
    : format === "splat"
      ? { name: "splat.spz", url: "/splats/table-fast.spz", contentType: "application/octet-stream", bytes: 1 }
      : format === "mp4" || format === "webm"
        ? { name: "render.mp4", url: "/showcase/venus-turntable.mp4", contentType: "video/mp4", bytes: 1 }
        : { name: "frame_0001.webp", url: "/showcase/porsche-cycles.webp", contentType: "image/webp", bytes: 1 };
  const id = `job_dev${Math.random().toString(36).slice(2, 10)}`;
  const job: Job = {
    id, kind: "scene", name: (pkg as { name?: string }).name ?? "playground (dev)", status: "queued", priority, progress: 0, createdAt: new Date().toISOString(),
    billedSeconds: 0, costUsd: 0, cached: false, warnings: [], outputs: [],
  };
  jobs.set(id, { job, polls: 0, output });
  return { ok: true, data: job };
}

function advance(id: string): QueueJob | null {
  const s = jobs.get(id);
  if (!s) return null;
  if (s.job.status === "canceled") return s.job;
  s.polls += 1;
  s.job = s.polls < 2
    ? { ...s.job, status: "rendering", progress: 0.5, engine: "blender-cycles", hardware: "gpu-l4" }
    : { ...s.job, status: "succeeded", progress: 1, billedSeconds: 9.8, costUsd: 0.0057, outputs: [s.output] };
  const thumb = s.job.status === "succeeded" && s.output.contentType.startsWith("image/") ? { url: s.output.url, width: 1600, height: 1000 } : undefined;
  return { ...s.job, ...(thumb ? { thumb } : {}) };
}

export async function devGetJob(id: string): Promise<ActionResult<Job>> {
  guard();
  const j = advance(id);
  return j ? { ok: true, data: j } : { ok: false, error: { code: "not_found", message: "Not found." } };
}

export async function devGetJobs(ids: string[]): Promise<ActionResult<QueueJob[]>> {
  guard();
  return { ok: true, data: ids.map(advance).filter((j): j is QueueJob => !!j) };
}

export async function devCancel(id: string): Promise<ActionResult<QueueJob>> {
  guard();
  const s = jobs.get(id);
  if (!s) return { ok: false, error: { code: "not_found", message: "Not found." } };
  s.job = { ...s.job, status: "canceled" };
  return { ok: true, data: s.job };
}

export async function devRecent(): Promise<ActionResult<QueueJob[]>> {
  guard();
  return {
    ok: true,
    data: [...jobs.values()].map((s) =>
      s.job.status === "succeeded" && s.output.contentType.startsWith("image/") ? { ...s.job, thumb: { url: s.output.url, width: 1600, height: 1000 } } : s.job,
    ),
  };
}
