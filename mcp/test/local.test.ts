import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { unzipSync } from "fflate";
import { describe, expect, it } from "vitest";
import { RendaClient } from "../src/client.js";
import { isLocalUri, sha256, uploadLocalPaths, zipDirectory } from "../src/local.js";
import { fakeFetch } from "./helpers.js";

async function fixtureDir() {
  const d = await mkdtemp(join(tmpdir(), "renda-mcp-"));
  await writeFile(join(d, "model.glb"), "GLB-BYTES");
  await writeFile(join(d, "sky.hdr"), "HDR-BYTES");
  await mkdir(join(d, "props"));
  await writeFile(join(d, "props", "chair.obj"), "o chair");
  return d;
}

describe("isLocalUri", () => {
  it("classifies uris", () => {
    expect(isLocalUri("./a.glb")).toBe(true);
    expect(isLocalUri("/abs/a.glb")).toBe(true);
    expect(isLocalUri("https://h/a.glb")).toBe(false);
    expect(isLocalUri("asset://" + "0".repeat(64))).toBe(false);
  });
});

describe("uploadLocalPaths", () => {
  it("uploads local refs, skips remote/builtin/asset ids, dedupes, and rewrites", async () => {
    const dir = await fixtureDir();
    const existing = sha256(new TextEncoder().encode("HDR-BYTES"));
    const f = fakeFetch(({ method, url, body }) => {
      if (url.endsWith("/api/v1/uploads")) {
        const exists = body.sha256 === existing;
        return { json: { assetUri: `asset://${body.sha256}`, exists, uploadUrl: exists ? undefined : `https://r2.test/put/${body.filename}` } };
      }
      if (method === "PUT" && url.startsWith("https://r2.test/")) return { status: 200, text: "" };
    });
    const client = new RendaClient({ baseUrl: "https://x.test", apiKey: "k", fetch: f.fetch });
    const pkg = {
      assets: { model: { uri: "./model.glb" }, sky: { uri: "sky.hdr" }, remote: { uri: "https://cdn/x.usdz" }, again: { uri: "./model.glb" } },
      scene: { root: "model", include: [{ asset: "props/chair.obj" }, { asset: "remote" }] },
      environment: { hdri: "studio" },
    };
    const r = await uploadLocalPaths(client, pkg as any, dir);
    const out: any = r.package;
    expect(out.assets.model.uri).toBe(`asset://${sha256(new TextEncoder().encode("GLB-BYTES"))}`);
    expect(out.assets.again.uri).toBe(out.assets.model.uri);
    expect(out.assets.sky.uri).toBe(`asset://${existing}`);
    expect(out.assets.remote.uri).toBe("https://cdn/x.usdz");
    expect(out.scene.root).toBe("model");
    expect(out.scene.include[0].asset).toMatch(/^asset:\/\/[0-9a-f]{64}$/);
    expect(out.scene.include[1].asset).toBe("remote");
    expect(out.environment.hdri).toBe("studio");
    // model uploaded once (cache), sky deduped server-side (no PUT), chair uploaded
    expect(r.uploaded).toHaveLength(3);
    expect(f.calls.filter((c) => c.method === "PUT").map((c) => c.url).sort()).toEqual(["https://r2.test/put/chair.obj", "https://r2.test/put/model.glb"]);
    expect((pkg.assets.model as any).uri).toBe("./model.glb"); // input untouched
  });

  it("rewrites a bare scene.root path and hdri path", async () => {
    const dir = await fixtureDir();
    const f = fakeFetch(({ url, body }) => (url.endsWith("/uploads") ? { json: { assetUri: `asset://${body.sha256}`, exists: true } } : undefined));
    const client = new RendaClient({ baseUrl: "https://x.test", apiKey: "k", fetch: f.fetch });
    const r = await uploadLocalPaths(client, { scene: { root: "./model.glb" }, environment: { hdri: "./sky.hdr" } } as any, dir);
    expect((r.package as any).scene.root).toMatch(/^asset:\/\//);
    expect((r.package as any).environment.hdri).toMatch(/^asset:\/\//);
  });

  it("uploads texture override sources", async () => {
    const dir = await fixtureDir();
    const f = fakeFetch(({ url, body }) => (url.endsWith("/uploads") ? { json: { assetUri: `asset://${body.sha256}`, exists: true } } : undefined));
    const client = new RendaClient({ baseUrl: "https://x.test", apiKey: "k", fetch: f.fetch });
    const pkg = { scene: { root: "https://cdn/x.glb", textureOverrides: [{ source: "./sky.hdr" }, { source: "https://cdn/oak.jpg" }] } };
    const r = await uploadLocalPaths(client, pkg as any, dir);
    const tex = (r.package as any).scene.textureOverrides;
    expect(tex[0].source).toMatch(/^asset:\/\/[0-9a-f]{64}$/);
    expect(tex[1].source).toBe("https://cdn/oak.jpg");
  });
});

describe("zipDirectory", () => {
  it("zips deterministically and skips node_modules", async () => {
    const dir = await fixtureDir();
    await mkdir(join(dir, "node_modules"));
    await writeFile(join(dir, "node_modules", "junk.js"), "x");
    const a = await zipDirectory(dir);
    const b = await zipDirectory(dir);
    expect(sha256(a)).toBe(sha256(b));
    expect(Object.keys(unzipSync(a)).sort()).toEqual(["model.glb", "props/chair.obj", "sky.hdr"]);
  });
});
