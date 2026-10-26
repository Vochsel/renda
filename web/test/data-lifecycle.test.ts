/**
 * Deletion, retention and terms acceptance against the real routes and Drizzle queries on PGlite built from
 * the real migrations. R2, Stripe, WorkOS and the Modal calls are faked.
 */
import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { PGlite } from "@electric-sql/pglite";
import { drizzle } from "drizzle-orm/pglite";
import { eq } from "drizzle-orm";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";
import type { JobOutput } from "@/lib/db/schema";

process.env.RENDA_WORKER_SECRET = "worker-secret";
process.env.RENDA_WORKER_TOKEN = "worker-token";
process.env.MODAL_SUBMIT_URL = "https://modal.test/submit";
process.env.APP_URL = "https://renda.test";
process.env.R2_OUTPUT_TTL_SECONDS = "3600";
process.env.FREE_SECONDS = "1800";
process.env.RATE_LIMIT_DISABLED = "1";
process.env.WORKOS_CLIENT_ID = "client_test";

const pg = new PGlite();
const testDb = drizzle(pg, { schema });
vi.mock("@/lib/db", () => ({ db: () => testDb, schema }));

// In-memory R2 (with prefix listing/deletion).
const objects = new Map<string, Buffer>();
vi.mock("@/lib/r2", () => ({
  assetKey: (sha: string) => `assets/${sha}`,
  stagingKey: (org: string, sha: string) => `staging/${org}/${sha}`,
  outputPrefix: (id: string) => `outputs/${id}/`,
  workPrefix: (id: string) => `work/${id}/`,
  objectExists: async (k: string) => objects.has(k),
  presignPut: async (k: string) => `https://r2.test/put/${k}`,
  presignGet: async (k: string) => `https://r2.test/get/${k}`,
  sha256OfObject: async (k: string) => createHash("sha256").update(objects.get(k)!).digest("hex"),
  copyObject: async (from: string, to: string) => void objects.set(to, objects.get(from)!),
  deleteObject: async (k: string) => void objects.delete(k),
  deletePrefix: async (prefix: string, keep: ReadonlySet<string> = new Set()) => {
    let n = 0;
    for (const k of [...objects.keys()]) if (k.startsWith(prefix) && !keep.has(k)) { objects.delete(k); n++; }
    return n;
  },
}));

const stripeCalls: { cancel: [string, unknown][] } = { cancel: [] };
vi.mock("@/lib/stripe", () => ({
  stripeConfigured: () => true,
  stripe: () => ({
    subscriptions: {
      list: async () => ({ data: [{ id: "sub_live", status: "active" }, { id: "sub_old", status: "canceled" }] }),
      cancel: async (id: string, params: unknown) => { stripeCalls.cancel.push([id, params]); return { id }; },
    },
  }),
}));

const workosCalls = { deleteUser: [] as string[], deleteOrganization: [] as string[] };
const magicUser = { id: "wos_user_magic", email: "magic@example.com", firstName: "Mag", lastName: null };
vi.mock("@/lib/workos", () => ({
  workos: () => ({
    userManagement: {
      deleteUser: async (id: string) => void workosCalls.deleteUser.push(id),
      createOrganizationMembership: async () => ({}),
      authenticateWithMagicAuth: async () => ({ user: magicUser, organizationId: undefined }),
    },
    organizations: {
      createOrganization: async () => ({ id: `wos_org_${Math.random().toString(36).slice(2, 10)}` }),
      deleteOrganization: async (id: string) => void workosCalls.deleteOrganization.push(id),
    },
  }),
}));

let session: { user: { id: string; email: string }; organizationId?: string } | null = null;
vi.mock("@workos-inc/authkit-nextjs", () => ({
  withAuth: vi.fn(async () => session),
  signOut: vi.fn(async () => { throw new Error("NEXT_REDIRECT signOut"); }),
}));
class Redirect extends Error {}
vi.mock("next/navigation", () => ({ redirect: (to: string) => { throw new Redirect(to); } }));

vi.stubGlobal("fetch", vi.fn(async (url: string) => {
  if (String(url).startsWith("https://modal.test")) return new Response(JSON.stringify({ callId: "fc-1" }), { status: 200 });
  throw new Error(`unexpected fetch ${url}`);
}));

const { generateApiKey } = await import("@/lib/apikeys");
const jobRoute = await import("@/app/api/v1/jobs/[id]/route");
const uploadRoute = await import("@/app/api/v1/uploads/[sha256]/route");
const meRoute = await import("@/app/api/v1/me/route");
const verifyRoute = await import("@/app/api/v1/auth/magic/verify/route");
const trialRoute = await import("@/app/api/v1/auth/trial/route");
const { deleteJobOutputs, expireOldOutputs, outputKeys } = await import("@/lib/job-outputs");
const { deleteAsset, expireUnusedAssets, ensureVerifiedAssets } = await import("@/lib/assets");
const { deleteAccount, purgeDeletedOrg, LATE_UPLOAD_GRACE_MS } = await import("@/lib/account-deletion");
const { runRetention } = await import("@/lib/retention");
const { recordTermsAcceptance, hasAcceptedCurrentTerms } = await import("@/lib/terms");
const { ensureAccount } = await import("@/lib/accounts");
const { TERMS_VERSION } = await import("@/lib/legal");
const { claimAction } = await import("@/app/claim/actions");
const { createTrial } = await import("@/lib/trials");

beforeAll(async () => {
  const dir = join(__dirname, "..", "drizzle");
  for (const f of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) {
    for (const stmt of readFileSync(join(dir, f), "utf8").split("--> statement-breakpoint")) {
      if (stmt.trim()) await pg.exec(stmt);
    }
  }
});

beforeEach(() => {
  objects.clear();
  stripeCalls.cancel.length = 0;
  workosCalls.deleteUser.length = 0;
  workosCalls.deleteOrganization.length = 0;
});

let seq = 0;
async function newOrg(name = "org") {
  const [org] = await testDb.insert(schema.orgs).values({ name: `${name} ${++seq}` }).returning();
  await testDb.insert(schema.billing).values({ orgId: org.id, freeSecondsRemaining: 1800 });
  const k = generateApiKey();
  await testDb.insert(schema.apiKeys).values({ orgId: org.id, name: "k", prefix: k.prefix, hash: k.hash });
  return { org, key: k.key };
}

const DAY = 86_400_000;
const out = (jobId: string, name: string): JobOutput => ({
  name, key: `outputs/${jobId}/${name}`, contentType: "image/png", bytes: 3, thumbKey: `outputs/${jobId}/${name}.thumb.webp`,
});
async function finishedJob(orgId: string, opts: { id?: string; outputs?: JobOutput[]; finishedAt?: Date; status?: "succeeded" | "rendering"; cacheKey?: string } = {}) {
  const [j] = await testDb.insert(schema.jobs).values({
    orgId, package: { version: 1 }, status: opts.status ?? "succeeded", cacheKey: opts.cacheKey ?? null,
    finishedAt: opts.status === "rendering" ? null : opts.finishedAt ?? new Date(),
  }).returning();
  const outputs = opts.outputs ?? [out(j.id, "beauty.png")];
  for (const k of outputKeys(outputs)) objects.set(k, Buffer.from("png"));
  await testDb.update(schema.jobs).set({ outputs }).where(eq(schema.jobs.id, j.id));
  return { ...j, outputs };
}
const jobRow = async (id: string) => (await testDb.select().from(schema.jobs).where(eq(schema.jobs.id, id)))[0];
const req = (url: string, key: string, init: RequestInit = {}) =>
  new Request(`https://renda.test${url}`, { ...init, headers: { authorization: `Bearer ${key}`, "content-type": "application/json" } });
const params = <T extends object>(p: T) => ({ params: Promise.resolve(p) }) as never;

describe("DELETE /api/v1/jobs/:id (outputs)", () => {
  it("deletes the job's whole prefix, clears outputs + cache key, and is idempotent", async () => {
    const { org, key } = await newOrg();
    const j = await finishedJob(org.id, { cacheKey: "ck1" });
    objects.set(`outputs/${j.id}/extra-frame.exr`, Buffer.from("x")); // a file the outputs list doesn't mention
    objects.set(`work/${j.id}/frames/0001.png`, Buffer.from("x"));

    const res = await jobRoute.DELETE(req(`/api/v1/jobs/${j.id}`, key, { method: "DELETE" }), params({ id: j.id }));
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ id: j.id, outputsDeleted: true, objectsDeleted: 3, objectsKept: 0 });
    expect([...objects.keys()].filter((k) => k.includes(j.id))).toEqual([]);
    const row = await jobRow(j.id);
    expect(row.outputs).toEqual([]);
    expect(row.cacheKey).toBeNull();
    expect(row.outputsDeletedAt).toBeInstanceOf(Date);

    const again = await jobRoute.DELETE(req(`/api/v1/jobs/${j.id}`, key, { method: "DELETE" }), params({ id: j.id }));
    expect(again.status).toBe(200);
  });

  it("409 while the job is running; 404 for another account's job", async () => {
    const a = await newOrg("a");
    const b = await newOrg("b");
    const running = await finishedJob(a.org.id, { status: "rendering" });
    const r1 = await jobRoute.DELETE(req(`/api/v1/jobs/${running.id}`, a.key, { method: "DELETE" }), params({ id: running.id }));
    expect(r1.status).toBe(409);
    expect((await r1.json()).error.hint).toMatch(/cancel/);
    expect(objects.has(running.outputs[0].key)).toBe(true);

    const done = await finishedJob(a.org.id);
    const r2 = await jobRoute.DELETE(req(`/api/v1/jobs/${done.id}`, b.key, { method: "DELETE" }), params({ id: done.id }));
    expect(r2.status).toBe(404);
    expect(objects.has(done.outputs[0].key)).toBe(true);
  });

  it("keeps files a cached copy still lists, and deletes them with the last job that lists them", async () => {
    const { org } = await newOrg();
    const original = await finishedJob(org.id, { cacheKey: "same" });
    const copy = await finishedJob(org.id, { cacheKey: "same", outputs: original.outputs }); // server-side cache hit
    const keys = outputKeys(original.outputs);

    const first = await deleteJobOutputs(org.id, original.id);
    expect(first).toMatchObject({ objectsDeleted: 0, objectsKept: 2 });
    for (const k of keys) expect(objects.has(k)).toBe(true);
    expect((await jobRow(original.id)).outputsDeletedAt).toBeNull(); // prefix still in use → left to retention
    expect((await jobRow(copy.id)).outputs).toHaveLength(1);

    const second = await deleteJobOutputs(org.id, copy.id);
    expect(second.objectsDeleted).toBe(2);
    for (const k of keys) expect(objects.has(k)).toBe(false);
  });
});

describe("retention: outputs", () => {
  it("purges jobs finished > 30 days ago, clears cache copies that list them, batch-limited and idempotent", async () => {
    const { org } = await newOrg();
    const now = new Date();
    const old1 = await finishedJob(org.id, { finishedAt: new Date(now.getTime() - 40 * DAY), cacheKey: "old" });
    const old2 = await finishedJob(org.id, { finishedAt: new Date(now.getTime() - 35 * DAY) });
    const copy = await finishedJob(org.id, { finishedAt: new Date(now.getTime() - DAY), outputs: old1.outputs, cacheKey: "old" });
    const fresh = await finishedJob(org.id, { finishedAt: new Date(now.getTime() - 29 * DAY) });

    // Limit 1: the oldest first.
    const r1 = await expireOldOutputs(30, 1, now);
    expect(r1.jobs).toBe(1);
    expect(objects.has(old1.outputs[0].key)).toBe(false);
    expect((await jobRow(copy.id)).outputs).toEqual([]);
    expect((await jobRow(copy.id)).cacheKey).toBeNull();
    expect(objects.has(old2.outputs[0].key)).toBe(true);

    const r2 = await expireOldOutputs(30, 100, now);
    expect(r2.jobs).toBeGreaterThanOrEqual(1);
    expect(objects.has(old2.outputs[0].key)).toBe(false);
    expect(objects.has(fresh.outputs[0].key)).toBe(true);
    expect((await jobRow(fresh.id)).outputs).toHaveLength(1);

    const r3 = await expireOldOutputs(30, 100, now);
    expect(r3.jobs).toBe(0); // idempotent: nothing left to do
  });
});

const sha = (b: Buffer) => createHash("sha256").update(b).digest("hex");
async function verifiedUpload(orgId: string, bytes: Buffer) {
  const s = sha(bytes);
  await testDb.insert(schema.assets).values({ orgId, sha256: s, filename: "m.glb", size: bytes.length, contentType: "model/gltf-binary" })
    .onConflictDoNothing();
  objects.set(`staging/${orgId}/${s}`, bytes);
  await ensureVerifiedAssets(orgId, [s]); // first use: hash-checked and promoted to assets/<sha>
  return s;
}
const assetRow = async (orgId: string, s: string) =>
  (await testDb.select().from(schema.assets).where(eq(schema.assets.orgId, orgId))).find((a) => a.sha256 === s);

describe("DELETE /api/v1/uploads/:sha256 (refcount)", () => {
  it("removes the registration; deletes the shared object only when no other account has the file", async () => {
    const a = await newOrg("a");
    const b = await newOrg("b");
    const bytes = Buffer.from(`shared model ${Math.random()}`);
    const s = await verifiedUpload(a.org.id, bytes);
    await verifiedUpload(b.org.id, bytes);
    expect(objects.has(`assets/${s}`)).toBe(true);

    const r1 = await uploadRoute.DELETE(req(`/api/v1/uploads/${s}`, a.key, { method: "DELETE" }), params({ sha256: s }));
    expect(r1.status).toBe(200);
    expect(await r1.json()).toMatchObject({ assetUri: `asset://${s}`, deleted: true, storageDeleted: false });
    expect(await assetRow(a.org.id, s)).toBeUndefined();
    expect(objects.has(`assets/${s}`)).toBe(true); // B still uses it

    const r2 = await uploadRoute.DELETE(req(`/api/v1/uploads/asset%3A%2F%2F${s}`, b.key, { method: "DELETE" }), params({ sha256: `asset%3A%2F%2F${s}` }));
    expect(await r2.json()).toMatchObject({ storageDeleted: true });
    expect(objects.has(`assets/${s}`)).toBe(false);

    const r3 = await uploadRoute.DELETE(req(`/api/v1/uploads/${s}`, b.key, { method: "DELETE" }), params({ sha256: s }));
    expect(r3.status).toBe(404);
    const bad = await uploadRoute.DELETE(req(`/api/v1/uploads/nope`, b.key, { method: "DELETE" }), params({ sha256: "nope" }));
    expect(bad.status).toBe(400);
  });

  it("409 while a running job uses the file", async () => {
    const { org } = await newOrg();
    const s = await verifiedUpload(org.id, Buffer.from(`in use ${Math.random()}`));
    await testDb.insert(schema.jobs).values({ orgId: org.id, status: "rendering", package: { assets: { m: { uri: `asset://${s}` } } } });
    await expect(deleteAsset(org.id, s)).rejects.toMatchObject({ code: "conflict" });
    expect(objects.has(`assets/${s}`)).toBe(true);
  });
});

describe("retention: uploads unused for 30 days", () => {
  it("tracks last use, expires per account, deletes the bytes when the last account's registration expires", async () => {
    const a = await newOrg("a");
    const b = await newOrg("b");
    const bytes = Buffer.from(`retained ${Math.random()}`);
    const s = await verifiedUpload(a.org.id, bytes);
    await verifiedUpload(b.org.id, bytes);
    const now = new Date();
    const old = new Date(now.getTime() - 31 * DAY);
    await testDb.update(schema.assets).set({ lastUsedAt: old }).where(eq(schema.assets.orgId, a.org.id));

    // A job using the asset counts as use.
    await testDb.update(schema.assets).set({ lastUsedAt: old }).where(eq(schema.assets.orgId, b.org.id));
    await ensureVerifiedAssets(b.org.id, [s]);
    expect((await assetRow(b.org.id, s))!.lastUsedAt.getTime()).toBeGreaterThan(now.getTime() - 60_000);

    const r1 = await expireUnusedAssets(30, 200, now);
    expect(r1.registrations).toBeGreaterThanOrEqual(1);
    expect(await assetRow(a.org.id, s)).toBeUndefined();
    expect(await assetRow(b.org.id, s)).toBeDefined();
    expect(objects.has(`assets/${s}`)).toBe(true);

    await testDb.update(schema.assets).set({ lastUsedAt: old }).where(eq(schema.assets.orgId, b.org.id));
    await expireUnusedAssets(30, 200, now);
    expect(objects.has(`assets/${s}`)).toBe(false);
    expect((await expireUnusedAssets(30, 200, now)).registrations).toBe(0);
  });

  it("runRetention runs every step (what the cron route calls)", async () => {
    const r = await runRetention();
    expect(r).toHaveProperty("assets");
    expect(r).toHaveProperty("outputs");
    expect(r).toHaveProperty("deletedAccounts");
  });
});

describe("account deletion", () => {
  async function populatedAccount() {
    const { org, key } = await newOrg("victim");
    await testDb.update(schema.orgs).set({ workosOrgId: `wos_org_victim_${seq}` }).where(eq(schema.orgs.id, org.id));
    const [user] = await testDb.insert(schema.users).values({ workosUserId: `wos_user_${seq}`, email: `v${seq}@example.com`, name: "Vic" }).returning();
    await testDb.insert(schema.memberships).values({ orgId: org.id, userId: user.id });
    await testDb.update(schema.billing).set({ stripeCustomerId: `cus_${seq}`, hasPaymentMethod: true }).where(eq(schema.billing.orgId, org.id));
    const done = await finishedJob(org.id);
    await testDb.insert(schema.usageEvents).values({ jobId: done.id, chunk: "0", orgId: org.id, hardware: "gpu-l4", seconds: 12, billedSeconds: 12, costUsd: 0.007 });
    const running = await finishedJob(org.id, { status: "rendering" });
    const other = await newOrg("other");
    const sharedBytes = Buffer.from(`shared ${Math.random()}`);
    const shared = await verifiedUpload(org.id, sharedBytes);
    await verifiedUpload(other.org.id, sharedBytes);
    const own = await verifiedUpload(org.id, Buffer.from(`own ${Math.random()}`));
    return { org, key, user, done, running, shared, own, other };
  }

  it("requires the org id as confirmation over the API", async () => {
    const { org, key } = await newOrg();
    const r = await meRoute.DELETE(req("/api/v1/me", key, { method: "DELETE", body: JSON.stringify({}) }), {} as never);
    expect(r.status).toBe(400);
    expect((await r.json()).error.hint).toMatch(/permanent/);
    expect((await testDb.select().from(schema.orgs).where(eq(schema.orgs.id, org.id)))[0].deletedAt).toBeNull();
  });

  it("revokes keys, cancels jobs, deletes content and people, cancels Stripe, keeps anonymised billing records", async () => {
    const acct = await populatedAccount();
    const res = await meRoute.DELETE(req("/api/v1/me", acct.key, { method: "DELETE", body: JSON.stringify({ confirm: acct.org.id }) }), {} as never);
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ deleted: true, orgId: acct.org.id, keysRevoked: 1, jobsCanceled: 1, subscriptionsCanceled: 1 });

    // Access is gone.
    const after = await meRoute.GET(req("/api/v1/me", acct.key), {} as never);
    expect(after.status).toBe(401);
    // Stripe: live subscription cancelled with a final invoice; the customer is kept.
    expect(stripeCalls.cancel).toEqual([["sub_live", { invoice_now: true, prorate: false }]]);
    // People.
    expect(await testDb.select().from(schema.users).where(eq(schema.users.id, acct.user.id))).toEqual([]);
    expect(workosCalls.deleteUser).toEqual([acct.user.workosUserId]);
    expect(workosCalls.deleteOrganization).toHaveLength(1);
    // Jobs: cancelled and scrubbed, files gone.
    const running = await jobRow(acct.running.id);
    expect(running.status).toBe("canceled");
    const done = await jobRow(acct.done.id);
    expect(done).toMatchObject({ package: {}, outputs: [], name: null, createdBy: null });
    expect(done.outputsDeletedAt).toBeInstanceOf(Date);
    expect([...objects.keys()].some((k) => k.startsWith(`outputs/${acct.done.id}/`))).toBe(false);
    // Uploads: unshared bytes deleted, shared bytes kept for the other account.
    expect(objects.has(`assets/${acct.own}`)).toBe(false);
    expect(objects.has(`assets/${acct.shared}`)).toBe(true);
    expect(await testDb.select().from(schema.assets).where(eq(schema.assets.orgId, acct.org.id))).toEqual([]);
    // Kept: tombstone org, billing row, usage events.
    const [org] = await testDb.select().from(schema.orgs).where(eq(schema.orgs.id, acct.org.id));
    expect(org).toMatchObject({ name: "Deleted account", workosOrgId: null });
    expect(org.deletedAt).toBeInstanceOf(Date);
    expect(org.purgedAt).toBeNull(); // waits out the late-upload grace period
    expect(await testDb.select().from(schema.usageEvents).where(eq(schema.usageEvents.orgId, acct.org.id))).toHaveLength(1);
    expect((await testDb.select().from(schema.billing).where(eq(schema.billing.orgId, acct.org.id)))[0].stripeCustomerId).toMatch(/^cus_/);

    // Late upload from the cancelled container, then the cron pass after the grace period.
    objects.set(`outputs/${acct.running.id}/late.png`, Buffer.from("late"));
    const early = await purgeDeletedOrg(acct.org.id);
    expect(early.done).toBe(false);
    const later = await purgeDeletedOrg(acct.org.id, undefined, new Date(Date.now() + LATE_UPLOAD_GRACE_MS + 1000));
    expect(later.done).toBe(true);
    expect(objects.has(`outputs/${acct.running.id}/late.png`)).toBe(false);

    // Idempotent.
    const again = await deleteAccount(acct.org.id);
    expect(again.alreadyDeleted).toBe(true);
  });

  it("usage_events can't be cascade-deleted with their org or job any more", async () => {
    const acct = await populatedAccount();
    await expect(testDb.delete(schema.jobs).where(eq(schema.jobs.id, acct.done.id))).rejects.toThrow();
    await expect(testDb.delete(schema.orgs).where(eq(schema.orgs.id, acct.org.id))).rejects.toThrow();
  });
});

describe("terms acceptance", () => {
  it("records version + timestamp once per version on user and org", async () => {
    const { org } = await newOrg();
    const [user] = await testDb.insert(schema.users).values({ workosUserId: `wos_t_${seq}`, email: "t@example.com" }).returning();
    expect(hasAcceptedCurrentTerms(user)).toBe(false);
    const t1 = new Date("2026-10-26T00:00:00Z");
    expect(await recordTermsAcceptance({ userId: user.id, orgId: org.id }, "dashboard", t1)).toEqual({ user: true, org: true });
    expect(await recordTermsAcceptance({ userId: user.id, orgId: org.id }, "dashboard", new Date())).toEqual({ user: false, org: false });
    const [u] = await testDb.select().from(schema.users).where(eq(schema.users.id, user.id));
    const [o] = await testDb.select().from(schema.orgs).where(eq(schema.orgs.id, org.id));
    expect(u).toMatchObject({ termsVersion: TERMS_VERSION, termsAcceptedAt: t1 });
    expect(o).toMatchObject({ termsVersion: TERMS_VERSION, termsAcceptedAt: t1 });
    expect(hasAcceptedCurrentTerms(u)).toBe(true);
  });

  it("WorkOS sign-up / first dashboard visit (ensureAccount) records acceptance on the new user and org", async () => {
    const acct = await ensureAccount({ id: `wos_new_${Math.random()}`, email: "new@example.com" });
    expect(acct.user.termsVersion).toBe(TERMS_VERSION);
    const [u] = await testDb.select().from(schema.users).where(eq(schema.users.id, acct.user.id));
    const [o] = await testDb.select().from(schema.orgs).where(eq(schema.orgs.id, acct.org.id));
    expect(u.termsAcceptedAt).toBeInstanceOf(Date);
    expect(o.termsVersion).toBe(TERMS_VERSION);
  });

  it("magic-code signup records acceptance and returns the terms links for the agent to relay", async () => {
    const res = await verifyRoute.POST(new Request("https://renda.test/api/v1/auth/magic/verify", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email: magicUser.email, code: "123456" }),
    }), {} as never);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.terms).toMatchObject({ version: TERMS_VERSION, termsUrl: "https://renda.test/legal/terms", privacyUrl: "https://renda.test/legal/privacy" });
    expect(body.next).toMatch(/Terms/);
    const [u] = await testDb.select().from(schema.users).where(eq(schema.users.workosUserId, magicUser.id));
    expect(u.termsVersion).toBe(TERMS_VERSION);
  });

  it("trial responses carry the terms links (no new required fields)", async () => {
    process.env.TRIAL_IP_SALT = "salt";
    process.env.TRIAL_MAX_PER_NETWORK_PER_DAY = "100";
    const res = await trialRoute.POST(new Request("https://renda.test/api/v1/auth/trial", { method: "POST", headers: { "x-forwarded-for": "203.0.113.9" } }), {} as never);
    expect(res.status).toBe(201);
    expect((await res.json()).terms.termsUrl).toBe("https://renda.test/legal/terms");
  });

  it("claiming a trial records acceptance on the claiming user and the claimed org", async () => {
    const trial = await createTrial("198.51.100.7");
    const token = new URL(trial.claimUrl).searchParams.get("token")!;
    session = { user: { id: `wos_claimer_${Math.random()}`, email: "claimer@example.com" } };
    const fd = new FormData();
    fd.set("token", token);
    await expect(claimAction(fd)).rejects.toThrow(Redirect);
    const [o] = await testDb.select().from(schema.orgs).where(eq(schema.orgs.id, trial.orgId));
    expect(o.claimedAt).toBeInstanceOf(Date);
    expect(o.termsVersion).toBe(TERMS_VERSION);
    const [u] = await testDb.select().from(schema.users).where(eq(schema.users.workosUserId, session.user.id));
    expect(u.termsVersion).toBe(TERMS_VERSION);
  });
});
