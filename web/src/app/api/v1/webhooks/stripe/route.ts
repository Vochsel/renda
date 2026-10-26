import { eq } from "drizzle-orm";
import { db, schema } from "@/lib/db";
import { env } from "@/lib/env";
import { stripe } from "@/lib/stripe";
import { activateMeteredSubscription, setSubscription } from "@/lib/billing";
import { captureError, track } from "@/lib/posthog";
import { recordTermsAcceptance } from "@/lib/terms";

const idOf = (v: string | { id: string } | null | undefined) => (typeof v === "string" ? v : v?.id ?? null);

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
        const sub = await activateMeteredSubscription(customer, setupIntent);
        if (sub) await setSubscription(customer, sub.id, sub.status);
        const orgId = await setPaymentMethod(customer, true);
        track("payment_method_added", { orgId });
        if (orgId) await recordTermsAcceptance({ orgId }, "checkout"); // Checkout shows the Terms notice (lib/billing.ts)
      }
      break;
    }
    case "payment_method.detached": {
      // A detached method no longer has a customer; the one it left is in previous_attributes.
      const pm = event.data.object;
      const prev = event.data.previous_attributes as { customer?: string | { id: string } | null } | undefined;
      const customer = idOf(pm.customer) ?? idOf(prev?.customer);
      if (customer) {
        const remaining = await stripe().paymentMethods.list({ customer, limit: 1 });
        const orgId = await setPaymentMethod(customer, remaining.data.length > 0);
        track("payment_method_removed", { orgId }, { has_payment_method: remaining.data.length > 0 });
      }
      break;
    }
    // Subscription state gates card-billed renders (lib/billing.ts cardBillable): past_due, unpaid and canceled
    // pause them until the customer fixes payment in the portal.
    case "customer.subscription.created":
    case "customer.subscription.updated":
    case "customer.subscription.deleted": {
      const sub = event.data.object;
      const customer = idOf(sub.customer);
      if (customer) {
        const orgId = await setSubscription(customer, sub.id, sub.status);
        track("subscription_status_changed", { orgId }, { status: sub.status, event: event.type });
      }
      break;
    }
    case "invoice.payment_failed": {
      const inv = event.data.object;
      const customer = idOf(inv.customer);
      const orgId = customer ? await orgForCustomer(customer) : null;
      track("invoice_payment_failed", { orgId }, { amount_due: inv.amount_due, attempt_count: inv.attempt_count });
      captureError(new Error("invoice payment failed"), { orgId: orgId ?? undefined, invoice: inv.id, source: "stripe_webhook" });
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

async function orgForCustomer(customerId: string): Promise<string | null> {
  const [row] = await db().select({ orgId: schema.billing.orgId }).from(schema.billing).where(eq(schema.billing.stripeCustomerId, customerId));
  return row?.orgId ?? null;
}
