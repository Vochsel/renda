import type { RendaPackage } from "@rendahq/spec";
import { formatFromPath } from "@rendahq/spec";
import { costUsd, PRICE_PER_MINUTE, round4, type Hardware } from "./pricing";
import type { HyperframesPackage } from "./packages";

/**
 * Cost model, kept deliberately simple and conservative. It mirrors the worker planner
 * (worker/renda_worker/planner.py); the worker's actual metering is what gets billed.
 */
export type Priority = "economy" | "standard" | "rush";
type ConcreteEngine = "blender-cycles" | "blender-eevee" | "blender-workbench" | "mitsuba" | "threejs" | "gsplat";

export const PRESET_SAMPLES: Record<ConcreteEngine, Record<"draft" | "standard" | "final", number>> = {
  "blender-cycles": { draft: 32, standard: 128, final: 512 },
  "blender-eevee": { draft: 16, standard: 64, final: 128 },
  "blender-workbench": { draft: 8, standard: 16, final: 32 },
  mitsuba: { draft: 16, standard: 64, final: 256 },
  threejs: { draft: 1, standard: 4, final: 8 },
  gsplat: { draft: 1, standard: 1, final: 1 }, // splats are rasterised once per frame
};

/** Seconds per frame on each class: `fixed + perMp * megapixels * sampleFactor`. null = unsupported. */
const FRAME_COST: Record<ConcreteEngine, Partial<Record<Hardware, { fixed: number; perMpSample: number }>>> = {
  "blender-cycles": {
    cpu: { fixed: 0.5, perMpSample: 0.04 },
    "gpu-l4": { fixed: 0.3, perMpSample: 0.004 },
    "gpu-l40s": { fixed: 0.2, perMpSample: 0.0018 },
  },
  "blender-eevee": {
    "gpu-l4": { fixed: 0.3, perMpSample: 0.006 },
    "gpu-l40s": { fixed: 0.2, perMpSample: 0.003 },
  },
  "blender-workbench": {
    cpu: { fixed: 0.3, perMpSample: 0.25 },
    "gpu-l4": { fixed: 0.15, perMpSample: 0.03 },
  },
  mitsuba: {
    cpu: { fixed: 0.3, perMpSample: 0.012 },
    "gpu-l4": { fixed: 0.3, perMpSample: 0.0015 },
    "gpu-l40s": { fixed: 0.2, perMpSample: 0.0007 },
  },
  threejs: {
    cpu: { fixed: 0.4, perMpSample: 0.25 },
    "gpu-l4": { fixed: 0.15, perMpSample: 0.02 },
  },
  // Gaussian splats: the CUDA raster is ~180 ms per million splats at 1080p on an L4, but host work per
  // frame (readback, background, composite, PNG) dominates — ~0.30 s at 720×720 (measured 2026-10-20).
  gsplat: {
    "gpu-l4": { fixed: 0.05, perMpSample: 0.6 },
    "gpu-l40s": { fixed: 0.04, perMpSample: 0.5 },
  },
};

/** Extra seconds per frame when splats are composited over a mesh engine's render. */
const SPLAT_LAYER_SECONDS = 0.15;

// STEP/IGES add an OpenCASCADE tessellation pass (cached per file + tolerance).
const LOAD_SECONDS: Record<string, number> = { blend: 12, fbx: 15, abc: 15, usd: 8, splat: 8, step: 10, iges: 8, default: 5 };

/**
 * Fan-out economics (mirrors core/renda/planner.py fanout_count; shared fixture
 * spec/fixtures/estimator/fanout.json). Each extra container costs its load overhead o:
 * N containers on W seconds of work cost ≈ N·o + W and take ≈ o + W/N.
 *   economy: fewest containers (one, unless a container would exceed 30 min of work)
 *   standard: split only while load stays ≤ 10% of each chunk; rush: while it stays ≤ 50%.
 */
const OVERHEAD_TARGET: Record<Priority, number> = { economy: 0.05, standard: 0.1, rush: 0.5 };
const MAX_CONTAINERS: Record<Priority, number> = { economy: 10, standard: 50, rush: 50 };
const ECONOMY_CHUNK_SECONDS = 1800;

export function fanoutCount(frames: number, loadSeconds: number, frameSeconds: number, priority: Priority): number {
  if (frames <= 1) return 1;
  const work = frames * Math.max(frameSeconds, 1e-3);
  let n: number;
  if (priority === "economy") n = Math.ceil(work / ECONOMY_CHUNK_SECONDS);
  else {
    const t = OVERHEAD_TARGET[priority];
    n = Math.floor(work / Math.max((loadSeconds * (1 - t)) / t, 1e-3));
  }
  return Math.min(Math.max(n, 1), MAX_CONTAINERS[priority], frames);
}

export type Estimate = {
  engine: ConcreteEngine | "export";
  hardware: Hardware;
  frames: number;
  samples: number;
  chunks: number;
  estSeconds: [number, number];
  estWallSeconds: [number, number];
  estCostUsd: [number, number];
  warnings: string[];
};

/** First choice of core's `choose_engine` (renda/planner.py); kept in sync by spec/fixtures/estimator/auto-engine.json. */
export function autoEngine(format: string | undefined, quality: "draft" | "standard" | "final", mixed = false): ConcreteEngine {
  if (format === "splat") return mixed ? (quality === "draft" ? "threejs" : "blender-cycles") : "gsplat";
  if (format === "blend" || format === "fbx" || format === "abc") return quality === "draft" ? "blender-eevee" : "blender-cycles";
  if (format === "threejs") return quality === "final" ? "blender-cycles" : "threejs";
  if (["stl", "obj", "ply", "3mf", "dae", "step", "iges"].includes(format ?? "")) {
    // draft previews run on three.js (CPU): ~2.7× cheaper than Workbench on an L4 and no GPU wait
    return ({ draft: "threejs", standard: "mitsuba", final: "blender-cycles" } as const)[quality];
  }
  return quality === "draft" ? "threejs" : "blender-cycles"; // gltf, usd, rir, unknown
}

export function resolveEngine(pkg: RendaPackage, rootFormat?: string): ConcreteEngine {
  if (pkg.render.engine !== "auto") return pkg.render.engine;
  return autoEngine(rootFormat, pkg.render.quality, rootFormat === "splat" && pkg.scene.include.length > 0);
}

export function rootFormat(pkg: RendaPackage): string | undefined {
  const asset = pkg.assets[pkg.scene.root];
  if (asset?.format) return asset.format;
  return formatFromPath(asset?.uri ?? pkg.scene.root);
}

export function frameCount(pkg: RendaPackage): number {
  if (!pkg.animation) return 1;
  const [a, b] = pkg.animation.frames;
  return Math.max(1, Math.floor((b - a) / pkg.animation.step) + 1);
}

/** Samples per texel for light bakes (mirrors core/renda/planner.py BAKE_SAMPLES). */
const BAKE_SAMPLES: Record<"blender-cycles" | "mitsuba" | "threejs", Record<"draft" | "standard" | "final", number>> = {
  "blender-cycles": { draft: 64, standard: 256, final: 1024 },
  mitsuba: { draft: 64, standard: 256, final: 1024 },
  threejs: { draft: 64, standard: 192, final: 384 },
};

/**
 * GLB output: a plain conversion (CPU) or a light bake. Calibrated on Modal (2026-10-19, 7.5k–60k triangle
 * models): Cycles 2048² @256 spp ≈ 40 s on an L4, Mitsuba ≈ 26 s, three.js 1024² @192 directions ≈ 15 s on CPU.
 */
export function estimateModel(pkg: RendaPackage, priority: Priority = "standard"): Estimate {
  const warnings: string[] = [];
  const bake = pkg.output.bake;
  const lo = 0.6, hi = 1.8;
  const done = (engine: Estimate["engine"], hardware: Hardware, samples: number, total: number): Estimate => ({
    engine, hardware, frames: 1, samples, chunks: 1,
    estSeconds: [round1(total * lo), round1(total * hi)],
    estWallSeconds: [round1(total * lo), round1(total * hi)],
    estCostUsd: [round4(costUsd(hardware, total) * lo), round4(costUsd(hardware, total) * hi)],
    warnings,
  });
  if (!bake) return done("export", "cpu", 0, 6);
  const engine = pkg.render.engine === "auto" ? "blender-cycles" : pkg.render.engine;
  if (engine !== "blender-cycles" && engine !== "mitsuba" && engine !== "threejs") {
    warnings.push(`${engine} can't bake; use blender-cycles, mitsuba or threejs`);
    return done(engine, "cpu", 0, 0);
  }
  const samples = bake.samples ?? BAKE_SAMPLES[engine][pkg.render.quality];
  const mtex = (bake.size * bake.size) / 1e6;
  if (engine === "threejs") return done(engine, "cpu", samples, 6 + samples * (0.02 + mtex / 20));
  const units = (mtex / 4.2) * (samples / 256) * (bake.mode === "ao" ? 0.3 : 1);
  const gpu = (engine === "blender-cycles" ? 14 + 24 * units : 8 + 18 * units);
  if (pkg.render.hardware === "cpu") return done(engine, "cpu", samples, gpu * 8);
  const hardware: Hardware = priority === "rush" && bake.size >= 4096 ? "gpu-l40s" : "gpu-l4";
  return done(engine, hardware, samples, hardware === "gpu-l40s" ? gpu * 0.5 : gpu);
}

/** Frame-time multiplier for lens effects; mirrors effects.frame_cost_factor in core. */
function effectsFactor(pkg: RendaPackage, engine: string): number {
  let f = 1;
  const mb = pkg.render.motionBlur;
  if (mb) {
    if (engine === "mitsuba") f *= 1.1; // samples split across subframes
    else if (["blender-workbench", "threejs", "gsplat"].includes(engine)) f *= mb.subframes; // emulated
    else if (engine === "blender-eevee") f *= 2;
    else f *= 1.15;
  }
  if (pkg.camera.lens?.fStop) f *= ["threejs", "gsplat"].includes(engine) ? 1.05 : 1.2;
  return f;
}

/** Splat bake presets (mirror core/renda/splatbake PRESETS). views 0 = direct mesh → Gaussians on CPU. */
const SPLAT_PRESETS = {
  fast: { views: 0, resolution: 0, steps: 0, train: "cpu" as Hardware },
  standard: { views: 96, resolution: 768, steps: 6000, train: "gpu-l4" as Hardware },
  high: { views: 200, resolution: 1024, steps: 15000, train: "gpu-l40s" as Hardware },
};
// gsplat MCMC refinement, production 2026-10-20 (~30 s per 1k steps at 768² on an L4); steps are an upper bound.
const TRAIN_SECONDS_PER_K_STEPS: Partial<Record<Hardware, number>> = { "gpu-l4": 30, "gpu-l40s": 12 };
const TRAIN_START_SECONDS = 15;
// Render the views inside the training container unless that adds more wall time than this.
const SINGLE_CONTAINER_MAX_VIEW_SECONDS: Record<Priority, number> = { economy: Infinity, standard: 240, rush: 20 };
const TRAINER_ENGINES = new Set<ConcreteEngine>(["blender-cycles", "blender-eevee", "blender-workbench", "mitsuba"]);

/** output.format "splat": views rendered in the training container (small jobs) or fanned out and streamed. */
export function estimateSplatBake(pkg: RendaPackage, priority: Priority = "standard"): Estimate {
  const sb: Partial<NonNullable<RendaPackage["output"]["splatBake"]>> = pkg.output.splatBake ?? {};
  const p = SPLAT_PRESETS[sb.preset ?? "standard"];
  const explicit = typeof sb.views === "number" ? sb.views : undefined;
  const views = sb.cameras?.length ?? explicit ?? p.views;
  const lo = 0.6, hi = 1.8;
  if (sb.preset === "fast" && !sb.cameras && explicit === undefined) {
    const total = 12;
    return { engine: "blender-cycles", hardware: "cpu", frames: 1, samples: 0, chunks: 1,
      estSeconds: [round1(total * lo), round1(total * hi)], estWallSeconds: [round1(total * lo), round1(total * hi)],
      estCostUsd: [round4(costUsd("cpu", total) * lo), round4(costUsd("cpu", total) * hi)],
      warnings: ["splat bake (fast): direct mesh → Gaussians; colours are albedo (no lighting)"] };
  }
  const res = sb.resolution ?? p.resolution;
  const steps = sb.steps ?? p.steps;
  // auto → Cycles or Eevee from the materials; estimate as Cycles (conservative)
  const viewEngine = (sb.engine && sb.engine !== "auto" ? sb.engine : "blender-cycles") as ConcreteEngine;
  const engine: ConcreteEngine = viewEngine === "gsplat" ? "blender-cycles" : viewEngine;
  const train: Hardware = p.train === "cpu" ? "gpu-l4" : p.train;
  // training views are draft + denoised unless the package asks for final quality / explicit samples
  const quality = pkg.render.quality === "final" ? "final" : "draft";
  const samples = pkg.render.samples ?? PRESET_SAMPLES[engine][quality];
  const perViewOn = (hw: Hardware) => {
    const c = FRAME_COST[engine][hw] ?? FRAME_COST[engine]["gpu-l4"] ?? FRAME_COST[engine].cpu!;
    return c.fixed + c.perMpSample * ((res * res) / 1e6) * samples;
  };
  const load = LOAD_SECONDS[rootFormat(pkg) ?? "default"] ?? LOAD_SECONDS.default;
  const trainSeconds = TRAIN_START_SECONDS + (steps / 1000) * (TRAIN_SECONDS_PER_K_STEPS[train] ?? 30) * Math.max((res / 768) ** 2, 0.25);
  const singleView = perViewOn(train) * views;
  let single = sb.fanOut === false ? true : sb.fanOut === true ? false
    : singleView <= SINGLE_CONTAINER_MAX_VIEW_SECONDS[priority] && sb.preset !== "high";
  const warnings: string[] = [];
  if (single && !TRAINER_ENGINES.has(engine)) {
    if (sb.fanOut === false) warnings.push(`splat bake: ${engine} views can't render inside the training container; fanning out`);
    single = false;
  }
  let hw: Hardware, chunks: number, cost: number, total: number, wall: number;
  if (single) {
    hw = train; chunks = 1;
    total = singleView + trainSeconds;
    wall = total;
    cost = costUsd(train, total);
  } else {
    hw = priority === "rush" && FRAME_COST[engine]["gpu-l40s"] ? "gpu-l40s" : FRAME_COST[engine]["gpu-l4"] ? "gpu-l4" : "cpu";
    const perView = perViewOn(hw);
    chunks = fanoutCount(views, load, perView, sb.fanOut === true ? "rush" : priority);
    const renderSeconds = perView * views + chunks * load;
    total = renderSeconds + trainSeconds;
    cost = costUsd(hw, renderSeconds) + costUsd(train, trainSeconds);
    wall = load + Math.ceil(views / chunks) * perView + trainSeconds * 0.9; // the trainer streams views in
  }
  warnings.push(`splat bake: ${views} ${engine} views at ${res}px ${single ? "in the training container" : `over ${chunks} containers, streamed`}, then ≤${steps} gsplat refinement steps on ${train}`);
  return {
    engine, hardware: hw, frames: views, samples, chunks,
    estSeconds: [round1(total * lo), round1(total * hi)],
    estWallSeconds: [round1(wall * lo), round1(wall * hi)],
    estCostUsd: [round4(cost * lo), round4(cost * hi)],
    warnings,
  };
}

// ---- Scene renders: a direct port of core/renda/planner.py (plan → choose_hardware/frame_seconds). ----
// Core routes the real jobs, so it is the source of truth; spec/fixtures/estimator/hardware.json
// (generated by core/scripts/gen_estimator_fixture.py) pins engine, hardware, chunks and seconds.
const HW: Hardware[] = ["cpu", "gpu-l4", "gpu-l40s"];
const THROUGHPUT: Record<Hardware, number> = { cpu: 1.2e6, "gpu-l4": 13e6, "gpu-l40s": 38e6 };
const ENGINE_COST: Record<ConcreteEngine, { cpu: number; gpu: number }> = {
  "blender-cycles": { cpu: 1.0, gpu: 1.0 },
  "blender-eevee": { cpu: 6.0, gpu: 0.06 },
  "blender-workbench": { cpu: 2.0, gpu: 0.02 },
  mitsuba: { cpu: 0.8, gpu: 0.7 },
  threejs: { cpu: 0.05, gpu: 0.01 },
  gsplat: { cpu: 40.0, gpu: 0.02 },
};
const SPLAT_SECONDS_PER_M: Record<Hardware, number> = { cpu: 150.0, "gpu-l4": 0.18, "gpu-l40s": 0.09 };
const GSPLAT_HOST_SECONDS_PER_1080P = 1.2;
const DEFAULT_SPLATS = 1_000_000;
const ENGINE_LOAD_SECONDS: Record<ConcreteEngine, number> = {
  "blender-cycles": 7.0, "blender-eevee": 5.5, "blender-workbench": 6.0, mitsuba: 4.5, threejs: 5.0, gsplat: 6.0,
};
const GPU_ONLY = new Set<ConcreteEngine>(["blender-eevee", "blender-workbench", "gsplat"]);
const MIN_FRAME_SECONDS: Partial<Record<ConcreteEngine, number>> = { threejs: 0.15, "blender-workbench": 0.2, "blender-eevee": 0.5, gsplat: 0.05 };
const DEADLINE: Record<Priority, number> = { economy: 3600, standard: 900, rush: 240 };

function frameSeconds(engine: ConcreteEngine, hw: Hardware, pkg: RendaPackage, samples: number, splats: number | null): number {
  const [w, h] = pkg.render.resolution;
  const kind = hw.startsWith("gpu") ? "gpu" : "cpu";
  let splatS = 0;
  if (splats) {
    const mp = (w * h) / 2.07e6;
    splatS = (splats / 1e6) * SPLAT_SECONDS_PER_M[hw] * (hw === "cpu" ? mp : Math.max(mp, 0.5));
  }
  if (engine === "gsplat") {
    const host = (GSPLAT_HOST_SECONDS_PER_1080P * w * h) / 2.07e6;
    return Math.max(splatS + host, MIN_FRAME_SECONDS.gsplat!);
  }
  const effSamples = engine === "blender-cycles" && pkg.render.denoise ? samples * 0.45 : samples;
  const work = w * h * effSamples * ENGINE_COST[engine][kind];
  const extra = engine !== "threejs" ? splatS : splatS * 0.2;
  return Math.max(work / THROUGHPUT[hw], MIN_FRAME_SECONDS[engine] ?? 0.3) + extra;
}

function chooseHardware(engine: ConcreteEngine, pkg: RendaPackage, priority: Priority, samples: number, nFrames: number,
  warnings: string[], splats: number | null): Hardware {
  const req = pkg.render.hardware;
  if (GPU_ONLY.has(engine)) {
    if (req === "cpu") warnings.push(`${engine} needs a GPU; ignoring render.hardware=cpu`);
    return priority === "rush" && nFrames > 200 ? "gpu-l40s" : "gpu-l4";
  }
  let candidates: Hardware[] = [...HW];
  if (req === "cpu") candidates = ["cpu"];
  else if (req === "gpu") candidates = ["gpu-l4", "gpu-l40s"];
  if (engine === "threejs" && req !== "gpu" && !splats) return "cpu";
  if (priority !== "rush" && candidates.includes("gpu-l40s") && candidates.length > 1) {
    candidates = candidates.filter((c) => c !== "gpu-l40s");
  }
  let best = candidates[0];
  let bestCost = Infinity;
  for (const hw of candidates) {
    const ft = frameSeconds(engine, hw, pkg, samples, splats);
    const total = ENGINE_LOAD_SECONDS[engine] + ft * nFrames;
    let cost = (total / 60) * PRICE_PER_MINUTE[hw];
    const wall = ENGINE_LOAD_SECONDS[engine] + ft * Math.ceil(nFrames / MAX_CONTAINERS[priority]);
    if (wall > DEADLINE[priority]) cost *= 10;
    if (cost < bestCost) { best = hw; bestCost = cost; }
  }
  return best;
}

/** core/renda/post.py is_active(post, emulate_exposure=True): is the shared post stage doing anything? */
function postActive(post: RendaPackage["post"]): boolean {
  if (!post) return false;
  return Math.abs(post.exposure) > 1e-9 || Math.abs(post.contrast) > 1e-9 || Math.abs(post.saturation - 1) > 1e-9
    || Math.abs(post.temperature) > 1e-9 || Math.abs(post.tint) > 1e-9 || post.vignette > 0 || post.chromaticAberration > 0
    || Math.abs(post.lensDistortion) > 1e-9 || (post.bloom != null && post.bloom.intensity > 0) || post.filmGrain > 0
    || post.sharpen > 0;
}

function assetFormat(pkg: RendaPackage, ref: string): string | undefined {
  const a = pkg.assets[ref];
  return a?.format ?? formatFromPath(a?.uri ?? ref);
}

export function estimateScene(pkg: RendaPackage, priority: Priority = "standard"): Estimate {
  if (pkg.output.format === "glb") return estimateModel(pkg, priority);
  if (pkg.output.format === "splat") return estimateSplatBake(pkg, priority);
  const warnings: string[] = [];
  const fmt = rootFormat(pkg);
  const includeFmts = pkg.scene.include.map((i) => assetFormat(pkg, i.asset) ?? "unknown");
  const hasSplats = fmt === "splat" || includeFmts.includes("splat");
  const mixed = hasSplats && (fmt !== "splat" || includeFmts.some((f) => f !== "splat"));
  const splats = hasSplats ? DEFAULT_SPLATS : null;
  const engine: ConcreteEngine = pkg.render.engine !== "auto" ? pkg.render.engine : autoEngine(fmt, pkg.render.quality, mixed);
  if (engine.startsWith("blender") && fmt === "threejs") {
    warnings.push("three.js JSON is converted to glTF for Blender; ShaderMaterials are approximated");
  }
  if (engine === "threejs" && fmt !== "threejs" && fmt !== "gltf" && fmt !== "splat") {
    warnings.push(`threejs engine renders .${fmt} via IR → glTF conversion`);
  }

  const frames = frameCount(pkg);
  let samples = pkg.render.samples ?? PRESET_SAMPLES[engine][pkg.render.quality];
  if (engine === "threejs") samples = Math.min(samples, 4);
  const hardware = chooseHardware(engine, pkg, priority, samples, frames, warnings, splats);

  let load = ENGINE_LOAD_SECONDS[engine] + (["blend", "fbx", "abc"].includes(fmt ?? "") && !engine.startsWith("blender") ? 6 : 0)
    + (fmt === "step" || fmt === "iges" ? 5 : 0);
  if (hasSplats && ["blender-cycles", "blender-eevee", "blender-workbench", "mitsuba"].includes(engine)) load += 4;
  const [w, h] = pkg.render.resolution;
  let frameS = frameSeconds(engine, hardware, pkg, samples, splats) * effectsFactor(pkg, engine);
  if (postActive(pkg.post)) frameS += (0.35 * w * h) / 2.07e6;

  // chunk_frames: N containers → ceil(frames/N) frames each (the last chunk may be shorter)
  const n = fanoutCount(frames, load, frameS, priority);
  const perChunk = Math.ceil(frames / n);
  const chunks = Math.ceil(frames / perChunk);
  const isVideo = ["mp4", "webm", "gif", "mov"].includes(pkg.output.format);
  const encode = isVideo ? (0.05 * frames * w * h) / 2.07e6 : 0;
  const total = chunks * load + frames * frameS + encode;
  const wall = load + perChunk * frameS + encode + (isVideo && chunks > 1 ? 5 : 0);

  const lo = 0.6, hi = 1.8;
  const cost = costUsd(hardware, total);
  return {
    engine,
    hardware,
    frames,
    samples,
    chunks,
    estSeconds: [round1(total * lo), round1(total * hi)],
    estWallSeconds: [round1(wall * lo), round1(wall * hi)],
    estCostUsd: [round4(cost * lo), round4(cost * hi)],
    warnings,
  };
}

export function estimateHyperframes(pkg: HyperframesPackage) {
  const fps = pkg.fps ?? 30;
  const duration = pkg.durationSec ?? 30;
  const mp = ((pkg.width ?? 1920) * (pkg.height ?? 1080)) / 1e6;
  const q = { draft: 0.6, looks: 1, delivery: 1.6 }[pkg.quality ?? "looks"];
  const total = 20 + duration * fps * (0.04 + 0.06 * mp) * q;
  const warnings = pkg.durationSec ? [] : ["durationSec not given; estimate assumes a 30 s composition"];
  return {
    engine: "hyperframes",
    hardware: "cpu" as Hardware,
    frames: Math.round(duration * fps),
    estSeconds: [round1(total * 0.6), round1(total * 1.8)] as [number, number],
    estCostUsd: [round4(costUsd("cpu", total * 0.6)), round4(costUsd("cpu", total * 1.8))] as [number, number],
    perMinuteUsd: PRICE_PER_MINUTE.cpu,
    warnings,
  };
}

function round1(n: number) {
  return Math.round(n * 10) / 10;
}

/** Cost and wall-time ranges for every priority, so callers can trade money for latency. */
export function estimatePriorities(pkg: RendaPackage): Record<Priority, Pick<Estimate, "hardware" | "chunks" | "estCostUsd" | "estWallSeconds">> {
  const out = {} as Record<Priority, Pick<Estimate, "hardware" | "chunks" | "estCostUsd" | "estWallSeconds">>;
  for (const pr of ["economy", "standard", "rush"] as Priority[]) {
    const e = estimateScene(pkg, pr);
    out[pr] = { hardware: e.hardware, chunks: e.chunks, estCostUsd: e.estCostUsd, estWallSeconds: e.estWallSeconds };
  }
  return out;
}
