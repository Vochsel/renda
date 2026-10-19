// Layering fixtures shared with core/tests/test_layers.py. Regenerate expectations with
// `UPDATE_FIXTURES=1 node --test test/layers.test.mjs` and review the diff.
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { flattenPackage, LayerError, safeParsePackage, isLayered, transformMatrix } from "../dist/index.js";
import { isPublicAddress, safeFetchText } from "../dist/node.js";

const root = fileURLToPath(new URL("../fixtures/layers/", import.meta.url)).replace(/\/$/, "");

/** Loaders that read fixtures; https://example.test/<p> maps to <case>/remote/<p>. */
function loaders(dir) {
  return {
    readFile: async (p) => readFileSync(p, "utf8"),
    fetchHttps: async (url) => {
      const u = new URL(url);
      assert.equal(u.hostname, "example.test");
      return readFileSync(`${dir}/remote${u.pathname}`, "utf8");
    },
  };
}

const sub = (v, dir) => JSON.parse(JSON.stringify(v).split(dir).join("${DIR}"));

for (const name of readdirSync(root).filter((n) => existsSync(`${root}/${n}/input.renda.json`))) {
  test(`layers fixture: ${name}`, async () => {
    const dir = `${root}/${name}`;
    const input = JSON.parse(readFileSync(`${dir}/input.renda.json`, "utf8"));
    assert.ok(isLayered(input));
    const r = await flattenPackage(input, { ...loaders(dir), base: dir });
    const got = sub({ package: r.package, provenance: r.provenance, layers: r.layers }, dir);
    const file = `${dir}/expected.json`;
    if (process.env.UPDATE_FIXTURES || !existsSync(file)) writeFileSync(file, JSON.stringify(got, null, 2) + "\n");
    assert.deepEqual(got, JSON.parse(readFileSync(file, "utf8")));
    const parsed = safeParsePackage(r.package);
    assert.ok(parsed.success, JSON.stringify(parsed.error?.issues));
  });
}

for (const c of JSON.parse(readFileSync(`${root}/errors/cases.json`, "utf8"))) {
  test(`layers error: ${c.entry} → ${c.code}`, async () => {
    const path = `${root}/errors/${c.entry}`;
    const dir = path.replace(/\/[^/]+$/, "");
    const input = JSON.parse(readFileSync(path, "utf8"));
    const l = loaders(dir);
    await assert.rejects(
      flattenPackage(input, {
        readFile: l.readFile,
        fetchHttps: c.noHttps ? undefined : l.fetchHttps,
        base: c.local ? dir : null,
        label: c.local ? path.replace(/\/[^/]+\/\.\.\//g, "/") : undefined,
        maxDepth: c.maxDepth,
        maxLayers: c.maxLayers,
      }),
      (e) => e instanceof LayerError && e.code === c.code && e.field === c.field,
    );
  });
}

test("unlayered packages pass through untouched", async () => {
  const pkg = { scene: { root: "./a.glb" }, render: { quality: "draft" } };
  const r = await flattenPackage(pkg, { base: "/somewhere" });
  assert.deepEqual(r.package, pkg);
  assert.deepEqual(r.layers, []);
});

test("parsePackage refuses unflattened packages with a clear message", () => {
  const r = safeParsePackage({ extends: "./base.renda.json", scene: { root: "a.glb" } });
  assert.equal(r.success, false);
  assert.match(r.error.issues[0].message, /flatten/);
});

test("transformMatrix matches core's trs (T·Rz·Ry·Rx·S, column-major)", () => {
  const m = transformMatrix({ translate: [1, 2, 3], rotateDeg: [0, 90, 0], scale: 2 });
  const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-12, `${a} != ${b}`);
  // x axis → -z (scaled by 2), translation in the last column
  [0, 0, -2, 0].forEach((v, i) => near(m[i], v));
  [1, 2, 3, 1].forEach((v, i) => near(m[12 + i], v));
});

test("isPublicAddress blocks private, loopback, link-local and mapped ranges", () => {
  for (const ip of ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
    "::1", "::", "fe80::1", "fc00::1", "fd12::3", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::a00:1", "ff02::1", "2002:a00:1::"]) {
    assert.equal(isPublicAddress(ip), false, ip);
  }
  for (const ip of ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8", "93.184.216.34"]) {
    assert.equal(isPublicAddress(ip), true, ip);
  }
});

test("safeFetchText refuses http, IP literals in private ranges and credentials before connecting", async () => {
  await assert.rejects(safeFetchText("http://example.com/a.json"), /only https/);
  await assert.rejects(safeFetchText("https://127.0.0.1/a.json"), /not a public address/);
  await assert.rejects(safeFetchText("https://[::1]/a.json"), /not a public address/);
  await assert.rejects(safeFetchText("https://169.254.169.254/latest/meta-data"), /not a public address/);
  await assert.rejects(safeFetchText("https://user:pw@example.com/a.json"), /credentials/);
  await assert.rejects(safeFetchText("https://localhost/a.json"), /non-public address/);
});

test("layers compose with texture overrides (sources rebased per layer) and bake output", async () => {
  const dir = `${root}/textures-and-bake`;
  const input = JSON.parse(readFileSync(`${dir}/input.renda.json`, "utf8"));
  const { package: pkg } = await flattenPackage(input, { ...loaders(dir), base: dir });
  const [label, promo] = pkg.scene.textureOverrides;
  assert.equal(label.source, `${dir}/brand/logo.png`); // relative to the layer that declared it
  assert.equal(promo.source, "https://cdn.example.test/promo.mp4");
  assert.deepEqual(pkg.output, { format: "glb", bake: { mode: "ao", size: 1024 } });
  const r = safeParsePackage(pkg);
  assert.ok(r.success, JSON.stringify(r.error?.issues));
  assert.equal(r.data.output.bake.mode, "ao");
});
