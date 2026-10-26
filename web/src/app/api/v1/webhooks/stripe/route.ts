import { eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { env } from "@/lib/env";
import { stripe } from "@/lib/stripe";
import { activateMeteredSubscription } from "@/lib/billing";
import { track } from "@/lib/posthog";
import { recordTermsAcceptance } from "@/lib/terms";

export async function POST(req: Request) {
  const raw = await req.text();
  let event;
  try {
    event = await stripe().webhooks.constructEventAsync(raw, req.headers.get("stripe-signature") ?? "", env("STRIPE_WEBHOOK_SECRET"));
  } catch {
    return Response.json({ error: { code: "invalid_signature", message: "Bad Stripe signature." } }, { status: 400 });
  }

  switch (event.type) {
    case "checkout.session.completed": {
      const s = event.data.object;
      const customer = typeof s.customer === "string" ? s.customer : s.customer?.id;
      if (s.mode === "setup" && customer) {
        const setupIntent = typeof s.setup_intent === "string" ? s.setup_intent : s.setup_intent?.id;
        await activateMeteredSubscription(customer, setupIntent);
        const orgId = await setPaymentMethod(customer, true);
        track("payment_method_added", { orgId });
        if (orgId) await recordTermsAcceptance({ orgId }, "checkout"); // Checkout shows the Terms notice (lib/billing.ts)
      }
      break;
    }
    case "payment_method.detached": {
      const pm = event.data.object;
      const customer = typeof pm.customer === "string" ? pm.customer : pm.customer?.id;
      if (customer) {
        const remaining = await stripe().paymentMethods.list({ customer, limit: 1 });
        const orgId = await setPaymentMethod(customer, remaining.data.length > 0);
        track("payment_method_removed", { orgId }, { has_payment_method: remaining.data.length > 0 });
      }
      break;
    }
  }
  return Response.json({ received: true });
}

async function setPaymentMethod(customerId: string, has: boolean): Promise<string | null> {
  const rows = await db().update(schema.billing).set({ hasPaymentMethod: has, updatedAt: new Date() })
    .where(eq(schema.billing.stripeCustomerId, customerId)).returning({ orgId: schema.billing.orgId });
  return rows[0]?.orgId ?? null;
}
