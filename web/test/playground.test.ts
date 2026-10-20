import { describe, expect, it } from "vitest";
import { safeParsePackage } from "@rendahq/spec";
import { buildPackage, copyAs, DEFAULT_OPTIONS, inlineGltf, pickScene, roleFor, type PlaygroundFile, type PlaygroundOptions } from "@/lib/playground";

const SHA = "a".repeat(64);
const HDR = "b".repeat(64);
const glb: PlaygroundFile = { name: "chair.glb", size: 10, uri: `asset://${SHA}`, role: "scene" };
const hdri: PlaygroundFile = { name: "studio.hdr", size: 10, uri: `asset://${HDR}`, role: "hdri" };
const opts = (o: Partial<PlaygroundOptions>): PlaygroundOptions => ({ ...DEFAULT_OPTIONS, ...o });
const valid = (pkg: unknown) => {
  const r = safeParsePackage(pkg);
  if (!r.success) throw new Error(JSON.stringify(r.error.issues));
  return r.data;
};

describe("playground package builder", () => {
  it("still: scene asset with sniffed format, studio HDRI, png", () => {
    const p = valid(buildPackage([glb], opts({ action: "still", resolution: [1920, 1080], quality: "final", engine: "mitsuba" })));
    expect(p.assets.scene).toMatchObject({ uri: `asset://${SHA}`, format: "gltf" });
    expect(p.render).toMatchObject({ engine: "mitsuba", resolution: [1920, 1080], quality: "final" });
    expect(p.output.format).toBe("png");
    expect(p.environment.hdri).toBe("studio");
  });

  it("uses a dropped HDRI and a transparent background", () => {
    const p = valid(buildPackage([glb, hdri], opts({ background: "transparent" })));
    expect(p.assets.env.uri).toBe(`asset://${HDR}`);
    expect(p.environment).toMatchObject({ hdri: "env", background: "transparent" });
    expect(p.output.transparent).toBe(true);
  });

  it("turntable: frames from seconds × fps; transparent → webm (mp4 has no alpha)", () => {
    const p = valid(buildPackage([glb], opts({ action: "turntable", turntableSeconds: 3, fps: 24 })));
    expect(p.animation).toMatchObject({ frames: [1, 72], fps: 24, turntable: { revolutions: 1 } });
    expect(p.output.format).toBe("mp4");
    expect(valid(buildPackage([glb], opts({ action: "turntable", background: "transparent" }))).output.format).toBe("webm");
  });

  it("bake: glb + bake block; engines that can't bake fall back to auto", () => {
    const p = valid(buildPackage([glb], opts({ action: "bake", bakeMode: "ao", bakeSize: 1024, engine: "blender-eevee" })));
    expect(p.output).toMatchObject({ format: "glb", bake: { mode: "ao", size: 1024 } });
    expect(p.render.engine).toBe("auto");
  });

  it("splat: spz with preset; gsplat isn't a view renderer", () => {
    const p = valid(buildPackage([glb], opts({ action: "splat", splatPreset: "fast", engine: "gsplat" })));
    expect(p.output).toMatchObject({ format: "splat", splatFormat: "spz", splatBake: { preset: "fast", engine: "auto" } });
  });

  it("convert: plain glb without a bake block", () => {
    const p = valid(buildPackage([glb], opts({ action: "glb" })));
    expect(p.output.format).toBe("glb");
    expect(p.output.bake ?? null).toBeNull();
  });

  it("colour backgrounds pass through; every action validates for STEP and splat inputs too", () => {
    expect(valid(buildPackage([glb], opts({ background: "#112233" }))).environment.background).toBe("#112233");
    for (const name of ["bracket.step", "garden.spz", "scene.usdz", "part.stl", "room.blend"]) {
      const f = { ...glb, name };
      for (const action of ["still", "turntable", "bake", "splat", "glb"] as const) {
        expect(() => valid(buildPackage([f], opts({ action })))).not.toThrow();
      }
    }
  });

  it("needs a scene file", () => {
    expect(() => buildPackage([hdri], DEFAULT_OPTIONS)).toThrow(/3D file/);
  });

  it("file roles and scene choice (prefers self-contained formats)", () => {
    expect(roleFor("a.glb")).toBe("scene");
    expect(roleFor("a.hdr")).toBe("hdri");
    expect(roleFor("a.bin")).toBe("sidecar");
    expect(roleFor("notes.txt")).toBe("unsupported");
    expect(pickScene([{ name: "a.gltf", role: "scene" as const }, { name: "b.glb", role: "scene" as const }])?.name).toBe("b.glb");
  });

  it("copy-as: CLI command per action, MCP tool call carries the package", () => {
    const o = opts({ action: "bake", bakeMode: "unlit", bakeSize: 2048 });
    const pkg = buildPackage([glb], o);
    const c = copyAs(pkg, o, "chair.glb");
    expect(c.cli).toBe("npx @rendahq/cli bake chair.glb --mode unlit --size 2048");
    expect(JSON.parse(c.mcp)).toMatchObject({ tool: "renda_bake", arguments: { priority: "standard" } });
    expect(JSON.parse(c.json)).toEqual(pkg);
  });
});

describe("inlineGltf", () => {
  it("embeds external buffers and images as data URIs and reports missing ones", () => {
    const gltf = JSON.stringify({ asset: { version: "2.0" }, buffers: [{ uri: "scene.bin", byteLength: 3 }], images: [{ uri: "tex/wood%20oak.png" }, { uri: "gone.jpg" }] });
    const { text, inlined, missing } = inlineGltf(gltf, new Map([["scene.bin", new Uint8Array([1, 2, 3])], ["wood oak.png", new Uint8Array([9])]]));
    const doc = JSON.parse(text);
    expect(doc.buffers[0].uri).toBe("data:application/octet-stream;base64,AQID");
    expect(doc.images[0].uri).toBe("data:image/png;base64,CQ==");
    expect(inlined).toEqual(["scene.bin", "wood oak.png"]);
    expect(missing).toEqual(["gone.jpg"]);
  });
});
