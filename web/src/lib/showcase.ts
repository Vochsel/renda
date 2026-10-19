/**
 * Real renders from the production pipeline (Modal, 2026-10-19), with the billed seconds the API recorded for each job.
 * Packages: testdata/showcase/*.renda.json. Cost is list price for the billed seconds per hardware class (GPU render + CPU coordination/encode).
 */
export type ShowcaseItem = {
  id: string;
  title: string;
  input: string;
  engine: string;
  hardware: "cpu" | "gpu-l4" | "gpu-l40s";
  resolution: [number, number];
  frames: number;
  quality: "draft" | "standard" | "final";
  billedSeconds: number;
  costUsd: number;
  image?: string;
  video?: string;
  poster?: string;
};

export const SHOWCASE: ShowcaseItem[] = [
  {
    "id": "hero-cycles",
    "title": "Studio still",
    "input": ".blend",
    "engine": "Blender Cycles",
    "hardware": "gpu-l4",
    "resolution": [
      1600,
      1000
    ],
    "frames": 1,
    "quality": "final",
    "billedSeconds": 9.3,
    "costUsd": 0.0047,
    "image": "/showcase/hero-cycles.webp"
  },
  {
    "id": "mitsuba-blend",
    "title": "Same .blend, different engine",
    "input": ".blend",
    "engine": "Mitsuba 3",
    "hardware": "gpu-l4",
    "resolution": [
      1000,
      1000
    ],
    "frames": 1,
    "quality": "final",
    "billedSeconds": 7.5,
    "costUsd": 0.0035,
    "image": "/showcase/mitsuba-blend.webp"
  },
  {
    "id": "splat-mixed-cycles",
    "title": "Mesh inside a splat environment",
    "input": ".glb + .spz",
    "engine": "Blender Cycles",
    "hardware": "gpu-l4",
    "resolution": [
      1280,
      720
    ],
    "frames": 1,
    "quality": "standard",
    "image": "/showcase/splat-mixed-cycles.webp",
    "billedSeconds": 21.9,
    "costUsd": 0.0114
  },
  {
    "id": "threejs-eevee",
    "title": "three.js scene, rendered in Blender",
    "input": "three.js JSON",
    "engine": "Blender Eevee",
    "hardware": "gpu-l4",
    "resolution": [
      1000,
      1000
    ],
    "frames": 1,
    "quality": "final",
    "billedSeconds": 9.3,
    "costUsd": 0.0039,
    "image": "/showcase/threejs-eevee.webp"
  },
  {
    "id": "splat-gsplat",
    "title": "Gaussian splat capture",
    "input": ".ply (3DGS)",
    "engine": "gsplat",
    "hardware": "gpu-l4",
    "resolution": [
      1280,
      720
    ],
    "frames": 1,
    "quality": "standard",
    "image": "/showcase/splat-gsplat.webp",
    "billedSeconds": 15.0,
    "costUsd": 0.0073
  },
  {
    "id": "stl-clay",
    "title": "Bare STL, clay material override",
    "input": ".stl",
    "engine": "Blender Cycles",
    "hardware": "gpu-l4",
    "resolution": [
      1000,
      1000
    ],
    "frames": 1,
    "quality": "standard",
    "billedSeconds": 5.1,
    "costUsd": 0.0023,
    "image": "/showcase/stl-clay.webp"
  },
  {
    "id": "usdz-turntable",
    "title": "USDZ turntable, 72 frames \u2192 MP4",
    "input": ".usdz",
    "engine": "Blender Cycles",
    "hardware": "gpu-l4",
    "resolution": [
      800,
      800
    ],
    "frames": 72,
    "quality": "draft",
    "billedSeconds": 45.8,
    "costUsd": 0.0224,
    "video": "/showcase/usdz-turntable.mp4",
    "poster": "/showcase/usdz-turntable-poster.webp"
  },
  {
    "id": "splat-turntable",
    "title": "Splat turntable, 48 frames \u2192 MP4",
    "input": ".splat",
    "engine": "gsplat",
    "hardware": "gpu-l4",
    "resolution": [
      720,
      720
    ],
    "frames": 48,
    "quality": "standard",
    "video": "/showcase/splat-turntable.mp4",
    "poster": "/showcase/splat-turntable-poster.webp",
    "billedSeconds": 30.0,
    "costUsd": 0.0149
  }
];
