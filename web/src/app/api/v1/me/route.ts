import { api } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { getBilling } from "@/lib/accounts";
import { isActiveTrial, trialLimits } from "@/lib/trials";
import { billingLimits, cardBillable, monthlyLimitUsd, spentThisMonthUsd } from "@/lib/billing";
import { assertDeletionConfirmed, deleteAccount } from "@/lib/account-deletion";

export const GET = api(async (req) => {
  const { org, user } = await authenticate(req);
  const b = await getBilling(org.id);
  return Response.json({
    user: user ? { id: user.id, email: user.email, name: user.name } : null,
    org: { id: org.id, name: org.name },
    balance: { freeSecondsLeft: Math.max(0, Math.floor(b.freeSecondsRemaining)) },
    billing: {
      hasPaymentMethod: b.hasPaymentMethod,
      subscriptionStatus: b.subscriptionStatus,
      canChargeCard: cardBillable(b),
      monthlyLimitUsd: monthlyLimitUsd(b),
      spentThisMonthUsd: Math.round((await spentThisMonthUsd(org.id)) * 100) / 100,
      activeJobs: b.activeJobs,
      maxActiveJobs: cardBillable(b) ? billingLimits.activeJobsPaid() : billingLimits.activeJobsFree(),
    },
    trial: isActiveTrial(org)
      ? {
          active: true,
          expiresAt: org.trialExpiresAt?.toISOString() ?? null,
          limits: { maxDimension: trialLimits.maxDimension(), priorities: ["economy", "standard"] },
          next: ["Ask the user to open the claim link (`renda claim` prints it) to keep this workspace and get the full free tier."],
        }
      : null,
  });
});

/**
 * Delete the whole account (workspace). Requires {"confirm": "<org id>"} in the body (or ?confirm=<org id>)
 * so an agent can't do it by accident. See lib/account-deletion.ts for what is deleted and what is kept.
 */
export const DELETE = api(async (req) => {
  const { org, user } = await authenticate(req);
  const body = (await req.json().catch(() => null)) as { confirm?: unknown } | null;
  assertDeletionConfirmed(org.id, body?.confirm ?? new URL(req.url).searchParams.get("confirm"));
  const r = await deleteAccount(org.id, { actorUserId: user?.id ?? null });
  return Response.json({
    deleted: true,
    orgId: r.orgId,
    keysRevoked: r.keysRevoked,
    jobsCanceled: r.jobsCanceled,
    subscriptionsCanceled: r.subscriptionsCanceled,
    storagePurged: r.purge.done,
    next: "The account is deleted and this API key no longer works. Remaining stored files are deleted in the background within a few hours. Usage already incurred is invoiced through Stripe.",
  });
});
