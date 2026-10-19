import { api } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { ApiError } from "@/lib/errors";
import { monthRange, usageSummary } from "@/lib/billing";

export const GET = api(async (req) => {
  const { org } = await authenticate(req);
  const q = new URL(req.url).searchParams;
  const def = monthRange();
  const from = q.get("from") ? new Date(q.get("from")!) : def.from;
  const to = q.get("to") ? new Date(q.get("to")!) : def.to;
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) throw new ApiError("invalid_request", "from/to must be ISO dates.");
  return Response.json(await usageSummary(org.id, from, to));
});
