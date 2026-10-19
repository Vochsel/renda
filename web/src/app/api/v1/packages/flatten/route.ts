import { z } from "zod";
import { safeParsePackage } from "@rendahq/spec";
import { api, readJson, zodIssues } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { flattenForOrg } from "@/lib/layers";

const Body = z.object({ package: z.record(z.string(), z.unknown()) });

/**
 * Resolve `extends` layers and `scene.include[].package` references into one flat package, with
 * provenance (field → layer). Layers here may be https:// URLs or this account's asset:// uploads.
 */
export const POST = api(async (req) => {
  const auth = await authenticate(req);
  const { package: input } = Body.parse(await readJson(req));
  const r = await flattenForOrg(auth.org.id, input);
  const v = safeParsePackage(r.package);
  return Response.json({
    package: r.package,
    provenance: r.provenance,
    layers: r.layers,
    valid: v.success,
    ...(v.success ? {} : { issues: zodIssues(v.error) }),
  });
});
