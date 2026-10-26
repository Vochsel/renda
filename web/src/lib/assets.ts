import { and, eq, inArray, lt, sql } from "drizzle-orm";
import { db, schema } from "./db";
import { ApiError } from "./errors";
import { assetKey, copyObject, deleteObject, objectExists, sha256OfObject, stagingKey } from "./r2";

const { assets } = schema;

/**
 * Upload policy. Assets are content-addressed (`asset://<sha256>`, stored once at `assets/<sha256>`),
 * but knowing a hash is not proof of having the file, and an upload URL doesn't check what's sent.
 * So every org uploads to its own staging key; the first time it uses the asset we hash the staged
 * bytes and only then (a) mark the asset verified for that org and (b) promote the bytes to the
 * shared key if they aren't there yet. Nobody can use another org's file by hash alone, and nobody
 * can plant different bytes under someone else's hash.
 */
export type AssetCheck = { sha: string; key: string };

/** Make sure this org may use each sha, verifying staged uploads on first use. Returns the R2 keys to read. */
export async function ensureVerifiedAssets(orgId: string, shas: string[]): Promise<AssetCheck[]> {
  if (!shas.length) return [];
  const rows = await db().select({ sha: assets.sha256, verifiedAt: assets.verifiedAt }).from(assets)
    .where(and(eq(assets.orgId, orgId), inArray(assets.sha256, shas)));
  const bySha = new Map(rows.map((r) => [r.sha, r]));
  const missing: string[] = [];
  const mismatched: string[] = [];
  const out: AssetCheck[] = [];
  for (const sha of shas) {
    const row = bySha.get(sha);
    const canonical = assetKey(sha);
    if (row?.verifiedAt) {
      if (await objectExists(canonical)) out.push({ sha, key: canonical });
      else missing.push(sha);
      continue;
    }
    const staged = stagingKey(orgId, sha);
    if (!row || !(await objectExists(staged))) {
      missing.push(sha);
      continue;
    }
    if ((await sha256OfObject(staged)) !== sha) {
      await deleteObject(staged).catch(() => {});
      mismatched.push(sha);
      continue;
    }
    if (!(await objectExists(canonical))) await copyObject(staged, canonical);
    await deleteObject(staged).catch(() => {});
    await db().update(assets).set({ verifiedAt: new Date() }).where(and(eq(assets.orgId, orgId), eq(assets.sha256, sha)));
    out.push({ sha, key: canonical });
  }
  if (mismatched.length) {
    throw new ApiError("asset_mismatch", undefined, { mismatched: mismatched.map((s) => `asset://${s}`) });
  }
  if (missing.length) throw new ApiError("asset_missing", undefined, { missing: missing.map((s) => `asset://${s}`) });
  await touchAssets(orgId, out.map((a) => a.sha));
  return out;
}

/** Mark assets as used now (retention counts 30 days from the last use). */
export async function touchAssets(orgId: string, shas: string[]): Promise<void> {
  if (!shas.length) return;
  await db().update(assets).set({ lastUsedAt: new Date() }).where(and(eq(assets.orgId, orgId), inArray(assets.sha256, shas)));
}

const ACTIVE_STATUSES = ["queued", "planning", "rendering", "encoding"] as const;

/**
 * Drop an org's registration of `sha` and delete what only it owned: its staging upload, and the shared
 * `assets/<sha>` object once no org references the hash any more. Idempotent per step.
 */
async function releaseAsset(orgId: string, sha: string): Promise<{ objectDeleted: boolean }> {
  await db().delete(assets).where(and(eq(assets.orgId, orgId), eq(assets.sha256, sha)));
  await deleteObject(stagingKey(orgId, sha));
  return { objectDeleted: await deleteSharedIfUnreferenced(sha) };
}

/** Delete `assets/<sha>` when no org has it registered. Returns whether it was deleted. */
export async function deleteSharedIfUnreferenced(sha: string): Promise<boolean> {
  const [other] = await db().select({ orgId: assets.orgId }).from(assets).where(eq(assets.sha256, sha)).limit(1);
  if (other) return false;
  await deleteObject(assetKey(sha));
  return true;
}

/**
 * DELETE /api/v1/uploads/:sha256 — remove this org's upload. Refused while one of the org's running jobs
 * references it (the worker may not have downloaded it yet).
 */
export async function deleteAsset(orgId: string, sha: string) {
  const [row] = await db().select({ sha: assets.sha256 }).from(assets)
    .where(and(eq(assets.orgId, orgId), eq(assets.sha256, sha))).limit(1);
  if (!row) throw new ApiError("not_found", `No upload asset://${sha} in this account.`, {}, "GET /api/v1/jobs shows which assets your jobs use; uploads are per account.");
  const [running] = await db().select({ id: schema.jobs.id }).from(schema.jobs)
    .where(and(
      eq(schema.jobs.orgId, orgId),
      inArray(schema.jobs.status, [...ACTIVE_STATUSES]),
      sql`${schema.jobs.package}::text like ${`%asset://${sha}%`}`,
    )).limit(1);
  if (running) {
    throw new ApiError("conflict", `asset://${sha} is used by running job ${running.id}.`, { jobId: running.id },
      "Wait for the job to finish (or POST /api/v1/jobs/{id}/cancel), then delete the upload.");
  }
  const { objectDeleted } = await releaseAsset(orgId, sha);
  return { assetUri: `asset://${sha}`, deleted: true, storageDeleted: objectDeleted };
}

/**
 * Retention: delete registrations unused for `days`, and the stored bytes once no org uses them.
 * Batch-limited and idempotent; safe to run every few minutes.
 */
export async function expireUnusedAssets(days: number, limit = 200, now = new Date()) {
  const cutoff = new Date(now.getTime() - days * 86_400_000);
  const stale = await db().select({ orgId: assets.orgId, sha: assets.sha256 }).from(assets)
    .where(lt(assets.lastUsedAt, cutoff)).limit(limit);
  let registrations = 0;
  const shas = new Set<string>();
  for (const a of stale) {
    // Re-check the cutoff in the delete itself: a job may have used the asset since the select.
    const gone = await db().delete(assets)
      .where(and(eq(assets.orgId, a.orgId), eq(assets.sha256, a.sha), lt(assets.lastUsedAt, cutoff)))
      .returning({ sha: assets.sha256 });
    if (!gone.length) continue;
    registrations++;
    await deleteObject(stagingKey(a.orgId, a.sha));
    shas.add(a.sha);
  }
  let objects = 0;
  for (const s of shas) if (await deleteSharedIfUnreferenced(s)) objects++;
  return { registrations, objects };
}

/** Account deletion: release up to `limit` of the org's uploads. Returns how many were released. */
export async function releaseOrgAssets(orgId: string, limit = 200): Promise<number> {
  const rows = await db().select({ sha: assets.sha256 }).from(assets).where(eq(assets.orgId, orgId)).limit(limit);
  for (const r of rows) await releaseAsset(orgId, r.sha);
  return rows.length;
}

/** True if the org has already proven it has these bytes (no upload needed). */
export async function isVerifiedFor(orgId: string, sha: string): Promise<boolean> {
  const [row] = await db().select({ verifiedAt: assets.verifiedAt }).from(assets)
    .where(and(eq(assets.orgId, orgId), eq(assets.sha256, sha))).limit(1);
  return Boolean(row?.verifiedAt) && (await objectExists(assetKey(sha)));
}
