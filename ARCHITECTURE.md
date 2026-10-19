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
    "materialOverrides": { "*": { "baseColor": "#d0d0d0", "roughness": 0.4 } }
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

The TS types/Zod live in `spec/src/index.ts`; the Python mirror (pydantic) lives in
`core/renda/spec.py`. Shared fixtures in `spec/examples/` are validated by both test suites.

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
  | PLY | `ply` | trimesh | vertex colours |
  | COLLADA | `dae` | trimesh + pycollada | |
  | 3MF | `3mf` | trimesh | |
  | FBX | `fbx` | Blender headless import → extract | no good pure-python FBX reader |
  | Alembic | `abc` | Blender headless import → extract | animated caches |
  | Gaussian splats | `splat` (3DGS `.ply` — detected by `f_dc_0/scale_0/rot_0/opacity` — `.splat`, `.spz` v2/v3; `.ksplat` threejs-only) | `ir/loaders/splat.py` | `Splats` primitive: means, log scales, quats, logit opacities, SH degree 0–3; robust (floater-free) bounds |

- **Splats** (`ir/splats.py`) live next to meshes on the Stage. `baked()` applies the object transform to
  positions, covariances and the view-dependent SH bands (rotated numerically, any degree).
- **On disk**: `.rir` = directory with `stage.json` + `meshes/*.npz` + `splats/*.npz` + `textures/`.
  Cacheable by content hash so a `.blend` is extracted once and rendered in any engine.
- **Adapters** (`engines/`): each engine declares `accepts` (native formats it loads
  directly) and `capabilities`. Routing: if the engine natively accepts the scene
  format use it (Blender + `.blend`), else convert via IR (Mitsuba + `.blend` → IR →
  Mitsuba dict with PLY meshes).

Engines: `blender-cycles`, `blender-eevee`, `blender-workbench` (natively load blend/usd/gltf/fbx/obj/stl/ply/abc via Blender importers, plus `.rir`), `mitsuba` (via IR), `threejs` (headless Chromium + three.js WebGL rasteriser — the cheapest preview engine, renders three.js JSON natively and anything else via IR→glTF; splats via Spark in the same scene, so meshes and splats occlude each other properly), `gsplat` (Gaussian splats only: nerfstudio gsplat on CUDA, a vectorised numpy reference rasteriser elsewhere). New engines (pbrt-v4, LuxCore, Arnold…) are one adapter file each.

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
| POST | `/jobs` | `{package, kind?:"scene"|"hyperframes", priority?:"economy"|"standard"|"rush", idempotencyKey?}` → `Job` |
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
2. **Result cache**: `sha256(canonical package + asset hashes + engine versions)` → previous outputs, billed $0.
3. **Asset cache**: content-addressed Modal Volume; assets downloaded once per region. `.blend → .rir` extraction cached by hash.
4. **Cold start**: slim pre-baked images, Modal memory snapshots, `scaledown_window` tuned, Cycles/OptiX kernel cache on a Volume.
5. **Chunked fan-out**: frames split into chunks sized so scene-load overhead < 10% of chunk time; `.map()` across containers — same cost, much faster wall time.
6. **Fewer samples, same image**: adaptive sampling + OIDN/OptiX denoise; quality presets tuned per engine; `draft` preview encouraged before `final` (skills teach agents this).
7. **Economy priority**: longer deadline → allows cheaper class + max container reuse.
8. **R2 storage**: zero egress fees; outputs expire after 30 days by default.

## 6. Video

- **3D animation / turntable**: frames rendered in chunks → `ffmpeg` encode (mp4 h264/h265, webm vp9, gif, prores) on CPU.
- **HyperFrames** (`kind:"hyperframes"`): HTML-composition videos rendered with the HyperFrames CLI in a Chromium image. A composition may reference Renda outputs (e.g. a turntable clip) — submit the 3D job first, pass its output URL into the composition.

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
