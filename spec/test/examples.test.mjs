import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { safeParsePackage, formatFromPath, packageForFile } from "../dist/index.js";

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
