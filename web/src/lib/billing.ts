import type Stripe from "stripe";
import { and, eq, gte, lt, sql } from "drizzle-orm";
import { db, schema } from "./db";
import { appUrl, envOptional } from "./env";
import { ApiError } from "./errors";
import { costUsd, PRICE_PER_MINUTE, round4, type Hardware } from "./pricing";
import { stripe } from "./stripe";

const { billing, usageEvents, orgs } = schema;
export type BillingRow = typeof billing.$inferSelect;

function envNumber(name: string, fallback: number): number {
  const v = Number(envOptional(name));
  return Number.isFinite(v) && v > 0 ? v : fallback;
}

/** Concurrency and overrun limits; env overrides so production can tune them without a release. */
export const billingLimits = {
  /** Jobs running at once in a workspace that has never bought credit (they can't render anyway). */
  activeJobsFree: () => envNumber("MAX_ACTIVE_JOBS_FREE", 2),
  activeJobsPaid: () => envNumber("MAX_ACTIVE_JOBS_PAID", 20),
  /** Credit a running job may overdraw (heartbeat lag) before it's stopped. */
  overrunGraceUsd: () => envNumber("CREDIT_OVERRUN_GRACE_USD", 0.05),
};

/** Prepaid credit purchases through Stripe Checkout: $1 units, adjustable on the payment page. */
export const CREDIT_PURCHASE = { minUsd: 5, maxUsd: 1000, defaultUsd: 25 } as const;

type BudgetState = Pick<BillingRow, "creditUsd" | "monthlyLimitUsd">;

/** The workspace's own monthly cap, or null (prepaid credit already bounds what it can spend). */
export function monthlyLimitUsd(b: Pick<BillingRow, "monthlyLimitUsd">): number | null {
  return b.monthlyLimitUsd ?? null;
}

export type BudgetEstimate = { estSeconds: [number, number]; estCostUsd: [number, number] };

/**
 * May a job with this estimate start? Pure; throws the error an agent can act on. Uses the low end of the
 * estimate so it never blocks a job that would fit; running jobs are re-checked by `checkRunningBudget`.
 */
export function checkBudget(b: BudgetState, spentThisMonthUsd: number, est: BudgetEstimate, maxCostUsd?: number | null): void {
  if (maxCostUsd != null && est.estCostUsd[0] > maxCostUsd) {
    throw new ApiError("job_over_budget", undefined, { estimatedCostUsd: est.estCostUsd, maxCostUsd });
  }
  const limit = monthlyLimitUsd(b);
  if (limit != null && spentThisMonthUsd + est.estCostUsd[0] > limit) {
    throw new ApiError("spend_limit", undefined, { monthlyLimitUsd: limit, spentThisMonthUsd: round4(spentThisMonthUsd), estimatedCostUsd: est.estCostUsd });
  }
  if (b.creditUsd <= 0) throw new ApiError("payment_required", undefined, { creditUsd: round4(b.creditUsd) });
  if (est.estCostUsd[0] > b.creditUsd) {
    throw new ApiError("insufficient_credit", undefined, { estimatedCostUsd: est.estCostUsd, creditUsd: round4(b.creditUsd) });
  }
}

export type RunningCheck = { stop: false } | { stop: true; reason: string };

/**
 * Should a running job be stopped? Pure. `pendingCostUsd` is in-flight cost not yet drawn from the credit
 * balance (billed chunks already have been); `jobCostUsd` is the job's billed + in-flight cost.
 */
export function checkRunningBudget(
  b: BudgetState,
  spentThisMonthUsd: number,
  running: { pendingCostUsd: number; jobCostUsd: number },
  maxCostUsd?: number | null,
): RunningCheck {
  if (maxCostUsd != null && running.jobCostUsd > maxCostUsd) {
    return { stop: true, reason: `Stopped: the job reached its maxCostUsd of $${maxCostUsd}.` };
  }
  const limit = monthlyLimitUsd(b);
  if (limit != null && spentThisMonthUsd + running.pendingCostUsd > limit) {
    return { stop: true, reason: `Stopped: the workspace reached its monthly spending limit of $${limit}. Raise it with PUT /api/v1/billing/limit.` };
  }
  if (running.pendingCostUsd > b.creditUsd + billingLimits.overrunGraceUsd()) {
    return { stop: true, reason: "Stopped: render credit ran out. Buy more (POST /api/v1/billing/checkout) and resubmit." };
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
export async function acquireJobSlot(orgId: string, b: Pick<BillingRow, "creditUsd">): Promise<void> {
  const max = maxActiveJobs(b);
  const taken = await db().update(billing).set({ activeJobs: sql`${billing.activeJobs} + 1` })
    .where(and(eq(billing.orgId, orgId), lt(billing.activeJobs, max))).returning({ n: billing.activeJobs });
  if (!taken.length) throw new ApiError("concurrency_limit", undefined, { maxActiveJobs: max });
}

export function maxActiveJobs(b: Pick<BillingRow, "creditUsd">): number {
  return b.creditUsd > 0 ? billingLimits.activeJobsPaid() : billingLimits.activeJobsFree();
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
 * Record one chunk's usage exactly once. The usage row, its free/billed split, the credit and free balances and
 * the job's totals change in one statement, so a crash or retry can't leave a chunk recorded but unbilled.
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
      update billing set free_seconds_remaining = billing.free_seconds_remaining - ins.free_seconds,
        credit_usd = billing.credit_usd - ins.cost_usd, updated_at = now()
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

/**
 * Stripe Checkout (payment mode) for prepaid credit: $1 units, quantity adjustable on the page between
 * CREDIT_PURCHASE.minUsd and maxUsd. Credit lands when the webhook sees the payment succeed (addCredit).
 */
export async function checkoutUrl(orgId: string, email?: string, amountUsd: number = CREDIT_PURCHASE.defaultUsd): Promise<string> {
  const quantity = Math.min(CREDIT_PURCHASE.maxUsd, Math.max(CREDIT_PURCHASE.minUsd, Math.round(amountUsd)));
  const customer = await ensureStripeCustomer(orgId, email);
  const params = {
    mode: "payment",
    customer,
    currency: "usd",
    line_items: [{
      price_data: { currency: "usd", unit_amount: 100, product_data: { name: "Renda render credit", description: "Prepaid render time at per-second prices. Credit doesn't expire." } },
      quantity,
      adjustable_quantity: { enabled: true, minimum: CREDIT_PURCHASE.minUsd, maximum: CREDIT_PURCHASE.maxUsd },
    }],
    invoice_creation: { enabled: true }, // a receipt/tax invoice for every purchase
    success_url: `${appUrl()}/dashboard/billing?credit=success`,
    cancel_url: `${appUrl()}/dashboard/billing?credit=canceled`,
    metadata: { renda_org: orgId, renda_kind: "credit" },
    payment_intent_data: { metadata: { renda_org: orgId, renda_kind: "credit" } },
    // Renda is the merchant (prices are its own), so keep this session out of Managed Payments.
    managed_payments: { enabled: false },
    // Terms acceptance for purchases via an agent's link (recorded by the webhook on completion).
    custom_text: { submit: { message: `By paying you agree to Renda's Terms (${appUrl()}/legal/terms) and Privacy Policy (${appUrl()}/legal/privacy). Credit doesn't expire; unused credit is refundable on request within 30 days.` } },
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
 * Credit a paid Checkout session, once (the purchase row is keyed by session id). Returns the amount credited,
 * or 0 for a replay.
 */
export async function addCredit(sessionId: string, orgId: string, amountUsd: number, paymentIntentId: string | null): Promise<number> {
  if (!(amountUsd > 0)) return 0;
  const res = await db().execute(sql`
    with ins as (
      insert into credit_purchases (stripe_session_id, org_id, payment_intent_id, amount_usd)
      values (${sessionId}, ${orgId}, ${paymentIntentId}, ${amountUsd}::float8)
      on conflict do nothing
      returning amount_usd
    ), bal as (
      update billing set credit_usd = billing.credit_usd + ins.amount_usd, updated_at = now()
      from ins where billing.org_id = ${orgId} returning 1
    )
    select amount_usd from ins`);
  const r = res.rows[0] as { amount_usd: number } | undefined;
  return r ? Number(r.amount_usd) : 0;
}

/**
 * A refund (from the Stripe dashboard) takes the refunded amount back off the balance. `totalRefundedUsd` is the
 * charge's cumulative refund, so replays and partial refunds only ever deduct the increase.
 */
export async function applyRefund(paymentIntentId: string, totalRefundedUsd: number): Promise<{ orgId: string; deductedUsd: number } | null> {
  const res = await db().execute(sql`
    with old as (
      select stripe_session_id, org_id, refunded_usd from credit_purchases where payment_intent_id = ${paymentIntentId} for update
    ), upd as (
      update credit_purchases p set refunded_usd = greatest(old.refunded_usd, least(${totalRefundedUsd}::float8, p.amount_usd))
      from old where p.stripe_session_id = old.stripe_session_id
      returning p.org_id, p.refunded_usd - old.refunded_usd as delta
    ), bal as (
      update billing set credit_usd = billing.credit_usd - upd.delta, updated_at = now()
      from upd where billing.org_id = upd.org_id and upd.delta > 0 returning 1
    )
    select org_id, delta from upd`);
  const r = res.rows[0] as { org_id: string; delta: number } | undefined;
  return r ? { orgId: r.org_id, deductedUsd: Number(r.delta) } : null;
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
