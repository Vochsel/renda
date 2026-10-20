import { api, readJson } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { rateLimit } from "@/lib/ratelimit";
import { registerUpload, UploadBody } from "@/lib/uploads";

/**
 * Register a content-addressed upload; see `registerUpload` for the possession/verification policy.
 * PUT the file to `uploadUrl` with the same Content-Type unless `exists` is true.
 */
export const POST = api(async (req) => {
  const { keyId, org } = await authenticate(req);
  await rateLimit("api_write_key", keyId);
  return Response.json(await registerUpload(org.id, UploadBody.parse(await readJson(req))));
});
