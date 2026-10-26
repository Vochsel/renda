import { and, eq, isNull, ne, or } from "drizzle-orm";
import { db, schema } from "./db";
import { TERMS_VERSION } from "./legal";
import { track } from "./posthog";

const { users, orgs } = schema;

export type AcceptanceVia = "signup" | "claim" | "magic_code" | "checkout" | "dashboard";

/**
 * Record that the current Terms + Privacy Policy (TERMS_VERSION) were accepted, on the user and/or org.
 * Keeps the first acceptance of each version: a row already on this version isn't touched. Returns which
 * rows were updated (for tests and analytics).
 */
export async function recordTermsAcceptance(
  who: { userId?: string | null; orgId?: string | null },
  via: AcceptanceVia,
  now = new Date(),
): Promise<{ user: boolean; org: boolean }> {
  const set = { termsVersion: TERMS_VERSION, termsAcceptedAt: now };
  let user = false;
  let org = false;
  if (who.userId) {
    const rows = await db().update(users).set(set)
      .where(and(eq(users.id, who.userId), or(isNull(users.termsVersion), ne(users.termsVersion, TERMS_VERSION))))
      .returning({ id: users.id });
    user = rows.length > 0;
  }
  if (who.orgId) {
    const rows = await db().update(orgs).set(set)
      .where(and(eq(orgs.id, who.orgId), or(isNull(orgs.termsVersion), ne(orgs.termsVersion, TERMS_VERSION))))
      .returning({ id: orgs.id });
    org = rows.length > 0;
  }
  if (user || org) track("terms_accepted", { userId: who.userId, orgId: who.orgId }, { version: TERMS_VERSION, via });
  return { user, org };
}

/** Whether this user has accepted the current version (the dashboard asks again when it changes). */
export function hasAcceptedCurrentTerms(row: { termsVersion: string | null }): boolean {
  return row.termsVersion === TERMS_VERSION;
}
