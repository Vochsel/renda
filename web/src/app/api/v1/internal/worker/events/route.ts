import { api } from "@/lib/http";
import { ApiError } from "@/lib/errors";
import { env, envOptional } from "@/lib/env";
import { verifyWorkerEvent } from "@/lib/hmac";
import { applyWorkerEvent, type WorkerEvent } from "@/lib/jobs";

/** Modal worker → server. Body is HMAC-SHA256 signed with RENDA_WORKER_SECRET. Accepts one event or an array. */
export const POST = api(async (req) => {
  const raw = await req.text();
  const sig = verifyWorkerEvent(
    raw,
    { signature: req.headers.get("x-renda-signature"), timestamp: req.headers.get("x-renda-timestamp") },
    env("RENDA_WORKER_SECRET"),
  );
  // Untimed signatures stay accepted while older worker containers drain after a deploy; set
  // RENDA_ACCEPT_UNTIMED_WORKER_EVENTS=0 once every worker sends x-renda-timestamp.
  const legacyOk = envOptional("RENDA_ACCEPT_UNTIMED_WORKER_EVENTS") !== "0";
  if (sig === "stale") throw new ApiError("invalid_signature", "Event timestamp is too old or in the future.");
  if (sig === "bad" || (sig === "legacy" && !legacyOk)) throw new ApiError("invalid_signature");
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
