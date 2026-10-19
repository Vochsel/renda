import { and, desc, eq, sql } from "drizzle-orm";
import type { NeonHttpDatabase } from "drizzle-orm/neon-http";
import { db, schema } from "./db";
import type { JobOutput } from "./db/schema";
import { ApiError } from "./errors";
import { presignGet } from "./r2";

const { jobs } = schema;

export type OutputKind = "image" | "video" | "model";

export const GALLERY_PAGE_SIZE = 24;

/** One render output, flattened out of its job, as the gallery and `GET /api/v1/outputs` return it. */
export type OutputItem = {
  id: string; // `${jobId}:${index}` — stable across pages
  jobId: string;
  jobName: string | null;
  name: string;
  kind: OutputKind;
  url: string;
  thumbUrl: string | null;
  contentType: string;
  width: number | null;
  height: number | null;
  bytes: number;
  placeholder: string | null;
  pass: string | null;
  engine: string | null;
  hardware: string | null;
  billedSeconds: number;
  costUsd: number;
  createdAt: string;
};

export type OutputsPage = { items: OutputItem[]; nextCursor: string | null };

/** The job columns the gallery needs. */
export type JobForOutputs = {
  id: string;
  name: string | null;
  createdAt: Date;
  engine: string | null;
  hardware: string | null;
  secondsCpu: number;
  secondsGpuL4: number;
  secondsGpuL40s: number;
  costUsd: number;
  outputs: JobOutput[];
};

/** Position just before the next output to return: job (createdAt ms + id) and output index within it. */
export type Cursor = { t: number; j: string; i: number };

/** Where a jobs query starts: at job (t, j) inclusive (to resume mid-job) or just after it. */
export type JobsAfter = { t: number; j: string; inclusive: boolean } | null;

export type FetchJobs = (orgId: string, after: JobsAfter, limit: number) => Promise<JobForOutputs[]>;

const MODEL_EXT = /\.(glb|gltf|ply|spz|splat|ksplat|usdz)$/i;

export function kindOf(o: Pick<JobOutput, "contentType" | "key">): OutputKind | null {
  if (o.contentType.startsWith("video/")) return "video";
  if (o.contentType.startsWith("image/")) return "image";
  if (o.contentType.startsWith("model/") || MODEL_EXT.test(o.key)) return "model";
  return null;
}

export function encodeCursor(c: Cursor): string {
  return Buffer.from(JSON.stringify([c.t, c.j, c.i])).toString("base64url");
}

export function decodeCursor(s: string): Cursor {
  try {
    const [t, j, i] = JSON.parse(Buffer.from(s, "base64url").toString("utf8"));
    if (Number.isFinite(t) && typeof j === "string" && j.length < 64 && Number.isInteger(i) && i >= 0) return { t, j, i };
  } catch {
    // fall through
  }
  throw new ApiError("invalid_request", "Invalid cursor.", { hint: "Pass the `nextCursor` from the previous page unchanged." });
}

/** Passes (depth, normal…) and kept video frames are hidden unless asked for: the gallery is for renders. */
export function isListed(o: JobOutput, opts: { kind?: OutputKind; all?: boolean }): boolean {
  const kind = kindOf(o);
  if (!kind || (opts.kind && kind !== opts.kind)) return false;
  if (!opts.all && (o.pass || o.key.includes("/frames/"))) return false;
  return true;
}

type Positioned = { job: JobForOutputs; index: number; output: JobOutput };

const BATCH = 50;
const MAX_BATCHES = 10;

/**
 * Walk jobs newest-first (createdAt ms desc, id desc) and flatten their listed outputs into one page.
 * Pure apart from `fetchJobs`, so pagination is unit-testable without a database.
 */
export async function collectOutputs(
  orgId: string,
  opts: { cursor?: Cursor | null; limit: number; kind?: OutputKind; all?: boolean },
  fetchJobs: FetchJobs,
): Promise<{ items: Positioned[]; next: Cursor | null }> {
  const items: Positioned[] = [];
  let after: JobsAfter = opts.cursor ? { t: opts.cursor.t, j: opts.cursor.j, inclusive: true } : null;
  let lastJob: JobForOutputs | null = null;
  for (let batch = 0; batch < MAX_BATCHES; batch++) {
    const rows = await fetchJobs(orgId, after, BATCH);
    for (const job of rows) {
      const start = opts.cursor && job.id === opts.cursor.j && job.createdAt.getTime() === opts.cursor.t ? opts.cursor.i : 0;
      for (let index = start; index < job.outputs.length; index++) {
        const output = job.outputs[index];
        if (!isListed(output, opts)) continue;
        if (items.length === opts.limit) return { items, next: { t: job.createdAt.getTime(), j: job.id, i: index } };
        items.push({ job, index, output });
      }
      lastJob = job;
    }
    if (rows.length < BATCH) return { items, next: null };
    after = { t: lastJob!.createdAt.getTime(), j: lastJob!.id, inclusive: false };
  }
  // Scanned MAX_BATCHES jobs without filling the page (sparse filter): let the client continue after them.
  return { items, next: lastJob ? { t: lastJob.createdAt.getTime(), j: lastJob.id, i: lastJob.outputs.length } : null };
}

const createdMs = sql`date_trunc('milliseconds', ${jobs.createdAt})`;

/**
 * Org-scoped jobs query for the gallery. createdAt is compared at millisecond precision because that's
 * what round-trips through JS Dates (Postgres stores microseconds).
 */
export function jobsForOutputsQuery(d: NeonHttpDatabase<typeof schema>, orgId: string, after: JobsAfter, limit: number) {
  const conds = [eq(jobs.orgId, orgId), eq(jobs.status, "succeeded")];
  if (after) {
    const t = new Date(after.t).toISOString();
    conds.push(
      after.inclusive
        ? sql`(${createdMs} < ${t}::timestamptz or (${createdMs} = ${t}::timestamptz and ${jobs.id} <= ${after.j}))`
        : sql`(${createdMs} < ${t}::timestamptz or (${createdMs} = ${t}::timestamptz and ${jobs.id} < ${after.j}))`,
    );
  }
  return d
    .select({
      id: jobs.id,
      name: jobs.name,
      createdAt: jobs.createdAt,
      engine: jobs.engine,
      hardware: jobs.hardware,
      secondsCpu: jobs.secondsCpu,
      secondsGpuL4: jobs.secondsGpuL4,
      secondsGpuL40s: jobs.secondsGpuL40s,
      costUsd: jobs.costUsd,
      outputs: jobs.outputs,
    })
    .from(jobs)
    .where(and(...conds))
    .orderBy(desc(createdMs), desc(jobs.id))
    .limit(limit);
}

// JS Dates carry ms, matching the date_trunc('milliseconds') comparison above.
const fetchJobsFromDb: FetchJobs = (orgId, after, limit) => jobsForOutputsQuery(db(), orgId, after, limit);

export async function toOutputItem({ job, index, output: o }: Positioned): Promise<OutputItem> {
  const kind = kindOf(o)!;
  const url = await presignGet(o.key, undefined, o.name);
  const thumbUrl = o.thumbKey ? await presignGet(o.thumbKey) : kind === "image" ? url : null;
  return {
    id: `${job.id}:${index}`,
    jobId: job.id,
    jobName: job.name,
    name: o.name,
    kind,
    url,
    thumbUrl,
    contentType: o.contentType,
    width: o.width ?? null,
    height: o.height ?? null,
    bytes: o.bytes,
    placeholder: o.placeholder ?? null,
    pass: o.pass ?? null,
    engine: job.engine,
    hardware: job.hardware,
    billedSeconds: Math.round((job.secondsCpu + job.secondsGpuL4 + job.secondsGpuL40s) * 10) / 10,
    costUsd: job.costUsd,
    createdAt: job.createdAt.toISOString(),
  };
}

export async function listOutputs(
  orgId: string,
  opts: { cursor?: string | null; limit?: number; kind?: OutputKind; all?: boolean } = {},
  fetchJobs: FetchJobs = fetchJobsFromDb,
): Promise<OutputsPage> {
  const limit = Math.min(Math.max(Math.trunc(opts.limit ?? 30), 1), 100);
  const cursor = opts.cursor ? decodeCursor(opts.cursor) : null;
  const { items, next } = await collectOutputs(orgId, { cursor, limit, kind: opts.kind, all: opts.all }, fetchJobs);
  return { items: await Promise.all(items.map(toOutputItem)), nextCursor: next ? encodeCursor(next) : null };
}
