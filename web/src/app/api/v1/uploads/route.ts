import { z } from "zod";
import { db, schema } from "@/lib/db";
import { api, readJson } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { rateLimit } from "@/lib/ratelimit";
import { presignPut, stagingKey } from "@/lib/r2";
import { isVerifiedFor } from "@/lib/assets";

const MAX_BYTES = 5 * 1024 ** 3;
const Body = z.object({
  filename: z.string().min(1).max(500),
  size: z.number().int().positive().max(MAX_BYTES, "max 5 GB per file"),
  sha256: z.string().regex(/^[0-9a-f]{64}$/, "lowercase hex sha256 of the file bytes"),
  // A plain MIME type; it's signed into the upload URL and stored, so no parameters or odd characters.
  contentType: z.string().max(100).regex(/^[a-z0-9][a-z0-9.+-]*\/[a-z0-9][a-z0-9.+-]*$/i, "a MIME type like model/gltf-binary").optional(),
});

/**
 * Register a content-addressed upload. If this account has already proven it has these bytes, no
 * upload is needed. Otherwise PUT the file to `uploadUrl` (this account's staging area) with the same
 * Content-Type; the bytes are hashed and checked against sha256 the first time a job uses them.
 * (Another account having uploaded the same file doesn't count: a hash isn't proof of having it.)
 */
export const POST = api(async (req) => {
  const { keyId, org } = await authenticate(req);
  await rateLimit("api_write_key", keyId);
  const b = Body.parse(await readJson(req));
  const contentType = b.contentType ?? "application/octet-stream";
  await db().insert(schema.assets)
    .values({ orgId: org.id, sha256: b.sha256, filename: b.filename, size: b.size, contentType })
    .onConflictDoNothing();
  const assetUri = `asset://${b.sha256}`;
  if (await isVerifiedFor(org.id, b.sha256)) return Response.json({ assetUri, exists: true });
  const uploadUrl = await presignPut(stagingKey(org.id, b.sha256), contentType, b.size);
  return Response.json({
    assetUri,
    exists: false,
    uploadUrl,
    uploadMethod: "PUT",
    uploadHeaders: { "content-type": contentType },
    expiresIn: 3600,
  });
});
