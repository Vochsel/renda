# renda (core)

A lightweight render CLI and scene-conversion layer that wraps **Blender** (Cycles, EEVEE, Workbench),
**Mitsuba 3** and **three.js**. It renders locally, or in the Renda cloud with `--cloud`.

```bash
uvx renda render teapot.blend --cloud          # no install: render in the Renda cloud (starts a free trial)
uv tool install "renda[all]"                   # or install it, with every local engine
renda render teapot.blend                      # → renders/frame_0001.png
renda render sneaker.usdz --turntable --format mp4 --res 1080x1080
renda render scene.json --engine threejs       # three.js JSON scene, cheapest preview
renda render capture.spz --engine threejs      # Gaussian splats locally (Spark); in the cloud auto → gsplat
renda render bracket.stl --engine mitsuba --quality final -o out/
renda info robot.fbx                           # format, triangles, bounds, cameras, lights
renda convert robot.fbx robot.glb              # any → .glb / .gltf / .obj / .usd(a|c) / .rir
renda init model.glb                           # writes model.renda.json to edit
renda engines                                  # what's installed here
```

Add `--json` to any command for machine-readable output: `{"status": "ok", …, "next"?: […]}`, or on
failure `{"status": "error", "reason", "message", "hint", "next"}`. The `hint` and `next` tell an agent what to
do. Exit codes match the Node CLI: 0 ok, 1 error, 2 usage, 3 auth needed, 4 payment required.

Cloud account commands: `renda trial` (no signup), `renda claim`, `renda signup <email>` / `renda verify <code>`,
`renda login`, `renda whoami`, `renda balance`, `renda billing`, `renda estimate`, `renda jobs`. `renda mcp add`
installs the MCP server into Claude Code / Cursor / Codex (via `npx @rendahq/cli`). Prefer Node? The same cloud
commands are in `npx @rendahq/cli …`.

## Formats

| Format | Extensions | How it's loaded |
|---|---|---|
| Blender | `.blend` | Blender opens it natively; other engines get it through `extract.py` → IR |
| USD | `.usd .usda .usdc .usdz` | `usd-core` (UsdPreviewSurface, UsdLux, cameras) |
| glTF | `.gltf .glb` | trimesh, plus our own pass for cameras and `KHR_lights_punctual` |
| three.js JSON | `.json` (`metadata.type: "Object"`) | pure Python: BufferGeometry (indexed, groups, interleaved), primitives, InstancedMesh, Standard/Physical/Basic/Lambert/Phong materials, data-URI textures, cameras, lights |
| OBJ / STL / PLY / COLLADA / 3MF | `.obj .stl .ply .dae .3mf` | trimesh. STL is welded and auto-smoothed at 30°, so CAD parts stay crisp |
| FBX / Alembic | `.fbx .abc` | Blender headless import → IR |
| CAD | `.step .stp .iges .igs` | OpenCASCADE via `cascadio` (extra `renda[cad]`): metres, Z-up, STEP colours, `scene.tessellation` density (default 0.05% of the bbox diagonal), cached |
| Gaussian splats | `.ply` (3DGS) `.splat` `.spz` (`.ksplat`: threejs only) | pure Python (`ir/loaders/splat.py`): SH degree 0–3; `.ply`/`.splat` are Y-down (`upAxis: "-Y"`), `.spz` Y-up |
| Renda IR | `.rir/` | directory: `stage.json` + `meshes/*.npz` + `splats/*.npz` + `textures/` |

Format detection checks magic bytes before the extension (`renda.formats.detect`).

## Engines

| Engine | Native formats | Hardware | Notes |
|---|---|---|---|
| `blender-cycles` | blend usd gltf fbx obj stl ply abc | CPU / GPU (OptiX → CUDA → HIP → Metal) | adaptive sampling, OIDN/OptiX denoise, persistent data across frames |
| `blender-eevee` | same | GPU | fast raster, good for drafts and turntables |
| `blender-workbench` | same | GPU | clay/solid previews |
| `mitsuba` | rir (everything else via IR) | CPU (LLVM), CUDA, Metal | depth/normal/albedo AOVs, OptiX denoise on CUDA, OIDN elsewhere |
| `threejs` | threejs, gltf, splats | headless Chromium (SwiftShader on CPU) | cheapest; AgX tonemapping like Blender; splats via Spark, mixed with meshes |
| `gsplat` | splats | GPU (nerfstudio gsplat); `RENDA_SPLAT_BACKEND=numpy` for the CPU reference | splat-only scenes; depth/alpha passes |

Splats in a mesh scene (or meshes in a splat capture, via `scene.include`) also render with Blender and Mitsuba:
the engine renders the meshes, Renda rasterises the splats from the same cameras and composites them by depth
(no engine lighting on the splats; the job says so).

Any engine can render any format: formats an engine can't load natively go through the IR
(`.blend → IR → Mitsuba`, `three.js JSON → IR → .glb → Blender`, and so on).

Blender lookup order: `$RENDA_BLENDER`, then `blender` on `PATH`, then `/Applications/Blender.app`, then the
`bpy` wheel (`renda[blender]`). **The system binary is preferred.** The `bpy` wheel pins one Python version and
is large. Blender 4.2 LTS and newer are the target. 4.0 is known to work.

For three.js: `uv tool install "renda[threejs]" && uvx playwright install chromium`. On first use the engine downloads
three.js r180 and Spark 2.3 (splats) from npm into `~/.cache/renda`.

## The Renda package

A `.renda.json` holds what scene formats can't express: output format, resolution, camera, HDRI, lights,
material overrides, turntables and quality. See `../spec` (the canonical Zod schema) and `../ARCHITECTURE.md`.
`renda.spec` mirrors it in pydantic, and the tests check that both produce identical defaults.

Scene-native setups: a `.blend` already has a camera and world lighting. When the package doesn't set
`camera` or `environment` explicitly, the file's own camera and world are used instead of auto-framing and the
studio HDRI.

Built-in HDRIs (`studio`, `sunset`, `night`, `white`) are generated procedurally on first use, so the repo
ships no binaries.

Layered packages (`extends`, `scene.include[].package`) are flattened by `renda.layers` (a port of
`flattenPackage` in `../spec`, checked against the same fixtures). `load_package(path)` and every CLI command
flatten automatically; `renda flatten pkg.renda.json --provenance` shows the result and which file set each
field. https layers are fetched through `renda.netsafe` (public addresses only, no redirects to private ones).

## Python API (used by the cloud worker)

```python
from renda.spec import load_package
from renda.assets import resolve
from renda.planner import plan, estimate_cost
from renda.render import render_frames
from renda.video import encode
from renda.cache import cache_key

pkg = load_package("hero.renda.json")
paths = resolve(pkg, base_dir, fetch=my_asset_fetcher)        # asset:// / https:// → local files
p = plan(pkg, paths, priority="standard")                     # engine, hardware, chunks, est_seconds, cache_key
lo, hi = estimate_cost(p, {"cpu": 0.02, "gpu-l4": 0.06, "gpu-l40s": 0.15})
for chunk in p.chunks:                                        # one container per chunk in the cloud
    r = render_frames(p, pkg, paths, out_dir, chunk)          # → RenderResult(files, warnings, seconds)
encode(frame_files, pkg.animation.fps, pkg.output, out_dir / "out.mp4")
```

The planner picks the cheapest hardware class that meets the priority's deadline. It sizes chunks so that
scene loading is ≤10% of a chunk's time (5% for economy, 25% for rush), with at most 50 containers. Throughput
constants in `planner.py` are first guesses and should be calibrated against real Modal runs.

## Cloud

```bash
renda signup you@example.com     # emails a 6-digit code
renda verify 123456              # saves an API key to ~/.config/renda/credentials.json
renda render scene.blend --cloud # uploads (sha256-deduped), renders, downloads
renda jobs | renda usage | renda billing | renda whoami | renda login (browser device flow)
```

`RENDA_API_URL` (default `https://renda.vochsel.com`) and `RENDA_API_KEY` override the saved credentials. The
client sends packages exactly as written, with local paths rewritten to `asset://<sha256>`. This lets the
server tell explicit fields apart from defaults.

## Development

```bash
uv sync --extra mitsuba --extra usd --extra threejs
uv run --with pytest --with jsonschema pytest -q
```

Engine tests skip when that engine isn't installed. The TS↔Python parity test needs `node` and a built
`spec/dist` (`pnpm -C ../spec build`).
