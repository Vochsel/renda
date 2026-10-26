import { createHash, createHmac, randomBytes } from "node:crypto";
import { and, eq, sql } from "drizzle-orm";
import { db, schema } from "./db";
import { env, envOptional, appUrl } from "./env";
import { ApiError } from "./errors";
import { createApiKey } from "./accounts";
import { workos } from "./workos";
import { identify, track } from "./posthog";
import { countHit } from "./ratelimit";
import { claimFreeGrant } from "./freegrants";

const { orgs, billing, trialRequests, memberships } = schema;

// Trial limits come from env so production values can be tuned without a release. The defaults are
// deliberately conservative; a deployment sets its own.
function envNumber(name: string, fallback: number): number {
  const v = Number(envOptional(name));
  return Number.isFinite(v) && v > 0 ? v : fallback;
}

export const trialLimits = {
  /** Free render-seconds a new trial starts with. */
  seconds: () => envNumber("TRIAL_SECONDS", 120),
  /** Lifetime of an unclaimed trial and its key. */
  ttlMs: () => envNumber("TRIAL_TTL_HOURS", 72) * 60 * 60 * 1000,
  /** Trials created per network per 24 h. */
  perNetworkPerDay: () => envNumber("TRIAL_MAX_PER_NETWORK_PER_DAY", 1),
  /** Longest side of a trial render, in pixels. */
  maxDimension: () => envNumber("TRIAL_MAX_DIMENSION", 1280),
};

export function trialSeconds(): number {
  return trialLimits.seconds();
}

type OrgRow = typeof orgs.$inferSelect;

/** Unclaimed trial org? (Claimed trials become normal orgs.) */
export function isActiveTrial(org: Pick<OrgRow, "isTrial" | "claimedAt">): boolean {
  return org.isTrial && !org.claimedAt;
}

export function isExpiredTrial(org: Pick<OrgRow, "isTrial" | "claimedAt" | "trialExpiresAt">, now = new Date()): boolean {
  return isActiveTrial(org) && !!org.trialExpiresAt && org.trialExpiresAt.getTime() < now.getTime();
}

/** Trial limits on a submitted job: capped resolution, no `rush` (the only route to L40S). */
export function assertWithinTrialLimits(
  org: Pick<OrgRow, "isTrial" | "claimedAt">,
  job: { resolution?: [number, number] | null; priority: string },
): void {
  if (!isActiveTrial(org)) return;
  const [w, h] = job.resolution ?? [0, 0];
  const maxDimension = trialLimits.maxDimension();
  if (Math.max(w, h) > maxDimension || job.priority === "rush") {
    throw new ApiError("trial_limit", `Anonymous trials are limited to ${maxDimension}px renders at standard/economy priority.`, {
      maxDimension,
      allowedPriorities: ["economy", "standard"],
    });
  }
}

export function hashToken(token: string): string {
  return createHash("sha256").update(token).digest("hex");
}

function hashIp(ip: string): string {
  // Keyed so the table can't be reversed into IPs. TRIAL_IP_SALT, else derived from the worker secret;
  // env() throws if neither is set, so trials fail closed rather than hashing with a public default.
  const key = envOptional("TRIAL_IP_SALT") ?? `trial-ip:${env("RENDA_WORKER_SECRET")}`;
  return createHmac("sha256", key).update(ip).digest("hex");
}

/**
 * The rate-limited unit for an address: IPv4 as is, IPv6 by its /64 (one subscriber's allocation; anything
 * finer is free to rotate through).
 */
export function networkOf(ip: string): string {
  const addr = ip.trim().toLowerCase().replace(/^\[|\]$/g, "");
  if (!addr.includes(":")) return addr;
  if (/^::ffff:\d+\.\d+\.\d+\.\d+$/.test(addr)) return addr.slice(7); // IPv4-mapped
  const [head, tail = ""] = addr.split("::");
  const h = head ? head.split(":") : [];
  const t = tail ? tail.split(":") : [];
  const full = addr.includes("::") ? [...h, ...Array(Math.max(0, 8 - h.length - t.length)).fill("0"), ...t] : h;
  return `${full.slice(0, 4).map((g) => g.replace(/^0+(?=.)/, "")).join(":")}::/64`;
}

export function claimUrlFor(token: string): string {
  return `${appUrl()}/claim?token=${encodeURIComponent(token)}`;
}

/** Create an unclaimed org with a few free render-minutes and an API key. */
export async function createTrial(ip: string | null) {
  const network = networkOf(ip ?? "unknown");
  const ipHash = hashIp(network);
  // Atomic count per network per day: parallel requests can't all slip through on a stale count.
  const hit = await countHit("trial_network", network, 24 * 60 * 60);
  if (hit.count > trialLimits.perNetworkPerDay()) throw new ApiError("trial_rate_limited");

  const token = randomBytes(24).toString("base64url");
  const expiresAt = new Date(Date.now() + trialLimits.ttlMs());
  const [org] = await db()
    .insert(orgs)
    .values({ name: "Trial workspace", isTrial: true, claimTokenHash: hashToken(token), trialExpiresAt: expiresAt })
    .returning();
  await db().insert(billing).values({ orgId: org.id, freeSecondsRemaining: trialSeconds() });
  await db().insert(trialRequests).values({ ipHash, orgId: org.id });
  const key = await createApiKey(org.id, null, "anonymous trial");
  track("trial_started", { orgId: org.id });
  return { apiKey: key.apiKey, orgId: org.id, claimUrl: claimUrlFor(token), freeSecondsLeft: trialSeconds(), expiresAt };
}

/** Attach a trial org to a signed-in user. Tops the free tier up to FREE_SECONDS, once per person. */
export async function claimTrial(token: string, user: { id: string; workosUserId: string; email: string }) {
  const [org] = await db().select().from(orgs).where(eq(orgs.claimTokenHash, hashToken(token))).limit(1);
  if (!org || !isActiveTrial(org) || isExpiredTrial(org)) throw new ApiError("claim_invalid");

  const name = `${user.email.split("@")[0]}'s workspace`;
  let workosOrgId: string | null = null;
  try {
    const wosOrg = await workos().organizations.createOrganization({ name, metadata: { renda_org: org.id } });
    workosOrgId = wosOrg.id;
    await workos().userManagement
      .createOrganizationMembership({ organizationId: wosOrg.id, userId: user.workosUserId, roleSlug: "admin" })
      .catch((e) => console.warn("workos membership", e));
  } catch (e) {
    console.warn("workos org for claimed trial", e); // claim still succeeds locally
  }

  const [claimed] = await db()
    .update(orgs)
    .set({ name, claimedAt: new Date(), claimTokenHash: null, workosOrgId })
    .where(and(eq(orgs.id, org.id), eq(orgs.isTrial, true), sql`${orgs.claimedAt} is null`))
    .returning();
  if (!claimed) throw new ApiError("claim_invalid"); // lost a race with another claim
  await db().insert(memberships).values({ orgId: org.id, userId: user.id }).onConflictDoNothing();
  identify({ userId: user.id, orgId: org.id }, { email: user.email }, { name, claimed_trial: true });
  track("trial_claimed", { userId: user.id, orgId: org.id });
  const topUp = (await claimFreeGrant(user.email, org.id)) ? Math.max(0, Number(env("FREE_SECONDS")) - trialSeconds()) : 0;
  if (topUp > 0) await db()
    .update(billing)
    .set({ freeSecondsRemaining: sql`${billing.freeSecondsRemaining} + ${topUp}`, updatedAt: new Date() })
    .where(eq(billing.orgId, org.id));
  return claimed;
}
