import { describe, expect, it } from "vitest";
import { parsePackage } from "@rendahq/spec";
import { readFileSync } from "node:fs";
import { autoEngine, estimateHyperframes, estimateScene, frameCount } from "@/lib/estimate";

describe("auto engine parity with core", () => {
  it("matches spec/fixtures/estimator/auto-engine.json", () => {
    const { cases } = JSON.parse(readFileSync(new URL("../../spec/fixtures/estimator/auto-engine.json", import.meta.url), "utf8"));
    for (const c of cases) expect([c.format, c.quality, autoEngine(c.format, c.quality, !!c.mixed)]).toEqual([c.format, c.quality, c.engine]);
  });
});

describe("estimate", () => {
  it("splat bakes: fast is a cheap CPU conversion; standard renders 96 views then trains", () => {
    const fast = estimateScene(parsePackage({ scene: { root: "x.glb" }, output: { format: "splat", splatBake: { preset: "fast" } } }));
    expect(fast.hardware).toBe("cpu");
    expect(fast.estCostUsd[1]).toBeLessThan(0.01);
    const std = estimateScene(parsePackage({ scene: { root: "x.glb" }, output: { format: "splat" } }));
    expect(std.frames).toBe(96);
    expect(std.hardware).toBe("gpu-l4");
    // views render inside the trainer (no extra containers); ≤4k steps with early stop
    expect(std.chunks).toBe(1);
    expect(std.estCostUsd[0]).toBeLessThan(0.12);
    expect(std.estCostUsd[1]).toBeGreaterThan(0.12);
  });
  it("draft still of a mesh defaults to three.js on CPU and is cheap", () => {
    const e = estimateScene(parsePackage({ scene: { root: "asset://" + "a".repeat(64) }, render: { quality: "draft", resolution: [1280, 720] } }));
    expect(e.engine).toBe("threejs");
    expect(e.hardware).toBe("cpu");
    expect(e.frames).toBe(1);
    expect(e.estCostUsd[1]).toBeLessThan(0.05);
    expect(e.estSeconds[0]).toBeLessThan(e.estSeconds[1]);
  });
  it("draft of a Blender-native file stays on Eevee (GPU)", () => {
    const e = estimateScene(parsePackage({ assets: { s: { uri: "asset://" + "b".repeat(64), format: "blend" } }, scene: { root: "s" }, render: { quality: "draft" } }));
    expect(e.engine).toBe("blender-eevee");
    expect(e.hardware).toMatch(/^gpu-/);
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

describe("glb output", () => {
  it("plain conversion is a cheap CPU export", () => {
    const e = estimateScene(parsePackage({ scene: { root: "https://h/a.fbx" }, output: { format: "glb" } }));
    expect(e.engine).toBe("export");
    expect(e.hardware).toBe("cpu");
  });

  it("bakes default to Cycles on an L4; three.js bakes on CPU; AO is cheaper", () => {
    const full = estimateScene(parsePackage({ scene: { root: "https://h/a.glb" }, output: { format: "glb", bake: {} } }));
    expect(full.engine).toBe("blender-cycles");
    expect(full.hardware).toBe("gpu-l4");
    const ao = estimateScene(parsePackage({ scene: { root: "https://h/a.glb" }, output: { format: "glb", bake: { mode: "ao" } } }));
    expect(ao.estSeconds[1]).toBeLessThan(full.estSeconds[1]);
    const three = estimateScene(parsePackage({ scene: { root: "https://h/a.glb" }, render: { engine: "threejs" }, output: { format: "glb", bake: { size: 1024 } } }));
    expect(three.hardware).toBe("cpu");
  });
});
