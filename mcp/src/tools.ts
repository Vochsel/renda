import { mkdir, readFile, writeFile } from "node:fs/promises";
import { basename, join, resolve } from "node:path";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { RendaPackage, LayerError, flattenPackage, formatFromPath, isLayered, safeParsePackage, type FlattenResult, type RendaPackageInput } from "@rendahq/spec";
import { type TrialResponse, RendaApiError, TERMINAL_STATUSES, type HyperframesPackage, type Job, type RendaClient } from "./client.js";
import { PACKAGE_GUIDE } from "./docs.js";
import { hyperframesQuality, resolveVideoVariables, type ResolvedVariables } from "./video.js";
import { isLocalUri, packageDir, uploadBytes, uploadFile, uploadLocalPaths, zipDirectory } from "./local.js";

export interface RegisterOptions {
  client: RendaClient;
  mode: "local" | "remote";
  /** Called after renda_verify succeeds (local mode persists it to the credentials file). */
  onApiKey?: (apiKey: string) => Promise<void> | void;
  /** Called after an anonymous trial starts (local mode persists key + claim link). */
  onTrial?: (trial: TrialResponse) => Promise<void> | void;
  /** Claim link from an earlier trial (local mode reads it from the credentials file). */
  claimUrl?: string;
  /** Override polling for tests. */
  pollIntervalMs?: number;
}

type Content =
  | { type: "text"; text: string }
  | { type: "image"; data: string; mimeType: string };

interface ToolResult {
  [k: string]: unknown;
  content: Content[];
  isError?: boolean;
}

function ok(data: unknown, next?: string, extra: Content[] = []): ToolResult {
  const text = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  return { content: [{ type: "text", text: next ? `${text}\n\nNext step: ${next}` : text }, ...extra] };
}

function fail(message: string, next?: string): ToolResult {
  return { content: [{ type: "text", text: next ? `${message}\nNext step: ${next}` : message }], isError: true };
}

/** Wrap a handler so API errors come back as agent-readable tool errors. */
function safe<A>(fn: (args: A, extra: any) => Promise<ToolResult>) {
  return async (args: A, extra: any): Promise<ToolResult> => {
    try {
      return await fn(args, extra);
    } catch (e) {
      if (e instanceof RendaApiError) return fail(e.toAgentText());
      if (e instanceof LayerError) {
        return fail(`Layer error (${e.code}) in ${e.layer}${e.field ? ` at ${e.field}` : ""}: ${e.message}`,
          "Fix that field in that layer; renda_flatten_package shows how the layers combine.");
      }
      return fail(`Error: ${(e as Error).message}`);
    }
  };
}

export interface PackageIssue {
  path: string;
  message: string;
  fix?: string;
}

/** Validate a package locally and describe each issue with a concrete fix. */
export function validatePackage(input: unknown): { valid: true; package: RendaPackage } | { valid: false; issues: PackageIssue[] } {
  const r = safeParsePackage(input);
  if (r.success) return { valid: true, package: r.data };
  const issues = r.error.issues.map((i: any): PackageIssue => {
    const path = i.path.length ? i.path.join(".") : "(root)";
    let fix: string | undefined;
    if (i.code === "invalid_value" && Array.isArray(i.values)) fix = `Use one of: ${i.values.map((v: unknown) => JSON.stringify(v)).join(", ")}`;
    else if (i.code === "too_small") fix = `Value must be ≥ ${i.minimum}`;
    else if (i.code === "too_big") fix = `Value must be ≤ ${i.maximum}`;
    else if (i.code === "invalid_type" && i.expected) fix = `Provide a value of type ${i.expected}`;
    else if (i.code === "invalid_union" && path.startsWith("camera")) fix = 'camera.mode must be "auto", "scene" or "lookat" (lookat requires position)';
    else if (path === "animation") fix = 'Add "animation": { "frames": [1, 120], "fps": 30 } or switch output.format to an image format';
    else if (path === "output.transparent") fix = 'Use output.format "png", "webp", "exr", "webm" or "mov", or set transparent false';
    else if (path === "scene" || path === "scene.root") fix = 'Add "scene": { "root": "<asset id or path to a .blend/.glb/.usd/...>" }';
    return { path, message: i.message, fix };
  });
  return { valid: false, issues };
}

const PackageArg = z.record(z.string(), z.any()).describe("A Renda package object (see resource renda://docs/package).");

const StarterOptions = {
  engine: z.enum(["auto", "blender-cycles", "blender-eevee", "blender-workbench", "mitsuba", "threejs", "gsplat"]).optional(),
  resolution: z.tuple([z.number().int(), z.number().int()]).optional().describe("[width, height]"),
  quality: z.enum(["draft", "standard", "final"]).optional().describe("Start with draft; switch to final once it looks right."),
  format: z.enum(["png", "jpg", "webp", "exr", "mp4", "webm", "gif", "mov"]).optional(),
  transparent: z.boolean().optional(),
  hdri: z.string().optional().describe('Built-in "studio" | "sunset" | "night", a path, or a URL to an .hdr/.exr'),
  turntable: z.boolean().optional().describe("Orbit the camera 360° (implies a video format)."),
  seconds: z.number().positive().optional().describe("Turntable/animation length in seconds (default 4)."),
  fps: z.number().positive().optional(),
};

type StarterOpts = { [K in keyof typeof StarterOptions]?: z.infer<(typeof StarterOptions)[K]> };

/** Build a minimal starter package for one scene file. Only sets what the caller asked for. */
export function starterPackage(file: string, o: StarterOpts = {}): RendaPackageInput {
  const pkg: any = { version: 1, name: basename(file).replace(/\.[^.]+$/, ""), assets: { scene: { uri: file } }, scene: { root: "scene" } };
  const fmt = formatFromPath(file);
  if (fmt === "threejs" || fmt === "splat") pkg.assets.scene.format = fmt;
  if (o.hdri) {
    if (["studio", "sunset", "night"].includes(o.hdri)) pkg.environment = { hdri: o.hdri };
    else {
      pkg.assets.env = { uri: o.hdri };
      pkg.environment = { hdri: "env" };
    }
  }
  if (o.transparent) pkg.environment = { ...(pkg.environment ?? {}), background: "transparent" };
  const render: any = {};
  if (o.engine) render.engine = o.engine;
  if (o.resolution) render.resolution = o.resolution;
  render.quality = o.quality ?? "draft";
  pkg.render = render;
  const fps = o.fps ?? 30;
  const isVideoFmt = o.format && ["mp4", "webm", "gif", "mov"].includes(o.format);
  if (o.turntable || isVideoFmt) {
    pkg.animation = { frames: [1, Math.max(1, Math.round((o.seconds ?? 4) * fps))], fps };
    if (o.turntable) pkg.animation.turntable = { revolutions: 1 };
  }
  pkg.output = { format: o.format ?? (pkg.animation ? (o.transparent ? "webm" : "mp4") : "png") };
  if (o.transparent) pkg.output.transparent = true;
  return pkg;
}

function summarizeJob(job: Job) {
  return {
    id: job.id, status: job.status, progress: job.progress, engine: job.engine, hardware: job.hardware,
    billedSeconds: job.billedSeconds, costUsd: job.costUsd, cached: job.cached,
    warnings: job.warnings, error: job.error, outputs: job.outputs,
  };
}

function jobNextStep(job: Job, mode: "local" | "remote"): string {
  switch (job.status) {
    case "succeeded":
      return mode === "local"
        ? `Outputs are presigned URLs (valid for a limited time). Call renda_download {id:"${job.id}", dir:"./renders"} to save them locally. If this was a draft and it looks right, re-render with render.quality "final".`
        : "Outputs are presigned URLs; share or fetch them. If this was a draft and it looks right, re-render with render.quality \"final\".";
    case "failed":
      return "Read `error` and `warnings`, fix the package (renda_validate_package helps), and resubmit.";
    case "canceled":
      return "Job was canceled; resubmit if needed.";
    default:
      return `Still ${job.status}. Call renda_get_job {id:"${job.id}", wait:true} to wait for completion.`;
  }
}

/** Inline small image outputs so the agent can see the render. */
async function imageBlocks(job: Job, maxBytes = 1_000_000, maxImages = 2): Promise<Content[]> {
  const blocks: Content[] = [];
  for (const o of job.outputs) {
    if (blocks.length >= maxImages) break;
    if (!/^image\/(png|jpeg|webp|gif)$/.test(o.contentType) || o.bytes > maxBytes) continue;
    try {
      const res = await fetch(o.url);
      if (!res.ok) continue;
      const buf = new Uint8Array(await res.arrayBuffer());
      if (buf.byteLength > maxBytes) continue;
      blocks.push({ type: "image", data: Buffer.from(buf).toString("base64"), mimeType: o.contentType });
    } catch {
      // previews are best-effort
    }
  }
  return blocks;
}

export function registerRendaTools(server: McpServer, opts: RegisterOptions): void {
  const { client, mode } = opts;
  const pollMs = opts.pollIntervalMs ?? 2000;
  const local = mode === "local";

  const waitWithProgress = async (id: string, extra: any, timeoutMs = 10 * 60_000) => {
    const token = extra?._meta?.progressToken;
    return client.waitForJob(id, {
      intervalMs: pollMs,
      timeoutMs,
      signal: extra?.signal,
      onProgress: async (job) => {
        if (token === undefined || !extra?.sendNotification) return;
        await extra.sendNotification({
          method: "notifications/progress",
          params: { progressToken: token, progress: job.progress ?? 0, total: 1, message: `${job.status}${job.engine ? ` (${job.engine})` : ""}` },
        }).catch(() => {});
      },
    });
  };

  const finishJob = async (job: Job, includeImage: boolean, preface?: unknown) => {
    const extraBlocks = includeImage && job.status === "succeeded" ? await imageBlocks(job) : [];
    const body = preface ? { ...(preface as object), job: summarizeJob(job) } : summarizeJob(job);
    if (job.status === "failed") return { ...ok(body, jobNextStep(job, mode)), isError: true };
    return ok(body, jobNextStep(job, mode), extraBlocks);
  };

  let claimUrl = opts.claimUrl;
  const startTrial = async () => {
    const t = await client.trial();
    client.apiKey = t.apiKey;
    claimUrl = t.claimUrl;
    await opts.onTrial?.(t);
    return t;
  };
  const claimNote = () => (claimUrl ? ` Share this link with the user so they can keep the workspace: ${claimUrl}` : "");

  // ---------- account ----------
  server.registerTool("renda_trial", {
    title: "Start an anonymous trial",
    description: "Start a Renda trial with no signup: returns an API key with ~5 free render-minutes (max 1920px, no rush priority) and a claim link. Use this when the user has no account and you want to render right away. Give the claim link to the human so they can keep the workspace; unclaimed trials expire after 7 days.",
    inputSchema: {},
    annotations: { openWorldHint: true },
  }, safe(async () => {
    if (client.apiKey) return ok({ alreadyAuthenticated: true, claimUrl }, "Already have an API key. Call renda_whoami or renda_render.");
    const t = await startTrial();
    return ok(
      { freeSecondsLeft: t.freeSecondsLeft, expiresAt: t.expiresAt, claimUrl: t.claimUrl, limits: t.limits, ...(local ? { apiKeySaved: true } : { apiKey: t.apiKey }) },
      `Tell the user: "I started a free Renda trial. Open ${t.claimUrl} to keep it." Then call renda_render.`,
    );
  }));

  server.registerTool("renda_claim", {
    title: "Get the trial claim link",
    description: "Return the link the human opens to claim (keep) an anonymous trial workspace, lifting trial limits and topping up to the full free tier. Never open it yourself; give it to the human.",
    inputSchema: {},
    annotations: { readOnlyHint: true },
  }, safe(async () => {
    if (claimUrl) return ok({ claimUrl }, `Give the user this link to open in their browser: ${claimUrl}`);
    return ok({ claimUrl: null }, "No pending trial claim link (already claimed, or signed up normally). Call renda_whoami.");
  }));


  server.registerTool("renda_signup", {
    title: "Sign up / log in to Renda",
    description: "Start Renda signup or login by email. Renda emails the user a 6-digit code (new accounts are created automatically and get free render minutes). After calling this, ASK THE HUMAN for the code from their email — never guess or invent it — then call renda_verify.",
    inputSchema: { email: z.string().email().describe("The human user's email address.") },
    annotations: { openWorldHint: true },
  }, safe(async ({ email }: { email: string }) => {
    await client.magicStart(email);
    return ok({ ok: true, email }, `Ask the user: "I sent a 6-digit code to ${email} — what is it?" Then call renda_verify {email, code}.`);
  }));

  server.registerTool("renda_verify", {
    title: "Verify email code",
    description: "Complete signup/login with the 6-digit code the human received by email. Returns and (locally) saves an API key so subsequent tools are authenticated.",
    inputSchema: {
      email: z.string().email(),
      code: z.string().regex(/^\d{6}$/).describe("6-digit code from the email, exactly as the user typed it."),
      keyName: z.string().optional().describe('Label for the API key, e.g. "claude-code on laptop".'),
    },
    annotations: { openWorldHint: true },
  }, safe(async ({ email, code, keyName }: { email: string; code: string; keyName?: string }) => {
    const res = await client.magicVerify(email, code, keyName ?? `renda-mcp (${mode})`);
    if (local) {
      client.apiKey = res.apiKey;
      await opts.onApiKey?.(res.apiKey);
      return ok({ user: res.user, org: res.org, apiKeySaved: true }, "You're authenticated. Call renda_whoami to see free minutes, or renda_render to render a file.");
    }
    await opts.onApiKey?.(res.apiKey);
    return ok({ user: res.user, org: res.org, apiKey: res.apiKey },
      "Store this API key securely and configure it as the Bearer token for this MCP server (Authorization: Bearer <key>). It is shown only once.");
  }));

  server.registerTool("renda_whoami", {
    title: "Current account",
    description: "Show the authenticated Renda user, org, remaining free render seconds, and whether a payment method is attached.",
    inputSchema: {},
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, safe(async () => {
    const me = await client.me();
    const next = me.billing.hasPaymentMethod || me.balance.freeSecondsLeft > 0
      ? "Ready to render. Call renda_render."
      : "No free minutes left and no card attached. Call renda_billing_link and give the URL to the human.";
    return ok(me, next);
  }));

  server.registerTool("renda_pricing", {
    title: "Pricing",
    description: "Price per render-minute by hardware class (cpu, gpu-l4, gpu-l40s), free tier, and billing rules. No auth needed.",
    inputSchema: {},
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, safe(async () => ok(await client.pricing(), "Use renda_estimate on a package to get a cost range before large renders.")));

  server.registerTool("renda_usage", {
    title: "Usage",
    description: "Render seconds used, minutes billed and cost for a date range (ISO dates; defaults to the current billing period).",
    inputSchema: { from: z.string().optional(), to: z.string().optional() },
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, safe(async ({ from, to }: { from?: string; to?: string }) => ok(await client.usage({ ...(from ? { from } : {}), ...(to ? { to } : {}) }))));

  server.registerTool("renda_billing_link", {
    title: "Billing link",
    description: "Get a Stripe Checkout URL where the HUMAN adds a payment card (needed after free minutes run out). Agents cannot complete payment themselves — give the URL to the user. Set portal=true for the billing portal (invoices, card changes).",
    inputSchema: { portal: z.boolean().optional() },
    annotations: { openWorldHint: true },
  }, safe(async ({ portal }: { portal?: boolean }) => {
    const { url } = portal ? await client.billingPortal() : await client.billingCheckout();
    return ok({ url }, "Send this URL to the user and ask them to complete it, then retry the render.");
  }));

  // ---------- packages ----------
  /**
   * Resolve `extends` / package references. Local mode reads files and fetches https layers itself
   * (SSRF-guarded); remote mode asks the API, which can also read asset:// layers.
   */
  const flatten = async (pkg: Record<string, unknown>, baseDir: string, label?: string): Promise<FlattenResult> => {
    if (!isLayered(pkg)) return { package: pkg, provenance: {}, layers: [] };
    if (local) {
      const { nodeLoaders } = await import("@rendahq/spec/node");
      return flattenPackage(pkg, { ...nodeLoaders(), base: baseDir, label });
    }
    return client.flattenPackage(pkg);
  };

  server.registerTool("renda_validate_package", {
    title: "Validate a Renda package",
    description: "Validate a .renda.json package (free). Layered packages (extends / package references) are flattened first. Returns each issue with its JSON path and a concrete fix, or the normalized package with defaults applied.",
    inputSchema: {
      package: PackageArg,
      ...(local ? { packageDir: z.string().optional().describe("Directory relative layer paths resolve against (default cwd).") } : {}),
    },
    annotations: { readOnlyHint: true },
  }, safe(async ({ package: input, packageDir: dir }: { package: Record<string, unknown>; packageDir?: string }) => {
    const pkg = (await flatten(input, dir ? resolve(dir) : process.cwd())).package;
    const r = validatePackage(pkg);
    if (r.valid) return ok({ valid: true, normalized: r.package }, "Package is valid. Call renda_estimate or renda_render.");
    return { ...ok({ valid: false, issues: r.issues }, "Apply the fixes and validate again."), isError: true };
  }));

  server.registerTool("renda_init_package", {
    title: "Create a starter package",
    description: "Generate a starter Renda package for a scene file (.blend, .usd/.usdz, .glb/.gltf, three.js .json, .fbx, .obj, .stl, .ply, .dae, .3mf, .abc). Defaults to a cheap draft still with auto camera and studio lighting." + (local ? " Pass writeTo to save it as a .renda.json file." : ""),
    inputSchema: {
      file: z.string().describe("Path or URL of the scene file."),
      ...StarterOptions,
      ...(local ? { writeTo: z.string().optional().describe("Write the package to this path, e.g. ./scene.renda.json") } : {}),
    },
  }, safe(async (args: { file: string; writeTo?: string } & StarterOpts) => {
    const { file, writeTo, ...o } = args;
    if (!formatFromPath(file)) return fail(`Unrecognized scene format for ${file}.`, "Supported: .blend .usd .usda .usdc .usdz .gltf .glb .json(three.js) .fbx .obj .stl .ply .dae .3mf .abc, CAD .step .stp .iges .igs, splats .ply .splat .spz .ksplat");
    const pkg = starterPackage(file, o);
    const v = validatePackage(pkg);
    if (!v.valid) return fail(`Generated package is invalid: ${JSON.stringify(v.issues)}`);
    if (local && writeTo) {
      await writeFile(resolve(/* turbopackIgnore: true */ writeTo), JSON.stringify(pkg, null, 2) + "\n");
      return ok({ path: resolve(writeTo), package: pkg }, `Edit as needed, then renda_render {packagePath:"${writeTo}"}.`);
    }
    return ok(pkg, "Adjust fields if needed, then renda_render {package}. Relative paths resolve against packageDir (or the working directory).");
  }));

  server.registerTool("renda_flatten_package", {
    title: "Flatten a layered package",
    description: "Resolve a package's `extends` layers (applied in order, later wins; objects merge, arrays replace, null removes) and `scene.include[].package` references (another package's scene placed by a transform, its asset ids namespaced) into one flat package. Layers can be paths"
      + (local ? " (relative to the file that names them)" : "") + ", https:// URLs or asset:// URIs. With provenance, also says which layer set each field — use it to debug \"why is this value here?\".",
    inputSchema: {
      package: PackageArg.optional(),
      ...(local ? {
        packagePath: z.string().optional().describe("Path to a .renda.json file."),
        packageDir: z.string().optional().describe("Directory relative layer paths in `package` resolve against (default cwd)."),
      } : {}),
      provenance: z.boolean().optional().describe("Include field → layer provenance (default true)."),
    },
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, safe(async (args: { package?: Record<string, unknown>; packagePath?: string; packageDir?: string; provenance?: boolean }) => {
    let r: FlattenResult;
    if (args.packagePath && local) {
      const { flattenFile } = await import("@rendahq/spec/node");
      r = await flattenFile(resolve(/* turbopackIgnore: true */ args.packagePath));
    } else if (args.package) {
      r = await flatten(args.package, args.packageDir ? resolve(args.packageDir) : process.cwd());
    } else {
      return fail("Provide package" + (local ? " or packagePath" : "") + ".");
    }
    const v = validatePackage(r.package);
    const body: Record<string, unknown> = { package: r.package, layers: r.layers };
    if (args.provenance !== false) body.provenance = r.provenance;
    if (!v.valid) body.issues = v.issues;
    return ok(body, v.valid ? "Render it with renda_render {package} (or pass the original file; it's flattened automatically)." : "The composed package has issues; fix them in the layer named by provenance.");
  }));

  // ---------- files ----------
  if (local) {
    server.registerTool("renda_upload", {
      title: "Upload a local file",
      description: "Upload a local file (scene, texture, HDRI) to Renda. Deduplicated by content hash — re-uploading the same bytes is instant. Returns an asset:// URI usable anywhere in a package. Usually unnecessary: renda_render uploads local paths automatically.",
      inputSchema: { path: z.string() },
      annotations: { idempotentHint: true, openWorldHint: true },
    }, safe(async ({ path }: { path: string }) => {
      const r = await uploadFile(client, resolve(path));
      return ok({ path: resolve(path), ...r }, `Reference it in a package as "${r.assetUri}".`);
    }));
  }

  // ---------- render ----------
  const GROUND_MODES: Record<string, string> = { shadow: "shadowCatcher", solid: "solid", reflective: "reflective" };
  const resolvePackage = async (args: { package?: Record<string, unknown>; file?: string; packagePath?: string; packageDir?: string; options?: StarterOpts; textureOverrides?: any[]; ground?: any },
    edit?: (pkg: any) => void) => {
    let pkg: any;
    let baseDir = args.packageDir ? resolve(args.packageDir) : process.cwd();
    let label: string | undefined;
    if (args.package) pkg = args.package;
    else if (args.packagePath) {
      if (!local) throw new Error("packagePath requires local mode; pass the package object instead.");
      label = resolve(/* turbopackIgnore: true */ args.packagePath);
      pkg = JSON.parse(await readFile(label, "utf8"));
      baseDir = args.packageDir ? baseDir : packageDir(args.packagePath);
    } else if (args.file) pkg = starterPackage(args.file, args.options ?? {});
    else throw new Error("Provide one of: package, packagePath, or file.");
    // Flatten layers first; tool options (bake settings, texture overrides) then apply on top.
    pkg = (await flatten(pkg, baseDir, label)).package;
    if (edit) {
      pkg = structuredClone(pkg);
      edit(pkg);
    }
    if (args.textureOverrides?.length) {
      pkg = { ...pkg, scene: { ...(pkg.scene ?? {}), textureOverrides: [...(pkg.scene?.textureOverrides ?? []), ...args.textureOverrides] } };
    }
    if (args.ground !== undefined) {
      const g = typeof args.ground === "string" ? { mode: GROUND_MODES[args.ground] ?? args.ground } : args.ground;
      const scene = { ...(pkg.scene ?? {}) };
      if (args.ground === "none" || args.ground === false) delete scene.ground;
      else scene.ground = { ...(scene.ground ?? {}), ...g };
      pkg = { ...pkg, scene };
    }
    const v = validatePackage(pkg);
    if (!v.valid) return { issues: v.issues } as const;
    let uploaded: unknown[] = [];
    if (local) {
      const r = await uploadLocalPaths(client, pkg, baseDir);
      pkg = r.package;
      uploaded = r.uploaded;
    } else {
      const localRefs = [...Object.values<any>(pkg.assets ?? {}).map((a) => a.uri), pkg.scene?.root,
        ...(pkg.scene?.textureOverrides ?? []).map((t: any) => t.source)]
        .filter((u) => typeof u === "string" && isLocalUri(u) && !(u in (pkg.assets ?? {})));
      if (localRefs.length) throw new RendaApiError(400, "local_path_in_remote_mode", `Remote MCP can't read local files: ${localRefs.join(", ")}`,
        "Use https:// URLs or asset:// URIs (upload via POST /api/v1/uploads or the local @rendahq/mcp server).");
    }
    return { pkg, uploaded } as const;
  };

  const RenderSource = {
    package: PackageArg.optional(),
    ...(local ? { packagePath: z.string().optional().describe("Path to a .renda.json file.") } : {}),
    file: z.string().optional().describe("A scene file path/URL to render with default settings (use instead of package)."),
    options: z.object(StarterOptions).optional().describe("Starter options when using file."),
    ...(local ? { packageDir: z.string().optional().describe("Directory relative paths resolve against (default: package file dir or cwd).") } : {}),
    textureOverrides: z.array(z.record(z.string(), z.any())).optional().describe(
      "Swap material textures (appended to scene.textureOverrides; later entries win). Each: {source: image or video "
      + (local ? "path/URL" : "https/asset URI") + " (png/jpg/webp/avif/heic/tiff/exr/hdr/svg/psd/gif/mp4/mov/webm — converted automatically), "
      + "match?: {material?, object?, texture?, slot?: baseColor|normal|roughness|metallic|emission|opacity} (exact name, glob like \"wood*\", or \"/regex/i\"), "
      + "uv?: {scale, offset, rotationDeg}, video?: {loop, startSec, speed, frame}, colorSpace?, strength?}. "
      + "Videos play in sync with the animation. Example: [{source: \"./oak.jpg\", match: {material: \"/wood/i\"}, uv: {scale: 4}}]."),
    ground: z.union([z.enum(["shadow", "solid", "reflective", "none"]), z.record(z.string(), z.any())]).optional().describe(
      "Ground plane under the scene (sets scene.ground). \"shadow\" = shadow catcher: only the shadows show, on any background "
      + "(transparent PNGs too) — the usual product-shot look. \"solid\" = matte floor, \"reflective\" = glossy floor, \"none\" removes it. "
      + "Or the full object: {mode: shadowCatcher|solid|reflective, offset?, size?, color?, roughness?, reflectivity?, shadowOpacity?, contactShadows?}."),
  };

  server.registerTool("renda_estimate", {
    title: "Estimate render cost",
    description: "Estimate hardware, render time range and cost range for a package before rendering. Free. Do this before large/final/video renders.",
    inputSchema: RenderSource,
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, safe(async (args: any) => {
    const r = await resolvePackage(args);
    if ("issues" in r) return fail(`Invalid package: ${JSON.stringify(r.issues, null, 2)}`, "Fix the issues and retry.");
    const est = await client.estimate(r.pkg);
    return ok(est, "If acceptable, call renda_render with the same package. To cut cost: lower resolution, quality draft, fewer frames, or priority economy.");
  }));

  server.registerTool("renda_render", {
    title: "Render a 3D scene",
    description: "Render a 3D file or Renda package in the cloud (Blender Cycles/Eevee, Mitsuba, three.js, gsplat). Meshes and Gaussian splats (.ply/.splat/.spz), alone or mixed via scene.include. Accepts a package object" + (local ? ", a .renda.json path," : "") + " or a bare scene file. "
      + (local ? "Local file paths are uploaded automatically. " : "Use https:// or asset:// URIs. ")
      + "By default waits for completion and returns output URLs plus a preview image. Tip: render quality \"draft\" first (cheap), check it, then \"final\".",
    inputSchema: {
      ...RenderSource,
      priority: z.enum(["economy", "standard", "rush"]).optional().describe("economy = cheapest, slower; rush = fastest, pricier."),
      wait: z.boolean().optional().describe("Wait for completion (default true, up to 10 min)."),
      includeImage: z.boolean().optional().describe("Inline small image outputs so you can see them (default true)."),
      idempotencyKey: z.string().optional(),
    },
    annotations: { openWorldHint: true },
  }, safe(async (args: any, extra: any) => {
    // No account yet (local): start a trial instead of failing, and surface the claim link.
    const trial = local && !client.apiKey ? await startTrial() : undefined;
    const r = await resolvePackage(args);
    if ("issues" in r) return fail(`Invalid package: ${JSON.stringify(r.issues, null, 2)}`, "Fix the issues (see `fix`) and retry.");
    let job = await client.createJob({ package: r.pkg, kind: "scene", priority: args.priority, idempotencyKey: args.idempotencyKey });
    if (trial) {
      if (args.wait !== false && !TERMINAL_STATUSES.has(job.status)) job = await waitWithProgress(job.id, extra);
      const res = await finishJob(job, args.includeImage !== false, { trialStarted: { claimUrl: trial.claimUrl, freeSecondsLeft: trial.freeSecondsLeft } });
      res.content[0] = { type: "text", text: (res.content[0] as { text: string }).text + `\nNo account was configured, so a free trial was started.${claimNote()}` };
      return res;
    }
    if (args.wait !== false && !TERMINAL_STATUSES.has(job.status)) job = await waitWithProgress(job.id, extra);
    return finishJob(job, args.includeImage !== false, r.uploaded.length ? { uploaded: r.uploaded } : undefined);
  }));

  server.registerTool("renda_bake", {
    title: "Bake lighting into a model → GLB",
    description: "Bake a scene's lighting (HDRI + lights, shadows, ambient occlusion, bounce light) into textures and return a .glb that looks lit in any viewer, game engine or three.js scene with no lights. "
      + "Takes the same inputs as renda_render. Engines: blender-cycles (default, GPU, full GI + glossy), mitsuba (GPU path tracer), threejs (cheap CPU, direct light + environment shadows, no bounces). "
      + "Modes: unlit (default; baked colour + KHR_materials_unlit), ao (ambient-occlusion texture, PBR kept), lightmap (lighting-only texture on TEXCOORD_1, see extras.renda.lightmap). "
      + "plain=true just converts the scene to GLB (no baking, cheap). Gaussian splats can't be baked.",
    inputSchema: {
      ...RenderSource,
      mode: z.enum(["unlit", "ao", "lightmap"]).optional().describe("Default unlit."),
      size: z.number().int().min(64).max(8192).optional().describe("Texture size in px (default 2048)."),
      engine: z.enum(["auto", "blender-cycles", "mitsuba", "threejs"]).optional(),
      quality: z.enum(["draft", "standard", "final"]).optional().describe("Samples per texel preset (default standard)."),
      samples: z.number().int().positive().optional(),
      uvs: z.enum(["auto", "existing"]).optional().describe("auto = generate lightmap UVs (default); existing = reuse the model's UVs (must not overlap)."),
      textureFormat: z.enum(["jpg", "png", "webp"]).optional(),
      plain: z.boolean().optional().describe("Convert to GLB without baking."),
      priority: z.enum(["economy", "standard", "rush"]).optional(),
      wait: z.boolean().optional().describe("Wait for completion (default true, up to 10 min)."),
    },
    annotations: { openWorldHint: true },
  }, safe(async (args: any, extra: any) => {
    const trial = local && !client.apiKey ? await startTrial() : undefined;
    const r = await resolvePackage(args, (pkg) => {
      delete pkg.animation;
      const output: any = { ...(pkg.output ?? {}), format: "glb" };
      delete output.passes;
      delete output.video;
      delete output.keepFrames;
      if (args.plain) delete output.bake;
      else {
        output.bake = { ...(output.bake ?? {}) };
        for (const k of ["mode", "size", "samples", "uvs", "textureFormat"]) if (args[k] !== undefined) output.bake[k] = args[k];
      }
      pkg.output = output;
      if (args.engine || args.quality) pkg.render = { ...(pkg.render ?? {}), ...(args.engine ? { engine: args.engine } : {}), ...(args.quality ? { quality: args.quality } : {}) };
    });
    if ("issues" in r) return fail(`Invalid package: ${JSON.stringify(r.issues, null, 2)}`, "Fix the issues (see `fix`) and retry.");
    let job = await client.createJob({ package: r.pkg, kind: "scene", priority: args.priority });
    if (args.wait !== false && !TERMINAL_STATUSES.has(job.status)) job = await waitWithProgress(job.id, extra);
    const res = await finishJob(job, false, trial ? { trialStarted: { claimUrl: trial.claimUrl, freeSecondsLeft: trial.freeSecondsLeft } } : undefined);
    if (trial) res.content[0] = { type: "text", text: (res.content[0] as { text: string }).text + `\nNo account was configured, so a free trial was started.${claimNote()}` };
    return res;
  }));

  server.registerTool("renda_bake_splat", {
    title: "Bake a 3D scene into a Gaussian splat",
    description: "Turn any mesh scene (glTF/GLB, .blend, USD, FBX, OBJ, STL…, with its lights/HDRI/materials) into a 3D Gaussian splat "
      + "(.spz by default; .ply/.splat). preset fast: mesh → Gaussians directly (albedo only, seconds, CPU, ~$0.003). standard (default): "
      + "~96 views placed by surface coverage, rendered with any engine (auto: Eevee for diffuse scenes, Cycles for glossy/glass) and "
      + "refined with gsplat so lighting, shadows and reflections are baked in (a few minutes, ~$0.1–0.2). Small bakes render the views "
      + "inside the training GPU; rush/high fan out and stream. high: 200 views at 1024px on an L40S. Pass `cameras` to use your own "
      + "training views. dataset=true also returns the views + an exact COLMAP model as a zip. "
      + "Check the cost first with renda_estimate on the same arguments if unsure. "
      + (local ? "Local file paths are uploaded automatically." : "Use https:// or asset:// URIs."),
    inputSchema: {
      ...RenderSource,
      preset: z.enum(["fast", "standard", "high"]).optional(),
      splatFormat: z.enum(["ply", "spz", "splat"]).optional().describe("Output file type (default spz, ~10x smaller than ply)."),
      views: z.number().int().min(8).max(1000).optional(),
      cameras: z.array(z.object({
        position: z.tuple([z.number(), z.number(), z.number()]),
        target: z.tuple([z.number(), z.number(), z.number()]),
        up: z.tuple([z.number(), z.number(), z.number()]).optional(),
        fovDeg: z.number().optional(),
      })).min(8).max(1000).optional().describe("Explicit training cameras (scene units/axes) instead of the automatic rig."),
      coverage: z.enum(["auto", "sphere", "hemisphere", "orbit"]).optional(),
      fanOut: z.union([z.literal("auto"), z.boolean()]).optional()
        .describe("true: render views in parallel containers; false: inside the training GPU; auto (default) picks by cost/latency."),
      steps: z.number().int().min(0).max(100000).optional(),
      shDegree: z.number().int().min(0).max(3).optional(),
      engine: z.enum(["auto", "blender-cycles", "blender-eevee", "blender-workbench", "mitsuba", "threejs"]).optional()
        .describe("Engine that renders the training views."),
      dataset: z.boolean().optional(),
      priority: z.enum(["economy", "standard", "rush"]).optional(),
      wait: z.boolean().optional().describe("Wait for completion (default true, up to 20 min)."),
    },
    annotations: { openWorldHint: true },
  }, safe(async (args: any, extra: any) => {
    const trial = local && !client.apiKey ? await startTrial() : undefined;
    const r = await resolvePackage(args, (pkg) => {
      const sb: Record<string, unknown> = { ...(pkg.output?.splatBake ?? {}) };
      for (const k of ["preset", "views", "cameras", "coverage", "fanOut", "steps", "shDegree", "engine", "dataset"] as const) {
        if (args[k] !== undefined) sb[k] = args[k];
      }
      pkg.output = { format: "splat", splatFormat: args.splatFormat ?? pkg.output?.splatFormat ?? "spz", splatBake: sb };
      delete pkg.animation;
    });
    if ("issues" in r) return fail(`Invalid package: ${JSON.stringify(r.issues, null, 2)}`, "Fix the issues (see `fix`) and retry.");
    let job = await client.createJob({ package: r.pkg, kind: "scene", priority: args.priority });
    if (args.wait !== false && !TERMINAL_STATUSES.has(job.status)) job = await waitWithProgress(job.id, extra, 20 * 60_000);
    const res = await finishJob(job, false, trial ? { trialStarted: { claimUrl: trial.claimUrl, freeSecondsLeft: trial.freeSecondsLeft } } : undefined);
    if (trial) res.content[0] = { type: "text", text: (res.content[0] as { text: string }).text + `\nNo account was configured, so a free trial was started.${claimNote()}` };
    return res;
  }));

  server.registerTool("renda_render_video", {
    title: "Render a HyperFrames video",
    description: "Render a HyperFrames HTML video composition in the cloud (headless Chromium), billed as CPU time. "
      + (local ? "Pass projectDir (zipped and uploaded automatically) or projectUri. " : "Pass projectUri (https:// or asset:// zip of the project). ")
      + "`variables` override the composition's data-composition-variables. To put 3D in a video slot (a <video data-var-src=\"clipUrl\">), "
      + "set that variable to `job:<id>` of a finished turntable render, an https URL"
      + (local ? ", a local .mp4/.png, or a local 3D file (.glb/.blend/.step/…: a turntable is rendered first automatically)." : ", or an asset:// upload."),
    inputSchema: {
      ...(local ? { projectDir: z.string().optional().describe("Local HyperFrames project directory.") } : {}),
      projectUri: z.string().optional().describe("https:// or asset:// URI of a zipped project."),
      composition: z.string().optional().describe("Composition file to render (default index.html)."),
      variables: z.record(z.string(), z.unknown()).optional()
        .describe("Composition variable values, e.g. {\"title\":\"Launch\",\"clipUrl\":\"job:job_abc\"}."),
      name: z.string().optional().describe("Output file stem."),
      width: z.number().int().positive().optional().describe("Only HyperFrames presets with the composition's aspect (1920x1080, 3840x2160, 1080x1920, 1080x1080…)."),
      height: z.number().int().positive().optional(),
      fps: z.number().int().positive().max(120).optional(),
      format: z.enum(["mp4", "webm", "mov", "gif"]).optional(),
      quality: z.enum(["draft", "looks", "delivery", "standard", "final"]).optional()
        .describe("draft (fast) | looks (default) | delivery (final). standard/final are accepted as looks/delivery."),
      durationSec: z.number().positive().optional().describe("Composition length, for a better cost estimate."),
      priority: z.enum(["economy", "standard", "rush"]).optional(),
      wait: z.boolean().optional(),
    },
    annotations: { openWorldHint: true },
  }, safe(async (args: any, extra: any) => {
    let project: string | undefined = args.projectUri;
    let uploaded: unknown;
    if (!project && local && args.projectDir) {
      const dir = resolve(args.projectDir);
      const zip = await zipDirectory(dir);
      const u = await uploadBytes(client, `${basename(dir)}.zip`, zip, "application/zip");
      project = u.assetUri;
      uploaded = { path: dir, ...u };
    }
    if (!project) return fail("No project given.", local ? "Pass projectDir or projectUri." : "Pass projectUri (https:// or asset://).");
    if (isLocalUri(project)) return fail(`projectUri must be https:// or asset://, got ${project}`, local ? "Use projectDir for local folders." : undefined);
    const pkg: HyperframesPackage = { project };
    for (const k of ["composition", "name", "width", "height", "fps", "format", "durationSec"] as const) if (args[k] !== undefined) (pkg as any)[k] = args[k];
    if (args.quality) pkg.quality = hyperframesQuality(args.quality);
    let resolved: ResolvedVariables | undefined;
    if (args.variables) {
      resolved = await resolveVideoVariables(client, args.variables, {
        baseDir: process.cwd(), // relative paths are relative to where the agent runs, like everything else
        local, waitJob: (j) => waitWithProgress(j.id, extra, 20 * 60_000),
      });
      pkg.variables = resolved.variables;
    }
    let job = await client.createJob({ package: pkg, kind: "hyperframes", priority: args.priority });
    if (args.wait !== false && !TERMINAL_STATUSES.has(job.status)) job = await waitWithProgress(job.id, extra, 20 * 60_000);
    const extraInfo: Record<string, unknown> = {};
    if (uploaded) extraInfo.uploaded = uploaded;
    if (resolved?.jobs.length) extraInfo.inputJobs = resolved.jobs;
    if (resolved?.uploaded.length) extraInfo.uploadedVariables = resolved.uploaded;
    return finishJob(job, false, Object.keys(extraInfo).length ? extraInfo : undefined);
  }));

  // ---------- jobs ----------
  server.registerTool("renda_get_job", {
    title: "Get job",
    description: "Get a render job's status, progress, cost, warnings and output URLs. wait=true blocks until it finishes (up to 10 min).",
    inputSchema: { id: z.string(), wait: z.boolean().optional(), includeImage: z.boolean().optional() },
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, safe(async ({ id, wait, includeImage }: { id: string; wait?: boolean; includeImage?: boolean }, extra: any) => {
    const job = wait ? await waitWithProgress(id, extra) : await client.getJob(id);
    return finishJob(job, includeImage !== false);
  }));

  server.registerTool("renda_list_jobs", {
    title: "List jobs",
    description: "List recent render jobs, newest first.",
    inputSchema: {
      limit: z.number().int().min(1).max(100).optional(),
      status: z.enum(["queued", "planning", "rendering", "encoding", "succeeded", "failed", "canceled"]).optional(),
    },
    annotations: { readOnlyHint: true, openWorldHint: true },
  }, safe(async (args: { limit?: number; status?: any }) => {
    const jobs = await client.listJobs(args);
    return ok(jobs.map((j) => ({ id: j.id, kind: j.kind, status: j.status, progress: j.progress, createdAt: j.createdAt, costUsd: j.costUsd, outputs: j.outputs.length })));
  }));

  server.registerTool("renda_cancel_job", {
    title: "Cancel job",
    description: "Cancel a queued or running job. You are billed only for seconds already rendered.",
    inputSchema: { id: z.string() },
    annotations: { destructiveHint: true, idempotentHint: true, openWorldHint: true },
  }, safe(async ({ id }: { id: string }) => ok(summarizeJob(await client.cancelJob(id)))));

  if (local) {
    server.registerTool("renda_download", {
      title: "Download job outputs",
      description: "Download all outputs of a finished job into a local directory.",
      inputSchema: { id: z.string(), dir: z.string().optional().describe("Default ./renders/<jobId>") },
      annotations: { openWorldHint: true },
    }, safe(async ({ id, dir }: { id: string; dir?: string }) => {
      const job = await client.getJob(id);
      if (job.status !== "succeeded") return fail(`Job ${id} is ${job.status}; nothing to download.`, jobNextStep(job, mode));
      const target = resolve(/* turbopackIgnore: true */ dir ?? join("renders", id));
      await mkdir(target, { recursive: true });
      const files: { path: string; bytes: number }[] = [];
      for (const o of job.outputs) {
        const res = await fetch(o.url);
        if (!res.ok) return fail(`Download of ${o.name} failed: ${res.status}`, "Output URLs expire; call renda_get_job for fresh URLs and retry.");
        const buf = new Uint8Array(await res.arrayBuffer());
        const path = join(target, basename(o.name));
        await writeFile(path, buf);
        files.push({ path, bytes: buf.byteLength });
      }
      return ok({ dir: target, files });
    }));
  }

  // ---------- resources ----------
  server.registerResource("renda-package-schema", "renda://schema/v1", {
    title: "Renda package JSON Schema",
    description: "JSON Schema (draft 2020-12) for *.renda.json packages.",
    mimeType: "application/schema+json",
  }, async (uri) => ({
    contents: [{ uri: uri.href, mimeType: "application/schema+json", text: JSON.stringify(packageJsonSchema(), null, 2) }],
  }));

  server.registerResource("renda-package-docs", "renda://docs/package", {
    title: "Renda package guide",
    description: "How to write a Renda package: fields, camera modes, engines, formats.",
    mimeType: "text/markdown",
  }, async (uri) => ({ contents: [{ uri: uri.href, mimeType: "text/markdown", text: PACKAGE_GUIDE }] }));

  // ---------- prompts ----------
  server.registerPrompt("render-file", {
    title: "Render a 3D file",
    description: "Render a scene file: draft first, then final.",
    argsSchema: { file: z.string().describe("Path or URL of the scene file") },
  }, ({ file }) => ({
    messages: [{
      role: "user",
      content: {
        type: "text",
        text: `Render ${file} with Renda. First call renda_render {file:"${file}"} (draft quality by default) and look at the preview. If framing/lighting look wrong, adjust the package (camera, environment, materialOverrides) and draft again. When it looks good, call renda_estimate with quality "final", tell me the cost, then render final and download the result.`,
      },
    }],
  }));

  server.registerPrompt("make-turntable", {
    title: "Make a turntable video",
    description: "360° turntable MP4 of a model.",
    argsSchema: { file: z.string(), seconds: z.string().optional().describe("Length in seconds (default 4)") },
  }, ({ file, seconds }) => ({
    messages: [{
      role: "user",
      content: {
        type: "text",
        text: `Make a ${seconds ?? "4"}-second 360° turntable MP4 of ${file} with Renda. Use renda_init_package {file:"${file}", turntable:true, seconds:${Number(seconds ?? 4)}, quality:"draft"}, render a single-frame still draft first to check framing (override animation away), then estimate and render the full turntable at standard quality and download it.`,
      },
    }],
  }));
}

let cachedSchema: unknown;
export function packageJsonSchema(): unknown {
  if (!cachedSchema) {
    const s: any = z.toJSONSchema(RendaPackage, { io: "input", unrepresentable: "any" });
    s.$id = "https://renda.dev/schema/v1.json";
    s.title = "Renda package";
    cachedSchema = s;
  }
  return cachedSchema;
}
