---
name: renda-video
description: Render videos with Renda. Covers 3D turntables and animations from scene files (mp4/webm/gif/mov via Blender, Mitsuba, three.js or gsplat — Gaussian splat captures spin too), HyperFrames HTML compositions rendered in the cloud, and combining a 3D render into a HyperFrames video. Use whenever the user wants a turntable, spin, orbit, 360 view, product video, animated render, Alembic/animation playback, a GIF of a model, or wants to render a HyperFrames project in the cloud or put a 3D model into a motion-graphics video.
---

# Video with Renda

There are two kinds of video job:

| | 3D video (`kind: "scene"`) | HyperFrames (`kind: "hyperframes"`) |
|---|---|---|
| Input | scene file + package with `animation` | HyperFrames project (HTML composition) |
| Tool | `renda_render` | `renda_render_video` |
| Rendered by | Blender / Mitsuba / three.js / gsplat, frames in parallel | headless Chromium |
| Use for | turntables, animated scenes, camera moves | titles, motion graphics, promos, captions, layouts |

## 3D turntables and animations

Fastest path: `renda_init_package {file, turntable: true, seconds: 4}` returns a valid package.
The pieces that make it a video:

```json
"animation": { "frames": [1, 120], "fps": 30, "turntable": { "revolutions": 1 } },
"output": { "format": "mp4", "video": { "codec": "h264", "crf": 20 } }
```

- `frames` is inclusive: 120 frames at 30 fps is 4 s. Cost scales with frame count, so
  keep turntables at 3–6 s unless asked otherwise.
- `turntable` orbits the camera around the bounds (works with `camera.mode: "auto"` or
  `lookat`). Without `turntable`, the file's own animation plays (Blender actions, Alembic
  caches, glTF animations, USD time samples) over `frames`.
- Formats: `mp4` (h264 default, `h265` smaller), `webm` (vp9, supports
  `transparent: true`), `mov` (`prores`, alpha, for editors), `gif` (short loops only; it gets large).
- `step: 2` renders every other frame for cheap previews.
- `keepFrames: true` also returns the PNG frames.

**Check the look before rendering every frame.** Render the package once as a still
(remove `animation`, set `output.format: "png"`, `quality: "draft"`), then fix framing and
lighting, then `renda_estimate` the full video, then render. A turntable that's framed
wrong costs 120× the still to discover.

Engines for video: `blender-eevee` is the usual sweet spot (GPU, fast, good PBR).
`blender-cycles` only for photoreal finals (estimate first). `threejs` is cheapest and
matches web viewers. Frames are split into chunks across machines automatically; this
improves wall time at no extra cost.

## HyperFrames compositions

HyperFrames builds videos from HTML compositions. Author the project with the
`hyperframes` skills if they're available (they cover composition structure, timing and
animation), then render on Renda instead of locally when the user wants cloud rendering,
the local machine is slow, or there's no local Chromium/ffmpeg.

```
renda_render_video { projectDir: "./my-video", format: "mp4" }
```

- `projectDir` is zipped (skipping `node_modules`, `.git`, `renders`) and uploaded. With
  remote MCP pass `projectUri` (https:// or asset:// zip) instead.
- Optional: `variables` (override the composition's `data-composition-variables`),
  `composition` (if the project has several), `name`, `fps`, `format` (`mp4|webm|mov|gif`),
  `quality` (`draft` while iterating | `looks` default | `delivery` final; `standard`/`final` are
  accepted as aliases), `durationSec` (better estimate). `width`/`height` only apply HyperFrames'
  supersampling presets with the composition's aspect (1920x1080, 3840x2160, 1080x1920, 1080x1080…).
- Validate locally first if the HyperFrames CLI is installed (`npx hyperframes lint`). Cloud renders
  fail on the same errors, but you pay for them.
- Media referenced by the composition must be inside the project dir, at public URLs, or passed
  through a variable (below). Billed as CPU time.

CLI: `renda video ./my-video --var title="Launch" --quality draft` (Node: `npx @rendahq/cli video …`,
Python: `uvx renda video …`). `--var` repeats; `--vars '{json}'` sets many at once.

## 3D inside a HyperFrames video

Give the composition a video slot bound to a variable, e.g.
`<video class="clip" data-start="0.5" data-duration="5.5" data-track-index="3" src="assets/placeholder.mp4" data-var-src="clipUrl" muted playsinline>`
with `clipUrl` declared in `data-composition-variables`. Then fill the variable — Renda fetches the
clip into the project before rendering, so presigned URLs and uploads both work:

| `clipUrl` value | What happens |
|---|---|
| `job:<id>` | Uses that finished job's video output (chain any scene render, any engine/settings). |
| `./robot.glb` (any 3D file: .blend, .usdz, .step, splats…) | Local mode only: uploads it, renders a 4 s 1080² draft turntable on a dark background, waits, drops the mp4 in. One command. |
| `./clip.mp4` / `./logo.png` | Local mode only: uploaded as `asset://…`. |
| `https://…/clip.mp4` | Downloaded by the worker (URL path must end in a media extension). |
| `asset://<sha256>` | An existing upload. |

```
renda video ./title-card --var clipUrl=./robot.glb --var title="Robot v2"          # one command
renda render robot.glb --format mp4 --turntable --res 1080x1080 --cloud --json      # or control the 3D job…
renda video ./title-card --var clipUrl=job:<id-from-above>                          # …and chain it
renda_render_video { projectDir: "./title-card", variables: { clipUrl: "job:job_abc", title: "Robot v2" } }
```

For transparency over a background, render the 3D job as `webm` with `output.transparent: true`.
Match fps between the 3D job and the composition to avoid resampling. A working example lives in
`testdata/hyperframes/title-card/` (variables: `title`, `subtitle`, `clipUrl`, `accent`).
