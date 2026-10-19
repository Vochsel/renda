import { timingSafeEqual } from "node:crypto";
import { envOptional } from "@/lib/env";
import { sweepStaleJobs } from "@/lib/jobs";

/** Vercel Cron (see vercel.json) — sends `Authorization: Bearer $CRON_SECRET`. */
export async function GET(req: Request) {
  const secret = envOptional("CRON_SECRET");
  const got = Buffer.from(req.headers.get("authorization") ?? "");
  const want = Buffer.from(`Bearer ${secret}`);
  if (!secret || got.length !== want.length || !timingSafeEqual(got, want)) {
    return Response.json({ error: { code: "unauthorized", message: "Cron only." } }, { status: 401 });
  }
  const failed = await sweepStaleJobs();
  return Response.json({ failed });
}
