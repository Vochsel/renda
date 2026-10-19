import { api } from "@/lib/http";
import { createTrial, trialLimits } from "@/lib/trials";

/**
 * Anonymous, claimable trial: an API key with a few free render-minutes, no signup.
 * The human opens `claimUrl` later to keep the workspace (and its renders).
 */
export const POST = api(async (req) => {
  const ip = req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? req.headers.get("x-real-ip");
  const t = await createTrial(ip);
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
      ],
    },
    { status: 201 },
  );
});
