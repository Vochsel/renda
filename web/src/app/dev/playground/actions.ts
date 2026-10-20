"use server";

import type { ActionResult } from "@/app/dashboard/playground/actions";
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
    id, kind: "scene", name: "playground (dev)", status: "queued", priority, progress: 0, createdAt: new Date().toISOString(),
    billedSeconds: 0, costUsd: 0, cached: false, warnings: [], outputs: [],
  };
  jobs.set(id, { job, polls: 0, output });
  return { ok: true, data: job };
}

export async function devGetJob(id: string): Promise<ActionResult<Job>> {
  guard();
  const s = jobs.get(id);
  if (!s) return { ok: false, error: { code: "not_found", message: "Not found." } };
  s.polls += 1;
  s.job = s.polls < 2
    ? { ...s.job, status: "rendering", progress: 0.5, engine: "blender-cycles", hardware: "gpu-l4" }
    : { ...s.job, status: "succeeded", progress: 1, billedSeconds: 9.8, costUsd: 0.0057, outputs: [s.output] };
  return { ok: true, data: s.job };
}
