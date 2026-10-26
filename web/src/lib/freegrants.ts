import { createHmac } from "node:crypto";
import { db, schema } from "./db";
import { env, envOptional } from "./env";

/**
 * The free tier is granted once per person, not once per workspace: claiming several trials or signing up with
 * `you+1@…` doesn't stack free minutes. Emails are compared normalised (case, plus-tags, Gmail dots) and stored
 * only as a keyed hash.
 */
export function normalizeEmail(email: string): string {
  const [rawLocal, rawDomain = ""] = email.trim().toLowerCase().split("@");
  let domain = rawDomain;
  let local = rawLocal.split("+")[0];
  if (domain === "googlemail.com") domain = "gmail.com";
  if (domain === "gmail.com") local = local.replace(/\./g, "");
  return `${local}@${domain}`;
}

function emailHash(email: string): string {
  const key = envOptional("TRIAL_IP_SALT") ?? `free-grant:${env("RENDA_WORKER_SECRET")}`;
  return createHmac("sha256", key).update(normalizeEmail(email)).digest("hex");
}

/** True the first time this person is granted the free tier (atomic); false if they already had it. */
export async function claimFreeGrant(email: string, orgId: string): Promise<boolean> {
  const rows = await db().insert(schema.freeGrants).values({ emailHash: emailHash(email), orgId })
    .onConflictDoNothing().returning({ emailHash: schema.freeGrants.emailHash });
  return rows.length > 0;
}
