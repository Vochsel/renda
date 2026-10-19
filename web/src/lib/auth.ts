import { and, eq, isNull } from "drizzle-orm";
import { withAuth } from "@workos-inc/authkit-nextjs";
import { db, schema } from "./db";
import { hashApiKey, looksLikeApiKey } from "./apikeys";
import { ApiError } from "./errors";
import { ensureAccount, type Account } from "./accounts";
import { isExpiredTrial } from "./trials";

const { apiKeys, orgs, users } = schema;

export type ApiAuth = { org: typeof orgs.$inferSelect; user: typeof users.$inferSelect | null; keyId: string };

export function bearer(req: Request): string | null {
  const h = req.headers.get("authorization");
  const m = h?.match(/^Bearer\s+(.+)$/i);
  return m ? m[1].trim() : null;
}

/** Resolve `Authorization: Bearer rnd_live_…` to an org. Throws 401 with onboarding hint. */
export async function authenticateKey(key: string | null): Promise<ApiAuth> {
  if (!key || !looksLikeApiKey(key)) throw new ApiError("unauthorized");
  const rows = await db()
    .select({ key: apiKeys, org: orgs, user: users })
    .from(apiKeys)
    .innerJoin(orgs, eq(orgs.id, apiKeys.orgId))
    .leftJoin(users, eq(users.id, apiKeys.userId))
    .where(and(eq(apiKeys.hash, hashApiKey(key)), isNull(apiKeys.revokedAt)))
    .limit(1);
  const row = rows[0];
  if (!row) throw new ApiError("unauthorized");
  if (isExpiredTrial(row.org)) throw new ApiError("trial_expired");
  void db().update(apiKeys).set({ lastUsedAt: new Date() }).where(eq(apiKeys.id, row.key.id)).catch(() => {});
  return { org: row.org, user: row.user, keyId: row.key.id };
}

export function authenticate(req: Request): Promise<ApiAuth> {
  return authenticateKey(bearer(req));
}

/** Dashboard pages/actions: signed-in WorkOS session → local user + org (created on first visit). */
export async function requireAccount(): Promise<Account> {
  const { user, organizationId } = await withAuth({ ensureSignedIn: true });
  return ensureAccount(user, organizationId);
}
