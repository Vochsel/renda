/**
 * Mesh → Gaussian splat demos on the homepage: real `renda splat --preset standard` jobs on the production
 * pipeline (Modal, 2026-10-20). The mesh is the exact GLB that was baked (also used by the bake demos);
 * the splat is the job's .spz output. Seconds are what the API billed (GPU views + training, CPU
 * coordination); cost is list price per hardware class. Quality is the job's holdout PSNR/SSIM.
 */
export type SplatDemo = {
  id: string;
  input: string;
  mesh: string;
  splat: string;
  meshBytes: number;
  splatBytes: number;
  gaussians: number;
  views: number;
  steps: number;
  billedSeconds: number;
  costUsd: number;
  psnr: number;
  ssim: number;
  /** The `--preset fast` bake of the same mesh: Gaussians placed on the surface, albedo only, no training. */
  fast: { splat: string; splatBytes: number; gaussians: number; billedSeconds: number; costUsd: number };
  credit?: { title: string; author: string; url: string; license: string };
};

// Cost ≈ billed seconds at the L4 rate ($0.035/min): the views and training run on L4; CPU coordination is ~2 s.
export const SPLAT_DEMOS: SplatDemo[] = [
  {
    id: "camera",
    fast: { splat: "/splats/camera-fast.spz", splatBytes: 2082297, gaussians: 300000, billedSeconds: 5.4, costUsd: 0.003 },
    input: ".glb (Sketchfab)",
    mesh: "/bakes/camera-before.glb",
    splat: "/splats/camera.spz",
    meshBytes: 1504596,
    splatBytes: 7947041,
    gaussians: 350000,
    views: 96,
    steps: 7000,
    billedSeconds: 281.9,
    costUsd: 0.16,
    psnr: 30.1,
    ssim: 0.956,
    credit: {
      title: "Canon AT-1 Retro Camera",
      author: "AleixoAlonso",
      url: "https://sketchfab.com/models/9de66868d0f240e985da00c9480bfc82",
      license: "CC BY 4.0",
    },
  },
  {
    id: "table",
    fast: { splat: "/splats/table-fast.spz", splatBytes: 2067873, gaussians: 300000, billedSeconds: 3.6, costUsd: 0.002 },
    input: ".glb",
    mesh: "/bakes/table-before.glb",
    splat: "/splats/table.spz",
    meshBytes: 183172,
    splatBytes: 10273137,
    gaussians: 350000,
    views: 96,
    steps: 7000,
    billedSeconds: 315.2,
    costUsd: 0.18,
    psnr: 35.2,
    ssim: 0.982,
  },
];
