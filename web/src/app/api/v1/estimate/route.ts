import { z } from "zod";
import { api, readJson } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { parseJobPackage } from "@/lib/packages";
import { estimateFor } from "@/lib/jobs";
import { flattenForOrg } from "@/lib/layers";

const Body = z.object({
  package: z.unknown(),
  kind: z.enum(["scene", "hyperframes"]).default("scene"),
  priority: z.enum(["economy", "standard", "rush"]).default("standard"),
});

export const POST = api(async (req) => {
  const auth = await authenticate(req);
  const b = Body.parse(await readJson(req));
  const pkg = b.kind === "scene" ? (await flattenForOrg(auth.org.id, b.package)).package : b.package;
  const parsed = parseJobPackage(b.kind, pkg);
  return Response.json(estimateFor(parsed, b.priority));
});
