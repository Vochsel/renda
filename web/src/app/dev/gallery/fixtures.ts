import type { OutputItem, OutputsPage } from "@/lib/outputs";
import { SHOWCASE } from "@/lib/showcase";

/** Dev-only gallery data built from the homepage showcase assets (no database, no R2). */
export function fixtureItems(count = 75): OutputItem[] {
  const now = Date.now();
  return Array.from({ length: count }, (_, i) => {
    if (i % 17 === 9) {
      return {
        id: `fx_${i}:0`, jobId: `job_fixture${i}`, jobName: "baked-diorama", name: "diorama.baked.glb", kind: "model",
        url: "#", thumbUrl: null, contentType: "model/gltf-binary", width: null, height: null, bytes: 2_400_000,
        placeholder: null, pass: null, engine: "blender-cycles", hardware: "gpu-l4", billedSeconds: 41.2, costUsd: 0.024,
        createdAt: new Date(now - i * 3_600_000).toISOString(),
      };
    }
    const s = SHOWCASE[i % SHOWCASE.length];
    const isVideo = Boolean(s.video);
    return {
      id: `fx_${i}:0`,
      jobId: `job_fixture${i}`,
      jobName: s.id,
      name: isVideo ? `${s.id}.mp4` : `${s.id}.webp`,
      kind: isVideo ? "video" : "image",
      url: (isVideo ? s.video : s.image)!,
      thumbUrl: (isVideo ? s.poster : s.image) ?? null,
      contentType: isVideo ? "video/mp4" : "image/webp",
      // Every 11th item lacks dimensions, like outputs from before the worker recorded them.
      width: i % 11 === 5 ? null : s.resolution[0],
      height: i % 11 === 5 ? null : s.resolution[1],
      bytes: 180_000 + i * 1_000,
      placeholder: null,
      pass: null,
      engine: s.engine,
      hardware: s.hardware,
      billedSeconds: s.billedSeconds,
      costUsd: s.costUsd,
      createdAt: new Date(now - i * 3_600_000).toISOString(),
    } satisfies OutputItem;
  });
}

export function fixturePage(cursor: string | null, limit: number): OutputsPage {
  const all = fixtureItems();
  const start = cursor ? Number(cursor) : 0;
  const end = Math.min(start + limit, all.length);
  return { items: all.slice(start, end), nextCursor: end < all.length ? String(end) : null };
}
