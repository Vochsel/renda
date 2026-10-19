/**
 * Light-bake demos on the homepage: real `renda bake` jobs on the production pipeline (Modal, 2026-10-19).
 * Seconds are what the API billed (GPU bake + CPU unwrap/encode); cost is list price per hardware class.
 * GLBs live in public/bakes/ (textures re-encoded at JPEG q80–85 for the web; the bake itself is unchanged).
 * Packages: `renda bake <file> --engine <engine> --mode unlit --size <size>`.
 */
export type BakeVariant = {
  id: string;
  label: string;
  src: string;
  bytes: number;
  /** null for the "before" model (plain GLB, original PBR materials). */
  bake: null | {
    engine: "Blender Cycles" | "Mitsuba 3" | "three.js";
    hardware: "cpu" | "gpu-l4";
    mode: "unlit" | "ao" | "lightmap";
    textureSize: number;
    samples: number;
    billedSeconds: number;
    costUsd: number;
  };
};

export type BakeDemo = {
  id: string;
  input: string;
  variants: BakeVariant[];
  defaultVariant: string;
  credit?: { title: string; author: string; url: string; license: string };
};

export const BAKE_DEMOS: BakeDemo[] = [
  {
    id: "camera",
    input: ".glb (Sketchfab)",
    defaultVariant: "cycles",
    credit: {
      title: "Canon AT-1 Retro Camera",
      author: "AleixoAlonso",
      url: "https://sketchfab.com/models/9de66868d0f240e985da00c9480bfc82",
      license: "CC BY 4.0",
    },
    variants: [
      { id: "before", label: "Before", src: "/bakes/camera-before.glb", bytes: 1504596, bake: null },
      {
        id: "cycles", label: "Cycles", src: "/bakes/camera-cycles.glb", bytes: 1416028,
        bake: { engine: "Blender Cycles", hardware: "gpu-l4", mode: "unlit", textureSize: 2048, samples: 256, billedSeconds: 41.1, costUsd: 0.0232 },
      },
    ],
  },
  {
    id: "table",
    input: ".glb (renda testdata)",
    defaultVariant: "mitsuba",
    variants: [
      { id: "before", label: "Before", src: "/bakes/table-before.glb", bytes: 183172, bake: null },
      {
        id: "cycles", label: "Cycles", src: "/bakes/table-cycles.glb", bytes: 578004,
        bake: { engine: "Blender Cycles", hardware: "gpu-l4", mode: "unlit", textureSize: 2048, samples: 256, billedSeconds: 41.1, costUsd: 0.0231 },
      },
      {
        id: "mitsuba", label: "Mitsuba", src: "/bakes/table-mitsuba.glb", bytes: 730584,
        bake: { engine: "Mitsuba 3", hardware: "gpu-l4", mode: "unlit", textureSize: 2048, samples: 256, billedSeconds: 24.9, costUsd: 0.0134 },
      },
      {
        id: "threejs", label: "three.js", src: "/bakes/table-threejs.glb", bytes: 391400,
        bake: { engine: "three.js", hardware: "cpu", mode: "unlit", textureSize: 1024, samples: 192, billedSeconds: 21.4, costUsd: 0.0054 },
      },
    ],
  },
];
