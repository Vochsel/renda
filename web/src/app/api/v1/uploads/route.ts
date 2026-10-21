import { api, readJson } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { rateLimit } from "@/lib/ratelimit";
import { registerUpload, UploadBody } from "@/lib/uploads";
import { clientOf, track } from "@/lib/posthog";

/**
 * Register a content-addressed upload; see `registerUpload` for the possession/verification policy.
 * PUT the file to `uploadUrl` with the same Content-Type unless `exists` is true.
 */
export const POST = api(async (req) => {
  const { keyId, org, user } = await authenticate(req);
  await rateLimit("api_write_key", keyId);
  const body = UploadBody.parse(await readJson(req));
  const out = await registerUpload(org.id, body);
  const ext = body.filename.includes(".") ? body.filename.slice(body.filename.lastIndexOf(".") + 1).toLowerCase().slice(0, 12) : "";
  track("upload_registered", { userId: user?.id, orgId: org.id }, { ext, bytes: body.size, exists: Boolean((out as { exists?: boolean }).exists), client: clientOf(req) });
  return Response.json(out);
});
