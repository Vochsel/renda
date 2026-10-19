import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/errors";
import { checkUris, parseJobPackage } from "@/lib/packages";

const SHA = "c".repeat(64);

function code(fn: () => unknown) {
  try { fn(); } catch (e) { return e instanceof ApiError ? e.code : "other"; }
  return "ok";
}

describe("package uri validation", () => {
  it("classifies uris", () => {
    const r = checkUris([`asset://${SHA}`, `asset://${SHA}`, "https://x.com/a.glb", "./local.blend", "asset://nothex", "/abs/path.obj"]);
    expect(r.assetShas).toEqual([SHA]);
    expect(r.localUris).toEqual(["./local.blend", "/abs/path.obj"]);
    expect(r.badUris).toEqual(["asset://nothex"]);
  });
  it("accepts asset:// and https scenes", () => {
    const p = parseJobPackage("scene", { scene: { root: `asset://${SHA}` } });
    expect(p.uris.assetShas).toEqual([SHA]);
    expect(code(() => parseJobPackage("scene", { assets: { s: { uri: "https://cdn.example.com/a.usdz" } }, scene: { root: "s" } }))).toBe("ok");
  });
  it("rejects local paths with the upload hint", () => {
    expect(code(() => parseJobPackage("scene", { scene: { root: "./model.glb" } }))).toBe("local_uri");
    expect(code(() => parseJobPackage("scene", { assets: { a: { uri: "teapot.blend" } }, scene: { root: "a" } }))).toBe("local_uri");
  });
  it("texture override sources are checked like other assets", () => {
    const tex = (source: string) => ({ scene: { root: `asset://${SHA}`, textureOverrides: [{ source, match: { material: "/wood/i" } }] } });
    const p = parseJobPackage("scene", tex(`asset://${"d".repeat(64)}`));
    expect(p.uris.assetShas).toContain("d".repeat(64));
    expect(code(() => parseJobPackage("scene", tex("./oak.jpg")))).toBe("local_uri");
    expect(code(() => parseJobPackage("scene", tex("https://cdn.example.com/screen.mp4")))).toBe("ok");
  });
  it("hdri asset refs are checked but built-ins are allowed", () => {
    expect(code(() => parseJobPackage("scene", { scene: { root: `asset://${SHA}` }, environment: { hdri: "studio" } }))).toBe("ok");
    expect(code(() => parseJobPackage("scene", { scene: { root: `asset://${SHA}` }, environment: { hdri: "./sky.hdr" } }))).toBe("local_uri");
  });
  it("schema errors are invalid_package with issues", () => {
    try {
      parseJobPackage("scene", { scene: { root: `asset://${SHA}` }, output: { format: "mp4" } });
      throw new Error("should fail");
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).code).toBe("invalid_package");
      expect(JSON.stringify((e as ApiError).toBody())).toMatch(/animation/);
    }
  });
  it("hyperframes packages", () => {
    expect(code(() => parseJobPackage("hyperframes", { project: `asset://${SHA}`, format: "mp4", quality: "looks" }))).toBe("ok");
    expect(code(() => parseJobPackage("hyperframes", { project: "./promo" }))).toBe("local_uri");
    expect(code(() => parseJobPackage("hyperframes", {}))).toBe("invalid_package");
  });

  it("hyperframes asset:// variables are inputs; other strings are plain values", () => {
    const clip = "d".repeat(64);
    const parsed = parseJobPackage("hyperframes", {
      project: `asset://${SHA}`,
      variables: { clipUrl: `asset://${clip}`, title: "Hi", link: "https://renda.dev", count: 2 },
    });
    expect(parsed.uris.assetShas.sort()).toEqual([SHA, clip].sort());
    expect(code(() => parseJobPackage("hyperframes", { project: `asset://${SHA}`, variables: { clip: "asset://nope" } }))).toBe("invalid_package");
  });
});
