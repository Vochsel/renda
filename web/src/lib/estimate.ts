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
  "blender-workbench": { draft: 1, standard: 8, final: 16 },
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
  // Gaussian splats (CUDA rasteriser; measured ~180 ms per million splats at 1080p on an L4)
  gsplat: {
    "gpu-l4": { fixed: 0.1, perMpSample: 0.09 },
    "gpu-l40s": { fixed: 0.06, perMpSample: 0.045 },
  },
};

/** Extra seconds per frame when splats are composited over a mesh engine's render. */
const SPLAT_LAYER_SECONDS = 0.15;

// STEP/IGES add an OpenCASCADE tessellation pass (cached per file + tolerance).
const LOAD_SECONDS: Record<string, number> = { blend: 12, fbx: 15, abc: 15, usd: 8, splat: 8, step: 10, iges: 8, default: 5 };
const MAX_CONTAINERS = 50;
const TARGET_CHUNK_SECONDS = 90;

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

export function resolveEngine(pkg: RendaPackage, rootFormat?: string): ConcreteEngine {
  if (pkg.render.engine !== "auto") return pkg.render.engine;
  if (rootFormat === "splat") {
    if (!pkg.scene.include.length) return "gsplat"; // splat-only: the cheapest engine
    return pkg.render.quality === "draft" ? "threejs" : "blender-cycles"; // meshes + splats
  }
  if (rootFormat === "threejs" && pkg.render.quality === "draft") return "threejs";
  if (pkg.render.quality === "draft") return "blender-eevee";
  return "blender-cycles";
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
  standard: { views: 96, resolution: 768, steps: 7000, train: "gpu-l4" as Hardware },
  high: { views: 200, resolution: 1024, steps: 30000, train: "gpu-l40s" as Hardware },
};
const TRAIN_SECONDS_PER_K_STEPS: Partial<Record<Hardware, number>> = { "gpu-l4": 22, "gpu-l40s": 10 };

/** output.format "splat": render N square views in parallel, then one refinement job (or a CPU conversion). */
export function estimateSplatBake(pkg: RendaPackage): Estimate {
  const sb: Partial<NonNullable<RendaPackage["output"]["splatBake"]>> = pkg.output.splatBake ?? {};
  const p = SPLAT_PRESETS[sb.preset ?? "standard"];
  const views = sb.views ?? p.views;
  const lo = 0.6, hi = 1.8;
  if (sb.preset === "fast" && !sb.views) {
    const total = 12;
    return { engine: "blender-cycles", hardware: "cpu", frames: 1, samples: 0, chunks: 1,
      estSeconds: [round1(total * lo), round1(total * hi)], estWallSeconds: [round1(total * lo), round1(total * hi)],
      estCostUsd: [round4(costUsd("cpu", total) * lo), round4(costUsd("cpu", total) * hi)],
      warnings: ["splat bake (fast): direct mesh → Gaussians; colours are albedo (no lighting)"] };
  }
  const res = sb.resolution ?? p.resolution;
  const steps = sb.steps ?? p.steps;
  const viewEngine = (sb.engine && sb.engine !== "auto" ? sb.engine : "blender-cycles") as ConcreteEngine;
  const engine: ConcreteEngine = viewEngine === "gsplat" ? "blender-cycles" : viewEngine;
  const hw: Hardware = FRAME_COST[engine]["gpu-l4"] ? "gpu-l4" : "cpu";
  const c = FRAME_COST[engine][hw]!;
  const samples = pkg.render.samples ?? PRESET_SAMPLES[engine][pkg.render.quality];
  const perView = c.fixed + c.perMpSample * (res * res / 1e6) * samples;
  const load = LOAD_SECONDS[rootFormat(pkg) ?? "default"] ?? LOAD_SECONDS.default;
  const chunks = Math.min(MAX_CONTAINERS, views, Math.max(1, Math.ceil(perView * views / TARGET_CHUNK_SECONDS)));
  const renderSeconds = perView * views + chunks * load;
  const train = p.train === "cpu" ? "gpu-l4" : p.train;
  const trainSeconds = 25 + (steps / 1000) * (TRAIN_SECONDS_PER_K_STEPS[train] ?? 22) * Math.max((res / 768) ** 2, 0.25);
  const total = renderSeconds + trainSeconds;
  const cost = costUsd(hw, renderSeconds) + costUsd(train, trainSeconds);
  const wall = perView * views / chunks + load + trainSeconds;
  return {
    engine, hardware: hw, frames: views, samples, chunks,
    estSeconds: [round1(total * lo), round1(total * hi)],
    estWallSeconds: [round1(wall * lo), round1(wall * hi)],
    estCostUsd: [round4(cost * lo), round4(cost * hi)],
    warnings: [`splat bake: ${views} ${engine} views at ${res}px, then ${steps} gsplat refinement steps on ${train}`],
  };
}

export function estimateScene(pkg: RendaPackage, priority: Priority = "standard"): Estimate {
  if (pkg.output.format === "glb") return estimateModel(pkg, priority);
  if (pkg.output.format === "splat") return estimateSplatBake(pkg);
  const warnings: string[] = [];
  const fmt = rootFormat(pkg);
  const engine = resolveEngine(pkg, fmt);
  const frames = frameCount(pkg);
  const samples = pkg.render.samples ?? PRESET_SAMPLES[engine][pkg.render.quality];
  const mp = (pkg.render.resolution[0] * pkg.render.resolution[1]) / 1e6;
  const passes = Math.max(1, pkg.output.passes.length * 0.15 + 0.85);
  const load = LOAD_SECONDS[fmt ?? "default"] ?? LOAD_SECONDS.default;
  const isVideo = ["mp4", "webm", "gif", "mov"].includes(pkg.output.format);

  let classes = Object.keys(FRAME_COST[engine]) as Hardware[];
  if (pkg.render.hardware === "cpu") {
    const cpu = classes.filter((c) => c === "cpu");
    if (cpu.length) classes = cpu;
    else warnings.push(`${engine} needs a GPU; ignoring hardware: "cpu"`);
  } else if (pkg.render.hardware === "gpu") {
    classes = classes.filter((c) => c !== "cpu");
  }
  if (engine === "mitsuba" && fmt && !["obj", "stl", "ply", "rir"].includes(fmt)) {
    warnings.push(`mitsuba renders ${fmt} via the Renda IR conversion; some materials may be approximated`);
  }

  const hasSplats = fmt === "splat" || pkg.scene.include.some((i) => {
    const a = pkg.assets[i.asset];
    return (a?.format ?? formatFromPath(a?.uri ?? i.asset)) === "splat";
  });
  const plans = classes.map((hw) => {
    const c = FRAME_COST[engine][hw]!;
    const splatLayer = hasSplats && engine !== "gsplat" && engine !== "threejs" ? SPLAT_LAYER_SECONDS : 0;
    const perFrame = (c.fixed + c.perMpSample * mp * samples) * passes * effectsFactor(pkg, engine) + splatLayer
      + (pkg.post ? 0.35 * mp / 2.07 : 0); // shared post stage
    const renderSeconds = perFrame * frames;
    const chunks = Math.min(MAX_CONTAINERS, frames, Math.max(1, Math.ceil(renderSeconds / TARGET_CHUNK_SECONDS)));
    const encode = isVideo ? 3 + frames * mp * 0.02 : 0;
    const total = renderSeconds + chunks * load + encode;
    const wall = renderSeconds / chunks + load + encode;
    return { hw, total, wall, chunks, cost: costUsd(hw, total) };
  });

  const byCost = [...plans].sort((a, b) => a.cost - b.cost);
  const byWall = [...plans].sort((a, b) => a.wall - b.wall);
  let pick = byCost[0];
  if (priority === "rush") pick = byWall[0];
  else if (priority === "standard" && pick.wall > 600) {
    // Accept up to 1.5× cost to get under ~10 minutes of wall time.
    pick = byCost.find((p) => p.wall <= 600 && p.cost <= byCost[0].cost * 1.5) ?? pick;
  }

  const lo = 0.6, hi = 1.8;
  return {
    engine,
    hardware: pick.hw,
    frames,
    samples,
    chunks: pick.chunks,
    estSeconds: [round1(pick.total * lo), round1(pick.total * hi)],
    estWallSeconds: [round1(pick.wall * lo), round1(pick.wall * hi)],
    estCostUsd: [round4(pick.cost * lo), round4(pick.cost * hi)],
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
