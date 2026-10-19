import { sql } from "drizzle-orm";
import {
  bigint, boolean, doublePrecision, index, jsonb, pgTable, primaryKey, real, text, timestamp, uniqueIndex,
} from "drizzle-orm/pg-core";

const id = (prefix: string) =>
  text("id").primaryKey().$defaultFn(() => `${prefix}_${crypto.randomUUID().replace(/-/g, "").slice(0, 20)}`);
const createdAt = () => timestamp("created_at", { withTimezone: true }).notNull().defaultNow();

export const orgs = pgTable("orgs", {
  id: id("org"),
  workosOrgId: text("workos_org_id").unique(),
  name: text("name").notNull(),
  // Anonymous trials (POST /api/v1/auth/trial): no user until someone claims it at /claim?token=…
  isTrial: boolean("is_trial").notNull().default(false),
  claimTokenHash: text("claim_token_hash").unique(),
  trialExpiresAt: timestamp("trial_expires_at", { withTimezone: true }),
  claimedAt: timestamp("claimed_at", { withTimezone: true }),
  createdAt: createdAt(),
});

/** One row per anonymous trial, for per-IP rate limiting (IP stored hashed). */
export const trialRequests = pgTable("trial_requests", {
  id: id("trq"),
  ipHash: text("ip_hash").notNull(),
  orgId: text("org_id").references(() => orgs.id, { onDelete: "cascade" }),
  createdAt: createdAt(),
}, (t) => [index("trial_requests_ip_created_idx").on(t.ipHash, t.createdAt)]);

export const users = pgTable("users", {
  id: id("usr"),
  workosUserId: text("workos_user_id").notNull().unique(),
  email: text("email").notNull(),
  name: text("name"),
  createdAt: createdAt(),
});

export const memberships = pgTable("memberships", {
  orgId: text("org_id").notNull().references(() => orgs.id, { onDelete: "cascade" }),
  userId: text("user_id").notNull().references(() => users.id, { onDelete: "cascade" }),
  role: text("role").notNull().default("owner"),
  createdAt: createdAt(),
}, (t) => [primaryKey({ columns: [t.orgId, t.userId] })]);

export const apiKeys = pgTable("api_keys", {
  id: id("key"),
  orgId: text("org_id").notNull().references(() => orgs.id, { onDelete: "cascade" }),
  userId: text("user_id").references(() => users.id, { onDelete: "set null" }),
  name: text("name").notNull(),
  prefix: text("prefix").notNull(),
  hash: text("hash").notNull().unique(),
  lastUsedAt: timestamp("last_used_at", { withTimezone: true }),
  revokedAt: timestamp("revoked_at", { withTimezone: true }),
  createdAt: createdAt(),
}, (t) => [index("api_keys_org_idx").on(t.orgId)]);

export const deviceCodes = pgTable("device_codes", {
  deviceCodeHash: text("device_code_hash").primaryKey(),
  userCode: text("user_code").notNull().unique(),
  status: text("status", { enum: ["pending", "approved", "denied", "consumed"] }).notNull().default("pending"),
  orgId: text("org_id").references(() => orgs.id, { onDelete: "cascade" }),
  userId: text("user_id").references(() => users.id, { onDelete: "cascade" }),
  expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
  createdAt: createdAt(),
});

export const assets = pgTable("assets", {
  orgId: text("org_id").notNull().references(() => orgs.id, { onDelete: "cascade" }),
  sha256: text("sha256").notNull(),
  filename: text("filename").notNull(),
  size: bigint("size", { mode: "number" }).notNull(),
  contentType: text("content_type").notNull(),
  createdAt: createdAt(),
}, (t) => [primaryKey({ columns: [t.orgId, t.sha256] })]);

export type JobOutput = {
  name: string;
  key: string;
  contentType: string;
  bytes: number;
  /** Render pass for non-beauty outputs (depth, normal, …). */
  pass?: string;
  /** Gallery metadata written by the worker (absent on outputs from before it existed). */
  width?: number;
  height?: number;
  thumbKey?: string;
  placeholder?: string;
};

export const jobs = pgTable("jobs", {
  id: id("job"),
  orgId: text("org_id").notNull().references(() => orgs.id, { onDelete: "cascade" }),
  createdBy: text("created_by"),
  kind: text("kind", { enum: ["scene", "hyperframes"] }).notNull().default("scene"),
  status: text("status", {
    enum: ["queued", "planning", "rendering", "encoding", "succeeded", "failed", "canceled"],
  }).notNull().default("queued"),
  priority: text("priority", { enum: ["economy", "standard", "rush"] }).notNull().default("standard"),
  package: jsonb("package").notNull(),
  name: text("name"),
  engine: text("engine"),
  hardware: text("hardware"),
  progress: real("progress").notNull().default(0),
  warnings: jsonb("warnings").$type<string[]>().notNull().default(sql`'[]'::jsonb`),
  error: text("error"),
  outputs: jsonb("outputs").$type<JobOutput[]>().notNull().default(sql`'[]'::jsonb`),
  secondsCpu: doublePrecision("seconds_cpu").notNull().default(0),
  secondsGpuL4: doublePrecision("seconds_gpu_l4").notNull().default(0),
  secondsGpuL40s: doublePrecision("seconds_gpu_l40s").notNull().default(0),
  costUsd: doublePrecision("cost_usd").notNull().default(0),
  cached: boolean("cached").notNull().default(false),
  cacheKey: text("cache_key"),
  modalCallId: text("modal_call_id"),
  idempotencyKey: text("idempotency_key"),
  estimate: jsonb("estimate"),
  createdAt: createdAt(),
  startedAt: timestamp("started_at", { withTimezone: true }),
  finishedAt: timestamp("finished_at", { withTimezone: true }),
}, (t) => [
  index("jobs_org_created_idx").on(t.orgId, t.createdAt),
  uniqueIndex("jobs_org_idem_idx").on(t.orgId, t.idempotencyKey),
  index("jobs_cache_key_idx").on(t.cacheKey),
]);

export const usageEvents = pgTable("usage_events", {
  jobId: text("job_id").notNull().references(() => jobs.id, { onDelete: "cascade" }),
  chunk: text("chunk").notNull().$type<string>(), // frame-chunk index, or "orchestrate" | "encode" | "hyperframes"
  orgId: text("org_id").notNull().references(() => orgs.id, { onDelete: "cascade" }),
  hardware: text("hardware").notNull(),
  seconds: doublePrecision("seconds").notNull(),
  freeSeconds: doublePrecision("free_seconds").notNull().default(0),
  billedSeconds: doublePrecision("billed_seconds").notNull().default(0),
  costUsd: doublePrecision("cost_usd").notNull().default(0),
  reportedToStripe: boolean("reported_to_stripe").notNull().default(false),
  createdAt: createdAt(),
}, (t) => [primaryKey({ columns: [t.jobId, t.chunk] }), index("usage_org_created_idx").on(t.orgId, t.createdAt)]);

export const billing = pgTable("billing", {
  orgId: text("org_id").primaryKey().references(() => orgs.id, { onDelete: "cascade" }),
  stripeCustomerId: text("stripe_customer_id").unique(),
  hasPaymentMethod: boolean("has_payment_method").notNull().default(false),
  freeSecondsRemaining: doublePrecision("free_seconds_remaining").notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});
