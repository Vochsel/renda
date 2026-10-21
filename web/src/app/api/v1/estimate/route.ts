import { z } from "zod";
import { api, readJson } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { rateLimit } from "@/lib/ratelimit";
import { parseJobPackage } from "@/lib/packages";
import { estimateWithPriorities } from "@/lib/jobs";
import { flattenForOrg } from "@/lib/layers";
import { clientOf, track } from "@/lib/posthog";

const Body = z.object({
  package: z.unknown(),
  kind: z.enum(["scene", "hyperframes"]).default("scene"),
  priority: z.enum(["economy", "standard", "rush"]).default("standard"),
  /** Fast mode: alias for priority "rush" (wider fan-out on faster GPUs, costs a bit more). */
  fast: z.boolean().optional(),
});

export const POST = api(async (req) => {
  const auth = await authenticate(req);
  await rateLimit("api_write_key", auth.keyId);
  const b = Body.parse(await readJson(req));
  const pkg = b.kind === "scene" ? (await flattenForOrg(auth.org.id, b.package)).package : b.package;
  const parsed = parseJobPackage(b.kind, pkg, { allowLocal: true });
  const est = estimateWithPriorities(parsed, b.fast ? "rush" : b.priority);
  track("estimate_requested", { userId: auth.user?.id, orgId: auth.org.id }, { kind: b.kind, priority: b.fast ? "rush" : b.priority, client: clientOf(req) });
  return Response.json(est);
});
