# Renda architecture

Renda is an open-source, agent-first cloud renderer for 3D files and scenes.
You send it a file (`.blend`, `.usd/.usda/.usdc/.usdz`, `.gltf/.glb`, `.fbx`, `.obj`, `.stl`,
`.ply`, `.dae`, `.3mf`, `.abc`, three.js JSON scenes, Gaussian splats `.ply`/`.splat`/`.spz`) or a
**Renda package** (`*.renda.json`) and get back images or video. Renders run on
Modal, are billed per second (invoiced in render-minutes), and every part of the
product — signup, billing, rendering, results — is reachable by an agent without
a browser.

```
             ┌───────────── agents ─────────────┐        humans
             │ skills/  · mcp/ (stdio)          │     web dashboard
             │ renda CLI (core/)  · remote MCP  │           │
             └──────────────┬───────────────────┘           │
                            ▼  HTTPS + API key               ▼  WorkOS AuthKit
                 ┌──────────────────────────────────────────────────┐
                 │ web/  Next.js (Vercel)                           │
                 │  /api/v1/*  REST     /api/mcp  remote MCP         │
                 │  Postgres (Drizzle) · R2 presigned URLs · Stripe │
                 └──────────────┬──────────────────▲────────────────┘
                   submit (HMAC)│                  │ events + metered seconds (HMAC)
                                ▼                  │
                 ┌──────────────────────────────────────────────────┐
                 │ worker/  Modal app                                │
                 │  plan → fan-out chunks (CPU / L4 / L40S) → encode │
                 │  asset cache Volume · result cache · snapshots    │
                 │  uses core/ (renda python pkg) inside images      │
                 └──────────────────────────────────────────────────┘
```

## Components

| Dir | What | Language |
|---|---|---|
| `spec/` | `@rendahq/spec`: the Renda package format. Zod schema is canonical; `schema/renda.v1.json` is generated from it. | TS |
| `core/` | `renda` Python package + `renda` CLI. Scene IR, loaders, engine adapters (Blender, Mitsuba, …), local rendering, and the cloud client commands. | Python |
| `worker/` | Modal app. Receives jobs, plans, renders in parallel chunks, encodes video, uploads to R2, reports events/usage. | Python |
| `web/` | Next.js dashboard + public REST API + remote MCP endpoint. WorkOS for accounts, Stripe for metered billing, Postgres via Drizzle, R2 for files. | TS |
| `mcp/` | `@rendahq/mcp`: stdio MCP server for local agents (can upload local files). | TS |
| `skills/` | Agent skills (`SKILL.md`) for rendering, authoring packages, onboarding, and video. | Markdown |

## 1. The Renda package (`*.renda.json`)

Most 3D formats can't say "render this at 1920×1080 with this HDRI from this
camera as an MP4". The package is a small typed JSON file that does, and points
at the scene files. Everything is optional except `scene`; defaults give a nice
framed studio render.

```jsonc
{
  "$schema": "https://renda.dev/schema/v1.json",
  "version": 1,
  "name": "teapot-hero",
  "assets": {
    "teapot": { "uri": "./teapot.blend" },               // local path, https://, or asset://<sha256>
    "studio": { "uri": "https://example.com/studio.hdr" }
  },
  "scene": {
    "root": "teapot",                                    // asset id or uri of the main scene file
    "include": [{ "asset": "prop", "transform": { "translate": [1, 0, 0] } }],
    "upAxis": "Z", "unitScale": 1,
    "materialOverrides": { "*": { "baseColor": "#d0d0d0", "roughness": 0.4 } },   // keys: name, glob, /regex/
    "textureOverrides": [                                // any image (png/jpg/webp/avif/heic/tiff/exr/hdr/svg/psd) or video
      { "match": { "material": "/wood/i" }, "source": "./oak.jpg", "uv": { "scale": 4 } },
      { "match": { "object": "Screen*", "slot": "emission" }, "source": "./promo.mp4", "strength": 2 }
    ]
  },
  "camera": { "mode": "auto", "fovDeg": 35, "elevationDeg": 20, "azimuthDeg": 35, "padding": 0.1 },
  // or { "mode": "scene", "name": "Camera.001" }
  // or { "mode": "lookat", "position": [3,2,3], "target": [0,0,0], "fovDeg": 40 }
  "environment": { "hdri": "studio", "intensity": 1, "rotationDeg": 0, "background": "hdri" },
  "lights": [{ "type": "sun", "direction": [-1,-1,-1], "intensity": 3 }],
  "render": {
    "engine": "auto",            // auto | blender-cycles | blender-eevee | blender-workbench | mitsuba | threejs
    "resolution": [1920, 1080],
    "quality": "standard",       // draft | standard | final  (maps to samples/denoise per engine)
    "samples": null, "denoise": true, "seed": 0,
    "hardware": "auto"           // auto | cpu | gpu  (worker picks the cheapest class that fits)
  },
  "animation": { "frames": [1, 120], "fps": 30, "turntable": { "revolutions": 1 } },
  "output": { "format": "mp4", "passes": ["beauty"], "transparent": false, "video": { "codec": "h264", "crf": 18 } }
}
```

Rules:
- `uri` resolution: relative paths are relative to the package file. `asset://<sha256>`
  refers to an already-uploaded blob. The CLI/MCP rewrite local paths to
  `asset://` on upload, so the server only ever sees `asset://` or `https://`.
- Coordinates (`camera.lookat`, light positions, `include[].transform`) are in the scene file's own axes
  and units, after `unitScale`; the up axis is `scene.upAxis` or the format's convention (glTF/three.js/
  OBJ/FBX/PLY: Y-up; Blender/USD default/STL/3MF/DAE as authored, usually Z-up; Gaussian splat `.ply`/`.splat`:
  `-Y` = COLMAP/OpenCV Y-down, `.spz`: Y-up). `renda info` prints both. A `-Y` stage is turned Y-up (180° about X)
  when the scene is built, so package coordinates for splat scenes are Y-up; includes are interpreted in the
  root's (normalised) axes.
- A bare scene file submitted without a package is wrapped in the default package.
- Fields an engine can't honour produce a **warning** in the job, never a silent drop.

### Composing packages (layers + references)

A package can be built from others, USD-style but small:

```jsonc
{
  "extends": ["https://cdn.example.com/studio.renda.json", "./brand.renda.json"],
  "scene": {
    "root": "./product.glb",
    "include": [{ "package": "./props/chair.renda.json", "as": "chair", "transform": { "translate": [1, 0, 0] } }]
  },
  "render": { "resolution": [3840, 2160] }
}
```

- **`extends`** layers packages underneath, in order; later layers win and the file itself wins over all.
  Objects deep-merge, arrays (`lights`, `passes`, `scene.include`) replace, `null` removes a field —
  except where `null` is a value (`environment.hdri`, `render.samples`). Objects whose kind differs
  (`camera.mode`) replace rather than mix. A layer's `null` removes from the layers *it* extends.
- **`scene.include[].package`** places another package's scene: its assets (ids namespaced `<as>.<id>`),
  root + includes, lights, scoped `materialOverrides`, `unitScale` and `upAxis`. Its camera, environment,
  render and output are ignored. Flattening emits plain includes with `transform` (placement in the
  root's axes), `upAxis` (converted to the root's up) and `localTransform` (anything in the referenced
  package's own axes), so `world = transform · C(upAxis → root up) · localTransform · content`.
- **Refs** are paths, `https://` URLs or `asset://` URIs. Relative paths — in `extends`, `package`, asset
  uris, `scene.root`, includes, HDRIs — resolve against the file that wrote them (a directory or a URL).
  A ref that's an asset id in the same layer stays an id; a ref that looks like a file path is a path.
- **Limits:** cycles are errors, depth ≤ 8, ≤ 64 layers, ≤ 1 MB per layer. Errors name the layer and field.
- **Where:** `flattenPackage` in `@rendahq/spec` (canonical) and `renda.layers` (Python port) share
  `spec/fixtures/layers`. CLIs and the local MCP flatten before upload (local layers + SSRF-safe https);
  the API flattens anything still layered with https (public addresses only, re-validated redirects,
  size + time caps) and the org's own `asset://` uploads, stores the flat package plus provenance on the
  job (`jobs.source`), and the worker only ever sees flat packages. `renda flatten pkg --provenance`,
  MCP `renda_flatten_package` and `POST /api/v1/packages/flatten` show the composed result.

The TS types/Zod live in `spec/src/index.ts`; the Python mirror (pydantic) lives in
`core/renda/spec.py`. Shared fixtures in `spec/examples/` are validated by both test suites.
`schema/renda.v1.json` is the *authoring* schema (accepts `extends`, package references and layers
without a `scene`); `parsePackage` / `load_package(dict)` accept only flat packages.

## 2. Scene IR + conversion layer (Hydra-lite)

Hydra's split — *scene delegates* produce a scene, *render delegates* consume it —
without Hydra's weight. In `core/renda/ir/`:

- `Stage`: flat list of `Mesh` (points/normals/uvs/indices as numpy, world transform,
  material id), `Material` (principled-BSDF subset: baseColor/metallic/roughness/
  emission/opacity/ior/transmission/normal map + texture refs), `Light`
  (point/spot/sun/area/dome), `Camera`, plus `bounds()`.
- **Loaders** (`ir/loaders/`), chosen by format sniffing (extension + magic bytes):

  | Format | ids | Loader | Notes |
  |---|---|---|---|
  | Blender | `blend` | Blender headless + `extract_blend.py` | bakes modifiers/instancing, principled BSDF → IR material |
  | USD | `usd` (`.usd/.usda/.usdc/.usdz`) | `usd-core` (pxr) | UsdPreviewSurface → IR material |
  | glTF | `gltf` (`.gltf/.glb`) | `pygltflib`/trimesh | metallic-roughness maps 1:1 to IR |
  | three.js JSON | `threejs` (`.json` with `metadata.type == "Object"`, format 4.x) | pure-python `threejs.py` | BufferGeometry (incl. base64/typed arrays), MeshStandard/Physical/Basic/Lambert/Phong materials, textures (data URIs or urls), lights, cameras, Group/Mesh hierarchy |
  | OBJ (+MTL) | `obj` | trimesh | MTL → IR material |
  | STL | `stl` | trimesh | ascii + binary, default material |
  | PLY | `ply` | trimesh | vertex colours (linear in the IR; glTF COLOR_0 on export) |
  | COLLADA | `dae` | trimesh + pycollada | |
  | 3MF | `3mf` | own XML reader (`threemf.py`) | basematerials + colorgroups → materials, components, build transforms |
  | FBX | `fbx` | Blender headless import → extract | no good pure-python FBX reader |
  | Alembic | `abc` | Blender headless import → extract | animated caches |
  | CAD | `step` (`.step/.stp`, ISO-10303-21 header), `iges` (`.iges/.igs`, column-73 section letters) | `ir/loaders/cad.py` via cascadio (OpenCASCADE → glTF), extra `renda[cad]` | STEP → metres with part/assembly colours; IGES → metres (OpenCASCADE always emits mm), no colours. Z-up. Tessellation: chord error 0.05% of bbox diagonal, 0.3 rad (`scene.tessellation` overrides, metres/radians), cached by content hash + tolerances. Planner treats CAD like STL product shots |
  | Gaussian splats | `splat` (3DGS `.ply` — detected by `f_dc_0/scale_0/rot_0/opacity` — `.splat`, `.spz` v2/v3; `.ksplat` threejs-only) | `ir/loaders/splat.py` | `Splats` primitive: means, log scales, quats, logit opacities, SH degree 0–3; robust (floater-free) bounds |

- **Splats** (`ir/splats.py`) live next to meshes on the Stage. `baked()` applies the object transform to
  positions, covariances and the view-dependent SH bands (rotated numerically, any degree).
- **On disk**: `.rir` = directory with `stage.json` + `meshes/*.npz` + `splats/*.npz` + `textures/`.
  Cacheable by content hash so a `.blend` is extracted once and rendered in any engine.
- **Adapters** (`engines/`): each engine declares `accepts` (native formats it loads
  directly) and `capabilities`. Routing: if the engine natively accepts the scene
  format use it (Blender + `.blend`), else convert via IR (Mitsuba + `.blend` → IR →
  Mitsuba dict with PLY meshes).

Engines: `blender-cycles`, `blender-eevee`, `blender-workbench` (natively load blend/usd/gltf/fbx/obj/stl/ply/abc via Blender importers, plus `.rir`), `mitsuba` (via IR), `threejs` (headless Chromium + three.js WebGL rasteriser — the cheapest preview engine, renders three.js JSON natively and anything else via IR→glTF; splats via Spark in the same scene, so meshes and splats occlude each other properly), `gsplat` (Gaussian splats only: nerfstudio gsplat on CUDA, a vectorised numpy reference rasteriser elsewhere). `filament` (Google Filament, real-time PBR, headless over EGL via the `filly` bindings; CPU containers with Mesa llvmpipe), `pbrt` (pbrt-v4 spectral path tracer: IR → pbrt scene with PLY meshes and an equal-area environment map; CPU). New engines are one adapter file each — see [docs/renderers.md](docs/renderers.md) for what else was evaluated and why it isn't in yet.

### Lens, motion blur and post effects across engines

`camera.lens` (focal length, f-stop → depth of field, focus, bokeh blades, anamorphic ratio), `render.antialias`,
`render.motionBlur` and `post` are engine-agnostic controls. `core/renda/effects.py` holds the support matrix
(native / emulated / approximated / unsupported per engine), which drives job warnings, `renda engines --json`
and the planner's cost factors:

- **Native** wherever the engine has the feature: Blender cameras (DoF, blades, ratio, pixel filter, motion
  blur — turntables become a keyframed pivot so the blur follows the true arc), Mitsuba's `thinlens` sensor
  and reconstruction filters, three.js tone mapping/exposure.
- **Emulated** around the engine otherwise: subframe accumulation in linear light for motion blur (Mitsuba
  splits its samples across subframes, so it costs the same; Workbench, three.js and gsplat pay
  `subframes` × frame time), and thin-lens depth of field from the depth buffer (three.js, gsplat) with a
  layered gather blur and polygonal/anamorphic bokeh (`core/renda/post.py`).
- **Post** (`renda.post`): white balance, bloom, contrast/saturation, lens distortion, chromatic aberration,
  vignette, sharpen and film grain run as one shared numpy stage on every finished frame, so they look the
  same on every engine. Exposure and tonemap (AgX/Filmic/ACES/Khronos Neutral/Standard) run inside the
  engine, before the image is display-referred.
- Lens maths (`core/renda/lens.py`) treats scene units as metres; `ResolvedCamera` carries focal length,
  focus distance and aperture so every engine focuses on the same plane.

### Ground plane / shadow catcher (`scene.ground`)

`core/renda/ground.py` places one plane for every engine — under the lowest point of the scene bounds
along the up axis (or at `offset`), sized from the scene diagonal — and engines add it *after* cameras
are resolved, so auto-framing never sees it. Solid and reflective floors are just a plane with a
principled material (4% F0 dielectric everywhere). The shadow catcher is native in Cycles
(`is_shadow_catcher`, shadows land in alpha on a transparent film) and three.js (`ShadowMaterial` fed by
two near-zero-intensity VSM shadow lights, so object lighting is unchanged). Eevee, Workbench and
Mitsuba emulate it differentially: render with a white matte plane, render the plane alone (same
lighting) and an object-coverage mask; shadow = 1 − L(with)/L(plane-only) above a small noise floor,
composited premultiplied, onto a backdrop render when the HDRI is visible. gsplat has no lighting, so
it approximates: a blurred top-down opacity map of the splats (weighted towards the floor for contact
shadows) is looked up through per-pixel ray/plane hits; reflective floors add the splats rendered from
the camera mirrored in the plane.

### Gaussian splats across engines

| Scene | auto picks | how |
|---|---|---|
| splats only | `gsplat` (GPU) | rasterise (EWA, SH colour) over the package background |
| splats + meshes, draft | `threejs` | Spark renders splats inside the three.js scene with the meshes |
| splats + meshes, standard/final | `blender-cycles` | hybrid (below) |

Hybrid (Blender, Mitsuba): the engine renders only the meshes on a transparent film from cameras Renda
computes (per-frame, passed explicitly so they match), plus a planar depth pass (Blender Z; Mitsuba's
radial depth converted). The splat layer is rasterised from the same cameras (gsplat on GPU, numpy on
CPU) with alpha-weighted expected depth, then composited in display space: background → splats behind
the mesh → mesh → splats in front. 3DGS colours are sRGB-fit, so splats are never tonemapped. Splats get
no engine lighting, shadows or reflections — the job warns about it. Splat-only scenes with an explicit
mesh engine skip the engine and rasterise directly. On Modal, GPU splat jobs run in a separate image
(CUDA devel base + torch + gsplat compiled at build + Blender + Mitsuba) so normal jobs keep slim images.

Engine adapter interface (`core/renda/engines/base.py`):

```python
class Engine(Protocol):
    name: str                       # "blender-cycles"
    accepts: set[str]               # native formats: {"blend","usd","gltf","fbx","obj","stl","ply","abc","rir"}
    capabilities: Capabilities      # gpu, video, passes, transparent, max_samples...
    def available(self) -> bool
    def render(self, job: RenderPlan, out_dir: Path, frames: list[int]) -> RenderResult
```

## 3. Public API (`web/`, `/api/v1`, JSON, `Authorization: Bearer rnd_live_…`)

| Method | Path | Body → Response |
|---|---|---|
| POST | `/auth/trial` | `{}` → `{apiKey, claimUrl, freeSecondsLeft, expiresAt, limits, next[]}` — anonymous claimable trial (a few free minutes, capped resolution, no `rush`, rate-limited per network, expires unless claimed; exact limits are deployment config and returned in `limits`/`expiresAt`) |
| POST | `/auth/magic/start` | `{email}` → `{ok}` — WorkOS Magic Auth emails a 6-digit code (creates user if new) |
| POST | `/auth/magic/verify` | `{email, code, keyName?}` → `{apiKey, user, org}` |
| POST | `/auth/device` | `{}` → `{deviceCode, userCode, verificationUri, interval, expiresIn}` |
| POST | `/auth/device/token` | `{deviceCode}` → `{apiKey}` or `428 {error:"authorization_pending"}` |
| GET | `/me` | → `{user, org, balance:{freeSecondsLeft}, billing:{hasPaymentMethod}}` |
| POST | `/uploads` | `{filename, size, sha256, contentType?}` → `{assetUri:"asset://<sha>", uploadUrl?, exists}` |
| POST | `/estimate` | `{package}` → `{hardware, estSeconds:[lo,hi], estCostUsd:[lo,hi], warnings}` |
| POST | `/jobs` | `{package, kind?:"scene"|"hyperframes", priority?:"economy"|"standard"|"rush", fast?: boolean (= rush), idempotencyKey?}` → `Job` |
| GET | `/jobs` / `/jobs/:id` | → `Job[]` / `Job` |
| POST | `/jobs/:id/cancel` | → `Job` |
| GET | `/usage` | `?from&to` → `{seconds:{cpu,gpu}, minutesBilled, costUsd, byDay[]}` |
| POST | `/billing/checkout` | → `{url}` Stripe Checkout (setup mode) to attach a card |
| GET | `/billing/portal` | → `{url}` |
| GET | `/pricing` | public → price table |
| POST | `/internal/worker/events` | worker → server, HMAC `x-renda-signature` |
| POST | `/webhooks/stripe` | Stripe |

```ts
type Job = {
  id: string; kind: "scene" | "hyperframes";
  status: "queued" | "planning" | "rendering" | "encoding" | "succeeded" | "failed" | "canceled";
  progress: number;            // 0..1
  engine?: string; hardware?: "cpu" | "gpu-l4" | "gpu-l40s";
  createdAt: string; startedAt?: string; finishedAt?: string;
  billedSeconds: number; costUsd: number; cached: boolean;
  warnings: string[]; error?: string;
  outputs: { name: string; url: string; contentType: string; bytes: number }[];  // presigned GET
};
```

Errors: `{error: {code, message, hint?}}` — `hint` always tells an agent what to do
next (e.g. `"code":"payment_required","hint":"POST /api/v1/billing/checkout and send the url to the user"`).

### Worker contract

Server → Modal: `POST {MODAL_SUBMIT_URL}` with `Authorization: Bearer {RENDA_WORKER_TOKEN}`,
body `{jobId, kind, package, priority, inputs: {[assetUri]: presignedGetUrl}, outputPrefix, uploadUrls?: never, callbackUrl}`.
The worker writes outputs to R2 directly (it holds R2 credentials) under `outputs/{jobId}/`.

Worker → server events (`POST callbackUrl`, body signed with HMAC-SHA256 of raw body
using `RENDA_WORKER_SECRET`, header `x-renda-signature: sha256=<hex>`):

```jsonc
{ "jobId": "...", "type": "status", "status": "rendering", "progress": 0.4, "engine": "blender-cycles", "hardware": "gpu-l4" }
{ "jobId": "...", "type": "usage", "hardware": "gpu-l4", "seconds": 37.2, "chunk": 3 }      // per finished container chunk
{ "jobId": "...", "type": "warning", "message": "mitsuba: passes [depth] unsupported, skipped" }
{ "jobId": "...", "type": "done", "outputs": [{ "key": "outputs/<id>/frame_0001.png", "contentType": "image/png", "bytes": 123 }], "cached": false }
{ "jobId": "...", "type": "failed", "error": "..." }
```

User-facing image/video outputs also carry gallery metadata the worker computes right after upload:
`width`, `height`, `thumbKey` (`outputs/<id>/thumbs/<name>.webp`, ≤480px; a poster for video) and
`placeholder` (a ~16px blurred webp data URI). The dashboard gallery and `GET /api/v1/outputs` read
these from `jobs.outputs`; `worker/scripts/backfill_thumbs.py` fills them in for older jobs.

## 4. Billing

- Meter = container-seconds actually spent on a job (scene load + render + encode),
  measured in the worker per chunk, by hardware class. Queue time and cold start
  before our code runs are **not** billed.
- Price per render-minute by class (defaults, `web/src/lib/pricing.ts`):
  `cpu` $0.015 · `gpu-l4` $0.035 · `gpu-l40s` $0.075.
- Free tier: 30 render-minutes for new orgs; then card required (Stripe Checkout setup mode).
- Stripe Billing Meters: one meter per hardware class (`render_seconds_cpu`, `render_seconds_gpu_l4`, `render_seconds_gpu_l40s`), each with its own metered price; usage is reported on each `usage` event past the free tier (idempotent by `jobId:chunk`).
- Cached results (identical package + asset hashes + engine version) cost $0.

## 5. Cost optimisation (the worker's job)

1. **Right-size hardware**: planner estimates cost per class from engine/res/samples/frames and picks the cheapest that meets the priority's latency target. Stills with Mitsuba/LLVM or Workbench go to CPU; Cycles/Eevee go to L4; only big `rush` jobs get L40S.
   **Fast mode** (`rush`, alias `fast: true` / `--fast`) trades a little more money for wall time: stills ≥ ~4 MP in Cycles/Eevee/Mitsuba split into padded tiles (32 px overlap so per-tile denoising leaves no seams; same camera, samples and seed) rendered in parallel and stitched losslessly by the coordinator; splat bakes fan views out and refine on an L40S.
2. **Result cache**: `sha256(canonical package + asset hashes + engine versions)` → previous outputs, billed $0.
3. **Asset cache**: content-addressed Modal Volume; assets downloaded once per region. `.blend → .rir` extraction cached by hash.
4. **Cold start**: slim pre-baked images, Modal memory snapshots, `scaledown_window` tuned, Cycles/OptiX kernel cache on a Volume.
5. **Chunked fan-out**: frames split into chunks sized so scene-load overhead < 10% of chunk time; `.map()` across containers — same cost, much faster wall time.
6. **Fewer samples, same image**: adaptive sampling + OIDN/OptiX denoise (Mitsuba uses OIDN via `pyoidn` wherever OptiX isn't available, with albedo/normal guides, a box pixel filter and firefly clamping); quality presets tuned per engine; `draft` preview encouraged before `final` (skills teach agents this).
7. **Economy priority**: longer deadline → allows cheaper class + max container reuse.
8. **R2 storage**: zero egress fees; outputs expire after 30 days by default.

## 6. Video

- **3D animation / turntable**: frames rendered in chunks → `ffmpeg` encode (mp4 h264/h265, webm vp9, gif, prores) on CPU.
- **HyperFrames** (`kind:"hyperframes"`): HTML-composition videos rendered with the HyperFrames CLI in a Chromium image. A composition may reference Renda outputs (e.g. a turntable clip) — submit the 3D job first, pass its output URL into the composition.

## 6b. Splat bakes (mesh scene → Gaussian splat)

`output.format: "splat"` (CLI `renda splat`, MCP `renda_bake_splat`) turns any mesh scene into a 3D
Gaussian splat (`splatFormat` spz by default, or ply/splat). Code: `core/renda/splatbake/`. Any mesh engine
renders the training views (Cycles, Eevee, Workbench, Mitsuba, three.js) with the full package (HDRI,
lights, material + texture overrides, layering); lens/blur/screen-space post are dropped for the views.

1. **Mesh → Gaussians** (Mesh2Splat-style, EA SEED 2025): area-weighted surface samples, flat discs
   oriented along the surface, base colour from textures. Preset `fast` stops here (CPU, seconds, albedo only).
2. **Training views**: a far Fibonacci shell framing the bounds (~55%) plus close-ups (~45%) placed by
   surface coverage: farthest-point sampling over surface samples (position + normal), weighted towards
   detailed regions (normals vary within a voxel) and glossy materials, each looking at the surface along
   its normal. `coverage` auto/sphere/hemisphere set the elevation band, `orbit` is one ring, and
   `splatBake.cameras` replaces the rig with explicit cameras. The rig is deterministic, so every
   container agrees on poses; engines receive it via `ir.camera.override_cameras`. `engine: auto` picks
   Eevee for diffuse scenes and Cycles when ≥3% of the surface is glossy/glass/emissive (decided from the
   loaded materials; planned and estimated as Cycles). Views are draft quality + denoised at exactly the
   training resolution.
   **Where they render**: small jobs render the views inside the training container (one GPU, one scene
   load, no Volume round trip). Above ~240 s of view rendering (standard), past ~20 s (rush), for preset
   `high` or with `fanOut: true` they fan out to parallel chunks on the assets Volume
   (`bakes/<job>/views/`), and the trainer is spawned up front: it starts as soon as 16 views have landed
   and keeps adding views until the orchestrator writes `.complete`.
3. **Exact COLMAP model**: PINHOLE intrinsics + OpenCV-convention poses straight from the rig (no SfM),
   points3D from the surface samples; returned with the views when `dataset: true`.
4. **Refinement** (`splatbake/train.py`, splat image, L4 standard / L40S high): positions start exact
   (from the mesh) and stay frozen for the first 1k steps while colour/opacity/shape fit; then gsplat MCMC
   densification capped at `maxGaussians`, antialiased rasterisation, half resolution for the first 15% of
   steps, 0.8·L1 + 0.2·(1−SSIM) on random backgrounds + alpha loss, depth and normal losses against the
   frozen mesh-surface Gaussians, a surfel term keeping Gaussians flat on the surface, progressive SH
   (glossy scenes: degree 2, or 3 for `high`; otherwise 1), and early stop on the held-out PSNR plateau (`steps` is an
   upper bound: 6k standard, 15k high). Gaussians below 1% opacity are pruned before export. Every 8th
   view is held out; PSNR/SSIM and a per-phase timing line are reported as job warnings.

Worker flow: orchestrate → either `train_splat_{l4,l40s}` alone (views rendered inside it), or the trainer
spawned up front + view chunks writing to the Volume (`local_dir`) → `.complete` marker → splat (+ dataset
zip) uploaded to R2 → views deleted. Usage chunk `train` covers views + training in single-container mode.

Measured on Modal (2026-10-20, isolated app, production code path):

| Bake | Billed | Cost | Wall | Holdout PSNR / SSIM | .spz |
|---|---|---|---|---|---|
| table, standard — before (5 view chunks + 7k steps, SH 2) | 315 s | $0.18 | ~6 min | 35.2 / 0.982 | 10.3 MB |
| table, standard — now (views in trainer, Cycles auto, SH 2, 6k steps) | 237 s | $0.136 | 4.1 min | 36.9 / 0.986 | 8.5 MB |
| table, Eevee views (forced) | 251 s | $0.125 | — | 33.6 / 0.979 | 7.8 MB |
| table, Mitsuba views | 344 s | $0.177 | 6.3 min | 35.5 / 0.983 | 11.3 MB |
| Canon camera, standard — now | 214 s | $0.124 | 4.0 min | 28.1 / 0.941 | 7.8 MB |
| Canon camera, high (200 views streamed into an L40S, 14.5k steps, SH 3) | 939 s | $0.99 | 12.9 min | 32.2 / 0.969 | 50 MB |

Holdout PSNR now includes the coverage rig's close-ups (harder views than the old far shell), so it is not
directly comparable across rigs; the before camera bake scored 30.1 dB on the old rig's holdout set.

## 7. Agent-first onboarding

Patterns borrowed from Stripe sandboxes, WorkOS unclaimed environments, Neon `init`, Convex anonymous
deployments and treg.to's llms.txt setup script:

- **One prompt**: the user pastes `set up renda — https://renda.vochsel.com/llms.txt`; that file is an
  imperative setup script (npx and uvx paths, trial → render → claim, MCP, pricing, errors). `/auth.md` describes
  every credential flow; `/.well-known/mcp/server.json` + `mcp/server.json` describe the MCP server.
- **One command**: `npx @rendahq/cli init` (or `uvx renda trial && uvx renda mcp add`) → trial key if none,
  MCP config for Claude Code / Cursor / Codex, skills, AGENTS.md note.
- **Anonymous, claimable trial**: `POST /api/v1/auth/trial` (no auth) creates an unclaimed org + key with
  `TRIAL_SECONDS`. The human opens `claimUrl` (`/claim?token=…`, WorkOS sign-in) to adopt the org; the free tier
  tops up to `FREE_SECONDS`. Unclaimed trials expire at `expiresAt` (enforced in API-key auth). The MCP
  `renda_render` and both CLIs start a trial automatically when no key is configured and surface the claim link.
- **Full accounts without a browser**: `renda signup <email>` → human reads the 6-digit code → `renda verify <code>`.
  Humans: `renda login` (device flow).
- **Agent-readable output**: JSON `{status, reason, message, hint, next[]}` when piped, gh-style exit codes
  (0 ok, 1 error, 2 usage, 3 auth, 4 payment), API keys never echoed into output.
- **Payment**: `payment_required` (exit 4) → `renda billing` returns a Stripe Checkout URL for the human.
- **Distribution**: npm `@rendahq/cli` + `@rendahq/mcp` (+ `/remote`, `/stdio` entries), PyPI `renda`, skills.sh
  (`npx skills add vochsel/renda`), Claude Code marketplace (`/plugin marketplace add vochsel/renda`), MCP registry.
