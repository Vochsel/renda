import { spawnSync } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline/promises";
import {
  RendaApiError, RendaClient, TERMINAL_STATUSES, clearCredentials, credentialsPath, loadCredentials, saveCredentials,
  starterPackage, uploadBytes, uploadLocalPaths, validatePackage, zipDirectory, type Credentials, type Job,
  hyperframesQuality, parseVarFlags, resolveVideoVariables,
} from "@rendahq/mcp";
import { parseTextureFlag } from "@rendahq/spec";
import { flattenFile } from "@rendahq/spec/node";
import { bool, num, parseArgs, parseFrames, parseRes, str, UsageError, type Parsed } from "./args.js";
import { addMcp, installSkills, parseAgents, realSys, removeMcp, writeAgentsSnippet, type StepResult, type Sys } from "./agents.js";
import { CLI_PACKAGE, DEFAULT_API_URL, MCP_PACKAGE, UV_INSTALL_HINT, VERSION } from "./constants.js";
import { CliError, EXIT, Out, processIo, type Io } from "./output.js";

export interface Deps {
  io: Io;
  env: Record<string, string | undefined>;
  fetch?: typeof fetch;
  sys: Sys;
  credentialsFile: string;
  /** Interactive yes/no or choice; undefined in non-TTY. */
  ask?: (question: string) => Promise<string>;
  /** Start the stdio MCP server (lazy import so tests don't load the SDK transport). */
  serveMcp?: () => Promise<void>;
  pollMs?: number;
}

export function defaultDeps(): Deps {
  return {
    io: processIo,
    env: process.env,
    sys: realSys,
    credentialsFile: credentialsPath(),
    ask: process.stdin.isTTY && process.stdout.isTTY
      ? async (q) => {
          const rl = createInterface({ input: process.stdin, output: process.stderr });
          try {
            return (await rl.question(q)).trim();
          } finally {
            rl.close();
          }
        }
      : undefined,
    serveMcp: async () => {
      const { serveStdio } = await import("@rendahq/mcp/stdio");
      await serveStdio();
    },
  };
}

const HELP = `renda — render 3D files and scenes in the cloud (Node CLI: ${CLI_PACKAGE})

Setup
  renda init [--agent claude-code|cursor|codex|all] [--yes] [--trial]   trial or sign in, then MCP + skills for your agent
  renda mcp                                  run the MCP server (stdio)
  renda mcp add|remove [--agent …] [--remote]

Account
  renda trial                                anonymous trial: 5 free render-minutes, no signup
  renda claim                                print the link that keeps a trial workspace
  renda signup <email> · renda verify <code> email-code sign in (agent friendly)
  renda login                                browser sign in (device code)
  renda logout · whoami · balance · usage · billing

Render
  renda render <file|pkg.renda.json> [--res WxH] [--quality draft|standard|final] [--engine …]
  renda bake <file|pkg.renda.json> [--mode unlit|ao|lightmap] [--size 2048] [--engine auto|blender-cycles|mitsuba|threejs]
               bake lighting into the textures and download a .glb   (--glb: plain conversion, no bake)
               [--frames a-b] [--format png|mp4|…] [-o dir] [--no-wait] [--max-minutes N] [--local]
               [--fast]  fast mode: wider fan-out on faster GPUs, costs a bit more (= --priority rush)
               [--texture "material=/Wood.*/i,slot=baseColor,scale=4:./oak.jpg"]  (repeatable; any image or video)
  renda splat <file|pkg> [--preset fast|standard|high] [--format spz|ply|splat] [--views N] [--steps N]
        [--coverage auto|sphere|hemisphere|orbit] [--fan-out | --no-fan-out]
               [--engine …] [--dataset] [--local]   bake any 3D scene into a Gaussian splat, lighting included
  renda estimate <file|pkg>                  time + cost before rendering
  renda jobs [id] [--cancel] [--download dir]
  renda validate <pkg.renda.json>            check a package against the spec
  renda flatten <pkg.renda.json> [--provenance] [-o out.json]   resolve extends / package references
  renda new <file>                           write a starter .renda.json
  renda video <hyperframes-dir>              render a HyperFrames composition (CPU, cloud)
               [--var key=value ...] [--vars '{json}'] [--quality draft|looks|delivery] [--format mp4|webm|mov|gif]
               [--fps N] [--composition file.html] [--name stem] [--duration secs] [--no-wait]
               --var values: text · local .mp4/.png (uploaded) · job:<id> (a finished render's clip)
               · a local 3D file (.glb/.blend/.step…: a turntable is rendered first, then dropped in)

Flags: --json (default when not a TTY) · exit codes 0 ok, 1 error, 2 usage, 3 auth needed, 4 payment required
Local rendering (Blender/Mitsuba on your machine): uvx renda render <file>
Docs for agents: ${DEFAULT_API_URL}/llms.txt`;

export async function run(argv: string[], deps: Deps = defaultDeps()): Promise<number> {
  let parsed: Parsed;
  try {
    parsed = parseArgs(argv);
  } catch (e) {
    return new Out(deps.io, !deps.io.isTTY).fail(e);
  }
  const json = bool(parsed.flags, "json") || !deps.io.isTTY;
  const out = new Out(deps.io, json);
  const [cmd, ...rest] = parsed.positionals;
  if (bool(parsed.flags, "version")) {
    out.ok({ version: VERSION }, VERSION);
    return EXIT.ok;
  }
  if (!cmd || bool(parsed.flags, "help") || cmd === "help") {
    out.ok({ usage: HELP }, HELP);
    return cmd || bool(parsed.flags, "help") ? EXIT.ok : EXIT.usage;
  }
  const ctx = new Ctx(deps, out, parsed.flags, parsed.lists ?? {});
  try {
    const handler = COMMANDS[cmd];
    if (!handler) throw new UsageError(`unknown command "${cmd}"`);
    await handler(ctx, rest);
    return EXIT.ok;
  } catch (e) {
    return out.fail(e);
  }
}

class Ctx {
  creds: Credentials = {};
  loaded = false;
  constructor(public deps: Deps, public out: Out, public flags: Parsed["flags"], public lists: Record<string, string[]> = {}) {}

  async load() {
    if (!this.loaded) {
      this.creds = await loadCredentials(this.deps.credentialsFile);
      this.loaded = true;
    }
    return this.creds;
  }
  get apiUrl() {
    return (this.deps.env.RENDA_API_URL || this.creds.apiUrl || DEFAULT_API_URL).replace(/\/+$/, "");
  }
  async client(): Promise<RendaClient> {
    await this.load();
    return new RendaClient({ baseUrl: this.apiUrl, apiKey: this.deps.env.RENDA_API_KEY || this.creds.apiKey, fetch: this.deps.fetch });
  }
  async save(c: Credentials) {
    await saveCredentials({ apiUrl: this.apiUrl, ...c }, this.deps.credentialsFile);
    this.creds = { ...this.creds, ...c };
  }
  /** An authenticated client; with `autoTrial`, start a trial when there's no key. */
  async authed(autoTrial = false): Promise<{ client: RendaClient; trial?: { claimUrl: string } }> {
    const client = await this.client();
    if (client.apiKey) return { client };
    if (!autoTrial) {
      throw new CliError("auth_required", "Not signed in.", EXIT.auth, "Start a free trial or sign in.", [
        "renda trial   # no signup, 5 free render-minutes", "renda login   # browser", "renda signup <email>   # emailed 6-digit code",
      ]);
    }
    const t = await client.trial();
    client.apiKey = t.apiKey;
    await this.save({ apiKey: t.apiKey, claimUrl: t.claimUrl, trialExpiresAt: t.expiresAt });
    this.out.info(`No account configured: started a free trial (${Math.round(t.freeSecondsLeft / 60)} render-minutes). Keep it: ${t.claimUrl}`);
    return { client, trial: { claimUrl: t.claimUrl } };
  }
}

type Handler = (ctx: Ctx, args: string[]) => Promise<void>;

const minutes = (s: number) => `${(s / 60).toFixed(1)} min`;

/** `--fast` is fast mode, an alias for `--priority rush`. */
function priorityOf(ctx: Ctx): "economy" | "standard" | "rush" {
  if (bool(ctx.flags, "fast")) return "rush";
  return (str(ctx.flags, "priority") as "economy" | "standard" | "rush" | undefined) ?? "standard";
}

const COMMANDS: Record<string, Handler> = {
  // ---------------- setup ----------------
  async init(ctx) {
    const { deps, out, flags } = ctx;
    await ctx.load();
    const steps: StepResult[] = [];
    const next: string[] = [];
    let claimUrl: string | undefined = ctx.creds.claimUrl;

    // 1. auth
    const client = await ctx.client();
    if (!client.apiKey) {
      let choice = bool(flags, "trial") || !deps.ask ? "t" : "";
      if (!choice && deps.ask) {
        const a = await deps.ask("No Renda account on this machine. [t]rial (no signup) / [l]ogin in browser / [s]kip? [t] ");
        choice = (a || "t").toLowerCase()[0];
      }
      if (choice === "t") {
        const t = await client.trial();
        await ctx.save({ apiKey: t.apiKey, claimUrl: t.claimUrl, trialExpiresAt: t.expiresAt });
        claimUrl = t.claimUrl;
        steps.push({ agent: "claude-code", action: "auth", where: ctx.deps.credentialsFile, ok: true, detail: `trial, ${minutes(t.freeSecondsLeft)} free` });
      } else if (choice === "l") {
        await COMMANDS.login(ctx, []);
      }
    } else {
      steps.push({ agent: "claude-code", action: "auth", where: ctx.deps.credentialsFile, ok: true, detail: "already signed in" });
    }

    // 2. MCP + skills per agent
    const agents = parseAgents(str(flags, "agent"), deps.sys);
    const remote = bool(flags, "remote");
    for (const agent of agents) {
      steps.push(await addMcp(agent, { remote, apiUrl: ctx.apiUrl, apiKey: ctx.creds.apiKey }, deps.sys));
      steps.push(await installSkills(agent, deps.sys));
    }

    // 3. AGENTS.md snippet only with consent
    let snippet = bool(flags, "yes");
    if (!snippet && deps.ask) snippet = /^y/i.test(await deps.ask("Add a short Renda note to AGENTS.md in this directory? [y/N] "));
    if (snippet) steps.push(await writeAgentsSnippet(deps.sys));

    if (claimUrl) next.push(`Give the user this link to keep the trial workspace: ${claimUrl}`);
    next.push("Restart your agent so it loads the renda MCP server.", "renda render <file> --quality draft");
    out.ok({ steps, agents, claimUrl: claimUrl ?? null }, () => [
      "Renda is set up.",
      ...steps.map((s) => `  ${s.ok ? "✓" : "✗"} ${s.agent.padEnd(11)} ${s.action.padEnd(10)} ${s.where}${s.detail ? `  (${s.detail})` : ""}`),
    ].join("\n"), next);
  },

  async mcp(ctx, args) {
    const sub = args[0];
    if (!sub || sub === "serve") {
      if (!ctx.deps.serveMcp) throw new CliError("unsupported", "MCP server unavailable in this context.");
      return ctx.deps.serveMcp();
    }
    const agents = parseAgents(str(ctx.flags, "agent"), ctx.deps.sys);
    await ctx.load();
    const steps: StepResult[] = [];
    if (sub === "add") {
      const remote = bool(ctx.flags, "remote");
      if (remote && !(ctx.deps.env.RENDA_API_KEY || ctx.creds.apiKey)) {
        throw new CliError("auth_required", "Remote MCP needs an API key.", EXIT.auth, "Run `renda trial` or `renda login` first, or omit --remote to use the local stdio server.");
      }
      for (const a of agents) steps.push(await addMcp(a, { remote, apiUrl: ctx.apiUrl, apiKey: ctx.deps.env.RENDA_API_KEY || ctx.creds.apiKey }, ctx.deps.sys));
    } else if (sub === "remove") {
      for (const a of agents) steps.push(await removeMcp(a, ctx.deps.sys));
    } else {
      throw new UsageError(`unknown subcommand "mcp ${sub}" (use add, remove, or no subcommand to serve)`);
    }
    ctx.out.ok({ steps }, steps.map((s) => `${s.ok ? "✓" : "✗"} ${s.agent} ${s.action} → ${s.where}${s.detail ? ` (${s.detail})` : ""}`).join("\n"),
      sub === "add" ? ["Restart your agent to load the renda MCP server."] : []);
  },

  // ---------------- account ----------------
  async trial(ctx) {
    const client = await ctx.client();
    if (client.apiKey && !bool(ctx.flags, "force")) {
      ctx.out.ok({ alreadySignedIn: true, claimUrl: ctx.creds.claimUrl ?? null }, "Already signed in on this machine (use --force for a fresh trial).", ["renda whoami"]);
      return;
    }
    client.apiKey = undefined;
    const t = await client.trial();
    await ctx.save({ apiKey: t.apiKey, claimUrl: t.claimUrl, trialExpiresAt: t.expiresAt });
    ctx.out.ok({ freeSecondsLeft: t.freeSecondsLeft, expiresAt: t.expiresAt, claimUrl: t.claimUrl, limits: t.limits ?? null, credentials: ctx.deps.credentialsFile },
      `Trial started: ${minutes(t.freeSecondsLeft)} of free rendering (max ${t.limits?.maxDimension ?? "?"}px), expires ${t.expiresAt.slice(0, 10)} unless claimed.\nKeep it: ${t.claimUrl}`,
      [`Give the user this link to keep the workspace: ${t.claimUrl}`, "renda render <file> --quality draft"]);
  },

  async claim(ctx) {
    await ctx.load();
    if (!ctx.creds.claimUrl) {
      ctx.out.ok({ claimUrl: null }, "No pending trial claim on this machine.", ["renda whoami"]);
      return;
    }
    ctx.out.ok({ claimUrl: ctx.creds.claimUrl, expiresAt: ctx.creds.trialExpiresAt ?? null },
      `Open this link in a browser to keep the trial workspace:\n${ctx.creds.claimUrl}`, ["Agents: give this link to the human; don't open it yourself."]);
  },

  async signup(ctx, [email]) {
    if (!email) throw new UsageError("usage: renda signup <email>");
    const client = await ctx.client();
    await client.magicStart(email);
    await ctx.save({});
    await writeFile(pendingEmailPath(ctx), email);
    ctx.out.ok({ email }, `Sent a 6-digit code to ${email}.`, [`Ask the user for the code from their email, then: renda verify <code>`]);
  },

  async verify(ctx, [code]) {
    if (!code || !/^\d{6}$/.test(code)) throw new UsageError("usage: renda verify <6-digit code>");
    const email = str(ctx.flags, "email") ?? (await readFile(pendingEmailPath(ctx), "utf8").catch(() => "")).trim();
    if (!email) throw new UsageError("No pending signup. Run `renda signup <email>` first (or pass --email).");
    const client = await ctx.client();
    const res = await client.magicVerify(email, code, `renda cli (${basename(ctx.deps.sys.home())})`);
    await ctx.save({ apiKey: res.apiKey });
    await clearCredentials(["claimUrl", "trialExpiresAt"], ctx.deps.credentialsFile);
    ctx.out.ok({ user: res.user, org: res.org, credentials: ctx.deps.credentialsFile }, `Signed in as ${res.user.email}.`, ["renda whoami", "renda init   # MCP + skills for your agent"]);
  },

  async login(ctx) {
    const client = await ctx.client();
    const d = await client.deviceStart();
    ctx.out.info(`Open ${d.verificationUriComplete ?? d.verificationUri} and confirm the code ${d.userCode}`);
    if (ctx.out.json) {
      // stdout stays a single final JSON object; the pending link goes to stderr so agents can relay it now.
      ctx.deps.io.stderr(JSON.stringify({ status: "pending", verificationUri: d.verificationUriComplete ?? d.verificationUri, userCode: d.userCode }) + "\n");
    }
    const deadline = Date.now() + d.expiresIn * 1000;
    const pollMs = ctx.deps.pollMs ?? d.interval * 1000;
    for (;;) {
      await new Promise((r) => setTimeout(r, pollMs));
      try {
        const t = await client.deviceToken(d.deviceCode);
        await ctx.save({ apiKey: t.apiKey });
        await clearCredentials(["claimUrl", "trialExpiresAt"], ctx.deps.credentialsFile);
        ctx.out.ok({ credentials: ctx.deps.credentialsFile }, "Signed in.", ["renda whoami"]);
        return;
      } catch (e) {
        if (e instanceof RendaApiError && (e.status === 428 || e.code === "authorization_pending")) {
          if (Date.now() > deadline) throw new CliError("expired_token", "The login code expired.", EXIT.auth, "Run `renda login` again.");
          continue;
        }
        throw e;
      }
    }
  },

  async logout(ctx) {
    await clearCredentials(["apiKey", "claimUrl", "trialExpiresAt"], ctx.deps.credentialsFile);
    ctx.out.ok({ credentials: ctx.deps.credentialsFile }, "Signed out (API key removed from this machine).");
  },

  async whoami(ctx) {
    const { client } = await ctx.authed();
    const me = await client.me();
    const trial = (me as any).trial;
    const next = trial?.active ? [`renda claim   # keep this trial workspace`] : [];
    if (!me.billing.hasPaymentMethod && me.balance.freeSecondsLeft <= 0) next.push("renda billing   # add a card");
    ctx.out.ok({ ...me }, [
      `${me.user?.email ?? "anonymous trial"} · ${me.org.name ?? me.org.id}`,
      `free left: ${minutes(me.balance.freeSecondsLeft)} · card: ${me.billing.hasPaymentMethod ? "yes" : "no"}${trial?.active ? ` · trial until ${String(trial.expiresAt).slice(0, 10)}` : ""}`,
    ].join("\n"), next);
  },

  async balance(ctx) {
    const { client } = await ctx.authed();
    const me = await client.me();
    ctx.out.ok({ freeSecondsLeft: me.balance.freeSecondsLeft, hasPaymentMethod: me.billing.hasPaymentMethod, trial: (me as any).trial ?? null },
      `${minutes(me.balance.freeSecondsLeft)} free · ${me.billing.hasPaymentMethod ? "card on file, usage billed per second" : "no card"}`,
      me.billing.hasPaymentMethod || me.balance.freeSecondsLeft > 0 ? [] : ["renda billing"]);
  },

  async usage(ctx) {
    const { client } = await ctx.authed();
    const u = await client.usage({ ...(str(ctx.flags, "from") ? { from: str(ctx.flags, "from")! } : {}), ...(str(ctx.flags, "to") ? { to: str(ctx.flags, "to")! } : {}) });
    ctx.out.ok({ ...u }, JSON.stringify(u, null, 2));
  },

  async billing(ctx) {
    const { client } = await ctx.authed();
    const me = await client.me();
    const { url } = me.billing.hasPaymentMethod ? await client.billingPortal() : await client.billingCheckout();
    ctx.out.ok({ url, hasPaymentMethod: me.billing.hasPaymentMethod }, url, ["Give this link to the account owner to open in a browser."]);
  },

  // ---------------- render ----------------
  async render(ctx, [file]) {
    if (!file) throw new UsageError("usage: renda render <file|package.renda.json>");
    if (bool(ctx.flags, "local")) return runLocal(ctx, file);
    const { pkg, baseDir } = await buildPackage(ctx, file);
    await submitAndFinish(ctx, pkg, baseDir);
  },

  async bake(ctx, [file]) {
    if (!file) {
      throw new UsageError("usage: renda bake <file|package.renda.json> [--mode unlit|ao|lightmap] [--size 2048] [--engine auto|blender-cycles|mitsuba|threejs] [--glb]");
    }
    if (bool(ctx.flags, "local")) return runLocal(ctx, file, "bake");
    const { pkg, baseDir } = await buildPackage(ctx, file, (p) => applyBake(p, ctx.flags));
    await submitAndFinish(ctx, pkg, baseDir);
  },

  // ---------------- splat bake ----------------
  async splat(ctx, [file]) {
    if (!file) throw new UsageError("usage: renda splat <file|package.renda.json> [--preset fast|standard|high] [--format ply|spz|splat]");
    if (bool(ctx.flags, "local")) return runLocal(ctx, file, "splat");
    const fmt = str(ctx.flags, "format") ?? "spz";
    if (!["ply", "spz", "splat"].includes(fmt)) throw new UsageError("--format must be ply, spz or splat");
    const { format: _fmt, engine: _engine, ...rest } = ctx.flags;
    const { pkg, baseDir } = await buildPackage(ctx, file, (p) => {
      const sb: Record<string, unknown> = { ...(p.output?.splatBake ?? {}) };
      sb.preset = str(ctx.flags, "preset") ?? sb.preset ?? "standard";
      for (const [flag, key] of [["views", "views"], ["steps", "steps"], ["sh-degree", "shDegree"]] as const) {
        const v = num(ctx.flags, flag);
        if (v !== undefined) sb[key] = v;
      }
      if (str(ctx.flags, "engine")) sb.engine = str(ctx.flags, "engine"); // the engine that renders the training views
      if (bool(ctx.flags, "dataset")) sb.dataset = true;
      if (str(ctx.flags, "coverage")) sb.coverage = str(ctx.flags, "coverage");
      if (bool(ctx.flags, "fan-out")) sb.fanOut = true;
      if (bool(ctx.flags, "no-fan-out")) sb.fanOut = false;
      p.output = { format: "splat", splatFormat: fmt, splatBake: sb };
      delete p.animation;
    }, rest);
    await submitAndFinish(ctx, pkg, baseDir);
  },

  async estimate(ctx, [file]) {
    if (!file) throw new UsageError("usage: renda estimate <file|package.renda.json>");
    const { pkg, baseDir } = await buildPackage(ctx, file);
    const { client } = await ctx.authed(true);
    const { package: uploadedPkg } = await uploadLocalPaths(client, pkg as any, baseDir);
    const est: any = await client.estimate(uploadedPkg as any, "scene", priorityOf(ctx));
    const [lo, hi] = est.estCostUsd ?? [0, 0];
    const f = est.priorities?.rush, st = est.priorities?.standard;
    const compare = f && st
      ? `\nfast (--fast): $${f.estCostUsd[1].toFixed(3)} in ~${minutes(f.estWallSeconds[1])}  vs  standard: $${st.estCostUsd[1].toFixed(3)} in ~${minutes(st.estWallSeconds[1])} (upper estimates)`
      : "";
    ctx.out.ok({ estimate: est }, `${est.engine ?? "?"} on ${est.hardware ?? "?"}: ~${minutes(est.estSeconds?.[0] ?? 0)}–${minutes(est.estSeconds?.[1] ?? 0)}, $${lo.toFixed(3)}–$${hi.toFixed(3)}${compare}`,
      [`renda render ${file}${hi > 0.5 ? " --max-minutes " + Math.ceil((est.estSeconds?.[1] ?? 60) / 60) : ""}`]);
  },

  async jobs(ctx, [id]) {
    const { client } = await ctx.authed();
    if (!id) {
      const list = await client.listJobs({ limit: num(ctx.flags, "limit") ?? 20 });
      ctx.out.ok({ jobs: list }, list.map((j) => `${j.id}  ${j.status.padEnd(9)} ${(j.engine ?? "").padEnd(16)} ${(j.billedSeconds ?? 0).toFixed(1)}s  ${j.createdAt}`).join("\n") || "No jobs yet.");
      return;
    }
    let job = bool(ctx.flags, "cancel") ? await client.cancelJob(id) : await client.getJob(id);
    if (ctx.flags.wait === true && !TERMINAL_STATUSES.has(job.status)) job = await waitJob(ctx, client, job);
    const dl = str(ctx.flags, "download");
    if (dl) {
      const files = await download(ctx, job, dl);
      ctx.out.ok({ job, files }, files.join("\n"));
      return;
    }
    ctx.out.ok({ job }, describeJob(job), job.status === "failed" ? ["Check `error`; fix the package and render again."] : []);
  },

  async validate(ctx, [file]) {
    if (!file) throw new UsageError("usage: renda validate <package.renda.json>");
    const raw = (await flattenFile(file)).package;
    const r = validatePackage(raw);
    if (!r.valid) throw new CliError("invalid_package", `${file} is not a valid Renda package.`, EXIT.error, "Fix the fields listed in `issues`.", [], { issues: r.issues });
    ctx.out.ok({ valid: true, package: r.package }, `${file} is valid.`);
  },

  async flatten(ctx, [file]) {
    if (!file) throw new UsageError("usage: renda flatten <package.renda.json> [--provenance] [-o out.json]");
    const r = await flattenFile(file);
    const v = validatePackage(r.package);
    const dest = str(ctx.flags, "output");
    if (dest) await writeFile(dest, JSON.stringify(r.package, null, 2) + "\n");
    const data: Record<string, unknown> = { package: r.package, layers: r.layers };
    if (bool(ctx.flags, "provenance")) data.provenance = r.provenance;
    if (!v.valid) data.issues = v.issues;
    if (dest) data.path = dest;
    const human = () => {
      let s = JSON.stringify(r.package, null, 2);
      if (bool(ctx.flags, "provenance")) {
        const w = Math.max(0, ...Object.keys(r.provenance).map((k) => k.length));
        s += "\n\n" + Object.entries(r.provenance).map(([k, l]) => `${k.padEnd(w)}  ← ${l}`).join("\n");
      }
      return s + (v.valid ? "" : `\n\ninvalid: ${JSON.stringify(v.issues)}`);
    };
    if (!v.valid) throw new CliError("invalid_package", "The composed package is invalid.", EXIT.error, "Fix the fields listed in `issues` in the layer named by provenance.", [], data);
    ctx.out.ok(data, human, [`renda render ${dest ?? file}`]);
  },

  async new(ctx, [file]) {
    if (!file) throw new UsageError("usage: renda new <scene file>");
    const pkg = starterPackage(`./${basename(file)}`, packageOverrides(ctx.flags));
    const dest = str(ctx.flags, "output") ?? join(dirname(file), basename(file).replace(/\.[^.]+$/, "") + ".renda.json");
    await writeFile(dest, JSON.stringify({ $schema: "https://renda.vochsel.com/api/v1/schema.json", ...pkg }, null, 2) + "\n");
    ctx.out.ok({ path: dest, package: pkg }, `wrote ${dest}`, [`renda render ${dest}`]);
  },

  async video(ctx, [dir]) {
    if (!dir) throw new UsageError("usage: renda video <hyperframes project dir> [--var key=value ...]");
    let vars: Record<string, unknown>;
    let quality: string | undefined;
    try {
      vars = parseVarFlags(ctx.lists.var, str(ctx.flags, "vars"));
      quality = hyperframesQuality(str(ctx.flags, "quality"));
    } catch (e) {
      throw new UsageError((e as Error).message);
    }
    const { client, trial } = await ctx.authed(true);
    const zip = await zipDirectory(resolve(dir));
    const up = await uploadBytes(client, `${basename(resolve(dir))}.zip`, zip, "application/zip");
    const pkg: Record<string, unknown> = { project: up.assetUri };
    for (const k of ["composition", "format", "name"]) if (str(ctx.flags, k)) pkg[k] = str(ctx.flags, k);
    if (quality) pkg.quality = quality;
    for (const k of ["width", "height", "fps"]) if (num(ctx.flags, k) !== undefined) pkg[k] = num(ctx.flags, k);
    if (num(ctx.flags, "duration") !== undefined) pkg.durationSec = num(ctx.flags, "duration");
    if (Object.keys(vars).length) {
      // job:<id> → that render's clip; local 3D file → turntable rendered first; local media → uploaded
      const resolved = await resolveVideoVariables(client, vars, {
        baseDir: ctx.deps.sys.cwd(), local: true, waitJob: (j) => waitJob(ctx, client, j),
      });
      for (const j of resolved.jobs) ctx.out.info(`${j.variable} ← ${j.jobId}`);
      pkg.variables = resolved.variables;
    }
    let job = await client.createJob({ package: pkg as any, kind: "hyperframes" });
    if (ctx.flags.wait !== false) job = await waitJob(ctx, client, job);
    await finish(ctx, job, trial?.claimUrl);
  },
};

// alias so docs that say `init-package` also work
COMMANDS["init-package"] = COMMANDS.new;

function pendingEmailPath(ctx: Ctx) {
  return join(dirname(ctx.deps.credentialsFile), "pending-email");
}

function packageOverrides(flags: Parsed["flags"]) {
  const o: Record<string, unknown> = {};
  if (str(flags, "engine")) o.engine = str(flags, "engine");
  if (str(flags, "res")) o.resolution = parseRes(str(flags, "res")!);
  o.quality = str(flags, "quality") ?? "standard";
  if (str(flags, "format")) o.format = str(flags, "format");
  if (str(flags, "hdri")) o.hdri = str(flags, "hdri");
  if (bool(flags, "transparent")) o.transparent = true;
  if (bool(flags, "turntable")) o.turntable = true;
  return o;
}

/** Upload local files, check the budget, submit, wait and download (shared by render, bake and splat). */
async function submitAndFinish(ctx: Ctx, pkg: any, baseDir: string) {
  const { client, trial } = await ctx.authed(true);
  const maxMinutes = num(ctx.flags, "max-minutes");
  const { package: uploadedPkg, uploaded } = await uploadLocalPaths(client, pkg as any, baseDir);
  if (uploaded.length) ctx.out.info(`uploaded ${uploaded.length} file(s) (${uploaded.filter((u) => u.deduped).length} already on server)`);
  if (maxMinutes !== undefined) {
    const est = await client.estimate(uploadedPkg as any);
    const hi = (est as any).estSeconds?.[1] ?? 0;
    if (hi / 60 > maxMinutes) {
      throw new CliError("over_budget", `Estimated up to ${minutes(hi)}, above --max-minutes ${maxMinutes}.`, EXIT.error,
        "Lower --res/--quality/--frames, or raise --max-minutes.", [], { estimate: est });
    }
  }
  let job = await client.createJob({ package: uploadedPkg as any, priority: priorityOf(ctx) });
  ctx.out.info(`job ${job.id} ${job.status}`);
  if (ctx.flags.wait !== false) job = await waitJob(ctx, client, job);
  await finish(ctx, job, trial?.claimUrl);
}

/** Bare scene file → starter package; .renda.json → read it and apply CLI overrides. */
async function buildPackage(ctx: Ctx, file: string, edit?: (pkg: any) => void, flags: Ctx["flags"] = ctx.flags): Promise<{ pkg: any; baseDir: string }> {
  const remote = /^(https?|asset):\/\//.test(file);
  const abs = remote ? file : resolve(file);
  const baseDir = remote ? ctx.deps.sys.cwd() : dirname(abs);
  let pkg: any;
  if (remote) {
    pkg = starterPackage(file, packageOverrides(flags) as any);
  } else if (file.endsWith(".renda.json")) {
    pkg = (await flattenFile(abs)).package; // extends / package references resolved against the file
    const f = flags;
    if (str(f, "engine") || str(f, "res") || str(f, "quality")) pkg.render = { ...(pkg.render ?? {}) };
    if (str(f, "engine")) pkg.render.engine = str(f, "engine");
    if (str(f, "res")) pkg.render.resolution = parseRes(str(f, "res")!);
    if (str(f, "quality")) pkg.render.quality = str(f, "quality");
    if (str(f, "format")) pkg.output = { ...(pkg.output ?? {}), format: str(f, "format") };
  } else {
    pkg = starterPackage(`./${basename(abs)}`, packageOverrides(flags) as any);
  }
  if (str(flags, "frames")) {
    pkg.animation = { ...(pkg.animation ?? { fps: 30 }), frames: parseFrames(str(flags, "frames")!) };
  }
  edit?.(pkg);
  for (const t of ctx.lists.texture ?? []) {
    let entry: any;
    try {
      entry = parseTextureFlag(t);
    } catch (e) {
      throw new UsageError((e as Error).message);
    }
    // paths on the command line are relative to the shell, not the package
    if (!/^(https?|asset|file):\/\//.test(entry.source) && !(pkg.assets ?? {})[entry.source]) {
      entry.source = resolve(ctx.deps.sys.cwd(), entry.source);
    }
    pkg.scene = { ...(pkg.scene ?? {}), textureOverrides: [...(pkg.scene?.textureOverrides ?? []), entry] };
  }
  const v = validatePackage(pkg);
  if (!v.valid) throw new CliError("invalid_package", "The package is invalid.", EXIT.error, "Fix the fields listed in `issues`.", [], { issues: v.issues });
  return { pkg, baseDir };
}

async function waitJob(ctx: Ctx, client: RendaClient, job: Job): Promise<Job> {
  let last = "";
  return client.waitForJob(job.id, {
    intervalMs: ctx.deps.pollMs ?? 2000,
    onProgress: (j) => {
      const line = `${j.status} ${Math.round((j.progress ?? 0) * 100)}%${j.engine ? ` · ${j.engine}` : ""}${j.hardware ? ` · ${j.hardware}` : ""}`;
      if (line !== last) ctx.out.info(line);
      last = line;
    },
  });
}

async function download(ctx: Ctx, job: Job, dir: string): Promise<string[]> {
  await mkdir(dir, { recursive: true });
  const files: string[] = [];
  const f = ctx.deps.fetch ?? fetch;
  for (const o of job.outputs ?? []) {
    const res = await f(o.url);
    if (!res.ok) throw new CliError("download_failed", `Downloading ${o.name} failed (${res.status}).`, EXIT.error, `Retry with: renda jobs ${job.id} --download ${dir}`);
    const path = join(dir, o.name);
    await writeFile(path, new Uint8Array(await res.arrayBuffer()));
    files.push(path);
  }
  return files;
}

function describeJob(j: Job): string {
  const lines = [`${j.id}  ${j.status}${j.engine ? ` · ${j.engine}` : ""}${j.hardware ? ` · ${j.hardware}` : ""}`,
    `billed ${(j.billedSeconds ?? 0).toFixed(1)}s · $${(j.costUsd ?? 0).toFixed(4)}${j.cached ? " · cached (free)" : ""}`];
  for (const w of j.warnings ?? []) lines.push(`warning: ${w}`);
  if (j.error) lines.push(`error: ${j.error}`);
  for (const o of j.outputs ?? []) lines.push(`${o.name}  ${o.url}`);
  return lines.join("\n");
}

async function finish(ctx: Ctx, job: Job, claimUrl?: string) {
  if (job.status === "failed") {
    throw new CliError("job_failed", job.error ?? "Render failed.", EXIT.error, `See \`renda jobs ${job.id}\``, [], { job });
  }
  const next: string[] = [];
  let files: string[] = [];
  if (job.status === "succeeded") {
    if (ctx.flags.output !== undefined || ctx.flags.wait !== false) files = await download(ctx, job, str(ctx.flags, "output") ?? "renders");
  } else {
    next.push(`renda jobs ${job.id} --wait --download renders`);
  }
  if (claimUrl) next.push(`Give the user this link to keep the trial workspace: ${claimUrl}`);
  ctx.out.ok({ job, files }, [describeJob(job), ...files.map((f) => `saved ${f}`)].join("\n"), next);
}


/** `renda bake` flags → output.format "glb" + output.bake (or a plain conversion with --glb). */
function applyBake(pkg: any, flags: Parsed["flags"]) {
  delete pkg.animation;
  const output: any = { ...(pkg.output ?? {}), format: "glb" };
  delete output.passes;
  delete output.video;
  delete output.keepFrames;
  if (bool(flags, "glb")) {
    delete output.bake;
  } else {
    const mode = str(flags, "mode") ?? "unlit";
    if (!["unlit", "ao", "lightmap"].includes(mode)) throw new UsageError(`--mode must be unlit, ao or lightmap, got ${mode}`);
    output.bake = { ...(output.bake ?? {}), mode, size: num(flags, "size") ?? output.bake?.size ?? 2048 };
    if (str(flags, "uvs")) output.bake.uvs = str(flags, "uvs");
    if (num(flags, "samples")) output.bake.samples = num(flags, "samples");
    if (str(flags, "texture-format")) output.bake.textureFormat = str(flags, "texture-format");
  }
  pkg.output = output;
}

/** `--local`: hand off to the Python CLI (Blender/Mitsuba on this machine) via uvx. */
async function runLocal(ctx: Ctx, file: string, command: "render" | "bake" | "splat" = "render") {
  const uvx = ctx.deps.sys.which("uvx");
  if (!uvx) throw new CliError("uv_missing", "Local rendering uses the Python CLI via uvx, which isn't installed.", EXIT.error, UV_INSTALL_HINT, [`uvx renda ${command} ${file}`]);
  const pass: string[] = ["renda", command, file];
  for (const [k, v] of Object.entries(ctx.flags)) {
    if (["local", "wait", "json", "max-minutes", "priority", "fast"].includes(k)) continue;
    if (k === "texture") {  // repeatable, and values contain commas: pass each one
      for (const t of ctx.lists.texture ?? []) pass.push("--texture", t);
      continue;
    }
    pass.push(`--${k}`);
    if (typeof v === "string") pass.push(v);
  }
  if (ctx.out.json) pass.push("--json");
  const r = spawnSync(uvx, pass, { stdio: "inherit" });
  if (r.status !== 0) throw new CliError("local_render_failed", `uvx renda exited with ${r.status}`, r.status ?? EXIT.error);
}

export { HELP, MCP_PACKAGE };
