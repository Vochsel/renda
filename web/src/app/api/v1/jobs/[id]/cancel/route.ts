import { api } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { cancelJob, serializeJob } from "@/lib/jobs";

export const POST = api(async (req, ctx: RouteContext<"/api/v1/jobs/[id]/cancel">) => {
  const { org } = await authenticate(req);
  const { id } = await ctx.params;
  return Response.json(await serializeJob(await cancelJob(org.id, id)));
});
