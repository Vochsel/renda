import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { RendaClient } from "../src/client.js";
import { starterPackage, validatePackage } from "../src/tools.js";
import { connect, fakeFetch, job } from "./helpers.js";

const text = (r: any) => r.content.map((c: any) => c.text ?? "").join("\n");

describe("tool registration", () => {
  it("local mode exposes all tools", async () => {
    const mcp = await connect(new RendaClient({ baseUrl: "https://x.test" }));
    const names = (await mcp.listTools()).tools.map((t) => t.name).sort();
    expect(names).toEqual([
      "renda_bake", "renda_billing_link", "renda_cancel_job", "renda_claim", "renda_download", "renda_estimate", "renda_get_job", "renda_init_package",
      "renda_list_jobs", "renda_pricing", "renda_render", "renda_render_video", "renda_signup", "renda_trial", "renda_upload",
      "renda_usage", "renda_validate_package", "renda_verify", "renda_whoami",
    ]);
  });

  it("remote mode omits filesystem tools", async () => {
    const mcp = await connect(new RendaClient({ baseUrl: "https://x.test" }), "remote");
    const names = (await mcp.listTools()).tools.map((t) => t.name);
    expect(names).not.toContain("renda_upload");
    expect(names).not.toContain("renda_download");
    expect(names).toContain("renda_render");
  });

  it("exposes resources and prompts", async () => {
    const mcp = await connect(new RendaClient({ baseUrl: "https://x.test" }));
    const res = (await mcp.listResources()).resources.map((r) => r.uri).sort();
    expect(res).toEqual(["renda://docs/package", "renda://schema/v1"]);
    const schema = await mcp.readResource({ uri: "renda://schema/v1" });
    expect(JSON.parse((schema.contents[0] as any).text).title).toBe("Renda package");
    const prompts = (await mcp.listPrompts()).prompts.map((p) => p.name).sort();
    expect(prompts).toEqual(["make-turntable", "render-file"]);
  });
});

describe("renda_validate_package", () => {
  it("returns paths and fixes", async () => {
    const mcp = await connect(new RendaClient({ baseUrl: "https://x.test" }));
    const r: any = await mcp.callTool({ name: "renda_validate_package", arguments: { package: { scene: { root: "a.glb" }, render: { engine: "vray" } } } });
    expect(r.isError).toBe(true);
    const t = text(r);
    expect(t).toContain("render.engine");
    expect(t).toContain("blender-cycles");
  });

  it("explains cross-field rules (video needs animation)", async () => {
    const mcp = await connect(new RendaClient({ baseUrl: "https://x.test" }));
    const r: any = await mcp.callTool({ name: "renda_validate_package", arguments: { package: { scene: { root: "a.glb" }, output: { format: "mp4" } } } });
    expect(r.isError).toBe(true);
    expect(text(r)).toContain('needs an animation range');
    expect(text(r)).toContain('frames');
  });

  it("accepts a valid package and normalizes defaults", async () => {
    const mcp = await connect(new RendaClient({ baseUrl: "https://x.test" }));
    const r: any = await mcp.callTool({ name: "renda_validate_package", arguments: { package: { scene: { root: "a.glb" } } } });
    expect(r.isError).toBeFalsy();
    expect(text(r)).toContain('"resolution"');
  });

  it("validatePackage helper", () => {
    const v = validatePackage({});
    expect(v.valid).toBe(false);
  });
});

describe("starterPackage", () => {
  it("builds a valid turntable", () => {
    const p: any = starterPackage("./shoe.usdz", { turntable: true, seconds: 2, fps: 24 });
    expect(p.animation).toEqual({ frames: [1, 48], fps: 24, turntable: { revolutions: 1 } });
    expect(p.output.format).toBe("mp4");
    expect(validatePackage(p).valid).toBe(true);
  });
  it("marks three.js json and custom hdri", () => {
    const p: any = starterPackage("scene.json", { hdri: "https://h/sky.hdr", transparent: true });
    expect(p.assets.scene.format).toBe("threejs");
    expect(p.environment).toEqual({ hdri: "env", background: "transparent" });
    expect(validatePackage(p).valid).toBe(true);
  });
});

describe("account flow", () => {
  it("signup tells agent to ask the human; verify saves key", async () => {
    const f = fakeFetch(({ url }) => {
      if (url.endsWith("/auth/magic/start")) return { json: { ok: true } };
      if (url.endsWith("/auth/magic/verify")) return { json: { apiKey: "rnd_live_new", user: { id: "u", email: "a@b.co" }, org: { id: "o" } } };
      if (url.endsWith("/me")) return { json: { user: { id: "u" }, org: { id: "o" }, balance: { freeSecondsLeft: 1800 }, billing: { hasPaymentMethod: false } } };
    });
    const client = new RendaClient({ baseUrl: "https://x.test", fetch: f.fetch });
    const saved: string[] = [];
    const mcp = await connect(client, "local", (k) => { saved.push(k); });
    expect(text(await mcp.callTool({ name: "renda_signup", arguments: { email: "a@b.co" } }))).toContain("6-digit code");
    const v: any = await mcp.callTool({ name: "renda_verify", arguments: { email: "a@b.co", code: "123456" } });
    expect(text(v)).not.toContain("rnd_live_new");
    expect(saved).toEqual(["rnd_live_new"]);
    await mcp.callTool({ name: "renda_whoami", arguments: {} });
    expect(f.calls.at(-1)!.headers.authorization).toBe("Bearer rnd_live_new");
  });

  it("surfaces payment_required hints as tool errors", async () => {
    const f = fakeFetch(({ url }) => {
      if (url.endsWith("/jobs")) return { status: 402, json: { error: { code: "payment_required", message: "Out of free minutes", hint: "Call renda_billing_link" } } };
    });
    const mcp = await connect(new RendaClient({ baseUrl: "https://x.test", apiKey: "k", fetch: f.fetch }), "remote");
    const r: any = await mcp.callTool({ name: "renda_render", arguments: { file: "https://h/a.glb" } });
    expect(r.isError).toBe(true);
    expect(text(r)).toContain("renda_billing_link");
  });
});

describe("renda_bake", () => {
  it("submits a glb package with the bake block, or a plain conversion", async () => {
    const f = fakeFetch(({ method, url }) => {
      if (method === "POST" && url.endsWith("/jobs")) return { json: job({ status: "succeeded", progress: 1, outputs: [{ name: "baked.glb", url: "https://r2.test/b.glb", contentType: "model/gltf-binary", bytes: 900 }] }) };
    });
    const mcp = await connect(new RendaClient({ baseUrl: "https://x.test", apiKey: "k", fetch: f.fetch }));
    const r: any = await mcp.callTool({ name: "renda_bake", arguments: { file: "https://h/a.glb", mode: "ao", size: 1024, engine: "mitsuba" } });
    expect(r.isError).toBeFalsy();
    const pkg = f.calls.find((c) => c.method === "POST" && c.url.endsWith("/jobs"))!.body.package;
    expect(pkg.output).toMatchObject({ format: "glb", bake: { mode: "ao", size: 1024 } });
    expect(pkg.render.engine).toBe("mitsuba");
    await mcp.callTool({ name: "renda_bake", arguments: { file: "https://h/a.glb", plain: true } });
    const plain = f.calls.filter((c) => c.method === "POST" && c.url.endsWith("/jobs"))[1].body.package;
    expect(plain.output.format).toBe("glb");
    expect(plain.output.bake).toBeUndefined();
  });
});

describe("renda_render", () => {
  it("uploads local files from a package path, submits, and waits", async () => {
    const dir = await mkdtemp(join(tmpdir(), "renda-render-"));
    await writeFile(join(dir, "bracket.stl"), "solid x");
    await writeFile(join(dir, "p.renda.json"), JSON.stringify({ scene: { root: "./bracket.stl" }, render: { quality: "draft" } }));
    let polls = 0;
    const f = fakeFetch(({ method, url, body }) => {
      if (url.endsWith("/uploads")) return { json: { assetUri: `asset://${body.sha256}`, exists: false, uploadUrl: "https://r2.test/put" } };
      if (method === "PUT") return { text: "" };
      if (method === "POST" && url.endsWith("/jobs")) return { json: job({ status: "queued" }) };
      if (url.endsWith("/jobs/job_1")) return { json: job(++polls < 2 ? { status: "rendering", progress: 0.5 } : { status: "succeeded", progress: 1, billedSeconds: 12, outputs: [{ name: "beauty.png", url: "https://r2.test/o.png", contentType: "image/png", bytes: 5_000_000 }] }) };
    });
    const mcp = await connect(new RendaClient({ baseUrl: "https://x.test", apiKey: "k", fetch: f.fetch }));
    const r: any = await mcp.callTool({ name: "renda_render", arguments: { packagePath: join(dir, "p.renda.json") } });
    expect(r.isError).toBeFalsy();
    const submitted = f.calls.find((c) => c.method === "POST" && c.url.endsWith("/jobs"))!.body;
    expect(submitted.kind).toBe("scene");
    expect(submitted.package.scene.root).toMatch(/^asset:\/\/[0-9a-f]{64}$/);
    expect(text(r)).toContain("succeeded");
    expect(text(r)).toContain("renda_download");
  });

  it("rejects local paths in remote mode with a hint", async () => {
    const f = fakeFetch(() => undefined);
    const mcp = await connect(new RendaClient({ baseUrl: "https://x.test", apiKey: "k", fetch: f.fetch }), "remote");
    const r: any = await mcp.callTool({ name: "renda_render", arguments: { file: "./local.glb" } });
    expect(r.isError).toBe(true);
    expect(text(r)).toContain("asset://");
    expect(f.calls).toHaveLength(0);
  });

  it("returns invalid package issues without calling the API", async () => {
    const f = fakeFetch(() => undefined);
    const mcp = await connect(new RendaClient({ baseUrl: "https://x.test", apiKey: "k", fetch: f.fetch }));
    const r: any = await mcp.callTool({ name: "renda_render", arguments: { package: { scene: { root: "https://h/a.glb" }, output: { format: "jpg", transparent: true } } } });
    expect(r.isError).toBe(true);
    expect(text(r)).toContain("output.transparent");
    expect(f.calls).toHaveLength(0);
  });

  it("init_package writes a file", async () => {
    const dir = await mkdtemp(join(tmpdir(), "renda-init-"));
    const mcp = await connect(new RendaClient({ baseUrl: "https://x.test" }));
    const out = join(dir, "s.renda.json");
    await mcp.callTool({ name: "renda_init_package", arguments: { file: "./robot.fbx", writeTo: out, quality: "final" } });
    const pkg = JSON.parse(await readFile(out, "utf8"));
    expect(pkg.render.quality).toBe("final");
    expect(pkg.scene.root).toBe("scene");
  });
});

describe("renda_render_video", () => {
  it("submits a hyperframes job from a uri", async () => {
    const f = fakeFetch(({ method, url }) => {
      if (method === "POST" && url.endsWith("/jobs")) return { json: job({ kind: "hyperframes", status: "queued" }) };
    });
    const mcp = await connect(new RendaClient({ baseUrl: "https://x.test", apiKey: "k", fetch: f.fetch }), "remote");
    const r: any = await mcp.callTool({ name: "renda_render_video", arguments: { projectUri: "https://h/proj.zip", format: "webm", fps: 60, wait: false } });
    expect(r.isError).toBeFalsy();
    const body = f.calls[0].body;
    expect(body.kind).toBe("hyperframes");
    expect(body.package).toEqual({ project: "https://h/proj.zip", fps: 60, format: "webm" });
  });
});


describe("anonymous trial", () => {
  it("renda_render with no key starts a trial and surfaces the claim link", async () => {
    const f = fakeFetch(({ method, url }) => {
      if (url.endsWith("/auth/trial")) return { status: 201, json: { apiKey: "rnd_live_trialtrialtrialtrialtrial", claimUrl: "https://renda.test/claim?token=t", freeSecondsLeft: 300, expiresAt: "2026-10-26T00:00:00Z" } };
      if (method === "POST" && url.endsWith("/jobs")) return { json: job({ status: "succeeded", progress: 1, outputs: [] }) };
    });
    const client = new RendaClient({ baseUrl: "https://x.test", fetch: f.fetch });
    let saved: any;
    const mcp = await connect(client, "local", undefined, { onTrial: (t) => { saved = t; } });
    const r: any = await mcp.callTool({ name: "renda_render", arguments: { file: "https://h/a.glb" } });
    expect(r.isError).toBeFalsy();
    expect(r.content[0].text).toContain("https://renda.test/claim?token=t");
    expect(saved.apiKey).toBe("rnd_live_trialtrialtrialtrialtrial");
    const jobCall = f.calls.find((c) => c.method === "POST" && c.url.endsWith("/jobs"))!;
    expect(jobCall.headers.authorization).toBe("Bearer rnd_live_trialtrialtrialtrialtrial");
    const claim: any = await mcp.callTool({ name: "renda_claim", arguments: {} });
    expect(claim.content[0].text).toContain("claim?token=t");
  });

  it("listJobs accepts the {jobs: [...]} envelope", async () => {
    const f = fakeFetch(({ url }) => (url.includes("/jobs") ? { json: { jobs: [job()] } } : undefined));
    const client = new RendaClient({ baseUrl: "https://x.test", apiKey: "rnd_live_x", fetch: f.fetch });
    expect((await client.listJobs()).length).toBe(1);
  });
});
