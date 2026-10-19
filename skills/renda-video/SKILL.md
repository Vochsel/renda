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
- Optional: `composition` (if the project has several), `width`, `height`, `fps`,
  `format` (`mp4|webm|mov|gif`), `quality` (`draft|standard|final`).
- Validate locally first if the HyperFrames CLI is installed. Cloud renders fail on the same
  errors, but you pay for them.
- Media referenced by the composition must be inside the project dir or at public URLs.

## 3D inside a HyperFrames video

1. Render the 3D part first as its own job: a turntable `webm` with
   `output.transparent: true` (to composite over a background) or an `mp4`.
2. Take the output URL from the finished job, or `renda_download` it into the HyperFrames
   project folder (better: presigned URLs expire, while project files are zipped with the project).
3. Reference it as a video clip in the composition, timed with the usual `data-*` attributes.
4. `renda_render_video { projectDir }`.

Match fps and resolution between the 3D job and the composition to avoid resampling.
