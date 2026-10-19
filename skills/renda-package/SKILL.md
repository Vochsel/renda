---
name: renda-package
description: Author and debug Renda packages (*.renda.json), the typed JSON that tells Renda how to render a scene: camera, HDRI/lighting, material overrides, resolution, quality, engine, passes, animation and output format, plus composing scenes from layered packages (extends, package references). Includes per-format quirks (.blend, USD/USDZ, glTF/GLB, three.js JSON, FBX, OBJ, STL, PLY, Alembic, CAD STEP/IGES, Gaussian splats .ply/.splat/.spz) and an engine capability matrix. Use whenever you write or edit a .renda.json, need to fix framing/orientation/lighting/materials of a Renda render, reuse a studio setup across shots, place one package's scene inside another, choose an engine, or render a format with special needs (STL/STEP with no materials or mm units, three.js scenes, Gaussian splat captures, scene cameras).
---

# Authoring Renda packages

A package is small JSON next to the scene files. Only `scene.root` is required. Every
omitted field gets a sensible default (auto camera, studio HDRI, 1920×1080, standard
quality, png). Write only what you need to change: smaller packages are easier to debug.

- Full field reference: `references/fields.md`
- Worked examples: `references/examples.md`
- JSON Schema: MCP resource `renda://schema/v1` (or `https://renda.dev/schema/v1.json`)

Always run `renda_validate_package` (free, local) after editing. It returns JSON paths
and concrete fixes.

## Skeleton

```json
{
  "$schema": "https://renda.dev/schema/v1.json",
  "version": 1,
  "assets": { "model": { "uri": "./model.glb" } },
  "scene": { "root": "model" },
  "render": { "quality": "draft" },
  "output": { "format": "png" }
}
```

URIs: relative paths resolve against the package file. Local paths are uploaded and
rewritten to `asset://<sha256>` automatically; `https://` URLs are fetched by the worker.
`scene.root` can also be a path directly, without an `assets` entry.

## Fixing common render problems

| Symptom | Fix |
|---|---|
| Model lying on its side / upside down | `scene.upAxis`: `"Z"` for Blender/STL/most CAD, `"Y"` for glTF/three.js/USD default, `"-Y"` (Y down) for COLMAP-style splat captures |
| Tiny or clipped | Camera `mode: "auto"` frames the bounds; increase `padding` (0.1 → 0.25) for breathing room. Bad `unitScale` (mm STL → `0.001`) skews lighting and DOF, not framing. |
| Wrong angle | `camera.azimuthDeg` (around the up axis), `elevationDeg` (above the horizon) |
| Want the file's camera | `"camera": { "mode": "scene", "name": "Camera.001" }` (omit `name` for the active camera) |
| Exact shot | `"camera": { "mode": "lookat", "position": [x,y,z], "target": [x,y,z], "fovDeg": 35 }`, coordinates in the scene's up-axis convention |
| Grey / flat STL or OBJ | `scene.materialOverrides: { "*": { "baseColor": "#3b82f6", "roughness": 0.5 } }` |
| Too dark / blown out | `environment.intensity`; add `lights` (a `sun` gives crisp shadows) |
| Need a cut-out | `output.transparent: true` + png/webp/exr (video: webm/mov) |
| Noisy | raise `render.quality` (draft → standard → final) before touching `samples` |

## Engine matrix

| | blender-cycles | blender-eevee | blender-workbench | mitsuba | threejs | gsplat |
|---|---|---|---|---|---|---|
| Look | path-traced, photoreal | real-time PBR | clay/solid | spectral path tracer | WebGL raster (matches web viewers) | Gaussian splat rasteriser |
| Native formats | blend usd gltf fbx obj stl ply abc | same | same | via IR (all) | threejs, gltf, splats via Spark (others via IR) | splats only (.ply .splat .spz) |
| Gaussian splats | composited by depth | composited | composited | composited | native, mixed with meshes | native, meshes ignored |
| Hardware | GPU (L4) | GPU | CPU | CPU (LLVM) / GPU | CPU/GPU, cheapest | GPU (L4), cheapest for splats |
| Transparent bg | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Passes depth/normal/albedo | ✓ | partial | ✗ | depth, normal, albedo | depth, normal | depth, alpha |
| Blender materials (node trees) | exact | most | flat colour | principled subset | principled subset | n/a |
| Volumes, hair, displacement | ✓ | partial | ✗ | ✗ | ✗ | ✗ |
| Good for | final stills, product shots | turntables, previews, stylised | geometry checks | physically exact stills | three.js scenes, fast thumbnails, splat + mesh previews | splat captures: stills, turntables |

`auto` picks Blender for `.blend` and the cheapest engine that fits the quality for
everything else. Anything an engine can't honour shows up in job `warnings`, never as a
silent drop. Read them.

## Per-format notes

- **.blend**: renders natively in Blender with full node materials, modifiers, and the
  file's own world, lights and cameras. `"camera": {"mode":"scene"}` keeps the artist's
  shot. Packed textures are fine; *external* textures must be uploaded too. Pack them
  (File → External Data → Pack Resources) or list them in `assets`. Other engines see a
  baked conversion (modifiers applied, principled BSDF approximated).
- **USD / USDZ**: UsdPreviewSurface maps cleanly. Default up axis is read from stage
  metadata. USDZ is self-contained; loose `.usda` with references needs every referenced
  file uploaded (keep the directory structure, or zip it into a `.usdz`).
- **glTF / GLB**: prefer `.glb` (single file). Metallic-roughness maps 1:1. Y-up.
  Draco/meshopt compression is supported. KHR extensions beyond the common ones may warn.
- **three.js JSON**: the output of `object.toJSON()` / `scene.toJSON()` (ObjectLoader
  format 4.x, `metadata.type: "Object"`). Set `"format": "threejs"` on the asset to skip
  sniffing. Embedded textures (data URIs) work; remote texture URLs must be public.
  MeshStandard/Physical map well; Lambert/Phong are approximated. ShaderMaterial is not
  supported and falls back to grey with a warning. Use `engine: "threejs"` for a match with the web app.
- **FBX**: imported through Blender. Materials are often partial; expect warnings and
  consider `materialOverrides`. Units are frequently cm: check scale.
- **OBJ**: include the `.mtl` and textures (upload alongside, or zip). Without MTL you get
  the default material.
- **STL**: no materials or colours, and usually mm and Z-up. Set `upAxis: "Z"`,
  `unitScale: 0.001`, and a `materialOverrides` colour.
- **PLY**: vertex colours are used automatically. Point clouds without faces render as
  small spheres in Blender engines.
- **Alembic (.abc)**: imported via Blender; set `animation.frames` to the cache range.
- **DAE / 3MF**: loaded via the IR loaders; simple materials only.
- **STEP (.step/.stp) and IGES (.iges/.igs)**: tessellated with OpenCASCADE. Units are converted to
  metres automatically (don't add `unitScale: 0.001` for a mm file; `unitScale` applies on top), Z up,
  STEP part/assembly colours become materials. Treat like a product shot (studio HDRI, auto camera).
  Faceting visible on curves? Tighten `scene.tessellation`: `{"linearDeflection": 0.00005, "angularDeflection": 0.15}`
  (metres, radians; default = 0.05% of the bounding-box diagonal and 0.3 rad). Big assemblies: loosen it
  for drafts. IGES carries no colours here — add `materialOverrides`. Local renders need
  `uvx --with 'renda[cad]' renda …`; the cloud always has it.
- **Gaussian splats** (3DGS `.ply`, antimatter15 `.splat`, Niantic `.spz`; `.ksplat` with
  `engine: "threejs"` + a lookat camera only): `auto` renders splat-only scenes with `gsplat` (CUDA,
  cheapest). Put a mesh *inside* a capture with `scene.include` (root = the splat, include = the
  `.glb`), or a capture inside a mesh scene the other way round: `threejs` renders both natively;
  Cycles/Eevee/Workbench/Mitsuba render the meshes and composite the splats by depth (the splats
  keep their baked lighting: no shadows, reflections or relighting from the engine; this is noted
  in the job warnings). Captures from COLMAP pipelines are Y-down, so `.ply`/`.splat` default to
  `upAxis: "-Y"`; if a render is upside down set `"Y"` (or `"Z"`). `.spz` is Y-up. Auto framing
  ignores faint floaters, but for big captures (rooms, landscapes) prefer a `lookat` camera.
  `.splat` stores no view-dependent colour; `.ply`/`.spz` keep spherical harmonics. Package lights
  don't affect splats.

## Multiple files

Add props with `scene.include`:

```json
"scene": { "root": "car", "include": [{ "asset": "ground", "transform": { "translate": [0,0,-0.01], "scale": 10 } }] }
```

An include can carry its own `materialOverrides` (only that file) and `upAxis` (its axes, converted to the
root's — e.g. a Z-up STL dropped into a Y-up glTF scene).

## Composing scenes (layers)

Split reusable setup from per-shot files instead of copying JSON around:

```jsonc
// studio.renda.json — shared lighting/output (no scene needed)
{ "environment": { "hdri": "studio", "background": "#f2f2f0" },
  "lights": [{ "type": "area", "position": [2, 3, 2], "target": [0, 0, 0], "intensity": 80 }],
  "render": { "engine": "blender-cycles", "quality": "final" }, "output": { "format": "webp" } }

// shot.renda.json
{ "extends": "./studio.renda.json",
  "scene": { "root": "./product.step",
             "include": [{ "package": "./props/stand.renda.json", "as": "stand", "transform": { "translate": [0, -0.2, 0] } }] },
  "render": { "resolution": [3840, 2160] } }
```

- `extends` (string or list): applied in order, later wins, this file wins over all. Objects merge;
  **arrays replace** (to add a light you must restate the list); `null` removes a field
  (`environment.hdri: null` = no HDRI). Switching `camera.mode` replaces the whole camera.
- `scene.include[].package`: places another package's *scene* — its assets (renamed `stand.<id>`),
  its includes, lights (moved with it), scoped material overrides, `unitScale` and `upAxis`. Its
  camera/environment/render/output are ignored. `as` sets the namespace (default `ref<i>`).
- Paths resolve against the file that wrote them; layers can be `https://` URLs (relative paths inside
  resolve against that URL) or `asset://` uploads. Cycles, >8 levels, >64 layers or >1 MB layers fail
  with the layer and field named.
- Debug with `renda flatten shot.renda.json --provenance` (or MCP `renda_flatten_package`): it prints
  the composed package and which file set each field. Render/estimate/validate flatten automatically.
