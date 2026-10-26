import { and, eq, isNull } from "drizzle-orm";
import { db, schema } from "./db";
import { env } from "./env";
import { generateApiKey } from "./apikeys";
import { workos } from "./workos";
import { identify, track } from "./posthog";
import { claimFreeGrant } from "./freegrants";
import { recordTermsAcceptance, type AcceptanceVia } from "./terms";
import { TERMS_VERSION } from "./legal";

const { orgs, users, memberships, apiKeys, billing } = schema;

export type WorkosUserLike = { id: string; email: string; firstName?: string | null; lastName?: string | null };
export type Account = { user: typeof users.$inferSelect; org: typeof orgs.$inferSelect };

export async function upsertUser(u: WorkosUserLike) {
  const name = [u.firstName, u.lastName].filter(Boolean).join(" ") || null;
  const [row] = await db()
    .insert(users)
    .values({ workosUserId: u.id, email: u.email, name })
    .onConflictDoUpdate({ target: users.workosUserId, set: { email: u.email, name } })
    .returning();
  return row;
}

/**
 * Find or create the user's org. New orgs get a WorkOS organization (so SSO/teams work later),
 * a membership on both sides, and the free-tier balance.
 */
export async function ensureAccount(u: WorkosUserLike, workosOrgId?: string | null, via: AcceptanceVia = "signup"): Promise<Account> {
  const user = await upsertUser(u);
  const existing = await db()
    .select({ org: orgs })
    .from(memberships)
    .innerJoin(orgs, eq(orgs.id, memberships.orgId))
    .where(workosOrgId ? and(eq(memberships.userId, user.id), eq(orgs.workosOrgId, workosOrgId)) : eq(memberships.userId, user.id))
    .limit(1);
  if (existing[0]) return { user, org: existing[0].org };

  let wosOrgId = workosOrgId ?? null;
  const name = `${u.email.split("@")[0]}'s workspace`;
  if (!wosOrgId) {
    const wosOrg = await workos().organizations.createOrganization({ name, metadata: { renda_user: user.id } });
    wosOrgId = wosOrg.id;
    await workos().userManagement.createOrganizationMembership({ organizationId: wosOrgId, userId: u.id, roleSlug: "admin" })
      .catch((e) => console.warn("workos membership", e)); // role slug may not exist in every env; membership is best-effort
  }
  const [org] = await db()
    .insert(orgs)
    .values({ name, workosOrgId: wosOrgId })
    .onConflictDoUpdate({ target: orgs.workosOrgId, set: { name: orgs.name } })
    .returning();
  await db().insert(memberships).values({ orgId: org.id, userId: user.id }).onConflictDoNothing();
  await db()
    .insert(billing)
    .values({ orgId: org.id, freeSecondsRemaining: (await claimFreeGrant(u.email, org.id)) ? Number(env("FREE_SECONDS")) : 0 })
    .onConflictDoNothing();
  identify({ userId: user.id, orgId: org.id }, { email: user.email, $set_once: { signed_up_at: new Date().toISOString() } }, { name: org.name });
  track("signed_up", { userId: user.id, orgId: org.id });
  // Account creation: every sign-up surface says "By continuing you agree to the Terms and Privacy Policy"
  // (and the magic-code API response carries the links for the agent to relay).
  const acceptedAt = new Date();
  await recordTermsAcceptance({ userId: user.id, orgId: org.id }, via, acceptedAt);
  const terms = { termsVersion: TERMS_VERSION, termsAcceptedAt: acceptedAt };
  return { user: { ...user, ...terms }, org: { ...org, ...terms } };
}

export async function createApiKey(orgId: string, userId: string | null, name: string) {
  const k = generateApiKey();
  const [row] = await db().insert(apiKeys).values({ orgId, userId, name, prefix: k.prefix, hash: k.hash }).returning();
  track("api_key_created", { userId, orgId }, { name });
  return { apiKey: k.key, id: row.id, prefix: row.prefix, name: row.name };
}

export async function listApiKeys(orgId: string) {
  return db()
    .select({ id: apiKeys.id, name: apiKeys.name, prefix: apiKeys.prefix, lastUsedAt: apiKeys.lastUsedAt, createdAt: apiKeys.createdAt })
    .from(apiKeys)
    .where(and(eq(apiKeys.orgId, orgId), isNull(apiKeys.revokedAt)))
    .orderBy(apiKeys.createdAt);
}

export async function revokeApiKey(orgId: string, keyId: string) {
  await db().update(apiKeys).set({ revokedAt: new Date() }).where(and(eq(apiKeys.orgId, orgId), eq(apiKeys.id, keyId)));
}

export async function getBilling(orgId: string) {
  const [row] = await db().select().from(billing).where(eq(billing.orgId, orgId));
  return row ?? {
    orgId, stripeCustomerId: null, hasPaymentMethod: false, freeSecondsRemaining: 0, updatedAt: new Date(),
    stripeSubscriptionId: null, subscriptionStatus: null, activeJobs: 0, monthlyLimitUsd: null,
  };
}
