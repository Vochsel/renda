import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { safeParsePackage, formatFromPath, packageForFile, parsePackage, parseTextureFlag } from "../dist/index.js";

const dir = new URL("../examples/", import.meta.url);
for (const f of readdirSync(dir).filter((f) => f.endsWith(".renda.json"))) {
  test(`example ${f} parses`, () => {
    const r = safeParsePackage(JSON.parse(readFileSync(new URL(f, dir), "utf8")));
    assert.ok(r.success, r.success ? "" : JSON.stringify(r.error.issues, null, 2));
  });
}

test("video needs animation", () => {
  const r = safeParsePackage({ scene: { root: "a.glb" }, output: { format: "mp4" } });
  assert.equal(r.success, false);
});

test("formats", () => {
  assert.equal(formatFromPath("x/Teapot.GLB"), "gltf");
  assert.equal(formatFromPath("https://h/a.usdz?sig=1"), "usd");
  assert.equal(formatFromPath("scene.json"), "threejs");
  assert.equal(formatFromPath("a.txt"), undefined);
});

test("bare file defaults", () => {
  const p = packageForFile("./robot.fbx");
  assert.equal(p.scene.root, "scene");
  assert.equal(p.render.resolution[0], 1920);
  assert.equal(p.camera.mode, "auto");
});

test("texture overrides parse with defaults", () => {
  const p = parsePackage({ scene: { root: "a.glb", textureOverrides: [{ source: "oak.jpg", match: { material: "/wood/i" } }] } });
  const t = p.scene.textureOverrides[0];
  assert.equal(t.colorSpace, "auto");
  assert.deepEqual(t.uv, { scale: 1, offset: [0, 0], rotationDeg: 0 });
  assert.deepEqual(t.video, { loop: true, startSec: 0, speed: 1 });
});

test("parseTextureFlag", () => {
  const e = parseTextureFlag("material=/Wood.*,x:y/i,slot=baseColor,scale=4,rotation=90:./oak.jpg");
  assert.deepEqual(e.match, { material: "/Wood.*,x:y/i", slot: "baseColor" });
  assert.deepEqual(e.uv, { scale: 4, rotationDeg: 90 });
  assert.equal(e.source, "./oak.jpg");
  const v = parseTextureFlag("object=Display,slot=emission,strength=3,loop=false,start=1.5:https://x.test/a.mp4");
  assert.equal(v.source, "https://x.test/a.mp4");
  assert.deepEqual(v.video, { loop: false, startSec: 1.5 });
  assert.equal(parseTextureFlag("./oak.jpg").source, "./oak.jpg");
  assert.deepEqual(parseTextureFlag("scale=2x3:a.png").uv, { scale: [2, 3] });
});
