import { z } from "zod";
import { api, readJson } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { rateLimit } from "@/lib/ratelimit";
import { listJobs, serializeJob, submitJob } from "@/lib/jobs";

const Body = z.object({
  package: z.unknown(),
  kind: z.enum(["scene", "hyperframes"]).default("scene"),
  priority: z.enum(["economy", "standard", "rush"]).default("standard"),
  idempotencyKey: z.string().min(1).max(200).optional(),
});

export const POST = api(async (req) => {
  const { keyId, org, user } = await authenticate(req);
  await rateLimit("api_write_key", keyId);
  const b = Body.parse(await readJson(req));
  const job = await submitJob({
    orgId: org.id,
    org,
    userId: user?.id ?? null,
    kind: b.kind,
    package: b.package,
    priority: b.priority,
    idempotencyKey: b.idempotencyKey ?? req.headers.get("idempotency-key") ?? undefined,
  });
  return Response.json(job, { status: 201 });
});

export const GET = api(async (req) => {
  const { org } = await authenticate(req);
  const q = new URL(req.url).searchParams;
  const rows = await listJobs(org.id, {
    limit: q.get("limit") ? Number(q.get("limit")) : undefined,
    before: q.get("before") ?? undefined,
    status: q.get("status") ?? undefined,
  });
  return Response.json({ jobs: await Promise.all(rows.map(serializeJob)) });
});
