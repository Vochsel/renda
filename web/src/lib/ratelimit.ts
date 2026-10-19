import { createHmac } from "node:crypto";
import { lt, sql } from "drizzle-orm";
import { db, schema } from "./db";
import { env, envOptional } from "./env";
import { ApiError } from "./errors";

/**
 * Fixed-window rate limits stored in Postgres, so they hold across Vercel instances without another
 * service. One upsert per checked request; only write and auth endpoints are limited.
 *
 * Limits are `count/seconds`. Defaults here are deliberately generic; production overrides them with
 * `RATE_LIMIT_<BUCKET>` env vars (e.g. `RATE_LIMIT_MAGIC_START_IP=5/600`). `RATE_LIMIT_DISABLED=1`
 * turns limiting off (local dev).
 */
const DEFAULTS = {
  magic_start_ip: "10/600",
  magic_start_email: "5/600",
  magic_verify_ip: "30/600",
  magic_verify_email: "10/600",
  device_start_ip: "30/600",
  device_token_ip: "300/60",
  device_approve_user: "20/600",
  api_write_key: "300/60",
} as const;

export type Bucket = keyof typeof DEFAULTS;

export function limitFor(bucket: Bucket): { count: number; seconds: number } {
  const raw = envOptional(`RATE_LIMIT_${bucket.toUpperCase()}`) ?? DEFAULTS[bucket];
  const m = raw.match(/^(\d+)\/(\d+)$/);
  if (!m) throw new Error(`RATE_LIMIT_${bucket.toUpperCase()} must look like "count/seconds", got ${raw}`);
  return { count: Number(m[1]), seconds: Number(m[2]) };
}

/** Keys are hashed so the table never holds raw IPs, emails or API keys. */
function hashKey(bucket: string, key: string): string {
  const salt = envOptional("TRIAL_IP_SALT") ?? `ratelimit:${env("RENDA_WORKER_SECRET")}`;
  return createHmac("sha256", salt).update(`${bucket}\0${key}`).digest("hex");
}

/** Count one request against `bucket` for `key`; throws 429 (with retryAfter) once over the limit. */
export async function rateLimit(bucket: Bucket, key: string, now = new Date()): Promise<void> {
  if (envOptional("RATE_LIMIT_DISABLED") === "1") return;
  const { count, seconds } = limitFor(bucket);
  const windowMs = seconds * 1000;
  const start = new Date(Math.floor(now.getTime() / windowMs) * windowMs);
  const { rateLimits } = schema;
  const [row] = await db()
    .insert(rateLimits)
    .values({ bucket, key: hashKey(bucket, key), windowStart: start, count: 1 })
    .onConflictDoUpdate({
      target: [rateLimits.bucket, rateLimits.key, rateLimits.windowStart],
      set: { count: sql`${rateLimits.count} + 1` },
    })
    .returning({ count: rateLimits.count });
  // Old windows are useless; trim occasionally rather than on every request.
  if (Math.random() < 0.01) {
    void db().delete(rateLimits).where(lt(rateLimits.windowStart, new Date(now.getTime() - 86_400_000))).catch(() => {});
  }
  if (row && row.count > count) {
    const retryAfter = Math.max(1, Math.ceil((start.getTime() + windowMs - now.getTime()) / 1000));
    throw new ApiError("rate_limited", undefined, { retryAfter }, `Back off and retry after ${retryAfter}s.`);
  }
}
