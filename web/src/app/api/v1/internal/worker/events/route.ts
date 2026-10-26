import { api } from "@/lib/http";
import { applyWorkerEvent, type WorkerEvent } from "@/lib/jobs";
import { authenticateWorkerEvents } from "@/lib/worker-auth";

/**
 * Modal worker → server. One event or an array, all for the same job, HMAC-SHA256 signed with that job's
 * key (HMAC(RENDA_WORKER_SECRET, "job:" + jobId), see lib/worker-auth.ts). Master-secret signatures are
 * accepted during rollout until RENDA_ACCEPT_MASTER_WORKER_EVENTS=0.
 */
export const POST = api(async (req) => {
  const raw = await req.text();
  const { events } = authenticateWorkerEvents(raw, {
    signature: req.headers.get("x-renda-signature"),
    timestamp: req.headers.get("x-renda-timestamp"),
  });
  const results = [];
  for (const ev of events) results.push(await applyWorkerEvent(ev as unknown as WorkerEvent));
  return Response.json({ ok: true, results });
});
