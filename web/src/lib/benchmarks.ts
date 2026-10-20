/**
 * Benchmark data for /benchmarks. Every number is a measured run, not a projection.
 * `source` says where it ran: "production" = renda.vochsel.com jobs; "isolated" = the same worker code
 * deployed as a separate Modal app with a fixed benchmark set (so production traffic and caches don't skew it).
 * Cost is list price for the billed seconds per hardware class (cpu $0.015, L4 $0.035, L40S $0.075 per min).
 */

export type Source = "production" | "isolated";

export type BeforeAfter = {
  label: string;
  before: number;
  after: number;
  note?: string;
};

/** Render optimisation pass, 2026-10-20 (isolated app, standard priority, new seed each run). */
export const OPTIMISATION = {
  date: "2026-10-20",
  source: "isolated" as Source,
  billedSeconds: [
    { label: "STL draft (Workbench)", before: 14.1, after: 9.4 },
    { label: "GLB still (Cycles)", before: 15.2, after: 10.1 },
    { label: "GLB still (Eevee)", before: 36.1, after: 9.8, note: "before run had a cold shader cache" },
    { label: "GLB still (Mitsuba)", before: 11.6, after: 10.4, note: "second run; first after-run billed 12.4 s" },
    { label: "GLB still (three.js)", before: 10.4, after: 7.3 },
    { label: "72-frame turntable (Cycles)", before: 53.4, after: 41.7 },
    { label: "48-frame splat turntable (gsplat)", before: 43.0, after: 27.1 },
    { label: ".blend still (Cycles)", before: 15.3, after: 8.2 },
    { label: "STEP still", before: 12.7, after: 7.5 },
  ] satisfies BeforeAfter[],
  costUsd: [
    { label: "STL draft (Workbench)", before: 0.0066, after: 0.0048 },
    { label: "GLB still (Cycles)", before: 0.0072, after: 0.0053 },
    { label: "GLB still (Eevee)", before: 0.0193, after: 0.0051, note: "before run had a cold shader cache" },
    { label: "GLB still (Mitsuba)", before: 0.0051, after: 0.0052 },
    { label: "GLB still (three.js)", before: 0.0026, after: 0.0018 },
    { label: "72-frame turntable (Cycles)", before: 0.0269, after: 0.0239 },
    { label: "48-frame splat turntable (gsplat)", before: 0.021, after: 0.0154 },
    { label: ".blend still (Cycles)", before: 0.0072, after: 0.0043 },
    { label: "STEP still", before: 0.0057, after: 0.0038 },
  ] satisfies BeforeAfter[],
};

/** Billing fixes found by measuring real jobs (production, 2026-10-19 → 2026-10-20). */
export const BILLING_FIXES = {
  source: "production" as Source,
  rows: [
    { label: "USDZ turntable, 72 frames (Cycles)", before: 273.1, after: 45.8 },
    { label: "Splat turntable, 48 frames (gsplat)", before: 214.6, after: 30.0 },
    { label: "Turntable, 24 frames (Eevee)", before: 133.1, after: 12.5 },
    { label: "Coordinator per job", before: 86, after: 2.3, note: "56–86 s before: it billed time spent waiting on other containers" },
  ] satisfies BeforeAfter[],
};

/** Cold start: seconds from calling a Modal function to our code running (isolated probe app, 2026-10-20). */
export const COLD_START = {
  date: "2026-10-20",
  source: "isolated" as Source,
  images: [
    { label: "Blender · L4", variant: "CPU snapshot (shipped)", samples: [31.9, 5.1, 30.0, 3.4, 24.8, 3.8, 6.5], warm: 0.1 },
    { label: "Blender · L4", variant: "GPU snapshot (not shipped)", samples: [15.3, 7.1, 9.2, 5.0, 4.9, 6.1, 2.1], warm: 0.1 },
    { label: "Splat · L4", variant: "CPU snapshot (shipped)", samples: [26.6, 12.4, 61.1, 3.4, 60.9, 8.8, 2.5], warm: 0.1 },
    { label: "Splat · L4", variant: "GPU snapshot (not shipped)", samples: [26.4, 12.4, 4.7, 5.3, 86.2, 10.5, 166.7], warm: 0.2 },
    { label: "CPU · 8 cores", variant: "CPU snapshot", samples: [9.9, 8.4, 2.8], warm: 0.15 },
    { label: "three.js · CPU", variant: "CPU snapshot", samples: [8.2, 7.1, 7.6], warm: 0.1 },
  ],
};

export type SplatRun = {
  label: string;
  date: string;
  source: Source;
  billedSeconds: number;
  costUsd: number;
  wallSeconds: number;
  psnr: number;
  note?: string;
};

/** Mesh → Gaussian splat, table scene, `standard` preset, over time. */
export const SPLAT_PROGRESSION: SplatRun[] = [
  { label: "First bake", date: "2026-10-19", source: "production", billedSeconds: 315.2, costUsd: 0.18, wallSeconds: 360, psnr: 35.2, note: "wall time ≈ 6 min" },
  { label: "Overhaul", date: "2026-10-20", source: "isolated", billedSeconds: 237, costUsd: 0.136, wallSeconds: 246, psnr: 36.9 },
  { label: "After deploy", date: "2026-10-20", source: "production", billedSeconds: 236.3, costUsd: 0.138, wallSeconds: 264, psnr: 33.5, note: "different 12 hold-out views each run" },
];

/** Other splat-bake configurations (isolated app, 2026-10-20 unless noted). PSNR on hold-out views. */
export const SPLAT_VARIANTS = [
  { scene: "Table", config: "fast preset (no training)", source: "production" as Source, billedSeconds: 3.6, costUsd: 0.002, wall: "seconds", psnr: null as number | null, size: "2.1 MB" },
  { scene: "Table", config: "standard, Cycles views", source: "isolated" as Source, billedSeconds: 237, costUsd: 0.136, wall: "4.1 min", psnr: 36.9, size: "8.5 MB" },
  { scene: "Table", config: "standard, Eevee views", source: "isolated" as Source, billedSeconds: 251, costUsd: 0.125, wall: "—", psnr: 33.6, size: "7.8 MB" },
  { scene: "Table", config: "standard, Mitsuba views", source: "isolated" as Source, billedSeconds: 344, costUsd: 0.177, wall: "6.3 min", psnr: 35.5, size: "11.3 MB" },
  { scene: "Table", config: "rush (4 × L40S view containers)", source: "isolated" as Source, billedSeconds: 201, costUsd: 0.152, wall: "—", psnr: 32.9, size: "8.2 MB" },
  { scene: "Canon AT-1", config: "fast preset", source: "production" as Source, billedSeconds: 5.4, costUsd: 0.003, wall: "seconds", psnr: null, size: "2.1 MB" },
  { scene: "Canon AT-1", config: "standard", source: "isolated" as Source, billedSeconds: 214, costUsd: 0.124, wall: "4.0 min", psnr: 28.1, size: "7.8 MB" },
  { scene: "Canon AT-1", config: "high (L40S, 200 views, SH 3)", source: "isolated" as Source, billedSeconds: 939, costUsd: 0.99, wall: "12.9 min", psnr: 32.2, size: "50 MB" },
];

/**
 * Estimate vs billed seconds for 9 production jobs submitted for this page (2026-10-20): the API's stored
 * estimate range at submit time against what was billed.
 */
export const ESTIMATES = {
  date: "2026-10-20",
  source: "production" as Source,
  rows: [
    { label: "Cycles still", lo: 5.2, hi: 15.6, actual: 9.5 },
    { label: "Eevee still", lo: 3.3, hi: 10, actual: 29.2, note: "cold shader cache on a fresh container" },
    { label: "Mitsuba still", lo: 3.5, hi: 10.4, actual: 10.9 },
    { label: "three.js still", lo: 3.6, hi: 10.8, actual: 6.1 },
    { label: "Cycles + lens & post", lo: 5.7, hi: 17.1, actual: 10.7 },
    { label: "Mitsuba CPU, denoised", lo: 3.3, hi: 9.8, actual: 7.5 },
    { label: "Mitsuba CPU, no denoise", lo: 3.3, hi: 9.8, actual: 4.5 },
    { label: "Splat render, fast .spz", lo: 3.3, hi: 9.8, actual: 12.2 },
    { label: "Splat render, standard .spz", lo: 3.3, hi: 9.8, actual: 11.9 },
  ],
  /** The optimisation pass's planner check across its benchmark set (isolated). */
  plannerInRange: { before: [6, 9] as [number, number], after: [9, 10] as [number, number] },
  /** A priced example: USDZ turntable, standard priority. */
  usdz: { lo: 0.0138, hi: 0.0414, actual: 0.0224 },
};

export type ComparePair = {
  id: string;
  title: string;
  before: { src: string; label: string; meta: string };
  after: { src: string; label: string; meta: string };
  width: number;
  height: number;
  note?: string;
};

/** A/B pairs. Production renders made for this page on 2026-10-20 unless noted. */
export const PAIRS: ComparePair[] = [
  {
    id: "denoise",
    title: "Mitsuba on CPU, 16 samples: no denoiser vs OIDN",
    before: { src: "/benchmarks/mitsuba-noisy.webp", label: "No denoise", meta: "CPU · 4.5 s billed · $0.0011" },
    after: { src: "/benchmarks/mitsuba-denoised.webp", label: "OIDN", meta: "CPU · 7.5 s billed · $0.0019" },
    width: 960,
    height: 640,
  },
  {
    id: "lens",
    title: "Cycles: plain vs 85 mm f/1.4 with vignette, chromatic aberration, bloom and grain",
    before: { src: "/benchmarks/engine-cycles.webp", label: "Plain", meta: "L4 · 9.5 s billed · $0.0051" },
    after: { src: "/benchmarks/lens-on.webp", label: "Lens + post", meta: "L4 · 10.7 s billed · $0.0056" },
    width: 960,
    height: 640,
  },
];

export type CompareSet = {
  id: string;
  title: string;
  items: { src: string; label: string; meta: string }[];
  width: number;
  height: number;
  note?: string;
};

export const SETS: CompareSet[] = [
  {
    id: "engines",
    title: "Same package, four renderers",
    items: [
      { src: "/benchmarks/engine-cycles.webp", label: "Cycles", meta: "L4 · 9.5 s billed · $0.0051" },
      { src: "/benchmarks/engine-eevee.webp", label: "Eevee", meta: "L4 · 29.2 s billed · $0.0166 (cold shader cache)" },
      { src: "/benchmarks/engine-mitsuba.webp", label: "Mitsuba", meta: "L4 · 10.9 s billed · $0.0054" },
      { src: "/benchmarks/engine-threejs.webp", label: "three.js", meta: "CPU · 6.1 s billed · $0.0015" },
    ],
    width: 960,
    height: 640,
    note: "Framing differs slightly by engine; the scene, lighting and camera angles are the same package.",
  },
  {
    id: "splat",
    title: "Mesh → splat: fast preset vs standard, rendered with gsplat",
    items: [
      { src: "/benchmarks/splat-fast.webp", label: "Fast (3.6 s bake)", meta: "albedo only · render 12.2 s billed · $0.0064" },
      { src: "/benchmarks/splat-standard.webp", label: "Standard (237 s bake)", meta: "baked lighting · render 11.9 s billed · $0.0037" },
    ],
    width: 960,
    height: 640,
    note: "Interactive version with the source mesh on the homepage.",
  },
];
