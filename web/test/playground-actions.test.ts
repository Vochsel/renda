/**
 * Playground server actions against the real libraries on PGlite (real migrations): session auth,
 * org scoping, and the same upload-verification policy as /api/v1. R2, WorkOS and Modal are faked.
 */
import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { PGlite } from "@electric-sql/pglite";
import { drizzle } from "drizzle-orm/pglite";
import { beforeAll, describe, expect, it, vi } from "vitest";
import * as schema from "@/lib/db/schema";

process.env.RENDA_WORKER_SECRET = "worker-secret";
process.env.RENDA_WORKER_TOKEN = "worker-token";
process.env.MODAL_SUBMIT_URL = "https://modal.test/submit";
process.env.APP_URL = "https://renda.test";
process.env.R2_OUTPUT_TTL_SECONDS = "3600";
process.env.RATE_LIMIT_DISABLED = "1";
process.env.MAX_ACTIVE_JOBS_FREE = "100"; // fake worker: submitted jobs stay queued

const pg = new PGlite();
const testDb = drizzle(pg, { schema });
vi.mock("@/lib/db", () => ({ db: () => testDb, schema }));

const objects = new Map<string, Buffer>();
vi.mock("@/lib/r2", () => ({
  assetKey: (sha: string) => `assets/${sha}`,
  stagingKey: (org: string, sha: string) => `staging/${org}/${sha}`,
  outputPrefix: (id: string) => `outputs/${id}/`,
  objectExists: async (k: string) => objects.has(k),
  presignPut: async (k: string) => `https://r2.test/put/${k}`,
  presignGet: async (k: string) => `https://r2.test/get/${k}`,
  getObjectText: async (k: string) => objects.get(k)!.toString("utf8"),
  sha256OfObject: async (k: string) => createHash("sha256").update(objects.get(k)!).digest("hex"),
  copyObject: async (from: string, to: string) => void objects.set(to, objects.get(from)!),
  deleteObject: async (k: string) => void objects.delete(k),
}));

// The signed-in WorkOS session; `null` = signed out (ensureSignedIn redirects, which throws).
let session: { user: { id: string; email: string }; organizationId: string } | null = null;
class RedirectToSignIn extends Error {}
vi.mock("@workos-inc/authkit-nextjs", () => ({
  withAuth: vi.fn(async () => {
    if (!session) throw new RedirectToSignIn("NEXT_REDIRECT");
    return session;
  }),
}));
vi.mock("next/navigation", () => ({
  unstable_rethrow: (e: unknown) => {
    if (e instanceof RedirectToSignIn) throw e;
  },
}));
vi.mock("@/lib/workos", () => ({ workos: () => ({ organizations: {}, userManagement: {} }) }));

const modalCalls: unknown[] = [];
vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
  if (String(url).startsWith("https://modal.test")) {
    modalCalls.push(JSON.parse(String(init?.body)));
    return new Response(JSON.stringify({ accepted: true, callId: "fc-1" }), { status: 200 });
  }
  throw new Error(`unexpected fetch ${url}`);
}));

const actions = await import("@/app/dashboard/playground/actions");
const { buildPackage, DEFAULT_OPTIONS, expandBatch } = await import("@/lib/playground");

/** Prepaid credit for every workspace created so far (accounts are created on the first action). */
const fund = () => testDb.update(schema.billing).set({ creditUsd: 100 });
const as = (who: "alice" | "bob") => {
  session = { user: { id: `user_${who}`, email: `${who}@example.com` }, organizationId: `org_wos_${who}` };
};
const BYTES = Buffer.from("glTF playground model");
const SHA = createHash("sha256").update(BYTES).digest("hex");
const pkg = () => buildPackage([{ name: "m.glb", size: BYTES.length, uri: `asset://${SHA}`, role: "scene" }], DEFAULT_OPTIONS);

beforeAll(async () => {
  const dir = join(__dirname, "..", "drizzle");
  for (const f of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) {
    for (const stmt of readFileSync(join(dir, f), "utf8").split("--> statement-breakpoint")) {
      if (stmt.trim()) await pg.exec(stmt);
    }
  }
});

describe("playground actions", () => {
  it("require a signed-in session (the sign-in redirect propagates)", async () => {
    session = null;
    await expect(actions.registerUploads([{ filename: "m.glb", size: 1, sha256: SHA }])).rejects.toBeInstanceOf(RedirectToSignIn);
    await expect(actions.submitPlayground(pkg(), "standard")).rejects.toBeInstanceOf(RedirectToSignIn);
  });

  it("upload → estimate → submit for the session's org, through the shared upload policy", async () => {
    as("alice");
    const reg = await actions.registerUploads([{ filename: "m.glb", size: BYTES.length, sha256: SHA, contentType: "model/gltf-binary" }]);
    expect(reg.ok).toBe(true);
    const ticket = reg.ok ? reg.data[0] : null;
    expect(ticket).toMatchObject({ exists: false, assetUri: `asset://${SHA}` });
    expect(ticket && !ticket.exists && ticket.uploadUrl).toMatch(/staging\/.+\//); // per-org staging, not the shared key
    if (ticket && !ticket.exists) objects.set(ticket.uploadUrl.replace("https://r2.test/put/", ""), BYTES); // the browser's PUT

    const est = await actions.estimatePlayground(pkg(), "standard");
    expect(est.ok && (est.data as { priorities: object }).priorities).toHaveProperty("rush");

    await fund();

    const sub = await actions.submitPlayground(pkg(), "standard", "pg-1");
    expect(sub.ok).toBe(true);
    expect(sub.ok && sub.data.status).toBe("queued");
    expect(modalCalls).toHaveLength(1);
    // idempotent retry returns the same job, no second submission
    await fund();
    const again = await actions.submitPlayground(pkg(), "standard", "pg-1");
    expect(again.ok && sub.ok && again.data.id).toBe(sub.ok && sub.data.id);
    expect(modalCalls).toHaveLength(1);

    const polled = await actions.getPlaygroundJob(sub.ok ? sub.data.id : "");
    expect(polled.ok).toBe(true);
  });

  it("another org can't read alice's job or use her file by hash", async () => {
    as("alice");
    await fund();
    const mine = await actions.submitPlayground(pkg(), "standard", "pg-2");
    const id = mine.ok ? mine.data.id : "";
    as("bob");
    const peek = await actions.getPlaygroundJob(id);
    expect(peek).toMatchObject({ ok: false, error: { code: "not_found" } });
    const reg = await actions.registerUploads([{ filename: "m.glb", size: BYTES.length, sha256: SHA }]);
    expect(reg.ok && reg.data[0].exists).toBe(false); // must upload its own copy
    await fund();
    const sub = await actions.submitPlayground(pkg(), "standard");
    expect(sub).toMatchObject({ ok: false, error: { code: "asset_missing" } });
  });

  it("batched status, cancel and restore are session-only and org-scoped", async () => {
    session = null;
    await expect(actions.getPlaygroundJobs(["x"])).rejects.toBeInstanceOf(RedirectToSignIn);
    await expect(actions.recentPlaygroundJobs()).rejects.toBeInstanceOf(RedirectToSignIn);

    as("alice");
    const items = expandBatch([{ name: "m.glb", size: BYTES.length, uri: `asset://${SHA}`, role: "scene" }], DEFAULT_OPTIONS, { engines: ["mitsuba"] }).items;
    expect(items).toHaveLength(2);
    const ids: string[] = [];
    for (const [i, it] of items.entries()) {
      await fund();
      const r = await actions.submitPlayground(it.pkg, "standard", `pg-batch-${i}`);
      expect(r.ok).toBe(true);
      if (r.ok) ids.push(r.data.id);
    }
    const polled = await actions.getPlaygroundJobs([...ids, "job_nope"]);
    expect(polled.ok && polled.data.map((j) => j.id).sort()).toEqual([...ids].sort());
    const tooMany = await actions.getPlaygroundJobs(Array.from({ length: 101 }, (_, i) => `job_${i}`));
    expect(tooMany).toMatchObject({ ok: false, error: { code: "invalid_request" } });

    const recent = await actions.recentPlaygroundJobs();
    expect(recent.ok && recent.data.map((j) => j.id)).toEqual(expect.arrayContaining(ids));
    expect(recent.ok && recent.data.every((j) => j.name?.startsWith("playground · "))).toBe(true);

    as("bob");
    const peek = await actions.getPlaygroundJobs(ids);
    expect(peek.ok && peek.data).toEqual([]); // other org's ids are simply absent
    const bobRecent = await actions.recentPlaygroundJobs();
    expect(bobRecent.ok && bobRecent.data.some((j) => ids.includes(j.id))).toBe(false);
    const steal = await actions.cancelPlaygroundJob(ids[0]);
    expect(steal).toMatchObject({ ok: false, error: { code: "not_found" } });

    as("alice");
    const canceled = await actions.cancelPlaygroundJob(ids[0]);
    expect(canceled.ok && canceled.data.status).toBe("canceled");
    const again = await actions.cancelPlaygroundJob(ids[0]);
    expect(again).toMatchObject({ ok: false, error: { code: "conflict" } });
  });

  it("returns API errors as values (bad package, bad priority, too many files)", async () => {
    as("alice");
    await fund();
    const bad = await actions.submitPlayground({ version: 1, scene: {} }, "standard");
    expect(bad).toMatchObject({ ok: false, error: { code: "invalid_package" } });
    const prio = await actions.estimatePlayground(pkg(), "turbo" as never);
    expect(prio).toMatchObject({ ok: false, error: { code: "invalid_request" } });
    const many = await actions.registerUploads(Array.from({ length: 21 }, () => ({ filename: "x", size: 1, sha256: SHA })));
    expect(many).toMatchObject({ ok: false, error: { code: "invalid_request" } });
  });
});
