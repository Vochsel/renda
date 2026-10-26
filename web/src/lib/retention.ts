import { envOptional } from "./env";
import { RETENTION } from "./legal";
import { expireUnusedAssets } from "./assets";
import { expireOldOutputs } from "./job-outputs";
import { purgeDeletedOrgs } from "./account-deletion";

/**
 * Data retention, run from the cron route (/api/internal/sweep, every 5 minutes). Each step is
 * batch-limited and idempotent, so a backlog drains over several runs:
 * - uploads unused for 30 days: the org's registration and staging upload, then the shared assets/<sha>
 *   once no account uses that file (lib/assets.ts);
 * - outputs of jobs finished more than 30 days ago (lib/job-outputs.ts). The R2 lifecycle rule in
 *   web/README.md does the same by object age; this is the backstop, and it also clears the job rows;
 * - storage of deleted accounts (lib/account-deletion.ts).
 * RETENTION_ASSET_DAYS / RETENTION_OUTPUT_DAYS override the policy defaults (staging/testing only: the
 * Privacy Policy states the defaults).
 */
export async function runRetention(now = new Date()) {
  const days = (name: string, fallback: number) => {
    const v = Number(envOptional(name));
    return Number.isFinite(v) && v > 0 ? v : fallback;
  };
  const assets = await expireUnusedAssets(days("RETENTION_ASSET_DAYS", RETENTION.assetDays), 200, now);
  const outputs = await expireOldOutputs(days("RETENTION_OUTPUT_DAYS", RETENTION.outputDays), 100, now);
  const deletedAccounts = await purgeDeletedOrgs(5, now);
  return { assets, outputs, deletedAccounts: Object.keys(deletedAccounts).length };
}
