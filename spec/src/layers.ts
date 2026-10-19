/**
 * Package layering ("Hydra-lite composition"):
 *
 * - `extends: ref | ref[]` layers other packages underneath this one, applied in order; later layers
 *   win and the file itself wins over all of them. Objects deep-merge, arrays replace, `null` removes
 *   a field (except where null is a real value: `environment.hdri`, `render.samples`). If two objects
 *   disagree on their kind (`camera.mode`), the stronger one replaces the weaker instead of merging.
 * - `scene.include[].package` places another package's whole scene (its assets, includes, lights and
 *   material overrides) by a transform. Its asset ids are namespaced `<as>.<id>` so they can't
 *   collide; its camera/environment/render/output are not used.
 * - Relative paths resolve against the file that declared them (a local directory or an https URL).
 *
 * The resolver is pure: it never touches the filesystem or network itself. Callers supply loaders
 * (see ./node for the local + SSRF-safe https ones), so the same code runs in CLIs, MCP and the API.
 * core/renda/layers.py is a line-for-line port; spec/fixtures/layers keeps the two in lockstep.
 */

export interface LayerLoaders {
  /** Read a local file (absolute path). Omit to forbid local layers (e.g. on the server). */
  readFile?: (absPath: string) => Promise<string>;
  /** Fetch an https URL. Must enforce SSRF protections. Omit to forbid remote layers. */
  fetchHttps?: (url: string) => Promise<string>;
  /** Read an uploaded asset (content hash). Omit to forbid asset:// layers. */
  readAsset?: (sha256: string) => Promise<string>;
}

export interface FlattenOptions extends LayerLoaders {
  /**
   * Where the top-level document lives: a local directory, an https URL of the document itself, or
   * null when it has no location (a package POSTed to the API). Relative refs need a base.
   */
  base?: string | null;
  /**
   * Provenance label for the top-level document (default "<root>"). Pass its absolute path or URL
   * when it has one, so a layer that extends the top-level file is reported as a cycle.
   */
  label?: string;
  maxDepth?: number;
  maxLayers?: number;
  maxBytes?: number;
}

export interface FlattenResult {
  package: Record<string, unknown>;
  /** Dotted field path → the layer that set it (path, URL, asset:// or the root label). */
  provenance: Record<string, string>;
  /** Every layer loaded, in load order (excluding the root document). */
  layers: string[];
}

export type LayerErrorCode =
  | "layer_cycle"
  | "layer_depth"
  | "layer_too_many"
  | "layer_too_large"
  | "layer_fetch"
  | "layer_parse"
  | "layer_ref"
  | "layer_not_allowed"
  | "layer_invalid";

export class LayerError extends Error {
  constructor(
    public code: LayerErrorCode,
    message: string,
    /** The layer whose content caused the error. */
    public layer: string,
    /** The field inside that layer, e.g. `extends[1]` or `scene.include[0].package`. */
    public field?: string,
  ) {
    super(`${layer}${field ? ` (${field})` : ""}: ${message}`);
    this.name = "LayerError";
  }
}

export const LAYER_LIMITS = { maxDepth: 8, maxLayers: 64, maxBytes: 1_000_000 } as const;

const BUILTIN_HDRIS = new Set(["studio", "sunset", "night"]);
/** Fields where `null` is a value, not a deletion. */
const NULLABLE = new Set(["environment.hdri", "render.samples"]);
/** Keys that say what kind of object this is; a mismatch replaces instead of merging. */
const DISCRIMINATORS = ["mode", "type"];
const SHA_URI = /^asset:\/\/([0-9a-f]{64})$/;

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
type Obj = { [k: string]: Json };
type Base = { kind: "path"; dir: string } | { kind: "https"; url: string } | { kind: "asset"; uri: string } | { kind: "none" };
type Ref = { kind: "path"; id: string } | { kind: "https"; id: string } | { kind: "asset"; id: string; sha: string };

const isObj = (v: unknown): v is Obj => !!v && typeof v === "object" && !Array.isArray(v);

/** True when the input still needs flattening (has `extends` or package references). */
export function isLayered(input: unknown): boolean {
  if (!isObj(input)) return false;
  if (input.extends !== undefined) return true;
  const inc = isObj(input.scene) ? input.scene.include : undefined;
  return Array.isArray(inc) && inc.some((i) => isObj(i) && "package" in i);
}
const clone = <T>(v: T): T => (v === undefined ? v : JSON.parse(JSON.stringify(v)));

/** `assets.chair.uri`, quoting keys that aren't identifier-like: `assets["a.b"].uri`. */
export function fieldPath(parent: string, key: string | number): string {
  if (typeof key === "number") return `${parent}[${key}]`;
  const safe = /^[A-Za-z_$][\w$-]*$/.test(key);
  if (!parent) return safe ? key : `["${key}"]`;
  return safe ? `${parent}.${key}` : `${parent}["${key}"]`;
}

// ---- refs + paths ------------------------------------------------------------------------------

function normalizePosix(p: string): string {
  const abs = p.startsWith("/");
  const out: string[] = [];
  for (const part of p.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") out.pop();
    else out.push(part);
  }
  return (abs ? "/" : "") + out.join("/");
}

function isAbsPath(p: string): boolean {
  return p.startsWith("/") || /^[A-Za-z]:[\\/]/.test(p);
}

function dirname(p: string): string {
  const i = p.replace(/\\/g, "/").lastIndexOf("/");
  return i <= 0 ? "/" : p.slice(0, i);
}

function hasScheme(s: string): boolean {
  return /^[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(s);
}

/** Resolve a uri written in a layer against that layer's base. Unresolvable relative paths are kept. */
function absolutize(uri: string, base: Base): string {
  if (hasScheme(uri)) {
    if (uri.startsWith("file://")) return decodeURIComponent(new URL(uri).pathname);
    return uri;
  }
  if (isAbsPath(uri)) return uri;
  if (base.kind === "path") return normalizePosix(`${base.dir.replace(/\\/g, "/")}/${uri}`);
  if (base.kind === "https") {
    const u = new URL(uri, base.url);
    return u.href;
  }
  return uri;
}

/** A ref to a layer (from `extends` or `include[].package`), already absolutized. */
function parseRef(raw: string, layer: string, field: string): Ref {
  const m = raw.match(SHA_URI);
  if (m) return { kind: "asset", id: raw, sha: m[1] };
  if (raw.startsWith("asset://")) throw new LayerError("layer_ref", `asset:// refs must be asset://<64 hex sha256>, got "${raw}"`, layer, field);
  if (raw.startsWith("https://")) return { kind: "https", id: new URL(raw).href };
  if (hasScheme(raw)) throw new LayerError("layer_ref", `unsupported scheme in "${raw}"; use https://, asset:// or a path`, layer, field);
  if (!isAbsPath(raw)) {
    throw new LayerError("layer_ref", `relative ref "${raw}" has no base to resolve against; use an https:// URL or asset:// URI`, layer, field);
  }
  return { kind: "path", id: normalizePosix(raw.replace(/\\/g, "/")) };
}

/** Is a scene/hdri ref a file path (vs. an asset id)? */
function looksLikePath(v: string): boolean {
  return hasScheme(v) || v.includes("/") || v.includes("\\") || v.startsWith(".") || /\.[A-Za-z0-9]{1,8}$/.test(v);
}

// ---- matrices (column-major, like glTF) -------------------------------------------------------

type Mat = number[];
const IDENTITY: Mat = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

function mul(a: Mat, b: Mat): Mat {
  const out = new Array(16).fill(0);
  for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
    let s = 0;
    for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
    out[c * 4 + r] = s;
  }
  return out;
}

/** Same convention as core's trs(): T · Rz · Ry · Rx · S, XYZ euler degrees. */
export function transformMatrix(t: unknown): Mat {
  if (!isObj(t)) return [...IDENTITY];
  if (Array.isArray(t.matrix) && t.matrix.length === 16) return (t.matrix as number[]).map(Number);
  const tr = (Array.isArray(t.translate) ? t.translate : [0, 0, 0]) as number[];
  const rot = (Array.isArray(t.rotateDeg) ? t.rotateDeg : [0, 0, 0]) as number[];
  const sc = t.scale === undefined ? 1 : (t.scale as number | number[]);
  const s = Array.isArray(sc) ? sc : [sc, sc, sc];
  const [rx, ry, rz] = rot.map((d) => (d * Math.PI) / 180);
  const [cx, sx, cy, sy, cz, sz] = [Math.cos(rx), Math.sin(rx), Math.cos(ry), Math.sin(ry), Math.cos(rz), Math.sin(rz)];
  // R = Rz · Ry · Rx (row-major entries)
  const R = [
    [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
    [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
    [-sy, cy * sx, cy * cx],
  ];
  const m = new Array(16).fill(0);
  for (let r = 0; r < 3; r++) for (let c = 0; c < 3; c++) m[c * 4 + r] = R[r][c] * s[c];
  m[12] = tr[0]; m[13] = tr[1]; m[14] = tr[2]; m[15] = 1;
  return m;
}

const round = (x: number) => {
  const v = Math.round(x * 1e10) / 1e10;
  return Object.is(v, -0) ? 0 : v;
};
const isIdentity = (m: Mat) => m.every((v, i) => Math.abs(v - IDENTITY[i]) < 1e-12);

/**
 * Axis conversion between up conventions, matching core: "-Y" (COLMAP) flips to "Y";
 * Z-up → Y-up is (x, y, z) → (x, z, -y).
 */
export function upConversion(from: string, to: string): Mat {
  const FLIP: Mat = [1, 0, 0, 0, 0, -1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 1];
  const Z_TO_Y: Mat = [1, 0, 0, 0, 0, 0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 1];
  const Y_TO_Z: Mat = [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1];
  const toY = from === "-Y" ? FLIP : from === "Z" ? Z_TO_Y : IDENTITY;
  const fromY = to === "-Y" ? FLIP : to === "Z" ? Y_TO_Z : IDENTITY;
  return mul(fromY, toY);
}

/** Up-axis convention of each format, as core's loaders assume when the file doesn't say. */
const FORMAT_UP: Record<string, string> = {
  gltf: "Y", threejs: "Y", obj: "Y", dae: "Y", ply: "Y", usd: "Y", splat: "Y",
  stl: "Z", "3mf": "Z", blend: "Z", fbx: "Z", abc: "Z", step: "Z", iges: "Z",
};
const EXT_FORMAT: Record<string, string> = {
  glb: "gltf", gltf: "gltf", json: "threejs", obj: "obj", dae: "dae", ply: "ply", usd: "usd", usda: "usd", usdc: "usd",
  usdz: "usd", splat: "splat", spz: "splat", ksplat: "splat", stl: "stl", "3mf": "3mf", blend: "blend", fbx: "fbx",
  abc: "abc", step: "step", stp: "step", iges: "iges", igs: "iges",
};

/** Best guess at a scene's up axis: declared upAxis, else its root file's format convention. */
function sceneUp(scene: Obj | undefined, assets: Obj | undefined): string | undefined {
  if (!scene) return undefined;
  if (typeof scene.upAxis === "string") return scene.upAxis;
  const ref = scene.root;
  if (typeof ref !== "string") return undefined;
  const asset = assets && isObj(assets[ref]) ? (assets[ref] as Obj) : undefined;
  if (asset && typeof asset.format === "string" && FORMAT_UP[asset.format]) return FORMAT_UP[asset.format];
  const uri = asset && typeof asset.uri === "string" ? asset.uri : ref;
  const ext = uri.toLowerCase().replace(/[?#].*$/, "").match(/\.([a-z0-9]+)$/)?.[1];
  return ext && EXT_FORMAT[ext] ? FORMAT_UP[EXT_FORMAT[ext]] : undefined;
}

function applyPoint(m: Mat, p: number[]): number[] {
  return [0, 1, 2].map((r) => round(m[r] * p[0] + m[4 + r] * p[1] + m[8 + r] * p[2] + m[12 + r]));
}

function applyDirection(m: Mat, d: number[]): number[] {
  const v = [0, 1, 2].map((r) => m[r] * d[0] + m[4 + r] * d[1] + m[8 + r] * d[2]);
  const n = Math.hypot(v[0], v[1], v[2]) || 1;
  return v.map((x) => round(x / n));
}

// ---- merge ------------------------------------------------------------------------------------

function dropProvenance(prov: Record<string, string>, path: string) {
  for (const k of Object.keys(prov)) {
    if (k === path || k.startsWith(`${path}.`) || k.startsWith(`${path}[`)) delete prov[k];
  }
}

type LabelOf = (path: string) => string;

function recordLeaves(v: Json, path: string, labelOf: LabelOf, prov: Record<string, string>) {
  if (isObj(v) && Object.keys(v).length) {
    for (const [k, c] of Object.entries(v)) recordLeaves(c, fieldPath(path, k), labelOf, prov);
  } else {
    prov[path] = labelOf(path);
  }
}

/**
 * Merge `over` onto `base` in place (base is owned), recording who set each leaf.
 * Objects merge, arrays/primitives replace, null deletes (except NULLABLE paths), and objects whose
 * discriminator (`mode`/`type`) differs replace rather than mix fields from two kinds.
 */
function mergeInto(base: Obj, over: Obj, labelOf: LabelOf, prov: Record<string, string>, path = "") {
  for (const [k, v] of Object.entries(over)) {
    const p = fieldPath(path, k);
    if (v === null && !NULLABLE.has(p)) {
      delete base[k];
      dropProvenance(prov, p);
      continue;
    }
    const cur = base[k];
    const kindClash = isObj(cur) && isObj(v) && DISCRIMINATORS.some((d) => d in cur && d in v && cur[d] !== v[d]);
    if (isObj(cur) && isObj(v) && !kindClash) {
      mergeInto(cur, v, labelOf, prov, p);
    } else {
      base[k] = clone(v);
      dropProvenance(prov, p);
      recordLeaves(v, p, labelOf, prov);
    }
  }
}

function deepMerge(...objs: (Obj | undefined)[]): Obj {
  const out: Obj = {};
  for (const o of objs) if (o) mergeInto(out, o, () => "", {});
  return out;
}

// ---- the resolver -----------------------------------------------------------------------------

interface Ctx {
  opts: Required<Pick<FlattenOptions, "maxDepth" | "maxLayers" | "maxBytes">> & LayerLoaders;
  layers: string[];
  cache: Map<string, FlattenResult>;
}

/** Rewrite relative uris in a layer against its base, so they survive being merged elsewhere. */
function absolutizeLayer(doc: Obj, base: Base): Obj {
  const out = clone(doc);
  const assets = isObj(out.assets) ? out.assets : undefined;
  const own = new Set(assets ? Object.keys(assets) : []);
  if (assets) for (const a of Object.values(assets)) if (isObj(a) && typeof a.uri === "string") a.uri = absolutize(a.uri, base);
  const fixRef = (v: Json | undefined): Json | undefined =>
    typeof v === "string" && !own.has(v) && looksLikePath(v) ? absolutize(v, base) : v;
  if (isObj(out.scene)) {
    if (typeof out.scene.root === "string") out.scene.root = fixRef(out.scene.root) as string;
    if (Array.isArray(out.scene.include)) {
      for (const inc of out.scene.include) {
        if (!isObj(inc)) continue;
        if (typeof inc.asset === "string") inc.asset = fixRef(inc.asset) as string;
        if (typeof inc.package === "string") inc.package = absolutize(inc.package, base);
      }
    }
  }
  if (isObj(out.scene) && Array.isArray(out.scene.textureOverrides)) {
    for (const t of out.scene.textureOverrides) if (isObj(t) && typeof t.source === "string") t.source = fixRef(t.source) as string;
  }
  if (isObj(out.environment) && typeof out.environment.hdri === "string" && !BUILTIN_HDRIS.has(out.environment.hdri)) {
    out.environment.hdri = fixRef(out.environment.hdri) as string;
  }
  if (typeof out.extends === "string") out.extends = absolutize(out.extends, base);
  else if (Array.isArray(out.extends)) out.extends = out.extends.map((e) => (typeof e === "string" ? absolutize(e, base) : e));
  return out;
}

async function loadRef(ref: Ref, ctx: Ctx, from: string, field: string): Promise<{ doc: Obj; base: Base }> {
  if (ctx.layers.length >= ctx.opts.maxLayers) {
    throw new LayerError("layer_too_many", `more than ${ctx.opts.maxLayers} layers`, from, field);
  }
  ctx.layers.push(ref.id);
  let text: string;
  try {
    if (ref.kind === "path") {
      if (!ctx.opts.readFile) throw new LayerError("layer_not_allowed", `local layer "${ref.id}" can't be read here; use an https:// URL or upload it and use its asset:// URI`, from, field);
      text = await ctx.opts.readFile(ref.id);
    } else if (ref.kind === "https") {
      if (!ctx.opts.fetchHttps) throw new LayerError("layer_not_allowed", `remote layers are disabled here ("${ref.id}")`, from, field);
      text = await ctx.opts.fetchHttps(ref.id);
    } else {
      if (!ctx.opts.readAsset) throw new LayerError("layer_not_allowed", `asset:// layers can't be read here ("${ref.id}")`, from, field);
      text = await ctx.opts.readAsset(ref.sha);
    }
  } catch (e) {
    if (e instanceof LayerError) throw e;
    throw new LayerError("layer_fetch", `couldn't load "${ref.id}": ${(e as Error).message}`, from, field);
  }
  if (text.length > ctx.opts.maxBytes) {
    throw new LayerError("layer_too_large", `"${ref.id}" is larger than ${ctx.opts.maxBytes} bytes`, from, field);
  }
  let doc: unknown;
  try {
    doc = JSON.parse(text);
  } catch (e) {
    throw new LayerError("layer_parse", `"${ref.id}" is not valid JSON: ${(e as Error).message}`, from, field);
  }
  if (!isObj(doc)) throw new LayerError("layer_parse", `"${ref.id}" must be a JSON object`, from, field);
  const base: Base = ref.kind === "path" ? { kind: "path", dir: dirname(ref.id) } : ref.kind === "https" ? { kind: "https", url: ref.id } : { kind: "asset", uri: ref.id };
  return { doc, base };
}

async function flattenRef(ref: Ref, ctx: Ctx, stack: string[], from: string, field: string): Promise<FlattenResult> {
  if (stack.includes(ref.id)) {
    throw new LayerError("layer_cycle", `cycle: ${[...stack, ref.id].join(" → ")}`, from, field);
  }
  if (stack.length >= ctx.opts.maxDepth) {
    throw new LayerError("layer_depth", `layers nest deeper than ${ctx.opts.maxDepth}`, from, field);
  }
  const hit = ctx.cache.get(ref.id);
  if (hit) return { package: clone(hit.package), provenance: { ...hit.provenance }, layers: hit.layers };
  const { doc, base } = await loadRef(ref, ctx, from, field);
  const r = await flattenDoc(doc, base, ref.id, ctx, [...stack, ref.id]);
  ctx.cache.set(ref.id, r);
  return { package: clone(r.package), provenance: { ...r.provenance }, layers: r.layers };
}

async function flattenDoc(raw: Obj, base: Base, label: string, ctx: Ctx, stack: string[]): Promise<FlattenResult> {
  const own = absolutizeLayer(raw, base);
  const acc: Obj = {};
  const prov: Record<string, string> = {};

  // 1. layers underneath, in order
  const ext = own.extends;
  delete own.extends;
  const refs = ext === undefined ? [] : Array.isArray(ext) ? ext : [ext];
  for (const [i, r] of refs.entries()) {
    const field = Array.isArray(ext) ? `extends[${i}]` : "extends";
    if (typeof r !== "string") throw new LayerError("layer_invalid", "extends entries must be strings", label, field);
    const ref = parseRef(r, label, field);
    const sub = await flattenRef(ref, ctx, stack, label, field);
    mergeInto(acc, sub.package as Obj, (p) => sub.provenance[p] ?? ref.id, prov);
  }

  // 2. package references declared in this layer
  const { doc: resolved, extraLights, refProvenance } = await resolveReferences(own, acc, label, ctx, stack);

  // 3. this layer on top
  mergeInto(acc, resolved, () => label, prov);
  if (extraLights.length) {
    const lights = Array.isArray(acc.lights) ? acc.lights : [];
    acc.lights = [...lights, ...extraLights];
    const start = lights.length;
    extraLights.forEach((_, i) => { prov[`lights[${start + i}]`] = refProvenance.lights[i]; });
  }
  for (const [k, v] of Object.entries(refProvenance.paths)) { dropProvenance(prov, k); prov[k] = v; }
  return { package: acc, provenance: prov, layers: [...ctx.layers] };
}

async function resolveReferences(own: Obj, under: Obj, label: string, ctx: Ctx, stack: string[]) {
  const extraLights: Json[] = [];
  const refProvenance = { paths: {} as Record<string, string>, lights: [] as string[] };
  const scene = isObj(own.scene) ? own.scene : undefined;
  const includes = scene && Array.isArray(scene.include) ? scene.include : [];
  if (!includes.some((i) => isObj(i) && "package" in i)) return { doc: own, extraLights, refProvenance };

  const out: Json[] = [];
  const extraAssets: Obj = {};
  for (const [i, inc] of includes.entries()) {
    if (!isObj(inc) || !("package" in inc)) { out.push(inc); continue; }
    const field = `scene.include[${i}].package`;
    if (typeof inc.package !== "string") throw new LayerError("layer_invalid", "package must be a string", label, field);
    const ns = typeof inc.as === "string" ? inc.as : `ref${i}`;
    if (!/^[A-Za-z0-9_-]+$/.test(ns)) throw new LayerError("layer_invalid", `"as" must match [A-Za-z0-9_-]+, got "${ns}"`, label, `scene.include[${i}].as`);
    const ref = parseRef(inc.package, label, field);
    const sub = (await flattenRef(ref, ctx, stack, label, field)).package as Obj;
    const subScene = isObj(sub.scene) ? sub.scene : undefined;
    if (!subScene || typeof subScene.root !== "string") {
      throw new LayerError("layer_invalid", `referenced package "${ref.id}" has no scene.root`, label, field);
    }
    const subAssets = isObj(sub.assets) ? sub.assets : {};
    const rename = (x: string) => (x in subAssets ? `${ns}.${x}` : x);
    for (const [id, a] of Object.entries(subAssets)) {
      const nid = `${ns}.${id}`;
      if (nid in extraAssets || (isObj(own.assets) && nid in own.assets)) {
        throw new LayerError("layer_invalid", `asset id "${nid}" collides; set a different "as"`, label, `scene.include[${i}].as`);
      }
      extraAssets[nid] = clone(a);
      refProvenance.paths[fieldPath("assets", nid)] = ref.id;
    }
    // world = placement · C(sub up → root up) · unitScale · (sub-internal transform) · content.
    // Core applies `transform · C(upAxis → root up) · localTransform`, so the placement (and the
    // uniform unit scale, which commutes with C) goes in `transform` and anything expressed in the
    // sub-package's own axes goes in `localTransform`.
    const unit = typeof subScene.unitScale === "number" ? subScene.unitScale : 1;
    const U: Mat = [unit, 0, 0, 0, 0, unit, 0, 0, 0, 0, unit, 0, 0, 0, 0, 1];
    const placed = transformMatrix(inc.transform);
    const outer = unit === 1 ? placed : mul(placed, U);
    const subUp = sceneUp(subScene, subAssets);
    // the root scene this lands in: this layer's, falling back to the layers underneath
    const rootScene = deepMerge(isObj(under.scene) ? under.scene : undefined, isObj(own.scene) ? own.scene : undefined);
    const rootAssets = deepMerge(isObj(under.assets) ? under.assets : undefined, isObj(own.assets) ? own.assets : undefined);
    const rawRootUp = sceneUp(rootScene, rootAssets);
    const rootUp = rawRootUp === "-Y" ? "Y" : rawRootUp; // core normalizes a Y-down root to Y-up
    const scopedBase = deepMerge(isObj(subScene.materialOverrides) ? subScene.materialOverrides : undefined);
    const parentOv = isObj(inc.materialOverrides) ? inc.materialOverrides : undefined;
    const keepWritten = unit === 1 && inc.transform !== undefined && !(isObj(inc.transform) && inc.transform.matrix);

    const emit = (asset: string, local: Mat, ov: Obj, up: string | undefined) => {
      const e: Obj = { asset };
      if (keepWritten) e.transform = clone(inc.transform as Json);
      else if (!isIdentity(outer)) e.transform = { matrix: outer.map(round) };
      if (up) e.upAxis = up;
      if (!isIdentity(local)) e.localTransform = { matrix: local.map(round) };
      if (Object.keys(ov).length) e.materialOverrides = ov;
      refProvenance.paths[`scene.include[${out.length}]`] = ref.id;
      out.push(e);
    };
    // the referenced root
    emit(rename(subScene.root), IDENTITY, deepMerge(scopedBase, parentOv), subUp);
    // its own includes: their placement is in the sub-package's axes → localTransform
    for (const si of Array.isArray(subScene.include) ? subScene.include : []) {
      if (!isObj(si) || typeof si.asset !== "string") continue;
      const siUp = typeof si.upAxis === "string" ? si.upAxis : undefined;
      let local = transformMatrix(si.transform);
      // a nested include in different axes: convert it into the sub-package's axes here
      if (siUp && subUp && siUp !== subUp) local = mul(local, upConversion(siUp, subUp));
      local = mul(local, transformMatrix(si.localTransform));
      const ov = deepMerge(scopedBase, isObj(si.materialOverrides) ? si.materialOverrides : undefined, parentOv);
      emit(rename(si.asset), local, ov, subUp ?? siUp);
    }
    // its lights (in the sub-package's axes), moved into this package
    const lightM = subUp && rootUp ? mul(outer, upConversion(subUp, rootUp)) : outer;
    for (const l of Array.isArray(sub.lights) ? sub.lights : []) {
      if (!isObj(l)) continue;
      const nl = clone(l);
      for (const k of ["position", "target"]) if (Array.isArray(nl[k])) nl[k] = applyPoint(lightM, nl[k] as number[]);
      if (Array.isArray(nl.direction)) nl.direction = applyDirection(lightM, nl.direction as number[]);
      extraLights.push(nl);
      refProvenance.lights.push(ref.id);
    }
  }
  const doc = clone(own);
  (doc.scene as Obj).include = out;
  if (Object.keys(extraAssets).length) doc.assets = { ...extraAssets, ...(isObj(own.assets) ? own.assets : {}) };
  return { doc, extraLights, refProvenance };
}

function baseFrom(base: string | null | undefined): Base {
  if (!base) return { kind: "none" };
  if (base.startsWith("https://")) return { kind: "https", url: base };
  if (base.startsWith("asset://")) return { kind: "asset", uri: base };
  if (hasScheme(base)) throw new LayerError("layer_ref", `unsupported base "${base}"`, "<root>");
  return { kind: "path", dir: base };
}

/**
 * Resolve `extends` and package references into a single flat package (raw JSON — defaults are not
 * applied, so "which fields did the author set" survives). Packages without either are returned
 * unchanged. Throws {@link LayerError}.
 */
export async function flattenPackage(input: unknown, opts: FlattenOptions = {}): Promise<FlattenResult> {
  const label = opts.label ?? "<root>";
  if (!isObj(input)) throw new LayerError("layer_parse", "a package must be a JSON object", label);
  if (!isLayered(input)) return { package: clone(input), provenance: {}, layers: [] };
  const ctx: Ctx = {
    opts: {
      maxDepth: opts.maxDepth ?? LAYER_LIMITS.maxDepth,
      maxLayers: opts.maxLayers ?? LAYER_LIMITS.maxLayers,
      maxBytes: opts.maxBytes ?? LAYER_LIMITS.maxBytes,
      readFile: opts.readFile,
      fetchHttps: opts.fetchHttps,
      readAsset: opts.readAsset,
    },
    layers: [],
    cache: new Map(),
  };
  const r = await flattenDoc(input as Obj, baseFrom(opts.base), label, ctx, [label]);
  return { package: r.package, provenance: r.provenance, layers: ctx.layers };
}
