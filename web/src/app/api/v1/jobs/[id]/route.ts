import { api } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { getJob, serializeJob } from "@/lib/jobs";

export const GET = api(async (req, ctx: RouteContext<"/api/v1/jobs/[id]">) => {
  const { org } = await authenticate(req);
  const { id } = await ctx.params;
  return Response.json(await serializeJob(await getJob(org.id, id)));
});
