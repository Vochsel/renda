import { z } from "zod";
import { db, schema } from "./db";
import { presignPut, stagingKey } from "./r2";
import { isVerifiedFor } from "./assets";

const MAX_BYTES = 5 * 1024 ** 3;

export const UploadBody = z.object({
  filename: z.string().min(1).max(500),
  size: z.number().int().positive().max(MAX_BYTES, "max 5 GB per file"),
  sha256: z.string().regex(/^[0-9a-f]{64}$/, "lowercase hex sha256 of the file bytes"),
  // A plain MIME type; it's signed into the upload URL and stored, so no parameters or odd characters.
  contentType: z.string().max(100).regex(/^[a-z0-9][a-z0-9.+-]*\/[a-z0-9][a-z0-9.+-]*$/i, "a MIME type like model/gltf-binary").optional(),
});
export type UploadInput = z.infer<typeof UploadBody>;

export type UploadTicket =
  | { assetUri: string; exists: true }
  | { assetUri: string; exists: false; uploadUrl: string; uploadMethod: "PUT"; uploadHeaders: Record<string, string>; expiresIn: number };

/**
 * Register a content-addressed upload for an org (shared by the API and the dashboard playground).
 * If this account has already proven it has these bytes, no upload is needed. Otherwise the caller PUTs
 * the file to `uploadUrl` (this account's staging area) with the same Content-Type; the bytes are
 * hashed and checked against sha256 the first time a job uses them. Another account having uploaded the
 * same file doesn't count: a hash isn't proof of having it.
 */
export async function registerUpload(orgId: string, input: UploadInput): Promise<UploadTicket> {
  const b = UploadBody.parse(input);
  const contentType = b.contentType ?? "application/octet-stream";
  await db().insert(schema.assets)
    .values({ orgId, sha256: b.sha256, filename: b.filename, size: b.size, contentType })
    .onConflictDoNothing();
  const assetUri = `asset://${b.sha256}`;
  if (await isVerifiedFor(orgId, b.sha256)) return { assetUri, exists: true };
  const uploadUrl = await presignPut(stagingKey(orgId, b.sha256), contentType, b.size);
  return { assetUri, exists: false, uploadUrl, uploadMethod: "PUT", uploadHeaders: { "content-type": contentType }, expiresIn: 3600 };
}
