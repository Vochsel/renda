/**
 * Data for /benchmarks: Renda against doing the same jobs yourself.
 *
 * Every number carries a basis:
 *  - "measured"  — we ran it (Renda jobs on the isolated benchmark copy of the worker; local runs on the Mac below)
 *  - "computed"  — arithmetic on a published price list, with the inputs and assumptions stated in ASSUMPTIONS
 *  - "published" — a number someone else measured and published (cited in SOURCES)
 * Prices checked 2026-10-20. Renda's list prices: CPU $0.015, L4 $0.035, L40S $0.075 per minute, billed per second.
 */

export type Basis = "measured" | "computed" | "published" | "estimate";

export type Cite = { label: string; url: string };

export const SOURCES = {
  awsG6: { label: "AWS g6.xlarge (1× L4) on-demand, us-east-1: $0.8048/h", url: "https://calculator.holori.com/aws/ec2/g6.xlarge/us-east-1" },
  gcpG2: { label: "GCP g2-standard-4 (1× L4) on-demand, us-central1: $0.7068/h", url: "https://cloud.google.com/products/compute/pricing/accelerator-optimized" },
  awsBilling: { label: "EC2: per-second billing, 60-second minimum", url: "https://aws.amazon.com/ec2/pricing" },
  gcpBilling: { label: "Compute Engine: per-second billing, 1-minute minimum", url: "https://techcrunch.com/2017/09/26/following-aws-google-compute-engine-also-moves-to-per-second-billing" },
  rebus: { label: "RebusFarm GPU: 0.53 ¢ per OctaneBench-hour (May 2026, as reported)", url: "https://superrendersfarm.com/article/super-renders-farm-vs-rebusfarm-comparison-2026" },
  ranch: { label: "Ranch Computing GPU: €0.005–0.009 per OB-hour by priority (2026, as reported)", url: "https://superrendersfarm.com/article/ranch-computing-vs-super-renders-farm-comparison-2026" },
  garage: { label: "GarageFarm GPU: $0.004–0.012 per OB-hour by priority (2026, as reported)", url: "https://superrendersfarm.com/article/garagefarm-vs-super-renders-farm-comparison-2026" },
  sheepit: { label: "SheepIt: free, volunteer machines, points-based queue", url: "https://www.sheepit-renderfarm.com/faq" },
  ob4090: { label: "RTX 4090 ≈ 1,352 OctaneBench 2020.1 points (OpenBenchmarking.org)", url: "https://openbenchmarking.org/vs/Graphics/NVIDIA+GeForce+RTX+4090" },
  gsplat: { label: "gsplat paper: 30k steps ≈ 19.4 min on an A100 (Mip-NeRF 360 average)", url: "https://arxiv.org/abs/2409.06765" },
  colmap: { label: "CuSfM paper: COLMAP on GPU ≈ 346 s per 100 frames (KITTI, sequential matching)", url: "https://arxiv.org/abs/2510.15271" },
} satisfies Record<string, Cite>;

/** The machine the "local" numbers were measured on. */
export const LOCAL_MACHINE = "MacBook Pro (Apple M1 Pro, 8P+2E cores, 32 GB) · Blender 4.0.2 · Mitsuba 3";

/** Inputs to the computed numbers. */
export const ASSUMPTIONS = {
  awsL4PerHour: 0.8048,
  gcpL4PerHour: 0.7068,
  vmMinimumSeconds: 60,
  /** Launch → Blender ready on a pre-baked image (driver and Blender already installed). An estimate, not a measurement. */
  vmBootSeconds: 90,
  hoursPerMonth: 730,
  /** OctaneBench points of the node a farm would run one GPU job on (one RTX 4090). */
  farmNodeOb: 1352,
  rebusUsdPerObHour: 0.0053,
  /** Assumed: a 4090 renders the job 3× faster than our L4. We found no published L4 Blender Open Data median, so this is a guess. */
  farmSpeedupVsL4: 3,
};

const vmCost = (seconds: number) =>
  (Math.max(ASSUMPTIONS.vmMinimumSeconds, ASSUMPTIONS.vmBootSeconds + seconds) / 3600) * ASSUMPTIONS.awsL4PerHour;
const farmCost = (seconds: number) =>
  (seconds / ASSUMPTIONS.farmSpeedupVsL4 / 3600) * ASSUMPTIONS.farmNodeOb * ASSUMPTIONS.rebusUsdPerObHour;

/** Per-GPU-hour list price of each option, for the comparison table. */
export const HOURLY = {
  renda: 0.035 * 60,
  aws: ASSUMPTIONS.awsL4PerHour,
  gcp: ASSUMPTIONS.gcpL4PerHour,
  farm: ASSUMPTIONS.farmNodeOb * ASSUMPTIONS.rebusUsdPerObHour,
};

export type Job = {
  id: string;
  label: string;
  short: string;
  /** Renda, measured on the isolated benchmark copy of the worker: billed seconds, cost, wall time (submit → output, cold container). */
  renda: { billedSeconds: number; costUsd: number; wallSeconds: number };
  /** This Mac, measured wall time with Blender already warm (a first launch adds shader/kernel compile). */
  localSeconds: number;
};

/** The same packages on Renda and locally, 2026-10-20. */
export const JOBS: Job[] = [
  { id: "glb-cycles", label: "GLB still, Cycles (640×480, standard)", short: "GLB still · Cycles", renda: { billedSeconds: 10.1, costUsd: 0.0053, wallSeconds: 38.6 }, localSeconds: 5.4 },
  { id: "blend-cycles", label: ".blend still, Cycles (640×480, standard)", short: ".blend still · Cycles", renda: { billedSeconds: 8.2, costUsd: 0.0043, wallSeconds: 37.9 }, localSeconds: 2.9 },
  { id: "mitsuba", label: "GLB still, Mitsuba (640×480, standard)", short: "GLB still · Mitsuba", renda: { billedSeconds: 10.4, costUsd: 0.0052, wallSeconds: 44.5 }, localSeconds: 4.2 },
  { id: "step", label: "STEP still (640×480, standard)", short: "STEP still", renda: { billedSeconds: 7.5, costUsd: 0.0038, wallSeconds: 44.3 }, localSeconds: 3.3 },
  { id: "turntable", label: "72-frame turntable, Cycles (800×800, draft)", short: "72-frame turntable", renda: { billedSeconds: 41.7, costUsd: 0.0239, wallSeconds: 72.1 }, localSeconds: 93.3 },
];

export type SeriesId = "renda" | "vm" | "farm" | "local" | "always";

/** Cost per job: Renda measured; VM and farm computed from published prices with ASSUMPTIONS. Local is $0 marginal (not plottable on a log axis). */
export const JOB_COSTS = JOBS.map((j) => ({
  label: j.short,
  values: { renda: j.renda.costUsd, vm: vmCost(j.renda.billedSeconds), farm: farmCost(j.renda.billedSeconds) } as Partial<Record<SeriesId, number>>,
}));

/** Wall time per job: Renda measured (cold container), this Mac measured, a per-job VM computed (boot estimate + the same render time). */
export const JOB_WALL = JOBS.map((j) => ({
  label: j.short,
  values: {
    renda: j.renda.wallSeconds,
    local: j.localSeconds,
    vm: ASSUMPTIONS.vmBootSeconds + j.renda.billedSeconds,
  } as Partial<Record<SeriesId, number>>,
}));

/** Monthly cost of rendering N Cycles stills (the GLB still above). */
export const VOLUME = (() => {
  const still = JOBS[0];
  const perJob = { renda: still.renda.costUsd, vm: vmCost(still.renda.billedSeconds), farm: farmCost(still.renda.billedSeconds) };
  const alwaysOn = ASSUMPTIONS.awsL4PerHour * ASSUMPTIONS.hoursPerMonth;
  const volumes = [10, 30, 100, 300, 1_000, 3_000, 10_000, 30_000, 100_000, 300_000];
  return {
    perJob,
    alwaysOn,
    /** Above this many stills a month, an always-on VM is cheaper than paying per job, if you keep it busy and run it yourself. */
    crossover: Math.round(alwaysOn / perJob.renda),
    series: [
      { id: "renda" as SeriesId, label: "Renda, per second", points: volumes.map((n) => ({ x: n, y: n * perJob.renda })) },
      { id: "vm" as SeriesId, label: "AWS L4 VM, started per job", points: volumes.map((n) => ({ x: n, y: n * perJob.vm })) },
      { id: "farm" as SeriesId, label: "Render farm, 4090 node", points: volumes.map((n) => ({ x: n, y: n * perJob.farm })) },
      { id: "always" as SeriesId, label: "AWS L4 VM, always on", points: volumes.map((n) => ({ x: n, y: alwaysOn })) },
    ],
  };
})();

/** Mesh → Gaussian splat: Renda's standard bake (measured) vs a DIY pipeline on an AWS L4 (published timings, computed cost). */
export const SPLAT_DIY = (() => {
  const steps = [
    { label: "Boot a GPU VM", seconds: ASSUMPTIONS.vmBootSeconds, basis: "estimate" as Basis, note: "pre-baked image" },
    { label: "Render ~100 views", seconds: 90, basis: "measured" as Basis, note: "our L4 view work" },
    { label: "COLMAP structure-from-motion", seconds: 346, basis: "published" as Basis, note: "KITTI, 100 frames · Renda skips this: camera poses are exact" },
    { label: "Train 30k steps (gsplat)", seconds: 19.4 * 60, basis: "published" as Basis, note: "A100 average · an L4 is slower, so this is a lower bound" },
  ];
  const total = steps.reduce((n, s) => n + s.seconds, 0);
  return {
    renda: { costUsd: 0.136, wallMinutes: 4.1, billedSeconds: 237, psnr: 36.9 },
    diy: { costUsd: (total / 3600) * ASSUMPTIONS.awsL4PerHour, wallMinutes: total / 60 },
    steps,
  };
})();

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
    note: "The scene, lighting and camera angles are the same package.",
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
