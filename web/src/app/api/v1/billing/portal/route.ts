import { api } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { portalUrl } from "@/lib/billing";

export const GET = api(async (req) => {
  const { org } = await authenticate(req);
  return Response.json({ url: await portalUrl(org.id) });
});
