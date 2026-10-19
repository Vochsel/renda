import { z } from "zod";
import { api, readJson } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { parseJobPackage } from "@/lib/packages";
import { estimateFor } from "@/lib/jobs";

const Body = z.object({
  package: z.unknown(),
  kind: z.enum(["scene", "hyperframes"]).default("scene"),
  priority: z.enum(["economy", "standard", "rush"]).default("standard"),
});

export const POST = api(async (req) => {
  await authenticate(req);
  const b = Body.parse(await readJson(req));
  const parsed = parseJobPackage(b.kind, b.package);
  return Response.json(estimateFor(parsed, b.priority));
});
