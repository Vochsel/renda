import { api } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { ApiError } from "@/lib/errors";
import { rateLimit } from "@/lib/ratelimit";
import { deleteAsset } from "@/lib/assets";
import { clientOf, track } from "@/lib/posthog";

/**
 * Delete this account's upload of a file (`asset://<sha256>`). The stored bytes are deleted too unless
 * another account has uploaded the same file. 409 while a running job uses it.
 */
export const DELETE = api(async (req, ctx: RouteContext<"/api/v1/uploads/[sha256]">) => {
  const { keyId, org, user } = await authenticate(req);
  await rateLimit("api_write_key", keyId);
  const sha = decodeURIComponent((await ctx.params).sha256).replace(/^asset:\/\//, "").toLowerCase();
  if (!/^[0-9a-f]{64}$/.test(sha)) throw new ApiError("invalid_request", "Expected a sha256 (64 hex characters) or asset://<sha256>.");
  const out = await deleteAsset(org.id, sha);
  track("upload_deleted", { userId: user?.id, orgId: org.id }, { storage_deleted: out.storageDeleted, client: clientOf(req) });
  return Response.json(out);
});
