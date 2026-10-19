import { z } from "zod";
import { isLayered } from "./layers.js";

/** Scene file formats Renda can ingest. */
export const SceneFormat = z.enum([
  "blend",
  "usd",
  "gltf",
  "threejs",
  "fbx",
  "obj",
  "stl",
  "ply",
  "dae",
  "3mf",
  "abc",
  "splat",
  "step",
  "iges",
  "rir",
]);
export type SceneFormat = z.infer<typeof SceneFormat>;

/** Extension → format. `.json` is sniffed (three.js JSON has `metadata.type === "Object"`). */
export const EXTENSION_FORMATS: Record<string, SceneFormat> = {
  ".blend": "blend",
  ".usd": "usd",
  ".usda": "usd",
  ".usdc": "usd",
  ".usdz": "usd",
  ".gltf": "gltf",
  ".glb": "gltf",
  ".json": "threejs",
  ".fbx": "fbx",
  ".obj": "obj",
  ".stl": "stl",
  ".ply": "ply",
  ".dae": "dae",
  ".3mf": "3mf",
  ".abc": "abc",
  ".splat": "splat",
  ".spz": "splat",
  ".ksplat": "splat",
  ".step": "step",
  ".stp": "step",
  ".iges": "iges",
  ".igs": "iges",
  ".rir": "rir",
};

export const Engine = z.enum([
  "auto",
  "blender-cycles",
  "blender-eevee",
  "blender-workbench",
  "mitsuba",
  "threejs",
  "gsplat",
]);
export type Engine = z.infer<typeof Engine>;

const Vec3 = z.tuple([z.number(), z.number(), z.number()]);
const Color = z.union([
  z.string().regex(/^#([0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/, "hex colour #rrggbb or #rrggbbaa"),
  Vec3,
]);

/** `./relative/path`, `https://…`, or `asset://<sha256>`. */
export const Uri = z.string().min(1);

export const Asset = z.strictObject({
  uri: Uri,
  format: SceneFormat.or(z.enum(["hdr", "exr", "png", "jpg", "webp", "mp4", "other"])).optional()
    .describe("Override format sniffing."),
  sha256: z.string().regex(/^[0-9a-f]{64}$/).optional(),
});

export const Transform = z.strictObject({
  translate: Vec3.default([0, 0, 0]),
  rotateDeg: Vec3.default([0, 0, 0]).describe("XYZ euler, degrees"),
  scale: z.union([z.number(), Vec3]).default(1),
  matrix: z.array(z.number()).length(16).optional()
    .describe("4×4 column-major (glTF order). When set, translate/rotateDeg/scale are ignored. Written by layer flattening when transforms compose."),
});

export const MaterialOverride = z.strictObject({
  baseColor: Color.optional(),
  metallic: z.number().min(0).max(1).optional(),
  roughness: z.number().min(0).max(1).optional(),
  emission: Color.optional(),
  emissionStrength: z.number().min(0).optional(),
  opacity: z.number().min(0).max(1).optional(),
  transmission: z.number().min(0).max(1).optional(),
  ior: z.number().min(1).optional(),
});

/** Reference to an asset id in `assets`, or an inline uri. */
const AssetRef = z.string().min(1);

const UpAxis = z.enum(["Y", "Z", "-Y"]);

/** An extra scene file placed into the scene (flattened form). */
export const Include = z.strictObject({
  asset: AssetRef,
  transform: Transform.optional().describe("Placement in the root scene's axes."),
  upAxis: UpAxis.optional()
    .describe("Axes of this file; converted to the root scene's up axis before `transform`. Unset = no conversion."),
  localTransform: Transform.optional()
    .describe("Applied in the file's own axes, before the up-axis conversion (written by layer flattening for nested references)."),
  materialOverrides: z.record(z.string(), MaterialOverride).optional()
    .describe('Overrides applied to this include only. Keys are material names or "*".'),
});

/** Before flattening: another package's whole scene (assets, lights, material overrides), placed by a transform. */
export const PackageInclude = z.strictObject({
  package: Uri.describe("Path, https:// URL or asset:// URI of a .renda.json whose scene is placed here."),
  as: z.string().regex(/^[A-Za-z0-9_-]+$/).optional()
    .describe("Namespace for the referenced package's asset ids (default ref<index>)."),
  transform: Transform.optional(),
  materialOverrides: z.record(z.string(), MaterialOverride).optional(),
});

export const Tessellation = z.strictObject({
  linearDeflection: z.number().positive().optional()
    .describe("Max chord error in metres. Default: 0.05% of the part's bounding-box diagonal."),
  angularDeflection: z.number().positive().max(3.14).optional().describe("Max angle between facets, radians. Default 0.3."),
});

export const Scene = z.strictObject({
  root: AssetRef.describe("Asset id or uri of the main scene file."),
  include: z.array(Include).default([]),
  upAxis: UpAxis.optional()
    .describe('Defaults to the source format\'s convention. "-Y" = COLMAP/OpenCV (Y down), the usual axes of Gaussian splat captures (.ply/.splat default to it).'),
  unitScale: z.number().positive().default(1)
    .describe("Multiplies scene units. STEP/IGES are converted to metres from their declared units first; this applies on top."),
  materialOverrides: z.record(z.string(), MaterialOverride).default({})
    .describe('Keys are material names or "*" for all.'),
  tessellation: Tessellation.optional().describe("Meshing density for CAD (STEP/IGES) inputs."),
});

export const Camera = z.discriminatedUnion("mode", [
  z.strictObject({
    mode: z.literal("auto"),
    fovDeg: z.number().gt(0).lt(180).default(35),
    elevationDeg: z.number().default(20),
    azimuthDeg: z.number().default(35),
    padding: z.number().min(0).default(0.1),
  }),
  z.strictObject({ mode: z.literal("scene"), name: z.string().optional().describe("Defaults to the active camera.") }),
  z.strictObject({
    mode: z.literal("lookat"),
    position: Vec3,
    target: Vec3.default([0, 0, 0]),
    up: Vec3.optional(),
    fovDeg: z.number().gt(0).lt(180).default(40),
  }),
]);

export const Environment = z.strictObject({
  hdri: AssetRef.nullable().default("studio").describe('Asset id/uri of an equirect .hdr/.exr, or "studio" | "sunset" | "night" built-ins. null = no environment lighting.'),
  intensity: z.number().min(0).default(1),
  rotationDeg: z.number().default(0),
  background: z.union([z.enum(["hdri", "transparent"]), Color]).default("hdri"),
});

export const Light = z.discriminatedUnion("type", [
  z.strictObject({ type: z.literal("sun"), direction: Vec3, intensity: z.number().min(0).default(3), color: Color.optional() }),
  z.strictObject({ type: z.literal("point"), position: Vec3, intensity: z.number().min(0).default(100), color: Color.optional(), radius: z.number().min(0).default(0.05) }),
  z.strictObject({ type: z.literal("spot"), position: Vec3, target: Vec3, intensity: z.number().min(0).default(100), angleDeg: z.number().gt(0).lte(180).default(45), color: Color.optional() }),
  z.strictObject({ type: z.literal("area"), position: Vec3, target: Vec3, size: z.tuple([z.number(), z.number()]).default([1, 1]), intensity: z.number().min(0).default(50), color: Color.optional() }),
]);

export const Quality = z.enum(["draft", "standard", "final"]);

export const Render = z.strictObject({
  engine: Engine.default("auto"),
  resolution: z.tuple([z.number().int().min(16).max(16384), z.number().int().min(16).max(16384)]).default([1920, 1080]),
  quality: Quality.default("standard"),
  samples: z.number().int().positive().nullable().default(null).describe("Overrides the quality preset."),
  denoise: z.boolean().default(true),
  seed: z.number().int().default(0),
  hardware: z.enum(["auto", "cpu", "gpu"]).default("auto"),
});

export const Animation = z.strictObject({
  frames: z.tuple([z.number().int(), z.number().int()]).describe("Inclusive [start, end]."),
  fps: z.number().positive().default(30),
  step: z.number().int().positive().default(1),
  turntable: z.strictObject({
    revolutions: z.number().default(1),
    axis: z.enum(["up", "X", "Y", "Z"]).default("up"),
  }).optional().describe("Orbit the camera around the scene bounds."),
});

export const ImageFormat = z.enum(["png", "jpg", "webp", "exr"]);
export const VideoFormat = z.enum(["mp4", "webm", "gif", "mov"]);

export const Output = z.strictObject({
  format: z.union([ImageFormat, VideoFormat]).default("png"),
  passes: z.array(z.enum(["beauty", "alpha", "depth", "normal", "albedo", "mask"])).default(["beauty"]),
  transparent: z.boolean().default(false),
  video: z.strictObject({
    codec: z.enum(["h264", "h265", "vp9", "prores", "gif"]).optional(),
    crf: z.number().int().min(0).max(51).default(18),
  }).default({ crf: 18 }),
  keepFrames: z.boolean().default(false).describe("Also return individual frames for video outputs."),
});

const packageShape = {
  $schema: z.string().optional(),
  version: z.literal(1).default(1),
  name: z.string().max(200).optional(),
  assets: z.record(z.string().regex(/^[A-Za-z0-9_.-]+$/), Asset).default({}),
  scene: Scene,
  camera: Camera.default({ mode: "auto", fovDeg: 35, elevationDeg: 20, azimuthDeg: 35, padding: 0.1 }),
  environment: Environment.default({ hdri: "studio", intensity: 1, rotationDeg: 0, background: "hdri" }),
  lights: z.array(Light).default([]),
  render: Render.default({
    engine: "auto", resolution: [1920, 1080], quality: "standard", samples: null, denoise: true, seed: 0, hardware: "auto",
  }),
  animation: Animation.optional(),
  output: Output.default({ format: "png", passes: ["beauty"], transparent: false, video: { crf: 18 }, keepFrames: false }),
};

export const RendaPackage = z.strictObject(packageShape).superRefine((pkg, ctx) => {
  const isVideo = VideoFormat.safeParse(pkg.output.format).success;
  if (isVideo && !pkg.animation) {
    ctx.addIssue({ code: "custom", path: ["animation"], message: `output.format "${pkg.output.format}" needs an animation range` });
  }
  if (pkg.output.transparent && ["jpg", "mp4", "gif"].includes(pkg.output.format)) {
    ctx.addIssue({ code: "custom", path: ["output", "transparent"], message: `${pkg.output.format} has no alpha channel; use png/webp/exr/webm/mov` });
  }
});

export type RendaPackageInput = z.input<typeof RendaPackage>;
export type RendaPackage = z.output<typeof RendaPackage>;

/**
 * What authors write: a package that may layer others (`extends`) and reference other packages'
 * scenes (`scene.include[].package`). Flatten it (`flattenPackage` in ./layers) to get a
 * {@link RendaPackage}. `scene` is optional because a layer (e.g. shared lighting) may omit it.
 */
export const RendaPackageSource = z.strictObject({
  ...packageShape,
  extends: z.union([Uri, z.array(Uri)]).optional()
    .describe("Packages layered underneath this one, applied in order (later wins; this file wins over all). Objects deep-merge, arrays replace, null removes a field."),
  scene: Scene.extend({ include: z.array(z.union([Include, PackageInclude])).default([]) }).partial({ root: true }).optional(),
});
export type RendaPackageSource = z.input<typeof RendaPackageSource>;

export const LAYERED_MESSAGE =
  "This package uses `extends` or package references; flatten it first (flattenPackage from @rendahq/spec, or `renda flatten`).";

/** Parse + apply defaults. Throws ZodError with paths on failure. */
export function parsePackage(input: unknown): RendaPackage {
  const r = safeParsePackage(input);
  if (!r.success) throw r.error;
  return r.data;
}

export function safeParsePackage(input: unknown): ReturnType<typeof RendaPackage.safeParse> {
  if (isLayered(input)) {
    return { success: false, error: new z.ZodError([{ code: "custom", path: ["extends"], message: LAYERED_MESSAGE, input }]) } as ReturnType<typeof RendaPackage.safeParse>;
  }
  return RendaPackage.safeParse(input);
}

export * from "./layers.js";

/** Format from a file name/uri, or undefined. */
export function formatFromPath(path: string): SceneFormat | undefined {
  const m = path.toLowerCase().match(/(\.[a-z0-9]+)(?:[?#].*)?$/);
  return m ? EXTENSION_FORMATS[m[1]] : undefined;
}

/** Wrap a bare scene file into a default package. */
export function packageForFile(uri: string, overrides: Partial<RendaPackageInput> = {}): RendaPackage {
  return parsePackage({ ...overrides, assets: { scene: { uri }, ...(overrides.assets ?? {}) }, scene: { root: "scene", ...(overrides.scene ?? {}) } });
}

export const JSON_SCHEMA_ID = "https://renda.dev/schema/v1.json";
