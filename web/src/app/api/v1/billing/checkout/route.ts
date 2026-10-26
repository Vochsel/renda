import { api } from "@/lib/http";
import { authenticate } from "@/lib/auth";
import { checkoutUrl } from "@/lib/billing";
import { appUrl } from "@/lib/env";
import { termsNotice } from "@/lib/legal";

/** Stripe Checkout (setup mode) link for a human to attach a card. */
export const POST = api(async (req) => {
  const { org, user } = await authenticate(req);
  const url = await checkoutUrl(org.id, user?.email);
  return Response.json({
    url,
    next: "Send this url to the user. Once they finish, renders are billed per second past the free tier, monthly in arrears. Tell the user that adding a card means agreeing to the Terms and Privacy Policy (links in `terms`).",
    terms: termsNotice(appUrl()),
  });
});
