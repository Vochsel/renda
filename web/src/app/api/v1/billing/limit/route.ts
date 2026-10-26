import { z } from "zod";
import { eq } from "drizzle-orm";
import { api, readJson } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { rateLimit } from "@/lib/ratelimit";
import { db, schema } from "@/lib/db";
import { getBillingRow, monthlyLimitUsd, spentThisMonthUsd } from "@/lib/billing";
import { track } from "@/lib/posthog";

const Body = z.object({
  /** USD per calendar month (UTC). null restores the default. */
  monthlyLimitUsd: z.number().positive().max(100_000).nullable(),
});

/** The workspace's monthly spending limit: new jobs are refused, and running ones stopped, once it's reached. */
export const GET = api(async (req) => {
  const { org } = await authenticate(req);
  const b = await getBillingRow(org.id);
  return Response.json({ monthlyLimitUsd: monthlyLimitUsd(b), spentThisMonthUsd: Math.round((await spentThisMonthUsd(org.id)) * 100) / 100 });
});

export const PUT = api(async (req) => {
  const { keyId, org, user } = await authenticate(req);
  await rateLimit("api_write_key", keyId);
  const b = Body.parse(await readJson(req));
  await db().update(schema.billing).set({ monthlyLimitUsd: b.monthlyLimitUsd, updatedAt: new Date() }).where(eq(schema.billing.orgId, org.id));
  track("spend_limit_set", { userId: user?.id, orgId: org.id }, { monthly_limit_usd: b.monthlyLimitUsd });
  const fresh = await getBillingRow(org.id);
  return Response.json({ monthlyLimitUsd: monthlyLimitUsd(fresh), spentThisMonthUsd: Math.round((await spentThisMonthUsd(org.id)) * 100) / 100 });
});
