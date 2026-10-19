import { api } from "@/lib/http";
import { ApiError } from "@/lib/errors";
import { env } from "@/lib/env";
import { verifySignature } from "@/lib/hmac";
import { applyWorkerEvent, type WorkerEvent } from "@/lib/jobs";

/** Modal worker → server. Body is HMAC-SHA256 signed with RENDA_WORKER_SECRET. Accepts one event or an array. */
export const POST = api(async (req) => {
  const raw = await req.text();
  if (!verifySignature(raw, req.headers.get("x-renda-signature"), env("RENDA_WORKER_SECRET"))) {
    throw new ApiError("invalid_signature");
  }
  let body: WorkerEvent | WorkerEvent[];
  try {
    body = JSON.parse(raw);
  } catch {
    throw new ApiError("invalid_request", "Body must be JSON.");
  }
  const events = Array.isArray(body) ? body : [body];
  const results = [];
  for (const ev of events) {
    if (!ev || typeof ev.jobId !== "string" || typeof ev.type !== "string") throw new ApiError("invalid_request", "event needs jobId and type");
    results.push(await applyWorkerEvent(ev));
  }
  return Response.json({ ok: true, results });
});
