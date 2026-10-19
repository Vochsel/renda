import { z } from "zod";
import { db, schema } from "@/lib/db";
import { api, readJson } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { assetKey, objectExists, presignPut } from "@/lib/r2";

const MAX_BYTES = 5 * 1024 ** 3;
const Body = z.object({
  filename: z.string().min(1).max(500),
  size: z.number().int().positive().max(MAX_BYTES, "max 5 GB per file"),
  sha256: z.string().regex(/^[0-9a-f]{64}$/, "lowercase hex sha256 of the file bytes"),
  contentType: z.string().max(200).optional(),
});

/**
 * Register a content-addressed upload. If the bytes already exist (any org uploaded the same file),
 * no upload is needed. Otherwise PUT the file to `uploadUrl` with the same Content-Type.
 */
export const POST = api(async (req) => {
  const { org } = await authenticate(req);
  const b = Body.parse(await readJson(req));
  const contentType = b.contentType ?? "application/octet-stream";
  await db().insert(schema.assets)
    .values({ orgId: org.id, sha256: b.sha256, filename: b.filename, size: b.size, contentType })
    .onConflictDoNothing();
  const assetUri = `asset://${b.sha256}`;
  if (await objectExists(assetKey(b.sha256))) return Response.json({ assetUri, exists: true });
  const uploadUrl = await presignPut(assetKey(b.sha256), contentType, b.size);
  return Response.json({
    assetUri,
    exists: false,
    uploadUrl,
    uploadMethod: "PUT",
    uploadHeaders: { "content-type": contentType },
    expiresIn: 3600,
  });
});
