import type Stripe from "stripe";
import { and, eq, gt, gte, isNotNull, lt, sql } from "drizzle-orm";
import { db, schema } from "./db";
import { appUrl, env, envOptional } from "./env";
import { ApiError } from "./errors";
import { costUsd, PRICE_PER_MINUTE, round4, type Hardware } from "./pricing";
import { stripe, stripeConfigured } from "./stripe";
import { captureError } from "./posthog";

const { billing, usageEvents, orgs } = schema;
export type BillingRow = typeof billing.$inferSelect;

function envNumber(name: string, fallback: number): number {
  const v = Number(envOptional(name));
  return Number.isFinite(v) && v > 0 ? v : fallback;
}

/** Spend and concurrency limits; env overrides so production can tune them without a release. */
export const billingLimits = {
  /** Jobs running at once in a workspace that can't be charged to a card (trials, free tier). */
  activeJobsFree: () => envNumber("MAX_ACTIVE_JOBS_FREE", 2),
  activeJobsPaid: () => envNumber("MAX_ACTIVE_JOBS_PAID", 20),
  /** Monthly spend cap for workspaces that haven't set their own. */
  defaultMonthlyUsd: () => envNumber("DEFAULT_MONTHLY_LIMIT_USD", 100),
  /** Running time allowed past a free balance before a free-tier job is stopped (heartbeat lag). */
  freeOverrunGraceSeconds: () => envNumber("FREE_OVERRUN_GRACE_SECONDS", 30),
};

// Stripe subscription states in which renders can't be charged to the card on file.
const BLOCKING_SUBSCRIPTION = new Set(["past_due", "unpaid", "canceled", "incomplete_expired", "paused"]);
// States of a subscription that is still ours to reuse (a new one would bill the same meters twice).
const OPEN_SUBSCRIPTION = new Set(["active", "trialing", "past_due", "unpaid", "incomplete", "paused"]);

type CardState = Pick<BillingRow, "hasPaymentMethod" | "subscriptionStatus">;

/** Can usage be charged to the card on file right now? */
export function cardBillable(b: CardState): boolean {
  return b.hasPaymentMethod && !BLOCKING_SUBSCRIPTION.has(b.subscriptionStatus ?? "");
}

export function monthlyLimitUsd(b: Pick<BillingRow, "monthlyLimitUsd">): number {
  return b.monthlyLimitUsd ?? billingLimits.defaultMonthlyUsd();
}

export type BudgetEstimate = { estSeconds: [number, number]; estCostUsd: [number, number] };

/**
 * May a job with this estimate start? Pure; throws the error an agent can act on. Uses the low end of the
 * estimate so it never blocks a job that would fit; running jobs are re-checked by `checkRunningBudget`.
 */
export function checkBudget(
  b: CardState & Pick<BillingRow, "freeSecondsRemaining" | "monthlyLimitUsd">,
  spentThisMonthUsd: number,
  est: BudgetEstimate,
  maxCostUsd?: number | null,
): void {
  if (maxCostUsd != null && est.estCostUsd[0] > maxCostUsd) {
    throw new ApiError("job_over_budget", undefined, { estimatedCostUsd: est.estCostUsd, maxCostUsd });
  }
  if (cardBillable(b)) {
    const limit = monthlyLimitUsd(b);
    if (spentThisMonthUsd + est.estCostUsd[0] > limit) {
      throw new ApiError("spend_limit", undefined, { monthlyLimitUsd: limit, spentThisMonthUsd: round4(spentThisMonthUsd), estimatedCostUsd: est.estCostUsd });
    }
    return;
  }
  if (b.freeSecondsRemaining <= 0) throw new ApiError(b.hasPaymentMethod ? "payment_failed" : "payment_required");
  if (est.estSeconds[0] > b.freeSecondsRemaining) {
    throw new ApiError("insufficient_free_minutes", undefined, {
      estimatedSeconds: est.estSeconds, freeSecondsLeft: Math.floor(b.freeSecondsRemaining),
    });
  }
}

export type RunningCheck = { stop: false } | { stop: true; reason: string };

/**
 * Should a running job be stopped? Pure. `running` is the job's billed + in-flight seconds and cost so far;
 * `pendingSeconds` is in-flight time not yet deducted from the free balance.
 */
export function checkRunningBudget(
  b: CardState & Pick<BillingRow, "freeSecondsRemaining" | "monthlyLimitUsd">,
  spentThisMonthUsd: number,
  running: { pendingSeconds: number; pendingCostUsd: number; jobCostUsd: number },
  maxCostUsd?: number | null,
): RunningCheck {
  if (maxCostUsd != null && running.jobCostUsd > maxCostUsd) {
    return { stop: true, reason: `Stopped: the job reached its maxCostUsd of $${maxCostUsd}.` };
  }
  if (cardBillable(b)) {
    const limit = monthlyLimitUsd(b);
    if (spentThisMonthUsd + running.pendingCostUsd > limit) {
      return { stop: true, reason: `Stopped: the workspace reached its monthly spending limit of $${limit}. Raise it with PUT /api/v1/billing/limit.` };
    }
    return { stop: false };
  }
  if (running.pendingSeconds > b.freeSecondsRemaining + billingLimits.freeOverrunGraceSeconds()) {
    return { stop: true, reason: "Stopped: free render minutes ran out. Add a card (POST /api/v1/billing/checkout) to keep rendering." };
  }
  return { stop: false };
}

export async function getBillingRow(orgId: string): Promise<BillingRow> {
  const [b] = await db().select().from(billing).where(eq(billing.orgId, orgId));
  if (!b) throw new ApiError("payment_required");
  return b;
}

/** Billed (non-free) spend this calendar month (UTC), from recorded usage. */
export async function spentThisMonthUsd(orgId: string, now = new Date()): Promise<number> {
  const { from, to } = monthRange(now);
  const [r] = await db().select({ cost: sql<number>`coalesce(sum(${usageEvents.costUsd}), 0)` }).from(usageEvents)
    .where(and(eq(usageEvents.orgId, orgId), gte(usageEvents.createdAt, from), lt(usageEvents.createdAt, to)));
  return Number(r?.cost ?? 0);
}

/** Take one of the org's concurrent-job slots, or throw concurrency_limit. Atomic (row lock + recheck). */
export async function acquireJobSlot(orgId: string, b: CardState): Promise<void> {
  const max = cardBillable(b) ? billingLimits.activeJobsPaid() : billingLimits.activeJobsFree();
  const taken = await db().update(billing).set({ activeJobs: sql`${billing.activeJobs} + 1` })
    .where(and(eq(billing.orgId, orgId), lt(billing.activeJobs, max))).returning({ n: billing.activeJobs });
  if (!taken.length) throw new ApiError("concurrency_limit", undefined, { maxActiveJobs: max });
}

export async function releaseJobSlot(orgId: string): Promise<void> {
  await db().update(billing).set({ activeJobs: sql`greatest(${billing.activeJobs} - 1, 0)` }).where(eq(billing.orgId, orgId));
}

/** Self-heal slot counters from the jobs table (a lost release would otherwise block an org forever). */
export async function recountActiveJobs(): Promise<void> {
  await db().execute(sql`
    update billing b set active_jobs = c.n
    from (select b2.org_id, (select count(*)::int from jobs j where j.org_id = b2.org_id
            and j.status in ('queued', 'planning', 'rendering', 'encoding')) as n from billing b2) c
    where b.org_id = c.org_id and b.active_jobs <> c.n`);
}

const SECONDS_SQL_COLUMN: Record<Hardware, string> = { cpu: "seconds_cpu", "gpu-l4": "seconds_gpu_l4", "gpu-l40s": "seconds_gpu_l40s" };

export type RecordedUsage = { freeSeconds: number; billedSeconds: number; costUsd: number };

/**
 * Record one chunk's usage exactly once. The usage row, its free/billed split, the free balance and the job's
 * totals change in one statement, so a crash or retry can't leave a chunk recorded but unbilled.
 * Returns null for a duplicate (job, chunk).
 */
export async function recordUsage(jobId: string, orgId: string, chunk: string, hardware: Hardware, seconds: number): Promise<RecordedUsage | null> {
  const col = sql.raw(SECONDS_SQL_COLUMN[hardware]);
  const perMinute = PRICE_PER_MINUTE[hardware];
  const res = await db().execute(sql`
    with old as (
      select free_seconds_remaining as f from billing where org_id = ${orgId} for update
    ), split as (
      select least(greatest(coalesce((select f from old), 0), 0), ${seconds}::float8) as free
    ), ins as (
      insert into usage_events (job_id, chunk, org_id, hardware, seconds, free_seconds, billed_seconds, cost_usd)
      select ${jobId}, ${chunk}, ${orgId}, ${hardware}, ${seconds}::float8, split.free, ${seconds}::float8 - split.free,
        round(((${seconds}::float8 - split.free) / 60 * ${perMinute}::float8)::numeric, 4)::float8
      from split
      on conflict do nothing
      returning free_seconds, billed_seconds, cost_usd
    ), bal as (
      update billing set free_seconds_remaining = billing.free_seconds_remaining - ins.free_seconds, updated_at = now()
      from ins where billing.org_id = ${orgId} returning 1
    ), job as (
      update jobs set ${col} = jobs.${col} + ${seconds}::float8, cost_usd = jobs.cost_usd + ins.cost_usd
      from ins where jobs.id = ${jobId} returning 1
    )
    select free_seconds, billed_seconds, cost_usd from ins`);
  const r = res.rows[0] as { free_seconds: number; billed_seconds: number; cost_usd: number } | undefined;
  return r ? { freeSeconds: Number(r.free_seconds), billedSeconds: Number(r.billed_seconds), costUsd: Number(r.cost_usd) } : null;
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

/**
 * After a card is attached: make it the default and start the metered subscription (idempotent). Reuses any
 * open subscription, including past_due ones (Stripe retries their invoice on the new default card).
 */
export async function activateMeteredSubscription(customerId: string, setupIntentId?: string): Promise<{ id: string; status: string } | null> {
  if (setupIntentId) {
    const si = await stripe().setupIntents.retrieve(setupIntentId);
    const pm = typeof si.payment_method === "string" ? si.payment_method : si.payment_method?.id;
    if (pm) await stripe().customers.update(customerId, { invoice_settings: { default_payment_method: pm } });
  }
  const existing = await stripe().subscriptions.list({ customer: customerId, status: "all", limit: 20 });
  const open = existing.data.find((s) => OPEN_SUBSCRIPTION.has(s.status));
  if (open) return { id: open.id, status: open.status };
  const items = (Object.keys(PRICE_ENV) as Hardware[])
    .map((h) => process.env[PRICE_ENV[h]])
    .filter((p): p is string => Boolean(p))
    .map((price) => ({ price }));
  if (!items.length) return null;
  const sub = await stripe().subscriptions.create({ customer: customerId, items }, { idempotencyKey: `renda-sub-${customerId}` });
  return { id: sub.id, status: sub.status };
}

/** Store the metered subscription's id and status (webhooks). Returns the org, if any. */
export async function setSubscription(customerId: string, subscriptionId: string, status: string): Promise<string | null> {
  const rows = await db().update(billing).set({ stripeSubscriptionId: subscriptionId, subscriptionStatus: status, updatedAt: new Date() })
    .where(eq(billing.stripeCustomerId, customerId)).returning({ orgId: billing.orgId });
  return rows[0]?.orgId ?? null;
}

/** Report billed seconds to the Stripe Billing Meter. Idempotent per (job, chunk). */
export async function reportMeter(orgId: string, jobId: string, chunk: string, hardware: Hardware, billedSeconds: number, at?: Date) {
  if (billedSeconds <= 0 || !stripeConfigured()) return false;
  const [b] = await db().select().from(billing).where(eq(billing.orgId, orgId));
  if (!b?.stripeCustomerId) return false;
  await stripe().billing.meterEvents.create({
    event_name: meterEventName(hardware),
    identifier: `${jobId}:${chunk}`,
    payload: { stripe_customer_id: b.stripeCustomerId, value: String(Math.ceil(billedSeconds)), hardware },
    ...(at ? { timestamp: Math.floor(at.getTime() / 1000) } : {}),
  });
  await db().update(usageEvents).set({ reportedToStripe: true })
    .where(and(eq(usageEvents.jobId, jobId), eq(usageEvents.chunk, chunk)));
  return true;
}

// Stripe only accepts meter events up to 35 days old.
const METER_MAX_AGE_MS = 34 * 24 * 60 * 60 * 1000;

/**
 * Re-send billed usage Stripe never confirmed (a Stripe error or a timeout after the row was written). Run from
 * the sweep. The meter identifier is the same (job:chunk), so Stripe drops a resend of one it already has.
 */
export async function reconcileUsage(limit = 200, now = Date.now()): Promise<number> {
  if (!stripeConfigured()) return 0;
  const rows = await db()
    .select({ jobId: usageEvents.jobId, chunk: usageEvents.chunk, orgId: usageEvents.orgId, hardware: usageEvents.hardware,
      billedSeconds: usageEvents.billedSeconds, createdAt: usageEvents.createdAt })
    .from(usageEvents)
    .innerJoin(billing, eq(billing.orgId, usageEvents.orgId))
    .where(and(
      eq(usageEvents.reportedToStripe, false), gt(usageEvents.billedSeconds, 0), isNotNull(billing.stripeCustomerId),
      lt(usageEvents.createdAt, new Date(now - 2 * 60_000)), gt(usageEvents.createdAt, new Date(now - METER_MAX_AGE_MS)),
    ))
    .orderBy(usageEvents.createdAt)
    .limit(limit);
  let sent = 0;
  for (const r of rows) {
    try {
      if (await reportMeter(r.orgId, r.jobId, r.chunk, r.hardware as Hardware, r.billedSeconds, r.createdAt)) sent += 1;
    } catch (e) {
      captureError(e, { orgId: r.orgId, job_id: r.jobId, chunk: r.chunk, source: "usage_reconcile" });
    }
  }
  return sent;
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
