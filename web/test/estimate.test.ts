import { describe, expect, it } from "vitest";
import { parsePackage } from "@rendahq/spec";
import { estimateHyperframes, estimateScene, frameCount } from "@/lib/estimate";

describe("estimate", () => {
  it("draft still defaults to eevee on GPU and is cheap", () => {
    const e = estimateScene(parsePackage({ scene: { root: "asset://" + "a".repeat(64) }, render: { quality: "draft", resolution: [1280, 720] } }));
    expect(e.engine).toBe("blender-eevee");
    expect(e.hardware).toMatch(/^gpu-/);
    expect(e.frames).toBe(1);
    expect(e.estCostUsd[1]).toBeLessThan(0.05);
    expect(e.estSeconds[0]).toBeLessThan(e.estSeconds[1]);
  });
  it("cpu-only request falls back with a warning for GPU-only engines", () => {
    const e = estimateScene(parsePackage({ scene: { root: "x.glb" }, render: { engine: "blender-eevee", hardware: "cpu" } }));
    expect(e.warnings.join(" ")).toMatch(/needs a GPU/);
  });
  it("mitsuba small stills can go to CPU under economy", () => {
    const e = estimateScene(parsePackage({ scene: { root: "x.obj" }, render: { engine: "mitsuba", quality: "draft", resolution: [640, 480] } }), "economy");
    expect(["cpu", "gpu-l4"]).toContain(e.hardware);
  });
  it("animation frames and chunking", () => {
    const pkg = parsePackage({ scene: { root: "x.blend" }, animation: { frames: [1, 240], fps: 24 }, output: { format: "mp4" }, render: { quality: "final" } });
    expect(frameCount(pkg)).toBe(240);
    const e = estimateScene(pkg);
    expect(e.chunks).toBeGreaterThan(1);
    expect(e.estWallSeconds[1]).toBeLessThan(e.estSeconds[1]);
  });
  it("rush is never slower than economy", () => {
    const pkg = parsePackage({ scene: { root: "x.blend" }, animation: { frames: [1, 120] }, output: { format: "mp4" }, render: { quality: "final", resolution: [3840, 2160] } });
    expect(estimateScene(pkg, "rush").estWallSeconds[0]).toBeLessThanOrEqual(estimateScene(pkg, "economy").estWallSeconds[0]);
  });
  it("hyperframes estimate warns without duration", () => {
    const e = estimateHyperframes({ project: "asset://" + "b".repeat(64) });
    expect(e.hardware).toBe("cpu");
    expect(e.warnings.length).toBe(1);
  });
});

describe("gaussian splats", () => {
  it("routes splat-only packages to gsplat on a GPU", () => {
    const e = estimateScene(parsePackage({
      assets: { g: { uri: "asset://" + "a".repeat(64), format: "splat" } },
      scene: { root: "g" },
    }));
    expect(e.engine).toBe("gsplat");
    expect(e.hardware.startsWith("gpu")).toBe(true);
  });

  it("composites splats into mixed scenes with a mesh engine", () => {
    const e = estimateScene(parsePackage({
      assets: { g: { uri: "asset://" + "a".repeat(64), format: "splat" }, t: { uri: "asset://" + "b".repeat(64), format: "gltf" } },
      scene: { root: "g", include: [{ asset: "t" }] },
    }));
    expect(e.engine).toBe("blender-cycles");
  });
});
