import { api } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { getBilling } from "@/lib/accounts";
import { isActiveTrial, trialLimits } from "@/lib/trials";

export const GET = api(async (req) => {
  const { org, user } = await authenticate(req);
  const b = await getBilling(org.id);
  return Response.json({
    user: user ? { id: user.id, email: user.email, name: user.name } : null,
    org: { id: org.id, name: org.name },
    balance: { freeSecondsLeft: Math.max(0, Math.floor(b.freeSecondsRemaining)) },
    billing: { hasPaymentMethod: b.hasPaymentMethod },
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
