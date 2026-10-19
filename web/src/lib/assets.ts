import { and, eq, inArray } from "drizzle-orm";
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
  return out;
}

/** True if the org has already proven it has these bytes (no upload needed). */
export async function isVerifiedFor(orgId: string, sha: string): Promise<boolean> {
  const [row] = await db().select({ verifiedAt: assets.verifiedAt }).from(assets)
    .where(and(eq(assets.orgId, orgId), eq(assets.sha256, sha))).limit(1);
  return Boolean(row?.verifiedAt) && (await objectExists(assetKey(sha)));
}
