import { and, eq, isNotNull, isNull } from "drizzle-orm";
import { db, schema } from "./db";
import { ApiError } from "./errors";
import { cancelJob, listActiveJobs } from "./jobs";
import { purgeOrgOutputs, repurgeJobsFinishedSince } from "./job-outputs";
import { releaseOrgAssets } from "./assets";
import { stripe, stripeConfigured } from "./stripe";
import { workos } from "./workos";
import { track } from "./posthog";

const { orgs, users, memberships, apiKeys, deviceCodes, jobs, billing, trialRequests } = schema;

/**
 * Account deletion.
 *
 * Deleted immediately: API keys and device codes, running jobs (cancelled), the Stripe subscription
 * (cancelled with a final invoice for usage so far), job contents (package, names, errors, output lists),
 * members' user rows (email, name) unless they belong to another workspace, and the WorkOS user/org.
 * Deleted from storage in batches (inline, then the retention cron): every output under outputs/<jobId>/,
 * and the org's uploads (the shared copy only once no other account has that file).
 *
 * Kept, anonymised: the org row (renamed, unlinked from WorkOS), job rows with only id/kind/status/
 * hardware/seconds/cost/timestamps, usage_events and the billing row (Stripe customer id, free balance).
 * Those are the invoicing and tax records for usage and credit purchases, and they contain no customer
 * content or personal details. usage_events' foreign keys restrict deletes (migration 0006) so a future
 * hard delete can't silently drop them.
 */

/** How long after deletion to re-sweep jobs that were cancelled mid-render (their containers may still upload). */
export const LATE_UPLOAD_GRACE_MS = 2 * 60 * 60 * 1000;

export type DeletionResult = {
  orgId: string;
  alreadyDeleted: boolean;
  keysRevoked: number;
  jobsCanceled: number;
  subscriptionsCanceled: number;
  usersDeleted: number;
  purge: PurgeResult;
};

export async function deleteAccount(orgId: string, opts: { actorUserId?: string | null } = {}): Promise<DeletionResult> {
  const [org] = await db().select().from(orgs).where(eq(orgs.id, orgId)).limit(1);
  if (!org) throw new ApiError("not_found", "No such account.");
  if (org.deletedAt) {
    return { orgId, alreadyDeleted: true, keysRevoked: 0, jobsCanceled: 0, subscriptionsCanceled: 0, usersDeleted: 0, purge: await purgeDeletedOrg(orgId) };
  }

  // 1. Billing first: if Stripe refuses, nothing has been destroyed yet and the caller can retry.
  const subscriptionsCanceled = await cancelStripeSubscriptions(orgId);

  // 2. Access: no key, device login or session can act for the account from here on.
  const keys = await db().delete(apiKeys).where(eq(apiKeys.orgId, orgId)).returning({ id: apiKeys.id });
  await db().delete(deviceCodes).where(eq(deviceCodes.orgId, orgId));

  // 3. Running jobs.
  let jobsCanceled = 0;
  for (const j of await listActiveJobs(orgId, 1000)) {
    try {
      await cancelJob(orgId, j.id);
      jobsCanceled++;
    } catch (e) {
      if (!(e instanceof ApiError && e.code === "conflict")) throw e; // finished meanwhile
    }
  }

  // 4. Customer content in the database. Late worker events still find the job row (usage is recorded);
  //    `done` is ignored for cancelled jobs, so outputs aren't re-listed.
  await db().update(jobs).set({
    package: {}, name: null, source: null, warnings: [], error: null, estimate: null, live: null,
    outputs: [], cacheKey: null, idempotencyKey: null, createdBy: null,
  }).where(eq(jobs.orgId, orgId));

  // 5. People. Members who belong to no other workspace are deleted here and in WorkOS.
  const members = await db().delete(memberships).where(eq(memberships.orgId, orgId)).returning({ userId: memberships.userId });
  let usersDeleted = 0;
  for (const { userId } of members) {
    const [other] = await db().select({ orgId: memberships.orgId }).from(memberships).where(eq(memberships.userId, userId)).limit(1);
    if (other) continue;
    const [u] = await db().delete(users).where(eq(users.id, userId)).returning({ workosUserId: users.workosUserId });
    if (!u) continue;
    usersDeleted++;
    await workos().userManagement.deleteUser(u.workosUserId).catch((e: unknown) => console.warn("workos deleteUser", e));
  }
  if (org.workosOrgId) {
    await workos().organizations.deleteOrganization(org.workosOrgId).catch((e: unknown) => console.warn("workos deleteOrganization", e));
  }

  // 6. Tombstone. Trial rate-limit rows keep their (hashed) network but lose the link to the account.
  await db().update(trialRequests).set({ orgId: null }).where(eq(trialRequests.orgId, orgId));
  await db().update(orgs).set({
    name: "Deleted account", workosOrgId: null, claimTokenHash: null, deletedAt: new Date(),
  }).where(eq(orgs.id, orgId));
  track("account_deleted", { orgId }, { jobs_canceled: jobsCanceled, by_user: Boolean(opts.actorUserId) });

  // 7. Storage: as much as fits now; the retention cron finishes the rest.
  const purge = await purgeDeletedOrg(orgId);
  return { orgId, alreadyDeleted: false, keysRevoked: keys.length, jobsCanceled, subscriptionsCanceled, usersDeleted, purge };
}

/** Cancel the org's live Stripe subscriptions, invoicing usage so far. The customer is kept for that invoice. */
async function cancelStripeSubscriptions(orgId: string): Promise<number> {
  const [b] = await db().select({ customer: billing.stripeCustomerId }).from(billing).where(eq(billing.orgId, orgId));
  if (!b?.customer || !stripeConfigured()) return 0;
  const subs = await stripe().subscriptions.list({ customer: b.customer, status: "all", limit: 100 });
  let n = 0;
  for (const s of subs.data) {
    if (s.status === "canceled" || s.status === "incomplete_expired") continue;
    await stripe().subscriptions.cancel(s.id, { invoice_now: true, prorate: false });
    n++;
  }
  return n;
}

export type PurgeResult = { jobs: number; assets: number; done: boolean };

/**
 * Delete a deleted org's stored files, in batches. Idempotent; marks orgs.purged_at when nothing is left
 * and the late-upload grace period has passed.
 */
export async function purgeDeletedOrg(orgId: string, limits = { jobs: 100, assets: 200 }, now = new Date()): Promise<PurgeResult> {
  const [org] = await db().select({ deletedAt: orgs.deletedAt, purgedAt: orgs.purgedAt }).from(orgs).where(eq(orgs.id, orgId));
  if (!org?.deletedAt) throw new Error(`org ${orgId} is not deleted`);
  if (org.purgedAt) return { jobs: 0, assets: 0, done: true };
  const jobsPurged = await purgeOrgOutputs(orgId, limits.jobs);
  const assetsReleased = await releaseOrgAssets(orgId, limits.assets);
  const more = jobsPurged >= limits.jobs || assetsReleased >= limits.assets;
  const graceOver = now.getTime() - org.deletedAt.getTime() >= LATE_UPLOAD_GRACE_MS;
  if (more || !graceOver) return { jobs: jobsPurged, assets: assetsReleased, done: false };
  // Containers cancelled at deletion may have uploaded after the first pass.
  await repurgeJobsFinishedSince(orgId, new Date(org.deletedAt.getTime() - 10 * 60_000));
  await db().update(orgs).set({ purgedAt: now }).where(eq(orgs.id, orgId));
  return { jobs: jobsPurged, assets: assetsReleased, done: true };
}

/** Cron: continue purging deleted orgs that still have files. */
export async function purgeDeletedOrgs(limit = 5, now = new Date()) {
  const due = await db().select({ id: orgs.id }).from(orgs)
    .where(and(isNotNull(orgs.deletedAt), isNull(orgs.purgedAt))).limit(limit);
  const out: Record<string, PurgeResult> = {};
  for (const o of due) out[o.id] = await purgeDeletedOrg(o.id, undefined, now);
  return out;
}

/** Confirmation token the API endpoint requires (the org id from GET /api/v1/me). */
export function assertDeletionConfirmed(orgId: string, confirm: unknown) {
  if (confirm !== orgId) {
    throw new ApiError("invalid_request", "Account deletion must be confirmed.", { confirmWith: "org.id from GET /api/v1/me" },
      "Deleting the account is permanent: it revokes every key, cancels running jobs and deletes all renders and uploads. Only do this if the user asked for it; then send {\"confirm\": \"<org id>\"} (the org.id from GET /api/v1/me).");
  }
}
