/**
 * Real renders of publicly licensed models and Gaussian splat captures (CC0 / CC BY 4.0 / MIT), made on the
 * production pipeline (Modal L4 GPUs / CPU, 2026-10-19). Billed seconds are what the API recorded for each job;
 * cost is list price per hardware class (GPU render + CPU coordination/encode).
 * Packages + attribution: testdata/showcase-public/ (CREDITS.md).
 */
export type Credit = { title: string; author: string; url: string; license: string };

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
  jobId: string;
  credit: Credit;
  image?: string;
  video?: string;
  poster?: string;
};

export const SHOWCASE: ShowcaseItem[] = [
  {
    "id": "porsche-cycles",
    "title": "Porsche 911 Turbo",
    "input": ".glb",
    "engine": "Blender Cycles",
    "hardware": "gpu-l4",
    "resolution": [
      1600,
      1000
    ],
    "frames": 1,
    "quality": "final",
    "billedSeconds": 17.4,
    "costUsd": 0.0087,
    "jobId": "job_5eb29766186140df9d30",
    "credit": {
      "title": "FREE 1975 Porsche 911 (930) Turbo",
      "author": "Lionsharp Studios",
      "url": "https://sketchfab.com/models/8568d9d14a994b9cae59499f0dbed21e",
      "license": "CC BY 4.0"
    },
    "image": "/showcase/porsche-cycles.webp"
  },
  {
    "id": "courtyard-mixed-cycles",
    "title": "Mesh plant in a captured courtyard",
    "input": ".glb + .spz",
    "engine": "Blender Cycles",
    "hardware": "gpu-l4",
    "resolution": [
      1000,
      750
    ],
    "frames": 1,
    "quality": "final",
    "billedSeconds": 23.0,
    "costUsd": 0.0129,
    "jobId": "job_494c93667dc94584864a",
    "credit": {
      "title": "SA3D_R&D_XP47 + Monstera Deliciosa",
      "author": "Stephane Agullo; Giora",
      "url": "https://superspl.at/view?id=cdcec084",
      "license": "CC BY 4.0"
    },
    "image": "/showcase/courtyard-mixed-cycles.webp"
  },
  {
    "id": "lizard-gsplat",
    "title": "Horned lizard sculpture capture",
    "input": ".spz",
    "engine": "gsplat",
    "hardware": "gpu-l4",
    "resolution": [
      1000,
      750
    ],
    "frames": 1,
    "quality": "final",
    "billedSeconds": 11.5,
    "costUsd": 0.0062,
    "jobId": "job_dd3f2fc7275c4915abdd",
    "credit": {
      "title": "Horned lizard (spz sample)",
      "author": "Niantic",
      "url": "https://github.com/nianticlabs/spz",
      "license": "MIT"
    },
    "image": "/showcase/lizard-gsplat.webp"
  },
  {
    "id": "camera-mitsuba",
    "title": "Canon AT-1",
    "input": ".glb",
    "engine": "Mitsuba 3",
    "hardware": "gpu-l4",
    "resolution": [
      1000,
      750
    ],
    "frames": 1,
    "quality": "final",
    "billedSeconds": 12.3,
    "costUsd": 0.0064,
    "jobId": "job_bf86119b88134e7b8e91",
    "credit": {
      "title": "Canon AT-1 Retro Camera",
      "author": "AleixoAlonso",
      "url": "https://sketchfab.com/models/9de66868d0f240e985da00c9480bfc82",
      "license": "CC BY 4.0"
    },
    "image": "/showcase/camera-mitsuba.webp"
  },
  {
    "id": "hall-turntable",
    "title": "Knock Community Hall, 72 frames \u2192 MP4",
    "input": ".spz",
    "engine": "gsplat",
    "hardware": "gpu-l4",
    "resolution": [
      960,
      720
    ],
    "frames": 72,
    "quality": "final",
    "billedSeconds": 55.6,
    "costUsd": 0.0281,
    "jobId": "job_72b7b2c6654e4a169438",
    "credit": {
      "title": "Knock Community Hall",
      "author": "scbenoit",
      "url": "https://superspl.at/scene/0ff2e6dc",
      "license": "CC BY 4.0"
    },
    "video": "/showcase/hall-turntable.mp4",
    "poster": "/showcase/hall-turntable-poster.webp"
  },
  {
    "id": "samurai-eevee",
    "title": "Cyber Samurai",
    "input": ".glb",
    "engine": "Blender Eevee",
    "hardware": "gpu-l4",
    "resolution": [
      1000,
      750
    ],
    "frames": 1,
    "quality": "final",
    "billedSeconds": 9.8,
    "costUsd": 0.0049,
    "jobId": "job_06ea95c531c343c5b88f",
    "credit": {
      "title": "Cyber Samurai",
      "author": "KhoaMinh",
      "url": "https://sketchfab.com/models/26ccafaddb2745ceb56ae5cfc65bfed5",
      "license": "CC BY 4.0"
    },
    "image": "/showcase/samurai-eevee.webp"
  },
  {
    "id": "raccoon-gsplat",
    "title": "Raccoon family carving capture",
    "input": ".spz",
    "engine": "gsplat",
    "hardware": "gpu-l4",
    "resolution": [
      1000,
      750
    ],
    "frames": 1,
    "quality": "final",
    "billedSeconds": 10.2,
    "costUsd": 0.0053,
    "jobId": "job_177cec19cf514799957f",
    "credit": {
      "title": "Raccoon family (spz sample)",
      "author": "Niantic",
      "url": "https://github.com/nianticlabs/spz",
      "license": "MIT"
    },
    "image": "/showcase/raccoon-gsplat.webp"
  },
  {
    "id": "craftsman-threejs",
    "title": "The Noble Craftsman diorama",
    "input": ".glb",
    "engine": "three.js",
    "hardware": "cpu",
    "resolution": [
      1000,
      750
    ],
    "frames": 1,
    "quality": "final",
    "billedSeconds": 10.5,
    "costUsd": 0.0026,
    "jobId": "job_8abe6f69cfca4a2ca525",
    "credit": {
      "title": "The Noble Craftsman",
      "author": "olmopotums",
      "url": "https://sketchfab.com/models/0e8ff87ffaa24731b2474badebca870d",
      "license": "CC BY 4.0"
    },
    "image": "/showcase/craftsman-threejs.webp"
  },
  {
    "id": "venus-turntable",
    "title": "Venus de Milo, 72 frames \u2192 MP4",
    "input": ".glb",
    "engine": "Blender Cycles",
    "hardware": "gpu-l4",
    "resolution": [
      960,
      720
    ],
    "frames": 72,
    "quality": "standard",
    "billedSeconds": 90.4,
    "costUsd": 0.0489,
    "jobId": "job_64f293de878b4b5eaf42",
    "credit": {
      "title": "Venus de Milo (Aphrodite of Milos)",
      "author": "SMK \u2013 National Gallery of Denmark",
      "url": "https://sketchfab.com/models/53082b5d6cef4c34a9701a2a24f58075",
      "license": "CC0"
    },
    "video": "/showcase/venus-turntable.mp4",
    "poster": "/showcase/venus-turntable-poster.webp"
  }
];
