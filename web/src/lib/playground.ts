/**
 * Dashboard playground: turn dropped files + a few choices into a Renda package. Pure (no DOM, no DB) so
 * the browser and the tests share it; the server validates whatever comes back like any other package.
 */
import { formatFromPath, type RendaPackageInput } from "@rendahq/spec";
import { CLI_PACKAGE } from "./install-snippets";
import { SITE_URL } from "./site";

export type PlaygroundAction = "still" | "turntable" | "bake" | "splat" | "glb";
export type Background = "studio" | "transparent" | `#${string}`;
export type Quality = "draft" | "standard" | "final";
export type Priority = "economy" | "standard" | "rush";

export const ACTIONS: { id: PlaygroundAction; label: string; hint: string }[] = [
  { id: "still", label: "Still", hint: "One image" },
  { id: "turntable", label: "Turntable", hint: "Orbit video" },
  { id: "bake", label: "Bake → GLB", hint: "Lighting baked into textures" },
  { id: "splat", label: "Splat", hint: "Gaussian splat (.spz)" },
  { id: "glb", label: "Convert → GLB", hint: "Any format to GLB" },
];

export const ENGINES = ["auto", "blender-cycles", "blender-eevee", "blender-workbench", "mitsuba", "threejs", "gsplat"] as const;
export type PlaygroundEngine = (typeof ENGINES)[number];
/** Engines that can bake lightmaps / render splat training views (spec cross-field rules). */
export const BAKE_ENGINES: PlaygroundEngine[] = ["auto", "blender-cycles", "mitsuba", "threejs"];
export const SPLAT_VIEW_ENGINES: PlaygroundEngine[] = ["auto", "blender-cycles", "blender-eevee", "mitsuba", "threejs"];

export const RESOLUTIONS: { label: string; value: [number, number] }[] = [
  { label: "1280×720", value: [1280, 720] },
  { label: "1920×1080", value: [1920, 1080] },
  { label: "1080×1080", value: [1080, 1080] },
  { label: "2048×2048", value: [2048, 2048] },
  { label: "1080×1920", value: [1080, 1920] },
];

export type PlaygroundOptions = {
  action: PlaygroundAction;
  engine: PlaygroundEngine;
  resolution: [number, number];
  quality: Quality;
  background: Background;
  priority: Priority;
  turntableSeconds: number;
  fps: number;
  bakeMode: "unlit" | "ao" | "lightmap";
  bakeSize: number;
  splatPreset: "fast" | "standard" | "high";
};

export const DEFAULT_OPTIONS: PlaygroundOptions = {
  action: "still",
  engine: "auto",
  resolution: [1280, 720],
  quality: "standard",
  background: "studio",
  priority: "standard",
  turntableSeconds: 3,
  fps: 24,
  bakeMode: "unlit",
  bakeSize: 2048,
  splatPreset: "standard",
};

/** A file the user dropped, once hashed. `uri` is asset://<sha256>. */
export type PlaygroundFile = { name: string; size: number; uri: string; role: FileRole };
export type FileRole = "scene" | "hdri" | "sidecar" | "unsupported";

const HDRI = /\.(hdr|exr)$/i;
/** Files a scene references by relative path; .gltf siblings get inlined in the browser. */
const SIDECAR = /\.(bin|mtl|png|jpe?g|webp|ktx2|tga|bmp|tiff?)$/i;

export function roleFor(name: string): FileRole {
  if (formatFromPath(name)) return "scene";
  if (HDRI.test(name)) return "hdri";
  if (SIDECAR.test(name)) return "sidecar";
  return "unsupported";
}

/** The scene file to render: the first scene-format file, preferring self-contained formats. */
export function pickScene<T extends { name: string; role: FileRole }>(files: T[]): T | undefined {
  const scenes = files.filter((f) => f.role === "scene");
  return scenes.find((f) => !/\.(gltf|obj)$/i.test(f.name)) ?? scenes[0];
}

/** Build the package for the chosen action. Engine/hardware choices the action can't use fall back to auto. */
export function buildPackage(files: PlaygroundFile[], o: PlaygroundOptions): RendaPackageInput {
  const scene = pickScene(files);
  if (!scene) throw new Error("Add a 3D file (.glb, .blend, .usdz, .fbx, .obj, .stl, .step, .ply, splats …).");
  const hdri = files.find((f) => f.role === "hdri");
  const fmt = formatFromPath(scene.name);
  const assets: NonNullable<RendaPackageInput["assets"]> = { scene: { uri: scene.uri, ...(fmt ? { format: fmt } : {}) } };
  if (hdri) assets.env = { uri: hdri.uri, format: /\.exr$/i.test(hdri.name) ? "exr" : "hdr" };

  const transparent = o.background === "transparent";
  const environment: NonNullable<RendaPackageInput["environment"]> = {
    hdri: hdri ? "env" : "studio",
    background: transparent ? "transparent" : o.background === "studio" ? "hdri" : o.background,
  };
  const name = `playground · ${scene.name}`.slice(0, 200);
  const base = { version: 1 as const, name, assets, scene: { root: "scene" }, environment };

  switch (o.action) {
    case "still":
      return {
        ...base,
        render: { engine: o.engine, resolution: o.resolution, quality: o.quality },
        output: { format: "png", transparent },
      };
    case "turntable": {
      const frames = Math.max(2, Math.round(o.turntableSeconds * o.fps));
      return {
        ...base,
        render: { engine: o.engine, resolution: o.resolution, quality: o.quality },
        animation: { frames: [1, frames], fps: o.fps, turntable: { revolutions: 1 } },
        output: transparent ? { format: "webm", transparent: true } : { format: "mp4" },
      };
    }
    case "bake":
      return {
        ...base,
        render: { engine: BAKE_ENGINES.includes(o.engine) ? o.engine : "auto", quality: o.quality },
        output: { format: "glb", bake: { mode: o.bakeMode, size: o.bakeSize } },
      };
    case "splat":
      return {
        ...base,
        output: {
          format: "splat",
          splatFormat: "spz",
          splatBake: { preset: o.splatPreset, engine: SPLAT_VIEW_ENGINES.includes(o.engine) ? o.engine : "auto" },
        },
      };
    case "glb":
      return { ...base, output: { format: "glb" } };
  }
}

/**
 * Inline a .gltf's external buffers/images (dropped next to it) as data URIs, so it's one self-contained file.
 * Returns the new JSON text and the names that were inlined; missing references are reported, not fatal.
 */
export function inlineGltf(gltfText: string, siblings: Map<string, Uint8Array>): { text: string; inlined: string[]; missing: string[] } {
  const doc = JSON.parse(gltfText) as { buffers?: { uri?: string }[]; images?: { uri?: string; mimeType?: string }[] };
  const inlined: string[] = [];
  const missing: string[] = [];
  const lookup = (uri: string) => {
    const name = decodeURIComponent(uri).split(/[\\/]/).pop()!;
    return { name, bytes: siblings.get(name) ?? siblings.get(name.toLowerCase()) };
  };
  const swap = (entry: { uri?: string; mimeType?: string }, mime: string) => {
    if (!entry.uri || entry.uri.startsWith("data:")) return;
    const { name, bytes } = lookup(entry.uri);
    if (!bytes) return void missing.push(name);
    entry.uri = `data:${mime};base64,${toBase64(bytes)}`;
    inlined.push(name);
  };
  for (const b of doc.buffers ?? []) swap(b, "application/octet-stream");
  for (const im of doc.images ?? []) swap(im, im.mimeType ?? mimeFor(im.uri ?? ""));
  return { text: JSON.stringify(doc), inlined, missing };
}

function mimeFor(uri: string): string {
  const ext = uri.toLowerCase().split(".").pop();
  return ext === "png" ? "image/png" : ext === "webp" ? "image/webp" : ext === "ktx2" ? "image/ktx2" : "image/jpeg";
}

function toBase64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

/** Content type to sign into the upload URL (a plain MIME type; the server rejects anything else). */
export function contentTypeFor(name: string): string {
  const n = name.toLowerCase();
  if (n.endsWith(".glb")) return "model/gltf-binary";
  if (n.endsWith(".gltf")) return "model/gltf+json";
  if (n.endsWith(".usdz")) return "model/vnd.usdz+zip";
  if (n.endsWith(".stl")) return "model/stl";
  if (n.endsWith(".obj")) return "model/obj";
  if (n.endsWith(".json")) return "application/json";
  if (n.endsWith(".png")) return "image/png";
  if (/\.jpe?g$/.test(n)) return "image/jpeg";
  if (n.endsWith(".hdr")) return "image/vnd.radiance";
  if (n.endsWith(".exr")) return "image/x-exr";
  return "application/octet-stream";
}

/** The same job as a CLI command, an MCP tool call and raw JSON — for "copy as". */
export function copyAs(pkg: RendaPackageInput, o: PlaygroundOptions, sceneName: string) {
  const res = `${o.resolution[0]}x${o.resolution[1]}`;
  const eng = o.engine === "auto" ? "" : ` --engine ${o.engine}`;
  // The CLI has no priority flag (it uses the planner's default); the MCP/JSON forms carry it.
  const prio = "";
  const file = /\s/.test(sceneName) ? JSON.stringify(sceneName) : sceneName;
  const cli = {
    still: `npx ${CLI_PACKAGE} render ${file} --res ${res} --quality ${o.quality}${eng}${prio}`,
    turntable: `npx ${CLI_PACKAGE} render ${file} --res ${res} --quality ${o.quality} --frames 1-${Math.round(o.turntableSeconds * o.fps)} --format mp4${eng}${prio}`,
    bake: `npx ${CLI_PACKAGE} bake ${file} --mode ${o.bakeMode} --size ${o.bakeSize}${eng}${prio}`,
    splat: `npx ${CLI_PACKAGE} splat ${file} --preset ${o.splatPreset} --format spz${prio}`,
    glb: `npx ${CLI_PACKAGE} bake ${file} --glb${prio}`,
  }[o.action];
  const tool = o.action === "bake" || o.action === "glb" ? "renda_bake" : o.action === "splat" ? "renda_bake_splat" : "renda_render";
  const mcp = JSON.stringify({ tool, arguments: { package: pkg, priority: o.priority } }, null, 2);
  const json = JSON.stringify(pkg, null, 2);
  const curl = `curl -X POST ${SITE_URL}/api/v1/jobs -H "Authorization: Bearer $RENDA_API_KEY" -H "content-type: application/json" -d @job.json`;
  return { cli, mcp, json, curl };
}
