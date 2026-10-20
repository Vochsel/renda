# renda-worker

The Modal app that renders Renda jobs. The web app submits jobs; the worker plans, renders in
parallel chunks on the cheapest hardware that fits, encodes video, writes outputs to R2 and
reports signed status/usage events back. See `../ARCHITECTURE.md` §3–6 for the full contract.

```
POST renda-submit ──► orchestrate (0.25 core, blender image)
                        │ fetch assets → content-addressed Volume `renda-assets`
                        │ plan (renda.planner) → result cache hit? → copy outputs, done(cached), $0
                        │ .map(chunks) ──► render_chunk_cpu | _blender_cpu | _l4 | _l40s | _threejs | _splat_l4 | _splat_l40s
                        │                    each bills its own wall seconds (usage event per chunk)
                        │ video? ──► encode_video (4 cores, ffmpeg)
                        └ done(outputs)  →  outputs/<jobId>/... in R2
POST renda-submit (kind=hyperframes) ──► render_hyperframes (Chromium + hyperframes CLI)
POST renda-cancel ──► cancel flag (modal.Dict) + FunctionCall.cancel()
```

## Layout

| File | What |
|---|---|
| `renda_worker/app.py` | Modal images, functions, endpoints. Wiring only. |
| `renda_worker/pipeline.py` | Scene / chunk / encode / HyperFrames pipelines with injected deps (unit-tested). |
| `renda_worker/dispatch.py` | Which runner renders which frames, where outputs go, progress budget. |
| `renda_worker/hardware.py` | Hardware classes → Modal resource shapes. |
| `renda_worker/events.py` | HMAC-signed events with retries and idempotency keys. |
| `renda_worker/assets.py` | Content-addressed asset cache with hash verification. |
| `renda_worker/storage.py` | R2 (S3 API), in-memory and directory storage. |
| `renda_worker/hyperframes.py` | `hyperframes render` command building. |
| `renda_worker/core_api.py` | The single seam onto the `renda` core package. |

## Images

| Image | Contents | Used by |
|---|---|---|
| `blender` | Blender 4.5.14 LTS, `renda[mitsuba,usd]`, ffmpeg, X11/EGL libs | orchestrate, `render_chunk_blender_cpu/l4/l40s` |
| `cpu` | `renda[mitsuba,usd]` (Mitsuba LLVM), ffmpeg | `render_chunk_cpu`, `encode_video` |
| `threejs` | Node 22, `renda[threejs]`, Playwright Chromium, three.js r180 + Spark 2.3 vendored at build | `render_chunk_threejs` (meshes and Gaussian splats) |
| `splat` | CUDA 12.4 devel base, torch 2.4.1 (cu124), gsplat 1.5.3 compiled for sm_89 at build, + everything in `blender` | `render_chunk_splat_l4/l40s`: `engine: gsplat`, and Blender/Mitsuba jobs with splats on GPU |
| `hyperframes` | Node 22, `hyperframes@0.8.173`, pinned Chrome, fonts, ffmpeg | `render_hyperframes` |
| `api` | fastapi + pydantic only | `submit`, `cancel` |

Splat routing (`dispatch.runner_for`): `gsplat` jobs and GPU jobs whose plan has `splats: true` go to the
`splat` image; `threejs` splat jobs stay in the Chromium image (Spark); CPU splat jobs use core's numpy
rasteriser in the normal CPU images. The splat image is several GB bigger than `blender`, which is why
only splat jobs pay its cold start. Torch/CUDA are never imported at snapshot time (Dr.Jit taught us
why), and `TORCH_EXTENSIONS_DIR` points at the kernel-cache Volume in case anything ever JIT-builds.
The first deploy compiles gsplat (~10–20 min); later deploys reuse the cached layer.

Core (`../core`) is baked into images with `add_local_dir(copy=True)` + `pip install`, so a core
change rebuilds only that layer.

## Job kinds

### `scene`
`package` is a Renda package (`../spec`). Every `asset://<sha256>` in it must appear in `inputs`
with a presigned GET URL.

### `hyperframes`
HTML-composition video rendered with the HyperFrames CLI. Billed as `cpu` seconds.

```jsonc
{
  "project": "asset://<sha256 of a .zip of the project dir>",  // or "https://…/project.zip"
  "name": "promo",                // optional, output file stem (default "video")
  "composition": "compositions/intro.html",  // optional, default index.html
  "format": "mp4",                // mp4 | webm | mov | gif   (webm/mov keep transparency)
  "quality": "looks",             // draft | looks | delivery
  "fps": 30,                      // 1–240 (gif capped at 30); default: the composition's data-fps
  "width": 3840, "height": 2160,  // optional; only HyperFrames supersampling presets apply:
                                  // 1920x1080 1080x1920 3840x2160 2160x3840 1080x1080 2160x2160
                                  // anything else → warning, composition's own size is used
  "durationSec": 6,               // optional, estimate hint only
  "variables": {                  // optional, passed as --variables
    "title": "Hi",
    "clipUrl": "asset://<sha256>" // asset:// or https://…/x.mp4 media values are downloaded into
  }                               // <project>/renda-inputs/ and replaced with that relative path
}
```

The zip may contain the project at its root or inside one top-level folder. Paths escaping
the archive are rejected. To put a 3D render in a composition, bind a `<video data-var-src="clipUrl">`
slot to a variable and pass the scene job's output (the CLIs/MCP accept `job:<id>` and resolve it).
The gallery thumbnail is taken ~40% into the video (frame 0 is usually a blank pre-roll).

## Endpoints

Both require `Authorization: Bearer $RENDA_WORKER_TOKEN`.

- `POST renda-submit` — body `{jobId, kind, package, priority, inputs, outputPrefix?, callbackUrl}` → `{accepted, callId}`; `422` with pydantic errors on a bad body.
- `POST renda-cancel` — body `{jobId}` → `{canceled}`. The server owns the job's `canceled` status; chunks already running still report the seconds they used.

Events go to `callbackUrl`, body signed `x-renda-signature: sha256=<hmac hex>` with
`RENDA_WORKER_SECRET`. Money-bearing events carry `idempotency-key`: `usage` → `<jobId>:<chunk>`
(chunk is an int, or `orchestrate` / `encode` / `hyperframes`), `done`/`failed` → `<jobId>:done|failed`.
The server must dedupe on it: Modal retries a preempted chunk and the retry reuses the key.

## Cost controls (what's actually implemented)

- **Right-sized runners** — `dispatch.runner_for`: Mitsuba on non-Blender formats goes to the slim
  `cpu` image; anything needing Blender to the Blender image; GPU classes only when the planner says so;
  three.js always on CPU (SwiftShader) rather than idling a GPU.
- **Cheap coordinator** — orchestrate runs on 0.25 core / 1 GiB and bills users only for its active
  phases, never for time spent waiting on chunks.
- **Result cache** — R2 manifest at `cache/v1/<cache_key>.json`; a hit is a server-side copy and $0.
- **Asset cache** — content-addressed Volume; each asset is downloaded once, hash-verified.
- **Cold starts** — `enable_memory_snapshot=True` on all Python render/encode/orchestrate
  functions with heavy imports preloaded at module scope; slim `api` image for endpoints.
- **Idle containers** — idle time inside the scaledown window is billed to us, and at low traffic the next job rarely
  lands in it, so windows are short: 15 s (L4) / 10 s (L40S) / 20 s (CPU, three.js) / 15 s (encode) / 60 s
  (orchestrate). Nothing is kept warm by default. Deploy-time overrides: `RENDA_SCALEDOWN_<CLASS>`, `RENDA_MIN_<CLASS>`,
  `RENDA_BUFFER_<CLASS>` with CLASS ∈ CPU, GPU_L4, GPU_L40S, ORCHESTRATE, ENCODE, HYPERFRAMES, THREEJS
  (e.g. `RENDA_BUFFER_GPU_L4=1 ./deploy.sh` for a launch window).
- **GPU capacity tails** — waiting for a free L4 took 25–166 s in testing, so the Blender/Mitsuba L4 runner may fall
  back to an A10G (`RENDA_L4_FALLBACK_GPUS`, default `A10G`; empty disables). Splat runners stay on L4/L40S: gsplat is
  compiled for sm_89 only.
- **Draft previews on CPU** — `engine: auto` + `quality: draft` renders mesh formats with three.js on CPU (≈2.7×
  cheaper than Workbench/Eevee on an L4 and no GPU wait); `.blend/.fbx/.abc` still draft on Eevee.
- **Measuring** — `scripts/coldstart_probe.py` (cold start per image config) and `scripts/bench_wall.py` (end-to-end
  worker wall time) run against isolated apps only.
- **OptiX kernel cache** — `XDG_CACHE_HOME` on the `renda-kernel-cache` Volume for GPU functions.
- **Chunking** — chunk sizes come from `renda.planner`; `.map(order_outputs=False)` for wall-clock speed at equal cost.
- **Preemptible only** — non-preemptible is 3x on Modal; chunks are idempotent and retried
  (`Retries(max_retries=2)`), orchestrate isn't retried (it reports its own failure).
- **Encode on CPU** — ffmpeg runs in a separate 4-core CPU function, never on a GPU container.

## Develop

```bash
cd worker
uv sync                       # deps + pytest into .venv
.venv/bin/pytest

# In-process run with the real core, no Modal/R2 (needs renda core + engines installed locally):
uv pip install -e '../core[mitsuba]'
.venv/bin/python scripts/submit_local.py ../spec/examples/minimal.renda.json --local --out ./renders
```

### Against Modal

```bash
modal secret create renda-worker RENDA_WORKER_TOKEN=... RENDA_WORKER_SECRET=...
modal secret create renda-r2 R2_ACCOUNT_ID=... R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=renda

modal serve renda_worker/app.py        # dev URLs: https://<ws>--renda-submit-dev.modal.run
./deploy.sh                                # prod URLs: https://<ws>--renda-submit.modal.run, …--renda-cancel.modal.run
```

Isolated copy for development (own endpoints `renda-submit-<name>`, volumes, dicts and secrets
`renda-worker-<name>` / `renda-r2-<name>`; production untouched): create those two secrets, then
`RENDA_APP_NAME=renda-worker-<name> ./deploy.sh`, and `modal app stop renda-worker-<name>` when done.
The hosted preview stack runs as `renda-worker-dev` on its own bucket.

Splat bakes (`output.format: "splat"`): view chunks write PNGs to the assets Volume
(`bakes/<job>/views/`), then `train_splat_l4` / `train_splat_l40s` (splat image) refine with gsplat and
upload the splat; usage is reported under chunk `train`.

Set `MODAL_SUBMIT_URL` / `MODAL_CANCEL_URL` and the same `RENDA_WORKER_TOKEN` / `RENDA_WORKER_SECRET`
in the web app. To see events locally, tunnel the web dev server (e.g. `cloudflared tunnel --url
http://localhost:3000`) and pass its URL as `--callback` to `scripts/submit_local.py`.

R2 lifecycle rules to add on the bucket: `work/` expire after 1 day, `outputs/` after 30 days,
`cache/` after 30 days.
