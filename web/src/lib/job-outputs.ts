import { and, eq, gte, isNull, lt, ne, sql } from "drizzle-orm";
import { db, schema } from "./db";
import type { JobOutput } from "./db/schema";
import { ApiError } from "./errors";
import { getJob, TERMINAL } from "./jobs";
import { deleteObject, deletePrefix, outputPrefix, workPrefix } from "./r2";

const { jobs } = schema;

/**
 * Deleting render outputs. A job's files live under `outputs/<jobId>/`, but a server-side cache hit (see
 * submitJob) copies another job's `outputs` array, so one R2 key can be listed by several jobs of the same
 * org. Keys still listed by another job are kept; everything else goes.
 */

/** Every R2 key an outputs array points at (files and their thumbnails). */
export function outputKeys(outputs: JobOutput[] | null | undefined): string[] {
  const keys = new Set<string>();
  for (const o of outputs ?? []) {
    if (o.key) keys.add(o.key);
    if (o.thumbKey) keys.add(o.thumbKey);
  }
  return [...keys];
}

/** Which of `keys` another job of the org (not `jobId`) still lists in its outputs. */
export async function keysReferencedElsewhere(orgId: string, jobId: string, keys: string[]): Promise<Set<string>> {
  if (!keys.length) return new Set();
  const list = sql.join(keys.map((k) => sql`${k}`), sql`, `);
  const res = await db().execute(sql`
    select o->>'key' as key, o->>'thumbKey' as thumb_key
    from ${jobs} j cross join lateral jsonb_array_elements(j.outputs) o
    where j.org_id = ${orgId} and j.id <> ${jobId}
      and (o->>'key' in (${list}) or o->>'thumbKey' in (${list}))`);
  const wanted = new Set(keys);
  const found = new Set<string>();
  for (const r of res.rows as { key: string | null; thumb_key: string | null }[]) {
    if (r.key && wanted.has(r.key)) found.add(r.key);
    if (r.thumb_key && wanted.has(r.thumb_key)) found.add(r.thumb_key);
  }
  return found;
}

/**
 * DELETE /api/v1/jobs/:id — delete a finished job's outputs from storage and clear them on the row.
 * 409 while the job is running. Idempotent.
 */
export async function deleteJobOutputs(orgId: string, jobId: string) {
  const job = await getJob(orgId, jobId);
  if (!TERMINAL.has(job.status)) {
    throw new ApiError("conflict", `Job is still ${job.status}; outputs can be deleted once it has finished.`, { status: job.status },
      "Cancel it first with POST /api/v1/jobs/{id}/cancel, or wait for it to finish, then retry.");
  }
  const keys = outputKeys(job.outputs);
  const keep = await keysReferencedElsewhere(orgId, jobId, keys);
  const own = outputPrefix(jobId);
  let deleted = await deletePrefix(own, keep);
  // Keys outside this job's prefix: this job is a cache copy of another job's outputs.
  for (const k of keys) {
    if (k.startsWith(own) || keep.has(k)) continue;
    await deleteObject(k);
    deleted++;
  }
  await deletePrefix(workPrefix(jobId));
  const keptOwn = [...keep].some((k) => k.startsWith(own));
  await db().update(jobs).set({
    outputs: [],
    // A cleared row must never serve as a cache hit (it would hand out an empty result).
    cacheKey: null,
    // If another job still lists files under this prefix, leave the prefix to the retention sweep.
    outputsDeletedAt: keptOwn ? null : new Date(),
  }).where(and(eq(jobs.id, jobId), eq(jobs.orgId, orgId)));
  return { id: jobId, outputsDeleted: true, objectsDeleted: deleted, objectsKept: keep.size };
}

/**
 * Remove a job's whole storage prefix and clear its files from every job of the org that lists them
 * (cache copies included), then mark the prefix deleted. Used by retention and account deletion.
 */
export async function purgeJobPrefix(job: { id: string; orgId: string }): Promise<number> {
  const prefix = outputPrefix(job.id);
  const n = await deletePrefix(prefix);
  await deletePrefix(workPrefix(job.id));
  await db().update(jobs).set({ outputs: [], cacheKey: null })
    .where(and(
      eq(jobs.orgId, job.orgId),
      sql`exists (select 1 from jsonb_array_elements(${jobs.outputs}) o where starts_with(o->>'key', ${prefix}))`,
    ));
  await db().update(jobs).set({ outputs: [], cacheKey: null, outputsDeletedAt: new Date() }).where(eq(jobs.id, job.id));
  return n;
}

/**
 * Retention: delete outputs of jobs that finished more than `days` ago. Batch-limited and idempotent
 * (each job is marked outputs_deleted_at once its prefix is gone). Backstop for the R2 lifecycle rule.
 */
export async function expireOldOutputs(days: number, limit = 100, now = new Date()) {
  const cutoff = new Date(now.getTime() - days * 86_400_000);
  const due = await db().select({ id: jobs.id, orgId: jobs.orgId }).from(jobs)
    .where(and(isNull(jobs.outputsDeletedAt), lt(jobs.finishedAt, cutoff)))
    .orderBy(jobs.finishedAt).limit(limit);
  let objects = 0;
  for (const j of due) objects += await purgeJobPrefix(j);
  return { jobs: due.length, objects };
}

/** Account deletion: purge up to `limit` of an org's job prefixes not yet deleted. */
export async function purgeOrgOutputs(orgId: string, limit = 100): Promise<number> {
  const due = await db().select({ id: jobs.id, orgId: jobs.orgId }).from(jobs)
    .where(and(eq(jobs.orgId, orgId), isNull(jobs.outputsDeletedAt))).limit(limit);
  for (const j of due) await purgeJobPrefix(j);
  return due.length;
}

/** Re-delete prefixes of jobs that were still running when the account was deleted (late uploads). */
export async function repurgeJobsFinishedSince(orgId: string, since: Date): Promise<number> {
  const late = await db().select({ id: jobs.id, orgId: jobs.orgId }).from(jobs)
    .where(and(eq(jobs.orgId, orgId), gte(jobs.finishedAt, since), ne(jobs.status, "succeeded")));
  for (const j of late) await purgeJobPrefix(j);
  return late.length;
}
