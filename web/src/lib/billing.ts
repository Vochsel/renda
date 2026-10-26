import type Stripe from "stripe";
import { and, eq, gte, lt, sql } from "drizzle-orm";
import { db, schema } from "./db";
import { appUrl, env } from "./env";
import { ApiError } from "./errors";
import { costUsd, round4, type Hardware } from "./pricing";
import { stripe, stripeConfigured } from "./stripe";

const { billing, usageEvents, orgs } = schema;

export async function assertCanRender(orgId: string) {
  const [b] = await db().select().from(billing).where(eq(billing.orgId, orgId));
  if (!b || (!b.hasPaymentMethod && b.freeSecondsRemaining <= 0)) throw new ApiError("payment_required");
}

/** Atomically take up to `seconds` from the free balance; returns how many were free. */
export async function consumeFreeSeconds(orgId: string, seconds: number): Promise<number> {
  const res = await db().execute(sql`
    with old as (select free_seconds_remaining as f from billing where org_id = ${orgId} for update)
    update billing b set free_seconds_remaining = greatest(old.f - ${seconds}, 0), updated_at = now()
    from old where b.org_id = ${orgId}
    returning old.f as before, b.free_seconds_remaining as after`);
  const row = res.rows[0] as { before: number; after: number } | undefined;
  if (!row) return 0;
  return Number(row.before) - Number(row.after);
}

/** Split metered seconds into free/billed and compute cost. Pure, for tests. */
export function splitUsage(hardware: Hardware, seconds: number, freeSeconds: number) {
  const billedSeconds = Math.max(0, seconds - freeSeconds);
  return { freeSeconds, billedSeconds, costUsd: costUsd(hardware, billedSeconds) };
}

export async function ensureStripeCustomer(orgId: string, email?: string): Promise<string> {
  const [b] = await db().select().from(billing).where(eq(billing.orgId, orgId));
  if (b?.stripeCustomerId) return b.stripeCustomerId;
  const [org] = await db().select().from(orgs).where(eq(orgs.id, orgId));
  const customer = await stripe().customers.create({ email, name: org?.name, metadata: { renda_org: orgId } });
  await db().update(billing).set({ stripeCustomerId: customer.id, updatedAt: new Date() }).where(eq(billing.orgId, orgId));
  return customer.id;
}

export async function checkoutUrl(orgId: string, email?: string): Promise<string> {
  const customer = await ensureStripeCustomer(orgId, email);
  const params = {
    mode: "setup",
    customer,
    currency: "usd",
    success_url: `${appUrl()}/dashboard/billing?setup=success`,
    cancel_url: `${appUrl()}/dashboard/billing?setup=canceled`,
    metadata: { renda_org: orgId },
    setup_intent_data: { metadata: { renda_org: orgId } },
    // Accounts with Managed Payments (Stripe as merchant of record) on by default reject setup mode.
    // Renda bills usage itself via meters, so opt this card-collection session out.
    managed_payments: { enabled: false },
    // Terms acceptance for cards added via an agent's link (recorded by the webhook on completion).
    custom_text: { submit: { message: `By adding a card you agree to Renda's Terms (${appUrl()}/legal/terms) and Privacy Policy (${appUrl()}/legal/privacy). Usage is billed monthly in arrears.` } },
  } as Stripe.Checkout.SessionCreateParams;
  const session = await stripe().checkout.sessions.create(params);
  return session.url!;
}

export async function portalUrl(orgId: string): Promise<string> {
  const customer = await ensureStripeCustomer(orgId);
  const session = await stripe().billingPortal.sessions.create({ customer, return_url: `${appUrl()}/dashboard/billing` });
  return session.url;
}

/**
 * One Stripe meter per hardware class (`render_seconds_cpu`, `render_seconds_gpu_l4`, `render_seconds_gpu_l40s`),
 * each with its own metered price, so classes can be priced independently.
 */
export function meterEventName(hardware: Hardware): string {
  return `${env("STRIPE_METER_EVENT")}_${hardware.replace(/-/g, "_")}`;
}

const PRICE_ENV: Record<Hardware, string> = { cpu: "STRIPE_PRICE_CPU", "gpu-l4": "STRIPE_PRICE_GPU_L4", "gpu-l40s": "STRIPE_PRICE_GPU_L40S" };

/** After a card is attached: make it the default and start the metered subscription (idempotent). */
export async function activateMeteredSubscription(customerId: string, setupIntentId?: string) {
  if (setupIntentId) {
    const si = await stripe().setupIntents.retrieve(setupIntentId);
    const pm = typeof si.payment_method === "string" ? si.payment_method : si.payment_method?.id;
    if (pm) await stripe().customers.update(customerId, { invoice_settings: { default_payment_method: pm } });
  }
  const existing = await stripe().subscriptions.list({ customer: customerId, status: "active", limit: 1 });
  if (existing.data.length) return existing.data[0].id;
  const items = (Object.keys(PRICE_ENV) as Hardware[])
    .map((h) => process.env[PRICE_ENV[h]])
    .filter((p): p is string => Boolean(p))
    .map((price) => ({ price }));
  if (!items.length) return null;
  const sub = await stripe().subscriptions.create({ customer: customerId, items }, { idempotencyKey: `renda-sub-${customerId}` });
  return sub.id;
}

/** Report billed seconds to the Stripe Billing Meter. Idempotent per (job, chunk). */
export async function reportMeter(orgId: string, jobId: string, chunk: string, hardware: Hardware, billedSeconds: number) {
  if (billedSeconds <= 0 || !stripeConfigured()) return false;
  const [b] = await db().select().from(billing).where(eq(billing.orgId, orgId));
  if (!b?.stripeCustomerId) return false;
  await stripe().billing.meterEvents.create({
    event_name: meterEventName(hardware),
    identifier: `${jobId}:${chunk}`,
    payload: { stripe_customer_id: b.stripeCustomerId, value: String(Math.ceil(billedSeconds)), hardware },
  });
  await db().update(usageEvents).set({ reportedToStripe: true })
    .where(and(eq(usageEvents.jobId, jobId), eq(usageEvents.chunk, chunk)));
  return true;
}

export async function usageSummary(orgId: string, from: Date, to: Date) {
  const where = and(eq(usageEvents.orgId, orgId), gte(usageEvents.createdAt, from), lt(usageEvents.createdAt, to));
  const byHw = await db()
    .select({ hardware: usageEvents.hardware, seconds: sql<number>`sum(${usageEvents.seconds})`, billed: sql<number>`sum(${usageEvents.billedSeconds})`, cost: sql<number>`sum(${usageEvents.costUsd})` })
    .from(usageEvents).where(where).groupBy(usageEvents.hardware);
  const byDay = await db()
    .select({ day: sql<string>`to_char(date_trunc('day', ${usageEvents.createdAt}), 'YYYY-MM-DD')`, seconds: sql<number>`sum(${usageEvents.seconds})`, cost: sql<number>`sum(${usageEvents.costUsd})` })
    .from(usageEvents).where(where).groupBy(sql`1`).orderBy(sql`1`);
  const seconds = { cpu: 0, gpu: 0 };
  let billedSeconds = 0, cost = 0;
  for (const r of byHw) {
    const s = Number(r.seconds);
    if (r.hardware === "cpu") seconds.cpu += s; else seconds.gpu += s;
    billedSeconds += Number(r.billed);
    cost += Number(r.cost);
  }
  return {
    from: from.toISOString(),
    to: to.toISOString(),
    seconds,
    byHardware: byHw.map((r) => ({ hardware: r.hardware, seconds: Number(r.seconds), costUsd: round4(Number(r.cost)) })),
    minutesBilled: Math.round((billedSeconds / 60) * 100) / 100,
    costUsd: round4(cost),
    byDay: byDay.map((d) => ({ day: d.day, seconds: Number(d.seconds), costUsd: round4(Number(d.cost)) })),
  };
}

export function monthRange(now = new Date()) {
  return { from: new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1)), to: new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1)) };
}
