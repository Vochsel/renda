# Renderers: what's in, what was evaluated

Renda's engines are one adapter each (`core/renda/engines/`). Checked 2026-10-20.

| Renderer | License | Headless Linux | GPU | Inputs | Status in Renda |
|---|---|---|---|---|---|
| Blender Cycles / Eevee / Workbench | GPL | ✓ | CUDA/OptiX, EGL | .blend + importers | **in** |
| Mitsuba 3 | BSD-3 | ✓ | CUDA | own XML/dict | **in** (via IR) |
| three.js (+ Spark) | MIT | headless Chromium | WebGL | glTF, three.js JSON, splats | **in** |
| gsplat | Apache-2.0 | ✓ | CUDA | Gaussian splats | **in** |
| **Google Filament** (via [filly](https://pypi.org/project/filly/)) | Apache-2.0 (filly MIT) | ✓ EGL, Mesa llvmpipe on CPU | GL/Vulkan | glTF 2.0 | **in** — `render.engine: "filament"` |
| **pbrt-v4** | Apache-2.0 | ✓ | CUDA/OptiX (needs the OptiX SDK; we build CPU-only) | own scene format | **in** — `render.engine: "pbrt"` (via IR → pbrt scene) |
| DreamWorks MoonRay | Apache-2.0 | ✓ | XPU (CUDA + OptiX) on supported Linux | RDL2; USD via hdMoonray / `hd_usd2rdl` | not yet — no prebuilt packages; the official route is a source build of ~20 dependencies in a Rocky 9 container, and the containerised build omits GPU support |
| Intel OSPRay Studio | Apache-2.0 | ✓ (`ospStudio batch`) | CPU (Embree) | glTF, OBJ, `.sg` scenes | not yet — batch mode works, but it ignored an injected camera FOV and HDRI light (it re-frames with its default camera); needs the OSPRay C/Python API to match framing and lighting |
| LuxCoreRender 2.10 | Apache-2.0 | ✓ (`pyluxcore` wheels) | CUDA 12.8 / OpenCL | own scene format | good next candidate: pip-installable, unbiased, GPU |
| Radeon ProRender | Apache-2.0 SDK, binary core | ✓ | Vulkan/HIP | USD via hdRPR | not evaluated in depth (binary core) |
| Godot 4 | MIT | `--headless` uses a dummy renderer (no images) | — | glTF | impractical headless |
| appleseed | MIT | ✓ | CPU | own / Blender | low activity, no 2025 release |

"Blue Sky's renderer" (CGIStudio) was never open-sourced; the open-sourced studio renderer is DreamWorks' MoonRay (Academy Software Foundation).

## Engine notes

**filament** — Google's real-time PBR rasteriser (the engine behind Android's SceneView and many web viewers). Rendered offscreen over EGL; IBL from the same equirect HDRI the other engines use (30 000 lux per unit of environment radiance, with package lights scaled the same way), AgX/ACES/Filmic/neutral tonemapping, MSAA + supersampling, SSAO, shadowed sun/spot/point lights, glTF animations, native depth of field. No splats, no texture overrides yet, no depth/normal passes. Linux x86_64 only (filly ships no macOS wheels), so it runs on the cloud and not on a Mac.

**pbrt** — the PBR book's spectral path tracer, as a ground-truth reference. IR → pbrt-v4 scene: meshes as binary PLY with world transforms baked in; principled materials → conductor (metallic), dielectric (transmission), coated-diffuse or diffuse; base-colour textures as sRGB image maps; the environment converted to pbrt's equal-area octahedral map with `imgtool makeequiarea`; package/scene lights as distant/point/spot/area lights; thin-lens depth of field. Output is a linear EXR that goes through the same tonemap + background composite as Mitsuba. CPU only (8-core containers).

Neither is picked by `engine: "auto"`; ask for them explicitly.

Sources: [MoonRay to ASWF](https://www.jonpeddie.com/news/dreamworks-opens-moonray-to-the-world/), [MoonRay install docs](https://docs.openmoonray.org/getting-started/installation), [MoonRay Rocky 9 container build](https://docs.openmoonray.org/getting-started/installation/building-moonray/rocky9_container_build/), [CGIStudio](https://history.siggraph.org/?p=66525), [filly](https://pypi.org/project/filly/), [Filament](https://github.com/google/filament), [pbrt-v4](https://github.com/mmp/pbrt-v4), [pbrt-v4 users guide](https://pbrt.org/users-guide-v4), [OSPRay Studio releases](https://github.com/RenderKit/ospray-studio/releases), [LuxCoreRender 2.10](https://www.cgchannel.com/2025/05/luxcorerender-2-10-and-blendluxcore-2-10-have-been-released/), [Godot 4 headless](https://ask.godotengine.org/150510/godot-headless-not-same-godot-window-cant-render-the-backend).
