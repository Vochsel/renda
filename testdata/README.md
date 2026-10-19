# Renda test set

One small studio scene: a wooden table, a gold torus knot, a red plastic sphere, an orange/white
checker-textured cube and a blue cylinder, with a camera, a sun and a fill light. It's saved in
every format Renda reads, plus packages that exercise the spec and a HyperFrames composition.
Everything is generated from code (`generate.py`, `generate_splats.py`), so it's all CC0 / license-clean.
About 15 MB in total (10 MB of it Gaussian splats).

```bash
uv run --project core python testdata/generate.py      # regenerate everything (needs Blender + node)
uv run --project core python testdata/generate_splats.py   # regenerate the Gaussian splat scenes (--big: 200k-splat perf files)
uv run --project core python testdata/smoke.py         # info every scene, validate every package (pydantic + Zod)
uv run --project core --extra mitsuba --extra threejs \
  python testdata/smoke.py --render --matrix --hyperframes   # + renders → out/contact.png, out/matrix.png
uv run --project core pytest tests/test_testdata.py    # loader/validation checks in CI
```

`RENDA_BLENDER` overrides the Blender binary (default `/Applications/Blender.app/Contents/MacOS/Blender`).

## `scenes/`

| File | Tests |
|---|---|
| `scene.blend` | Native Blender: Principled materials, packed texture, camera, sun + area light, world |
| `anim.blend` | Scene animation (knot spins, sphere bounces), 48 frames |
| `scene.usda` | USD ASCII with UsdPreviewSurface, external texture (`textures/checker.png`), camera, lights |
| `scene.usdz` | USDZ (zip) package |
| `scene.glb` | Binary glTF: PBR materials, embedded texture, camera, `KHR_lights_punctual` sun (in lux) |
| `scene.gltf` + `scene.bin` + `checker.png` | glTF with external buffer and texture (sibling files must travel along) |
| `scene.fbx` | FBX (binary) with embedded texture, camera, lights. Goes through Blender's importer |
| `scene.obj` + `scene.mtl` | OBJ with MTL materials. `scene.mtl` must be **rejected** as a scene (sidecar) |
| `scene.stl` | Binary STL, single mesh (the knot), no materials |
| `scene_ascii.stl` | ASCII STL of the same mesh |
| `scene.ply` | Whole scene as one mesh with height-based **vertex colours**. Read as Y-up |
| `scene.dae` | COLLADA (Z_UP) with materials |
| `scene.3mf` | 3MF (Z-up per spec), geometry only |
| `scene.abc` | Alembic with 24 animated frames and a camera, no materials |
| `threejs/scene.json` | three.js `Object3D.toJSON()` (format 4.6) made with real three@0.170: parametric geometries (Box, Sphere, Cylinder, **TorusKnot**), Group hierarchy, MeshStandardMaterial, data-URI PNG texture, DirectionalLight, AmbientLight, PerspectiveCamera |

`splats/` (procedural, `generate_splats.py`):

| File | Tests |
|---|---|
| `splats/garden.ply` | 3DGS binary PLY, 41k splats, SH degree 1, COLMAP Y-down axes, plus faint far-away floaters (auto-framing must ignore them) |
| `splats/garden.splat` | antimatter15 `.splat` of the same scene (band-0 colour only), Y-down |
| `splats/garden.spz` | Niantic `.spz` v2 of the same scene, Y-up; all three must render identically |
| `splats/environment.ply` | 79k-splat room (floor at y=-1, walls, potted plants) for placing `scene.glb`'s table inside a capture |

`hdri/sky.hdr`: a 256×128 Radiance HDR (sky gradient, sun blob, grey ground) for custom environment tests.

## `packages/`

| Package | Scene | Engine | Exercises |
|---|---|---|---|
| `still-cycles` | blend | Cycles | Native .blend, file camera + world |
| `still-eevee` | glb | EEVEE | JPG output |
| `still-workbench` | obj | Workbench | WebP output, clay preview |
| `still-mitsuba` | usdz | Mitsuba | USD → IR → Mitsuba |
| `still-threejs` | three.js JSON | three.js | Native three.js JSON, file camera |
| `blend-to-mitsuba` | blend | Mitsuba | Cross-engine: .blend → IR via Blender extraction |
| `threejs-to-cycles` | three.js JSON | Cycles | Cross-engine: three.js → IR → glTF → Blender, camera bridged |
| `turntable-mp4` | glb | EEVEE | Auto camera turntable, solid background, H.264 |
| `transparent-passes` | gltf | Cycles | Transparent PNG + alpha / depth (16-bit) / normal passes |
| `lookat-camera` | fbx | Cycles | Look-at camera. Coordinates are in the file's axes (Y-up for FBX) |
| `scene-camera` | usda | Mitsuba | Camera picked by name from the file |
| `stl-material-override` | stl | auto | `materialOverrides["*"]` on a material-less mesh, package sun light |
| `custom-hdri` | ply | Mitsuba | HDRI from `assets`, rotation, vertex colours |
| `include-transforms` | dae + stl | EEVEE | `include[]` with translate / rotate / non-uniform scale |
| `anim-gif` | anim.blend | Workbench | Scene animation, frame `step`, GIF |
| `webm-transparent` | 3mf | EEVEE | Transparent VP9 WebM turntable, material override |
| `alembic-still` | abc | Cycles | Single frame of an Alembic cache, file camera |
| `splat-still-auto` | garden.ply | auto → gsplat | Splat-only still, `-Y` default + warning, robust framing |
| `splat-spz-threejs` | garden.spz | three.js | Splats rendered natively by Spark |
| `splat-turntable-mp4` | garden.splat | gsplat | Splat turntable → H.264 |
| `mixed-mesh-splat-{cycles,eevee,mitsuba,threejs}` | environment.ply + scene.glb | each | Mesh table inside a splat room: depth compositing (Blender/Mitsuba), native (three.js), matching look-at camera |

## `hyperframes/`

`title-card/` is a minimal HyperFrames composition: animated title, subtitle and accent rule,
plus a video slot (`clipUrl` variable, default `assets/placeholder.mp4`) meant for a Renda
turntable. `npx hyperframes check` passes. `title-card.hyperframes.json` is the matching
`kind: "hyperframes"` job package (see `worker/README.md`). The CLI/MCP zip `project` and upload it.

## Known gaps (as of the last smoke run)

- PLY vertex colours only show in Mitsuba. Blender and three.js render the mesh white.
- `scene.3mf` and `scene.abc` carry no materials: trimesh's 3MF export drops colours, and Alembic stores none.
- Blender's USD exporter scales light intensities (sun ÷4, area ×4). Renda reads USD values as written, so `scene.usda` lighting differs a little from `scene.blend`.
- Mitsuba on macOS/CPU has no denoiser, so `draft` renders are grainy and gold shows fireflies.
- Splats composited over Blender/Mitsuba don't receive that engine's lighting or shadows and use one expected depth per pixel, so semi-transparent splats right at a mesh surface are approximated.
- The numpy splat rasteriser (used without CUDA) takes a few seconds per frame for ~50k splats at 720p; real captures (1–5M splats) should render on the cloud GPU path.
