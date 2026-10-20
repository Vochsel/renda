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
  "filament",
  "pbrt",
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
  format: SceneFormat.or(z.enum([
    "hdr", "exr", "png", "jpg", "webp", "avif", "heic", "tiff", "svg", "psd", "gif", "mp4", "mov", "webm", "other",
  ])).optional()
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

/** Name pattern: exact name, case-insensitive glob (`*` `?` `[...]`), or `/regex/flags`. */
export const NamePattern = z.string().min(1);

export const MaterialOverride = z.strictObject({
  object: NamePattern.optional().describe("Only materials used by objects whose name matches."),
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

export const TextureSlot = z.enum(["baseColor", "normal", "roughness", "metallic", "emission", "opacity"]);
export type TextureSlot = z.infer<typeof TextureSlot>;

export const TextureOverride = z.strictObject({
  match: z.strictObject({
    material: NamePattern.optional(),
    object: NamePattern.optional(),
    texture: NamePattern.optional().describe("File name of a texture the material already uses."),
    slot: TextureSlot.optional().describe("Default: the slot `texture` matched, else baseColor."),
  }).default({}).describe("Empty = every material."),
  source: AssetRef.describe("Any image (png/jpg/webp/avif/heic/tiff/exr/hdr/svg/psd/gif) or video (mp4/mov/webm/gif/apng)."),
  uv: z.strictObject({
    scale: z.union([z.number(), z.tuple([z.number(), z.number()])]).default(1),
    offset: z.tuple([z.number(), z.number()]).default([0, 0]),
    rotationDeg: z.number().default(0),
  }).default({ scale: 1, offset: [0, 0], rotationDeg: 0 }),
  video: z.strictObject({
    loop: z.boolean().default(true),
    startSec: z.number().min(0).default(0),
    speed: z.number().positive().default(1),
    frame: z.number().int().min(0).optional().describe("Stills: use this source frame instead of startSec."),
  }).default({ loop: true, startSec: 0, speed: 1 }),
  colorSpace: z.enum(["auto", "srgb", "linear"]).default("auto")
    .describe("auto: sRGB for baseColor/emission, linear for data maps."),
  strength: z.number().min(0).optional().describe("Normal-map strength or emission strength."),
});

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

/** Ground plane under the scene: a shadow catcher (shadows only, composited onto any background) or a visible floor. */
export const Ground = z.strictObject({
  enabled: z.boolean().default(true),
  mode: z.enum(["shadowCatcher", "solid", "reflective"]).default("shadowCatcher")
    .describe("shadowCatcher: only the shadows show (transparent-friendly); solid: a matte floor; reflective: a glossy floor."),
  offset: z.number().nullable().default(null)
    .describe("Height of the plane along the up axis, in scene units. null = the bottom of the scene's bounds."),
  size: z.number().positive().nullable().default(null).describe("Plane side length. null = large enough to reach the horizon."),
  color: Color.default("#d9d9d9").describe("Floor colour for solid/reflective."),
  roughness: z.number().min(0).max(1).default(0.6),
  reflectivity: z.number().min(0).max(1).default(0.5).describe("reflective mode: strength of reflections."),
  shadowOpacity: z.number().min(0).max(1).default(1).describe("shadowCatcher: shadow darkness multiplier."),
  contactShadows: z.boolean().default(true).describe("Ambient-occlusion style darkening where the object meets the floor."),
});

export const Scene = z.strictObject({
  root: AssetRef.describe("Asset id or uri of the main scene file."),
  include: z.array(Include).default([]),
  upAxis: UpAxis.optional()
    .describe('Defaults to the source format\'s convention. "-Y" = COLMAP/OpenCV (Y down), the usual axes of Gaussian splat captures (.ply/.splat default to it).'),
  unitScale: z.number().positive().default(1)
    .describe("Multiplies scene units. STEP/IGES are converted to metres from their declared units first; this applies on top."),
  materialOverrides: z.record(z.string(), MaterialOverride).default({})
    .describe('Keys are material names, globs, /regex/flags, or "*" for all.'),
  textureOverrides: z.array(TextureOverride).default([])
    .describe("Replace material textures with images or videos; later entries win."),
  tessellation: Tessellation.optional().describe("Meshing density for CAD (STEP/IGES) inputs."),
  ground: Ground.optional().describe("Add a ground plane / shadow catcher under the scene. Recommended for product shots."),
});

/** Physical lens: focal length (overrides fovDeg) and depth of field (on when fStop is set). */
export const Lens = z.strictObject({
  focalLengthMm: z.number().positive().max(5000).optional().describe("Overrides fovDeg; measured across sensorWidthMm (the larger image side)."),
  sensorWidthMm: z.number().positive().default(36),
  fStop: z.number().positive().max(256).optional().describe("Aperture f-number. Setting it turns depth of field on."),
  focusDistance: z.number().positive().optional().describe("Scene units from the camera. Overrides focusTarget."),
  focusTarget: z.union([z.literal("auto"), Vec3, z.string().min(1)]).default("auto")
    .describe('"auto" = the camera target; [x,y,z] = a point; any other string = an object name.'),
  apertureBlades: z.number().int().min(0).max(16).default(0).describe("0 = round bokeh, 3+ = polygonal."),
  apertureRotationDeg: z.number().default(0),
  anamorphicRatio: z.number().positive().max(4).default(1).describe(">1 stretches bokeh vertically (anamorphic look)."),
});

export const Camera = z.discriminatedUnion("mode", [
  z.strictObject({
    mode: z.literal("auto"),
    fovDeg: z.number().gt(0).lt(180).default(35),
    elevationDeg: z.number().default(20),
    azimuthDeg: z.number().default(35),
    padding: z.number().min(0).default(0.1),
    lens: Lens.optional(),
  }),
  z.strictObject({ mode: z.literal("scene"), name: z.string().optional().describe("Defaults to the active camera."), lens: Lens.optional() }),
  z.strictObject({
    mode: z.literal("lookat"),
    position: Vec3,
    target: Vec3.default([0, 0, 0]),
    up: Vec3.optional(),
    fovDeg: z.number().gt(0).lt(180).default(40),
    lens: Lens.optional(),
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
  antialias: z.strictObject({
    filter: z.enum(["gaussian", "box", "blackman-harris", "mitchell"]).default("gaussian"),
    filterWidth: z.number().min(0.01).max(10).default(1.5).describe("Pixels."),
  }).optional(),
  motionBlur: z.strictObject({
    shutter: z.number().gt(0).max(1).default(0.5).describe("Fraction of a frame the shutter is open (0.5 = 180°)."),
    position: z.enum(["center", "start", "end"]).default("center"),
    subframes: z.number().int().min(2).max(64).default(8).describe("Samples in time where motion blur is emulated."),
  }).optional().describe("Needs an animation (a camera turntable or scene animation)."),
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
export const ModelFormat = z.enum(["glb"]);

/**
 * Bake lighting into textures and return the model as a GLB.
 * - `unlit`: full lighting (direct + indirect + shadows) baked into baseColor, material set to KHR_materials_unlit.
 *   Looks identical in any glTF viewer, no lights needed. The default.
 * - `ao`: ambient occlusion baked into occlusionTexture; PBR materials kept, so the model still reacts to viewer lighting.
 * - `lightmap`: lighting only (no albedo) in a separate texture on TEXCOORD_1, referenced from
 *   `extras.renda.lightmap` on each material — for engines with lightmap slots (three.js, Babylon, Unity, Godot).
 */
export const Bake = z.strictObject({
  mode: z.enum(["unlit", "ao", "lightmap"]).default("unlit"),
  size: z.number().int().min(64).max(8192).default(2048).describe("Texture size in pixels (square atlas)."),
  uvs: z.enum(["auto", "existing"]).default("auto")
    .describe("auto = generate non-overlapping lightmap UVs (xatlas); existing = reuse TEXCOORD_0 (must not overlap)."),
  samples: z.number().int().positive().nullable().default(null).describe("Samples per texel; overrides render.quality."),
  margin: z.number().int().min(0).max(64).default(4).describe("Padding/dilation in pixels around UV islands."),
  textureFormat: z.enum(["png", "jpg", "webp"]).default("jpg"),
});

/** `output.format: "splat"` bakes the scene into a 3D Gaussian splat (see `splatBake`). */
export const SplatFormat = z.enum(["ply", "spz", "splat"]);

const SplatBakeCamera = z.strictObject({
  position: Vec3,
  target: Vec3,
  up: Vec3.optional(),
  fovDeg: z.number().gt(1).lt(170).default(40),
});

export const SplatBake = z.strictObject({
  preset: z.enum(["fast", "standard", "high"]).default("standard")
    .describe("fast: mesh → Gaussians directly (albedo, seconds). standard/high: + rendered views and gsplat refinement (lighting baked in)."),
  views: z.union([z.literal("auto"), z.number().int().min(8).max(1000)]).nullable().default(null)
    .describe('Rendered training views: "auto"/null = preset default, or a count. Ignored when `cameras` is given.'),
  cameras: z.array(SplatBakeCamera).min(8).max(1000).nullable().default(null)
    .describe("Explicit training cameras (scene units, the file's axes); replaces the automatic rig."),
  resolution: z.number().int().min(128).max(2048).nullable().default(null).describe("Square view resolution in px."),
  steps: z.number().int().min(0).max(100000).nullable().default(null).describe("Refinement iterations (an upper bound: training stops early on a quality plateau)."),
  shDegree: z.number().int().min(0).max(3).nullable().default(null).describe("Spherical-harmonic degree (view-dependent colour); default: 2 for glossy scenes (3 with preset high), 1 otherwise."),
  maxGaussians: z.number().int().min(1000).max(10_000_000).nullable().default(null),
  engine: Engine.default("auto").describe("Engine that renders the training views (not gsplat). auto: Eevee for diffuse scenes, Cycles when glossy/glass/emissive."),
  coverage: z.enum(["auto", "sphere", "hemisphere", "orbit"]).default("auto")
    .describe("auto: framing shell + close-ups placed by surface coverage and detail; sphere/hemisphere: elevation band; orbit: one ring."),
  fanOut: z.union([z.literal("auto"), z.boolean()]).default("auto")
    .describe("Render views in parallel containers (true), inside the training container (false), or let the planner decide."),
  init: z.enum(["surface", "lit"]).default("surface")
    .describe("What training starts from. surface: albedo-coloured surface samples. lit: a splat with the lighting path-traced in (Mitsuba); training needs ~2/3 of the steps (the default drops accordingly) and comes out sharper. Ignored by preset fast."),
  dataset: z.boolean().default(false).describe("Also return the views + exact COLMAP sparse model as a zip."),
});

export const Output = z.strictObject({
  format: z.union([ImageFormat, VideoFormat, ModelFormat, z.literal("splat")]).default("png"),
  passes: z.array(z.enum(["beauty", "alpha", "depth", "normal", "albedo", "mask"])).default(["beauty"]),
  transparent: z.boolean().default(false),
  video: z.strictObject({
    codec: z.enum(["h264", "h265", "vp9", "prores", "gif"]).optional(),
    crf: z.number().int().min(0).max(51).default(18),
  }).default({ crf: 18 }),
  keepFrames: z.boolean().default(false).describe("Also return individual frames for video outputs."),
  bake: Bake.nullable().optional()
    .describe('Bake lighting into textures (needs format "glb"). Omit/null with format "glb" = plain conversion to GLB.'),
  splatFormat: SplatFormat.optional().describe('File type for format "splat" (default spz: compressed, widely supported).'),
  splatBake: SplatBake.optional().describe('Settings for format "splat".'),
});

/** Look and lens post effects. Exposure and tonemap run inside the engine where it can; the rest is a shared
 *  stage applied identically to every engine's output. */
export const Post = z.strictObject({
  exposure: z.number().min(-10).max(10).default(0).describe("Stops (EV)."),
  tonemap: z.enum(["agx", "filmic", "aces", "neutral", "standard"]).default("agx"),
  contrast: z.number().min(-1).max(1).default(0),
  saturation: z.number().min(0).max(2).default(1),
  temperature: z.number().min(-1).max(1).default(0).describe("-1 cool … +1 warm."),
  tint: z.number().min(-1).max(1).default(0).describe("-1 green … +1 magenta."),
  vignette: z.number().min(0).max(1).default(0),
  chromaticAberration: z.number().min(0).max(1).default(0),
  lensDistortion: z.number().min(-1).max(1).default(0).describe("<0 barrel, >0 pincushion."),
  bloom: z.strictObject({
    intensity: z.number().min(0).max(4).default(0.3),
    threshold: z.number().min(0).max(1).default(0.8).describe("Display luminance where the glow starts."),
    radius: z.number().min(0).max(1).default(0.4).describe("Relative glow size."),
  }).optional(),
  filmGrain: z.number().min(0).max(1).default(0),
  sharpen: z.number().min(0).max(1).default(0),
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
  post: Post.optional(),
};

export const RendaPackage = z.strictObject(packageShape).superRefine((pkg, ctx) => {
  if (pkg.render.motionBlur && !(pkg.animation && pkg.animation.frames[1] > pkg.animation.frames[0])) {
    ctx.addIssue({ code: "custom", path: ["render", "motionBlur"], message: "motion blur needs movement: add an animation range (e.g. a turntable)" });
  }
  const isVideo = VideoFormat.safeParse(pkg.output.format).success;
  if (isVideo && !pkg.animation) {
    ctx.addIssue({ code: "custom", path: ["animation"], message: `output.format "${pkg.output.format}" needs an animation range` });
  }
  if (pkg.output.format === "splat") {
    if (pkg.animation) ctx.addIssue({ code: "custom", path: ["animation"], message: 'output.format "splat" bakes a static scene; remove animation' });
    if (pkg.output.splatBake?.engine === "gsplat") {
      ctx.addIssue({ code: "custom", path: ["output", "splatBake", "engine"], message: "training views need a mesh renderer (cycles, eevee, mitsuba, threejs or auto)" });
    }
  } else if (pkg.output.splatFormat || pkg.output.splatBake) {
    ctx.addIssue({ code: "custom", path: ["output", "format"], message: 'splatFormat/splatBake need output.format "splat"' });
  }
  if (pkg.output.transparent && ["jpg", "mp4", "gif"].includes(pkg.output.format)) {
    ctx.addIssue({ code: "custom", path: ["output", "transparent"], message: `${pkg.output.format} has no alpha channel; use png/webp/exr/webm/mov` });
  }
  if (pkg.output.bake && pkg.output.format !== "glb") {
    ctx.addIssue({ code: "custom", path: ["output", "bake"], message: 'bake returns a model: set output.format to "glb"' });
  }
  if (pkg.output.format === "glb" && pkg.animation) {
    ctx.addIssue({ code: "custom", path: ["animation"], message: 'output.format "glb" exports/bakes a single pose; remove animation' });
  }
  if (pkg.output.bake && ["gsplat", "blender-eevee", "blender-workbench"].includes(pkg.render.engine)) {
    ctx.addIssue({ code: "custom", path: ["render", "engine"], message: `${pkg.render.engine} can't bake; use auto, blender-cycles, mitsuba or threejs` });
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

const TEX_KEYS = new Set(["material", "object", "texture", "slot", "scale", "offset", "rotation", "colorspace", "cs",
  "strength", "loop", "start", "speed", "frame"]);

/**
 * `--texture "material=/Wood.*\/i,slot=baseColor,scale=4:./oak.jpg"` → a textureOverrides entry (mirrors
 * renda.cli.parse_texture_flag). Before the first top-level `:` come comma-separated key=value pairs
 * (material, object, texture, slot, scale=4|2x3, offset=0.1x0.2, rotation, colorSpace, strength, loop,
 * start, speed, frame); after it, the source path/URL. No `=` → the whole value is the source.
 * Relative paths are returned as written; callers resolve them.
 */
export function parseTextureFlag(value: string): z.input<typeof TextureOverride> {
  const s = value.trim();
  const spec: Record<string, string> = {};
  let pos = 0;
  for (;;) {
    const m = /^([A-Za-z]+)=/.exec(s.slice(pos));
    if (!m || !TEX_KEYS.has(m[1].toLowerCase())) break;
    const key = m[1].toLowerCase();
    pos += m[0].length;
    const rest = s.slice(pos);
    const mm = rest.startsWith("/") ? /^\/(?:\\.|[^/\\])*\/[imsx]*/.exec(rest) : /^[^,:]*/.exec(rest);
    if (!mm) throw new Error(`--texture: unterminated /regex/ in "${value}"`);
    spec[key] = mm[0];
    pos += mm[0].length;
    if (s[pos] === ",") { pos++; continue; }
    if (s[pos] === ":") { pos++; break; }
    throw new Error(`--texture: expected ',' or ':' after ${key}= in "${value}" (format: material=Wood*,slot=baseColor:./oak.jpg)`);
  }
  const source = Object.keys(spec).length ? s.slice(pos) : s;
  if (!source) throw new Error(`--texture: missing image/video after ':' in "${value}"`);
  const pair = (v: string): [number, number] => {
    const [a, b] = v.toLowerCase().split("x");
    return [Number(a), Number(b ?? a)];
  };
  const entry: any = { source };
  const match: any = {};
  for (const k of ["material", "object", "texture", "slot"]) if (spec[k] !== undefined) match[k] = spec[k];
  if (Object.keys(match).length) entry.match = match;
  const uv: any = {};
  if (spec.scale !== undefined) { const [x, y] = pair(spec.scale); uv.scale = x === y ? x : [x, y]; }
  if (spec.offset !== undefined) uv.offset = pair(spec.offset);
  if (spec.rotation !== undefined) uv.rotationDeg = Number(spec.rotation);
  if (Object.keys(uv).length) entry.uv = uv;
  const video: any = {};
  if (spec.loop !== undefined) video.loop = !["0", "false", "no", "off"].includes(spec.loop.toLowerCase());
  if (spec.start !== undefined) video.startSec = Number(spec.start);
  if (spec.speed !== undefined) video.speed = Number(spec.speed);
  if (spec.frame !== undefined) video.frame = Number(spec.frame);
  if (Object.keys(video).length) entry.video = video;
  const cs = spec.colorspace ?? spec.cs;
  if (cs) entry.colorSpace = cs.toLowerCase();
  if (spec.strength !== undefined) entry.strength = Number(spec.strength);
  return entry;
}
