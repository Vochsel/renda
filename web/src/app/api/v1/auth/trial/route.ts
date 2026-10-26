import { api } from "@/lib/http";
import { createTrial, trialLimits } from "@/lib/trials";
import { clientIp } from "@/lib/clientip";
import { appUrl } from "@/lib/env";
import { termsNotice } from "@/lib/legal";

/**
 * Anonymous, claimable workspace: an API key, no signup and no free render time (buy credit to render).
 * The human opens `claimUrl` later to keep the workspace (and its renders).
 */
export const POST = api(async (req) => {
  const t = await createTrial(clientIp(req));
  return Response.json(
    {
      status: "ok",
      apiKey: t.apiKey,
      claimUrl: t.claimUrl,
      freeSecondsLeft: t.freeSecondsLeft,
      expiresAt: t.expiresAt.toISOString(),
      limits: { maxDimension: trialLimits.maxDimension(), priorities: ["economy", "standard"] },
      hint: "Store apiKey (shown once) and send it as `Authorization: Bearer <apiKey>`. Give claimUrl to the human so they can keep this workspace.",
      next: [
        "Render something: POST /api/v1/jobs (or `renda render <file>`).",
        "Share claimUrl with the user; unclaimed trials expire at expiresAt.",
        "Tell the user that using Renda means agreeing to the Terms and Privacy Policy (links in `terms`).",
      ],
      terms: termsNotice(appUrl()),
    },
    { status: 201 },
  );
});
