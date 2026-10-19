import { and, eq } from "drizzle-orm";
import { z } from "zod";
import { db, schema } from "@/lib/db";
import { api, readJson } from "@/lib/http";
import { ApiError } from "@/lib/errors";
import { hashApiKey } from "@/lib/apikeys";
import { createApiKey } from "@/lib/accounts";
import { clientIp } from "@/lib/clientip";
import { rateLimit } from "@/lib/ratelimit";

const { deviceCodes } = schema;
const Body = z.object({ deviceCode: z.string().min(10) });

export const POST = api(async (req) => {
  await rateLimit("device_token_ip", clientIp(req));
  const { deviceCode } = Body.parse(await readJson(req));
  const hash = hashApiKey(deviceCode);
  const [row] = await db().select().from(deviceCodes).where(eq(deviceCodes.deviceCodeHash, hash));
  if (!row || row.status === "consumed") throw new ApiError("expired_token");
  if (row.expiresAt < new Date()) throw new ApiError("expired_token");
  if (row.status === "denied") throw new ApiError("forbidden", "The user denied this device.", {}, "Start over with POST /api/v1/auth/device.");
  if (row.status === "pending") throw new ApiError("authorization_pending");
  // Approved: consume exactly once.
  const claimed = await db().update(deviceCodes).set({ status: "consumed" })
    .where(and(eq(deviceCodes.deviceCodeHash, hash), eq(deviceCodes.status, "approved"))).returning();
  if (!claimed.length || !row.orgId) throw new ApiError("expired_token");
  const key = await createApiKey(row.orgId, row.userId, "cli (device login)");
  return Response.json({ apiKey: key.apiKey });
});
