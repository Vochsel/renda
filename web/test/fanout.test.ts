import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parsePackage } from "@rendahq/spec";
import { estimatePriorities, estimateScene, fanoutCount, type Priority } from "@/lib/estimate";

type Case = { frames: number; loadSeconds: number; frameSeconds: number; priority: Priority; containers: number };
const fixture = JSON.parse(readFileSync(join(__dirname, "../../spec/fixtures/estimator/fanout.json"), "utf8")) as { cases: Case[] };

describe("fan-out economics (shared with core/renda/planner.py)", () => {
  for (const c of fixture.cases) {
    it(`${c.frames} frames, ${c.frameSeconds}s/frame, ${c.priority} → ${c.containers} containers`, () => {
      expect(fanoutCount(c.frames, c.loadSeconds, c.frameSeconds, c.priority)).toBe(c.containers);
    });
  }

  it("priorities trade money for wall time", () => {
    const tt = parsePackage({ scene: { root: "x.glb" }, render: { resolution: [1920, 1080] },
      animation: { frames: [1, 240], turntable: {} }, output: { format: "mp4" } });
    const p = estimatePriorities(tt);
    expect(p.economy.chunks).toBeLessThan(p.standard.chunks);
    expect(p.standard.chunks).toBeLessThanOrEqual(p.rush.chunks);
    expect(p.rush.estWallSeconds[0]).toBeLessThan(p.economy.estWallSeconds[0]);
  });
});

describe("splat bake estimates", () => {
  const glb = { scene: { root: "x.glb" }, output: { format: "splat" as const } };
  it("standard renders views inside the trainer; rush and explicit fanOut stream from parallel containers", () => {
    const std = estimateScene(parsePackage(glb));
    expect(std.chunks).toBe(1);
    expect(std.hardware).toBe("gpu-l4");
    const rush = estimateScene(parsePackage(glb), "rush");
    expect(rush.chunks).toBeGreaterThan(1);
    expect(rush.estWallSeconds[0]).toBeLessThan(std.estWallSeconds[0]);
    const forced = estimateScene(parsePackage({ ...glb, output: { format: "splat" as const, splatBake: { fanOut: true } } }));
    expect(forced.chunks).toBeGreaterThan(1);
  });
  it("explicit cameras set the view count; three.js views always fan out", () => {
    const cams = Array.from({ length: 24 }, (_, i) => ({ position: [Math.cos(i), 1, Math.sin(i)] as [number, number, number], target: [0, 0, 0] as [number, number, number] }));
    expect(estimateScene(parsePackage({ ...glb, output: { format: "splat" as const, splatBake: { cameras: cams } } })).frames).toBe(24);
    const tj = estimateScene(parsePackage({ ...glb, output: { format: "splat" as const, splatBake: { engine: "threejs", fanOut: false } } }));
    expect(tj.chunks).toBeGreaterThanOrEqual(1);
    expect(tj.warnings.some((w) => w.includes("fanning out"))).toBe(true);
  });
});
