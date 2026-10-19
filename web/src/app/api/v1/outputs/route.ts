import { z } from "zod";
import { api } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { listOutputs } from "@/lib/outputs";

const Query = z.object({
  cursor: z.string().max(512).optional(),
  limit: z.coerce.number().int().min(1).max(100).default(30),
  kind: z.enum(["image", "video", "model"]).optional(),
  all: z.enum(["0", "1", "true", "false"]).optional(),
});

/** Every render output of the caller's org, newest first, cursor-paginated (what the dashboard gallery shows). */
export const GET = api(async (req) => {
  const { org } = await authenticate(req);
  const q = Query.parse(Object.fromEntries(new URL(req.url).searchParams));
  return Response.json(
    await listOutputs(org.id, { cursor: q.cursor, limit: q.limit, kind: q.kind, all: q.all === "1" || q.all === "true" }),
  );
});
