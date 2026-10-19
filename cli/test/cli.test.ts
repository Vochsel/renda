import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { run, type Deps } from "../src/cli.js";
import { parseArgs } from "../src/args.js";
import { removeTomlTable, type Sys } from "../src/agents.js";

type Route = (r: { method: string; url: string; body: any; headers: Record<string, string> }) => { status?: number; json?: unknown } | undefined;

function fakeFetch(route: Route) {
  const calls: { method: string; url: string; body: any; headers: Record<string, string> }[] = [];
  const f = (async (input: any, init: any = {}) => {
    const url = String(input);
    const method = init.method ?? "GET";
    const headers = (init.headers ?? {}) as Record<string, string>;
    const body = typeof init.body === "string" ? JSON.parse(init.body) : init.body;
    calls.push({ method, url, body, headers });
    const r = route({ method, url, body, headers });
    if (!r) return new Response(JSON.stringify({ error: { code: "not_found", message: `no route ${method} ${url}` } }), { status: 404 });
    return new Response(r.json === undefined ? "" : JSON.stringify(r.json), { status: r.status ?? 200 });
  }) as typeof fetch;
  return { fetch: f, calls };
}

async function setup(route: Route, opts: { creds?: object; sys?: Partial<Sys> } = {}) {
  const dir = await mkdtemp(join(tmpdir(), "renda-cli-"));
  const home = join(dir, "home");
  await mkdir(home, { recursive: true });
  const credentialsFile = join(home, ".config", "renda", "credentials.json");
  if (opts.creds) {
    await mkdir(join(home, ".config", "renda"), { recursive: true });
    await writeFile(credentialsFile, JSON.stringify(opts.creds));
  }
  const out: string[] = [];
  const err: string[] = [];
  const ran: string[][] = [];
  const f = fakeFetch(route);
  const deps: Deps = {
    io: { stdout: (s) => out.push(s), stderr: (s) => err.push(s), isTTY: false },
    env: { RENDA_API_URL: "https://api.test" },
    fetch: f.fetch,
    credentialsFile,
    pollMs: 1,
    sys: { home: () => home, cwd: () => dir, which: () => null, run: (c, a) => { ran.push([c, ...a]); return { status: 0, stdout: "", stderr: "" }; }, ...opts.sys },
  };
  const json = () => JSON.parse(out.join(""));
  return { deps, dir, home, credentialsFile, out, err, json, calls: f.calls, ran };
}

const TRIAL = { apiKey: "rnd_live_trialtrialtrialtrialtrial", claimUrl: "https://api.test/claim?token=abc", freeSecondsLeft: 300, expiresAt: "2026-10-26T00:00:00Z" };
const job = (o: object = {}) => ({ id: "job_1", kind: "scene", status: "queued", progress: 0, billedSeconds: 0, costUsd: 0, cached: false, warnings: [], outputs: [], createdAt: "2026-10-19T00:00:00Z", ...o });

describe("args", () => {
  it("parses flags, aliases and --no-wait", () => {
    const p = parseArgs(["render", "a.glb", "--res", "640x480", "-o", "out", "--no-wait", "--json", "--agent", "cursor", "--agent", "codex"]);
    expect(p.positionals).toEqual(["render", "a.glb"]);
    expect(p.flags).toMatchObject({ res: "640x480", output: "out", wait: false, json: true, agent: "cursor,codex" });
  });
});

describe("exit codes + JSON contract", () => {
  it("usage error → 2", async () => {
    const t = await setup(() => undefined);
    expect(await run(["render"], t.deps)).toBe(2);
    expect(t.json()).toMatchObject({ status: "error", reason: "usage" });
  });

  it("402 → 4 with billing next step", async () => {
    const t = await setup(({ url }) => (url.endsWith("/me") ? { status: 402, json: { error: { code: "payment_required", message: "Out of minutes", hint: "add a card" } } } : undefined), { creds: { apiKey: "rnd_live_xxxxxxxxxxxxxxxxxxxxxxxx" } });
    expect(await run(["whoami"], t.deps)).toBe(4);
    expect(t.json().next[0]).toContain("renda billing");
  });

  it("never prints API keys in output", async () => {
    const t = await setup(({ url }) => (url.endsWith("/auth/trial") ? { status: 201, json: TRIAL } : undefined));
    expect(await run(["trial"], t.deps)).toBe(0);
    expect(t.out.join("")).not.toContain(TRIAL.apiKey);
    expect(t.json().claimUrl).toBe(TRIAL.claimUrl);
    const saved = JSON.parse(await readFile(t.credentialsFile, "utf8"));
    expect(saved).toMatchObject({ apiKey: TRIAL.apiKey, claimUrl: TRIAL.claimUrl, apiUrl: "https://api.test" });
  });
});

describe("render", () => {
  it("auto-starts a trial, uploads the file, waits, downloads, and returns the claim link", async () => {
    let polls = 0;
    const t = await setup(({ method, url }) => {
      if (url.endsWith("/auth/trial")) return { status: 201, json: TRIAL };
      if (url.endsWith("/uploads")) return { json: { assetUri: `asset://${"a".repeat(64)}`, uploadUrl: "https://r2.test/put", exists: false } };
      if (url === "https://r2.test/put") return { json: {} };
      if (method === "POST" && url.endsWith("/jobs")) return { status: 201, json: job() };
      if (url.endsWith("/jobs/job_1")) return { json: job(++polls < 2 ? { status: "rendering", progress: 0.5 } : { status: "succeeded", progress: 1, outputs: [{ name: "frame_0001.png", url: "https://r2.test/o.png", contentType: "image/png", bytes: 3 }] }) };
      if (url === "https://r2.test/o.png") return { json: "png" };
    });
    await writeFile(join(t.dir, "model.glb"), "glTF....");
    const code = await run(["render", join(t.dir, "model.glb"), "--res", "640x480", "-o", join(t.dir, "out")], t.deps);
    expect(code).toBe(0);
    const res = t.json();
    expect(res.job.status).toBe("succeeded");
    expect(res.next.join(" ")).toContain(TRIAL.claimUrl);
    expect(existsSync(join(t.dir, "out", "frame_0001.png"))).toBe(true);
    const submitted = t.calls.find((c) => c.method === "POST" && c.url.endsWith("/jobs"))!;
    expect(submitted.headers.authorization).toBe(`Bearer ${TRIAL.apiKey}`);
    expect(submitted.body.package.render.resolution).toEqual([640, 480]);
    expect(submitted.body.package.assets.scene.uri).toBe(`asset://${"a".repeat(64)}`);
  });

  it("--texture (repeatable) adds texture overrides and uploads their files", async () => {
    let n = 0;
    const t = await setup(({ method, url, body }) => {
      if (url.endsWith("/uploads")) return { json: { assetUri: `asset://${String(++n).repeat(64).slice(0, 64)}`, exists: true } };
      if (method === "POST" && url.endsWith("/jobs")) return { status: 201, json: job() };
      if (url.endsWith("/jobs/job_1")) return { json: job({ status: "succeeded", progress: 1, outputs: [] }) };
    }, { creds: { apiKey: "rnd_live_xxxxxxxxxxxxxxxxxxxxxxxx" } });
    await writeFile(join(t.dir, "model.glb"), "glTF....");
    await writeFile(join(t.dir, "oak.jpg"), "JPEG");
    await writeFile(join(t.dir, "screen.mp4"), "MP4");
    const code = await run(["render", join(t.dir, "model.glb"),
      "--texture", `material=/wood,oak/i,scale=4:${join(t.dir, "oak.jpg")}`,
      "--texture", `object=Display,slot=emission:${join(t.dir, "screen.mp4")}`, "-o", join(t.dir, "out")], t.deps);
    expect(code).toBe(0);
    const pkg = t.calls.find((c) => c.method === "POST" && c.url.endsWith("/jobs"))!.body.package;
    expect(pkg.scene.textureOverrides).toHaveLength(2);
    expect(pkg.scene.textureOverrides[0]).toMatchObject({ match: { material: "/wood,oak/i" }, uv: { scale: 4 } });
    expect(pkg.scene.textureOverrides[1]).toMatchObject({ match: { object: "Display", slot: "emission" } });
    for (const t of pkg.scene.textureOverrides) expect(t.source).toMatch(/^asset:\/\//);
  });

  it("--max-minutes refuses over-budget jobs before submitting", async () => {
    const t = await setup(({ method, url }) => {
      if (url.endsWith("/estimate")) return { json: { engine: "blender-cycles", hardware: "gpu-l4", estSeconds: [300, 900], estCostUsd: [0.2, 0.5] } };
      if (method === "POST" && url.endsWith("/jobs")) throw new Error("should not submit");
    }, { creds: { apiKey: "rnd_live_xxxxxxxxxxxxxxxxxxxxxxxx" } });
    const code = await run(["render", "https://example.com/a.glb", "--max-minutes", "5"], t.deps);
    expect(code).toBe(1);
    expect(t.json().reason).toBe("over_budget");
  });

  it("--local without uvx explains how to install uv", async () => {
    const t = await setup(() => undefined);
    expect(await run(["render", "a.glb", "--local"], t.deps)).toBe(1);
    expect(t.json()).toMatchObject({ reason: "uv_missing" });
    expect(t.json().hint).toContain("astral.sh/uv");
  });
});

describe("init + mcp add", () => {
  it("non-interactive init: trial, MCP for cursor + codex, bundled skills for codex", async () => {
    const t = await setup(({ url }) => (url.endsWith("/auth/trial") ? { status: 201, json: TRIAL } : undefined));
    const code = await run(["init", "--agent", "cursor,codex", "--yes"], t.deps);
    expect(code).toBe(0);
    const cursor = JSON.parse(await readFile(join(t.home, ".cursor", "mcp.json"), "utf8"));
    expect(cursor.mcpServers.renda).toEqual({ command: "npx", args: ["-y", "@rendahq/mcp"] });
    const toml = await readFile(join(t.home, ".codex", "config.toml"), "utf8");
    expect(toml).toContain("[mcp_servers.renda]");
    expect(await readFile(join(t.dir, "AGENTS.md"), "utf8")).toContain("<!-- renda -->");
    expect(t.json().claimUrl).toBe(TRIAL.claimUrl);
  });

  it("claude-code uses `claude mcp add` when the binary exists", async () => {
    const t = await setup(() => undefined, { creds: { apiKey: "rnd_live_xxxxxxxxxxxxxxxxxxxxxxxx" }, sys: { which: (c) => (c === "claude" ? "/usr/bin/claude" : null) } });
    expect(await run(["mcp", "add", "--agent", "claude-code"], t.deps)).toBe(0);
    expect(t.ran.some((r) => r.join(" ") === "claude mcp add --scope user renda -- npx -y @rendahq/mcp")).toBe(true);
  });

  it("mcp remove strips the codex table", () => {
    const toml = `[a]\nx = 1\n\n[mcp_servers.renda]\ncommand = "npx"\n\n[b]\ny = 2\n`;
    expect(removeTomlTable(toml, "mcp_servers.renda")).toBe(`[a]\nx = 1\n\n[b]\ny = 2\n`);
  });
});

describe("signup / verify", () => {
  it("remembers the email between signup and verify and clears a pending claim", async () => {
    const t = await setup(({ url, body }) => {
      if (url.endsWith("/auth/magic/start")) return { json: { ok: true } };
      if (url.endsWith("/auth/magic/verify") && body.email === "a@b.co" && body.code === "123456") return { json: { apiKey: "rnd_live_verifiedverifiedverified", user: { id: "u", email: "a@b.co" }, org: { id: "o" } } };
    }, { creds: { claimUrl: "https://old" } });
    expect(await run(["signup", "a@b.co"], t.deps)).toBe(0);
    t.out.length = 0;
    expect(await run(["verify", "123456"], t.deps)).toBe(0);
    const saved = JSON.parse(await readFile(t.credentialsFile, "utf8"));
    expect(saved.apiKey).toBe("rnd_live_verifiedverifiedverified");
    expect(saved.claimUrl).toBeUndefined();
  });
});
