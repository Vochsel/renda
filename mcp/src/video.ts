import { stat } from "node:fs/promises";
import { extname, resolve } from "node:path";
import { formatFromPath } from "@rendahq/spec";
import { TERMINAL_STATUSES, type HyperframesPackage, type Job, type JobOutput, type RendaClient } from "./client.js";
import { isLocalUri, uploadFile } from "./local.js";

/** Media a composition can show directly (uploaded as-is when a variable points at a local file). */
export const VIDEO_VARIABLE_MEDIA = new Set([".mp4", ".mov", ".m4v", ".webm", ".mkv", ".gif", ".png", ".jpg", ".jpeg",
  ".webp", ".avif", ".svg", ".mp3", ".wav", ".m4a", ".aac", ".ogg"]);

/** HyperFrames' own quality names, plus the scene-render names agents tend to reach for. */
export function hyperframesQuality(q: string | undefined): HyperframesPackage["quality"] | undefined {
  if (!q) return undefined;
  const map: Record<string, HyperframesPackage["quality"]> = {
    draft: "draft", looks: "looks", standard: "looks", delivery: "delivery", final: "delivery", high: "delivery",
  };
  const v = map[q];
  if (!v) throw new Error(`quality must be draft | looks | delivery (got ${q})`);
  return v;
}

export interface ResolvedVariables {
  variables: Record<string, unknown>;
  uploaded: { variable: string; path: string; assetUri: string }[];
  /** Turntables rendered (or existing jobs reused) to fill a variable. */
  jobs: { variable: string; jobId: string; url: string; billedSeconds?: number }[];
}

export interface ResolveOptions {
  /** Directory relative variable paths resolve against (the shell's cwd for the CLI). */
  baseDir: string;
  /** Local mode can read files; remote mode passes non-uri strings through untouched. */
  local: boolean;
  /** Wait for a job to finish (CLI: progress bar; MCP: progress notifications). */
  waitJob?: (job: Job) => Promise<Job>;
  /** Seconds of turntable rendered for a 3D file variable (default 4). */
  turntableSeconds?: number;
}

function videoOutput(job: Job): JobOutput | undefined {
  return job.outputs.find((o) => o.contentType.startsWith("video/")) ?? job.outputs.find((o) => o.contentType.startsWith("image/"));
}

async function isFile(path: string) {
  try {
    return (await stat(path)).isFile();
  } catch {
    return false;
  }
}

/**
 * Turn user-friendly variable values into ones the worker can fetch:
 * - `job:<id>`             → that job's video (or image) output URL — chain a 3D render into a video
 * - local 3D scene file    → renders a cloud turntable mp4 first, then uses its URL (one-command composite)
 * - local media file       → uploaded, becomes asset://<sha256> (the worker copies it into the project)
 * - anything else          → unchanged (text, colours, numbers, https URLs, project-relative paths)
 */
export async function resolveVideoVariables(client: RendaClient, input: Record<string, unknown> | undefined,
  opts: ResolveOptions): Promise<ResolvedVariables> {
  const out: ResolvedVariables = { variables: { ...(input ?? {}) }, uploaded: [], jobs: [] };
  const wait = async (job: Job) => {
    const done = TERMINAL_STATUSES.has(job.status) ? job : opts.waitJob ? await opts.waitJob(job) : await client.waitForJob(job.id);
    if (done.status !== "succeeded") throw new Error(`job ${done.id} ${done.status}${done.error ? `: ${done.error}` : ""}`);
    const o = videoOutput(done);
    if (!o) throw new Error(`job ${done.id} has no video or image output to use`);
    return { job: done, url: o.url };
  };

  for (const [key, value] of Object.entries(out.variables)) {
    if (typeof value !== "string") continue;
    const jobRef = value.match(/^job:([A-Za-z0-9_-]+)$/);
    if (jobRef) {
      const { job, url } = await wait(await client.getJob(jobRef[1]));
      out.variables[key] = url;
      out.jobs.push({ variable: key, jobId: job.id, url, billedSeconds: job.billedSeconds });
      continue;
    }
    if (!opts.local || !isLocalUri(value)) continue;
    const abs = resolve(/* turbopackIgnore: true */ opts.baseDir, value);
    if (!(await isFile(abs))) continue; // e.g. "assets/clip.mp4" inside the project, or plain text
    const ext = extname(abs).toLowerCase();
    const fmt = ext === ".json" ? undefined : formatFromPath(abs);
    if (fmt) {
      const up = await uploadFile(client, abs);
      out.uploaded.push({ variable: key, path: abs, assetUri: up.assetUri });
      const seconds = opts.turntableSeconds ?? 4;
      const pkg: any = {
        version: 1, name: `${key}-turntable`, assets: { scene: { uri: up.assetUri, ...(fmt === "splat" ? { format: fmt } : {}) } },
        scene: { root: "scene" }, environment: { hdri: "studio", background: "#111827" },
        render: { resolution: [1080, 1080], quality: "draft" },
        animation: { frames: [1, Math.round(seconds * 30)], fps: 30, turntable: { revolutions: 1 } },
        output: { format: "mp4" },
      };
      const { job, url } = await wait(await client.createJob({ package: pkg }));
      out.variables[key] = url;
      out.jobs.push({ variable: key, jobId: job.id, url, billedSeconds: job.billedSeconds });
    } else if (VIDEO_VARIABLE_MEDIA.has(ext)) {
      const up = await uploadFile(client, abs);
      out.variables[key] = up.assetUri;
      out.uploaded.push({ variable: key, path: abs, assetUri: up.assetUri });
    }
  }
  return out;
}

/** Parse repeatable `--var key=value` flags plus an optional `--vars '{json}'` object. */
export function parseVarFlags(pairs: readonly string[] = [], json?: string): Record<string, unknown> {
  const vars: Record<string, unknown> = {};
  if (json) {
    const parsed = JSON.parse(json);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("--vars must be a JSON object");
    Object.assign(vars, parsed);
  }
  for (const p of pairs) {
    const i = p.indexOf("=");
    if (i < 1) throw new Error(`--var expects key=value (got ${p})`);
    vars[p.slice(0, i)] = p.slice(i + 1);
  }
  return vars;
}
