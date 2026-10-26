import { z } from "zod";
import { api } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { checkoutUrl, CREDIT_PURCHASE } from "@/lib/billing";
import { appUrl } from "@/lib/env";
import { termsNotice } from "@/lib/legal";

const Body = z.object({ amountUsd: z.number().min(CREDIT_PURCHASE.minUsd).max(CREDIT_PURCHASE.maxUsd).optional() });

/** Stripe Checkout link for a human to buy prepaid render credit (the amount is adjustable on the page). */
export const POST = api(async (req) => {
  const { org, user } = await authenticate(req);
  const text = await req.text();
  const b = Body.parse(text.trim() ? JSON.parse(text) : {});
  const amountUsd = b.amountUsd ?? CREDIT_PURCHASE.defaultUsd;
  const url = await checkoutUrl(org.id, user?.email, amountUsd);
  return Response.json({
    url,
    amountUsd,
    limits: { minUsd: CREDIT_PURCHASE.minUsd, maxUsd: CREDIT_PURCHASE.maxUsd },
    next: `Send this url to the user to buy prepaid render credit ($${amountUsd} preselected; they can change it between $${CREDIT_PURCHASE.minUsd} and $${CREDIT_PURCHASE.maxUsd}). Credit is added as soon as the payment succeeds; GET /api/v1/me shows balance.creditUsd. Tell the user that paying means agreeing to the Terms and Privacy Policy (links in \`terms\`).`,
    terms: termsNotice(appUrl()),
  });
});
