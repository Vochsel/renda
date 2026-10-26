import { api } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { getJob, serializeJob } from "@/lib/jobs";
import { deleteJobOutputs } from "@/lib/job-outputs";
import { rateLimit } from "@/lib/ratelimit";
import { clientOf, track } from "@/lib/posthog";

export const GET = api(async (req, ctx: RouteContext<"/api/v1/jobs/[id]">) => {
  const { org } = await authenticate(req);
  const { id } = await ctx.params;
  return Response.json(await serializeJob(await getJob(org.id, id)));
});

/** Delete a finished job's outputs (files in storage + the list on the job). 409 while it's running. */
export const DELETE = api(async (req, ctx: RouteContext<"/api/v1/jobs/[id]">) => {
  const { keyId, org, user } = await authenticate(req);
  await rateLimit("api_write_key", keyId);
  const { id } = await ctx.params;
  const out = await deleteJobOutputs(org.id, id);
  track("job_outputs_deleted", { userId: user?.id, orgId: org.id }, { job_id: id, objects: out.objectsDeleted, client: clientOf(req) });
  return Response.json(out);
});
