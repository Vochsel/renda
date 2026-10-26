import type Stripe from "stripe";
import { env } from "@/lib/env";
import { stripe } from "@/lib/stripe";
import { addCredit, applyRefund } from "@/lib/billing";
import { captureError, track } from "@/lib/posthog";
import { recordTermsAcceptance } from "@/lib/terms";

const idOf = (v: string | { id: string } | null | undefined) => (typeof v === "string" ? v : v?.id ?? null);

/**
 * Stripe → server. Prepaid credit: a paid Checkout session (lib/billing.ts checkoutUrl) adds its amount to the
 * workspace's balance, once per session; refunds made in the Stripe dashboard take it back off.
 * Subscribe the endpoint to checkout.session.completed, checkout.session.async_payment_succeeded and charge.refunded.
 */
export async function POST(req: Request) {
  const raw = await req.text();
  let event;
  try {
    event = await stripe().webhooks.constructEventAsync(raw, req.headers.get("stripe-signature") ?? "", env("STRIPE_WEBHOOK_SECRET"));
  } catch {
    return Response.json({ error: { code: "invalid_signature", message: "Bad Stripe signature." } }, { status: 400 });
  }

  switch (event.type) {
    // Card payments are paid on completion; bank debits etc. complete first and succeed later.
    case "checkout.session.completed":
    case "checkout.session.async_payment_succeeded": {
      const s = event.data.object as Stripe.Checkout.Session;
      if (s.mode === "payment" && s.payment_status === "paid") await creditSession(s);
      break;
    }
    case "charge.refunded": {
      const charge = event.data.object as Stripe.Charge;
      const pi = idOf(charge.payment_intent);
      if (!pi) break;
      const r = await applyRefund(pi, charge.amount_refunded / 100);
      if (r && r.deductedUsd > 0) track("credit_refunded", { orgId: r.orgId }, { amount_usd: r.deductedUsd });
      break;
    }
  }
  return Response.json({ received: true });
}

async function creditSession(s: Stripe.Checkout.Session) {
  const orgId = s.metadata?.renda_org;
  if (!orgId || s.metadata?.renda_kind !== "credit") return;
  // Credit what was paid for (pre-tax subtotal), not what the link was opened with: the amount is adjustable.
  const amountUsd = (s.amount_subtotal ?? 0) / 100;
  try {
    const added = await addCredit(s.id, orgId, amountUsd, idOf(s.payment_intent));
    if (added > 0) {
      track("credit_purchased", { orgId }, { amount_usd: added });
      await recordTermsAcceptance({ orgId }, "checkout"); // Checkout shows the Terms notice (lib/billing.ts)
    }
  } catch (e) {
    // Paid but not credited is the worst billing failure: surface it and let Stripe retry the webhook.
    captureError(e, { orgId, session: s.id, source: "stripe_credit" });
    throw e;
  }
}
