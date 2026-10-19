/** Install/remove the Renda MCP server + skills into coding agents (Claude Code, Cursor, Codex). */
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { cp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { GITHUB_REPO, MCP_PACKAGE } from "./constants.js";

export const AGENTS = ["claude-code", "cursor", "codex"] as const;
export type Agent = (typeof AGENTS)[number];

export interface Sys {
  home: () => string;
  cwd: () => string;
  /** Path of an executable on PATH, or null. */
  which: (cmd: string) => string | null;
  run: (cmd: string, args: string[]) => { status: number | null; stdout: string; stderr: string };
}

export const realSys: Sys = {
  home: () => homedir(),
  cwd: () => process.cwd(),
  which: (cmd) => {
    const r = spawnSync(process.platform === "win32" ? "where" : "which", [cmd], { encoding: "utf8" });
    return r.status === 0 ? r.stdout.split("\n")[0].trim() || null : null;
  },
  run: (cmd, args) => {
    const r = spawnSync(cmd, args, { encoding: "utf8" });
    return { status: r.status, stdout: r.stdout ?? "", stderr: r.stderr ?? "" };
  },
};

export function parseAgents(v: string | undefined, sys: Sys): Agent[] {
  if (!v) return detectAgents(sys);
  if (v === "all") return [...AGENTS];
  const list = v.split(",").map((s) => s.trim()).filter(Boolean);
  for (const a of list) if (!(AGENTS as readonly string[]).includes(a)) throw new Error(`unknown agent "${a}" (use ${AGENTS.join(", ")} or all)`);
  return list as Agent[];
}

/** Agents that look installed; Claude Code if nothing is detected. */
export function detectAgents(sys: Sys): Agent[] {
  const found: Agent[] = [];
  if (sys.which("claude") || existsSync(join(sys.home(), ".claude"))) found.push("claude-code");
  if (existsSync(join(sys.home(), ".cursor"))) found.push("cursor");
  if (sys.which("codex") || existsSync(join(sys.home(), ".codex"))) found.push("codex");
  return found.length ? found : ["claude-code"];
}

export interface McpTarget {
  remote: boolean;
  apiUrl: string;
  /** Only used for remote (HTTP) installs, which need a bearer header. Never printed. */
  apiKey?: string;
}

const stdioServer = () => ({ command: "npx", args: ["-y", MCP_PACKAGE] });
const remoteServer = (t: McpTarget) => ({ type: "http", url: `${t.apiUrl}/api/mcp`, headers: { Authorization: `Bearer ${t.apiKey ?? ""}` } });

async function readJson(path: string): Promise<any> {
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch {
    return {};
  }
}

async function writeJson(path: string, data: unknown) {
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, JSON.stringify(data, null, 2) + "\n");
}

export interface StepResult {
  agent: Agent;
  action: string;
  where: string;
  ok: boolean;
  detail?: string;
}

/** Add the MCP server to one agent's config. */
export async function addMcp(agent: Agent, t: McpTarget, sys: Sys): Promise<StepResult> {
  if (agent === "claude-code") {
    if (sys.which("claude")) {
      sys.run("claude", ["mcp", "remove", "renda", "--scope", "user"]); // idempotent re-add
      const args = t.remote
        ? ["mcp", "add", "--scope", "user", "--transport", "http", "renda", `${t.apiUrl}/api/mcp`, "--header", `Authorization: Bearer ${t.apiKey ?? ""}`]
        : ["mcp", "add", "--scope", "user", "renda", "--", "npx", "-y", MCP_PACKAGE];
      const r = sys.run("claude", args);
      return { agent, action: "mcp add", where: "claude (user scope)", ok: r.status === 0, detail: r.status === 0 ? undefined : (r.stderr || r.stdout).trim() };
    }
    // No claude binary: project-scoped .mcp.json in the current directory.
    const path = join(sys.cwd(), ".mcp.json");
    const cfg = await readJson(path);
    cfg.mcpServers = { ...(cfg.mcpServers ?? {}), renda: t.remote ? remoteServer(t) : stdioServer() };
    await writeJson(path, cfg);
    return { agent, action: "mcp add", where: path, ok: true };
  }
  if (agent === "cursor") {
    const path = join(sys.home(), ".cursor", "mcp.json");
    const cfg = await readJson(path);
    cfg.mcpServers = { ...(cfg.mcpServers ?? {}), renda: t.remote ? { url: `${t.apiUrl}/api/mcp`, headers: remoteServer(t).headers } : stdioServer() };
    await writeJson(path, cfg);
    return { agent, action: "mcp add", where: path, ok: true };
  }
  // codex: ~/.codex/config.toml
  const path = join(sys.home(), ".codex", "config.toml");
  let toml = "";
  try {
    toml = await readFile(path, "utf8");
  } catch {}
  toml = removeTomlTable(toml, "mcp_servers.renda");
  const block = t.remote
    ? `[mcp_servers.renda]\nurl = "${t.apiUrl}/api/mcp"\nbearer_token_env_var = "RENDA_API_KEY"\n`
    : `[mcp_servers.renda]\ncommand = "npx"\nargs = ["-y", "${MCP_PACKAGE}"]\n`;
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, (toml.trimEnd() ? toml.trimEnd() + "\n\n" : "") + block);
  return { agent, action: "mcp add", where: path, ok: true, detail: t.remote ? "set RENDA_API_KEY in your environment" : undefined };
}

export async function removeMcp(agent: Agent, sys: Sys): Promise<StepResult> {
  if (agent === "claude-code") {
    if (sys.which("claude")) {
      const r = sys.run("claude", ["mcp", "remove", "renda", "--scope", "user"]);
      return { agent, action: "mcp remove", where: "claude (user scope)", ok: r.status === 0 };
    }
    const path = join(sys.cwd(), ".mcp.json");
    const cfg = await readJson(path);
    if (cfg.mcpServers) delete cfg.mcpServers.renda;
    if (existsSync(path)) await writeJson(path, cfg);
    return { agent, action: "mcp remove", where: path, ok: true };
  }
  if (agent === "cursor") {
    const path = join(sys.home(), ".cursor", "mcp.json");
    const cfg = await readJson(path);
    if (cfg.mcpServers) delete cfg.mcpServers.renda;
    if (existsSync(path)) await writeJson(path, cfg);
    return { agent, action: "mcp remove", where: path, ok: true };
  }
  const path = join(sys.home(), ".codex", "config.toml");
  if (existsSync(path)) await writeFile(path, removeTomlTable(await readFile(path, "utf8"), "mcp_servers.renda"));
  return { agent, action: "mcp remove", where: path, ok: true };
}

/** Drop `[name]` and its keys (up to the next table header). */
export function removeTomlTable(toml: string, name: string): string {
  const lines = toml.split("\n");
  const out: string[] = [];
  let skipping = false;
  for (const line of lines) {
    const header = line.match(/^\s*\[([^\]]+)\]\s*$/);
    if (header) skipping = header[1].trim() === name;
    if (!skipping) out.push(line);
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n");
}

/** Skills bundled into the npm package at build time (cli/skills/<name>/SKILL.md). */
export function bundledSkillsDir(): string {
  return fileURLToPath(new URL("../skills/", import.meta.url));
}

const SKILL_DIRS: Partial<Record<Agent, (home: string) => string>> = {
  "claude-code": (home) => join(home, ".claude", "skills"),
  codex: (home) => join(home, ".codex", "skills"),
};

/** Copy bundled skills into the agent's skills dir. Cursor has no skills dir: point at `npx skills add`. */
export async function installSkills(agent: Agent, sys: Sys, srcDir = bundledSkillsDir()): Promise<StepResult> {
  const target = SKILL_DIRS[agent];
  if (!target) return { agent, action: "skills", where: "-", ok: true, detail: `run: npx skills add ${GITHUB_REPO} -a ${agent}` };
  if (!existsSync(srcDir)) return { agent, action: "skills", where: "-", ok: false, detail: `bundled skills missing; run: npx skills add ${GITHUB_REPO}` };
  const dest = target(sys.home());
  const names: string[] = [];
  for (const name of await readdir(srcDir)) {
    if (!existsSync(join(srcDir, name, "SKILL.md"))) continue;
    await rm(join(dest, name), { recursive: true, force: true });
    await mkdir(dest, { recursive: true });
    await cp(join(srcDir, name), join(dest, name), { recursive: true });
    names.push(name);
  }
  return { agent, action: "skills", where: dest, ok: names.length > 0, detail: names.join(", ") };
}

export const AGENTS_SNIPPET_MARKER = "<!-- renda -->";

export function agentsSnippet(): string {
  return `${AGENTS_SNIPPET_MARKER}
## 3D rendering (Renda)
Render 3D files (.blend, .usdz, .glb, three.js JSON, .fbx, .obj, .stl, Gaussian splats .ply/.splat/.spz …) and videos with Renda:
the \`renda\` MCP tools, or \`npx ${MCP_PACKAGE.replace("/mcp", "/cli")} render <file>\` / \`uvx renda render <file> --cloud\`.
Draft first (\`--quality draft\`), \`renda estimate\` before big jobs, share trial claim links with the user.
`;
}

/** Append the snippet to AGENTS.md (or CLAUDE.md when only that exists). Idempotent. */
export async function writeAgentsSnippet(sys: Sys): Promise<StepResult> {
  const claude = join(sys.cwd(), "CLAUDE.md");
  const agents = join(sys.cwd(), "AGENTS.md");
  const path = !existsSync(agents) && existsSync(claude) ? claude : agents;
  let text = "";
  try {
    text = await readFile(path, "utf8");
  } catch {}
  if (text.includes(AGENTS_SNIPPET_MARKER)) return { agent: "claude-code", action: "agents.md", where: path, ok: true, detail: "already present" };
  await writeFile(path, (text.trimEnd() ? text.trimEnd() + "\n\n" : "") + agentsSnippet());
  return { agent: "claude-code", action: "agents.md", where: path, ok: true };
}
