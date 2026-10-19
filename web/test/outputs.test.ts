import { describe, expect, it, vi } from "vitest";

vi.mock("@/lib/r2", () => ({ presignGet: async (key: string) => `https://r2.test/${key}` }));

import { drizzle } from "drizzle-orm/neon-http";
import { neon } from "@neondatabase/serverless";
import * as schema from "@/lib/db/schema";
import type { JobOutput } from "@/lib/db/schema";
import { toJobOutput } from "@/lib/jobs";
import {
  collectOutputs,
  decodeCursor,
  encodeCursor,
  isListed,
  jobsForOutputsQuery,
  kindOf,
  listOutputs,
  type FetchJobs,
  type JobForOutputs,
} from "@/lib/outputs";

const png = (name: string, extra: Partial<JobOutput> = {}): JobOutput => ({
  name, key: `outputs/x/${name}`, contentType: "image/png", bytes: 10, ...extra,
});

function job(id: string, t: number, outputs: JobOutput[]): JobForOutputs {
  return { id, name: null, createdAt: new Date(t), engine: "mitsuba", hardware: "gpu-l4", secondsCpu: 1, secondsGpuL4: 2, secondsGpuL40s: 0, costUsd: 0.01, outputs };
}

/** In-memory stand-in for the SQL query: same org filter, ordering and `after` semantics. */
function fakeDb(byOrg: Record<string, JobForOutputs[]>): FetchJobs & { calls: number } {
  const f = (async (orgId, after, limit) => {
    f.calls++;
    const rows = [...(byOrg[orgId] ?? [])].sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime() || (a.id < b.id ? 1 : -1));
    return rows
      .filter((r) => {
        if (!after) return true;
        const t = r.createdAt.getTime();
        return t < after.t || (t === after.t && (after.inclusive ? r.id <= after.j : r.id < after.j));
      })
      .slice(0, limit);
  }) as FetchJobs & { calls: number };
  f.calls = 0;
  return f;
}

describe("cursor", () => {
  it("round-trips and rejects garbage", () => {
    const c = { t: 1760000000123, j: "job_abc", i: 2 };
    expect(decodeCursor(encodeCursor(c))).toEqual(c);
    expect(() => decodeCursor("not-a-cursor")).toThrow(/cursor/i);
    expect(() => decodeCursor(Buffer.from(JSON.stringify([1, "j", -1])).toString("base64url"))).toThrow();
  });
});

describe("kinds and listing", () => {
  it("classifies outputs", () => {
    expect(kindOf({ contentType: "image/webp", key: "a.webp" })).toBe("image");
    expect(kindOf({ contentType: "video/mp4", key: "a.mp4" })).toBe("video");
    expect(kindOf({ contentType: "model/gltf-binary", key: "a.glb" })).toBe("model");
    expect(kindOf({ contentType: "application/octet-stream", key: "out/a.spz" })).toBe("model");
    expect(kindOf({ contentType: "application/zip", key: "dataset.zip" })).toBeNull();
  });

  it("hides passes and kept frames unless all", () => {
    expect(isListed(png("frame_0001.depth.png", { pass: "depth" }), {})).toBe(false);
    expect(isListed(png("frame_0001.depth.png", { pass: "depth" }), { all: true })).toBe(true);
    expect(isListed({ ...png("f.png"), key: "outputs/x/frames/f.png" }, {})).toBe(false);
    expect(isListed(png("f.png"), { kind: "video" })).toBe(false);
  });
});

describe("collectOutputs pagination", () => {
  // 12 jobs, newest first by time; two share a timestamp; job 7 has 3 outputs (one is a pass).
  const jobs = Array.from({ length: 12 }, (_, n) =>
    job(`job_${String(n).padStart(2, "0")}`, n === 4 ? 5000 : 1000 * n, n === 7 ? [png("a.png"), png("b.depth.png", { pass: "depth" }), png("c.png")] : [png("only.png")]),
  );
  const fetch = fakeDb({ org_a: jobs, org_b: [job("job_b", 99_000, [png("secret.png")])] });

  it("walks every output exactly once, newest first, across page boundaries (incl. mid-job)", async () => {
    const seen: string[] = [];
    let cursor = null;
    for (let page = 0; page < 20; page++) {
      const { items, next } = await collectOutputs("org_a", { cursor, limit: 2 }, fetch);
      seen.push(...items.map((p) => `${p.job.id}:${p.index}`));
      if (!next) break;
      cursor = next;
    }
    expect(seen).toEqual([
      "job_11:0", "job_10:0", "job_09:0", "job_08:0", "job_07:0", "job_07:2", "job_06:0",
      "job_05:0", "job_04:0", "job_03:0", "job_02:0", "job_01:0", "job_00:0",
    ]);
    expect(new Set(seen).size).toBe(seen.length);
  });

  it("orders same-timestamp jobs by id and resumes inside them", async () => {
    const first = await collectOutputs("org_a", { limit: 5 }, fetch); // ends inside job_07
    expect(first.next).toEqual({ t: 7000, j: "job_07", i: 2 });
    const second = await collectOutputs("org_a", { cursor: first.next, limit: 3 }, fetch);
    expect(second.items.map((p) => `${p.job.id}:${p.index}`)).toEqual(["job_07:2", "job_06:0", "job_05:0"]);
    // job_05 and job_04 share t=5000: the cursor lands on job_04 and resumes there, not past it.
    expect(second.next).toEqual({ t: 5000, j: "job_04", i: 0 });
    const third = await collectOutputs("org_a", { cursor: second.next, limit: 1 }, fetch);
    expect(third.items.map((p) => p.job.id)).toEqual(["job_04"]);
  });

  it("is scoped to the org", async () => {
    const a = await collectOutputs("org_a", { limit: 100 }, fetch);
    expect(a.items.some((p) => p.job.id === "job_b")).toBe(false);
    const b = await collectOutputs("org_b", { limit: 100 }, fetch);
    expect(b.items.map((p) => p.job.id)).toEqual(["job_b"]);
    expect((await collectOutputs("org_c", { limit: 10 }, fetch)).items).toEqual([]);
  });

  it("stops scanning on sparse filters and hands back a resumable cursor", async () => {
    const many = Array.from({ length: 600 }, (_, n) => job(`job_${String(n).padStart(4, "0")}`, n * 10, [png("x.png")]));
    const f = fakeDb({ org: many });
    const r = await collectOutputs("org", { limit: 5, kind: "video" }, f);
    expect(r.items).toEqual([]);
    expect(f.calls).toBe(10);
    expect(r.next).not.toBeNull();
  });
});

describe("listOutputs", () => {
  it("serialises items with presigned urls and thumbnails", async () => {
    const f = fakeDb({
      org: [job("job_1", 1000, [png("frame_0001.png", { width: 800, height: 450, thumbKey: "outputs/x/thumbs/frame_0001.webp", placeholder: "data:image/webp;base64,AAA" })])],
    });
    const page = await listOutputs("org", { limit: 10 }, f);
    expect(page.nextCursor).toBeNull();
    expect(page.items[0]).toMatchObject({
      id: "job_1:0", kind: "image", width: 800, height: 450, url: "https://r2.test/outputs/x/frame_0001.png",
      thumbUrl: "https://r2.test/outputs/x/thumbs/frame_0001.webp", billedSeconds: 3, engine: "mitsuba",
    });
  });

  it("clamps the page size", async () => {
    const f = fakeDb({ org: Array.from({ length: 150 }, (_, n) => job(`j${String(n).padStart(3, "0")}`, n, [png("p.png")])) });
    expect((await listOutputs("org", { limit: 1000 }, f)).items).toHaveLength(100);
  });
});

describe("SQL", () => {
  const d = drizzle(neon("postgresql://u:p@localhost/db"), { schema });

  it("filters by org and succeeded status, newest first", () => {
    const q = jobsForOutputsQuery(d, "org_123", null, 50).toSQL();
    expect(q.sql).toMatch(/"org_id" = \$1/);
    expect(q.params[0]).toBe("org_123");
    expect(q.params).toContain("succeeded");
    expect(q.sql).toMatch(/order by date_trunc\('milliseconds', "jobs"."created_at"\) desc, "jobs"."id" desc/);
  });

  it("applies the keyset condition", () => {
    const inclusive = jobsForOutputsQuery(d, "org", { t: 1000, j: "job_x", inclusive: true }, 50).toSQL();
    expect(inclusive.sql).toContain("<=");
    const exclusive = jobsForOutputsQuery(d, "org", { t: 1000, j: "job_x", inclusive: false }, 50).toSQL();
    expect(exclusive.sql).not.toContain("<=");
    expect(exclusive.params).toContain("job_x");
  });
});

describe("worker output sanitising", () => {
  it("keeps valid gallery metadata and drops the rest", () => {
    const o = toJobOutput({
      key: "outputs/j/frame_0001.png", contentType: "image/png", bytes: 5, width: 640, height: 360,
      thumbKey: "outputs/j/thumbs/frame_0001.webp", placeholder: "data:image/webp;base64,xx", pass: "depth",
    });
    expect(o).toMatchObject({ name: "frame_0001.png", width: 640, height: 360, thumbKey: "outputs/j/thumbs/frame_0001.webp", pass: "depth" });
    const bad = toJobOutput({
      key: "outputs/j/a.png", contentType: "image/png", bytes: 5, width: -1, height: 2,
      thumbKey: "outputs/other_job/thumbs/a.webp", placeholder: "javascript:alert(1)",
    });
    expect(bad.width).toBeUndefined();
    expect(bad.thumbKey).toBeUndefined();
    expect(bad.placeholder).toBeUndefined();
  });
});
