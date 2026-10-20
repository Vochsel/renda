# Renda package field reference (v1)

Defaults shown after `=`. The canonical source is `@rendahq/spec` (Zod) and the generated
JSON Schema at `renda://schema/v1`.

## Top level
- `$schema` string, optional: `"https://renda.dev/schema/v1.json"` for editor completion
- `version` `1` = 1
- `name` string ≤ 200, optional: shown in the dashboard
- `assets` `{ [id]: Asset }` = `{}`: ids match `[A-Za-z0-9_.-]+`
- `scene` **required**
- `camera` = `{ mode: "auto" }`
- `environment` = `{ hdri: "studio", intensity: 1, rotationDeg: 0, background: "hdri" }`
- `lights` `Light[]` = `[]`
- `render` (see below)
- `animation`, optional: required for video outputs
- `output` = `{ format: "png" }`

## Asset
- `uri` string: `./relative`, `/absolute` (local, auto-uploaded), `https://…`, `asset://<sha256>`
- `format`, optional: `blend usd gltf threejs fbx obj stl ply dae 3mf abc splat rir hdr exr png jpg webp mp4 other` (`splat` = Gaussian splats: 3DGS .ply, .splat, .spz, .ksplat)
- `sha256`, optional: 64 hex chars

## scene
- `root` string: asset id or uri
- `include` `[{ asset, transform? }]` = `[]`
- `upAxis` `"Y" | "Z" | "-Y"`, optional: defaults to the format's convention (`"-Y"` = Y down, COLMAP/OpenCV, the default for splat `.ply`/`.splat`)
- `unitScale` number > 0 = 1: multiply scene units to get metres
- `materialOverrides` `{ [pattern]: MaterialOverride }` = `{}`: keys are an exact material name, a
  case-insensitive glob (`"wood*"`), `/regex/flags` (`"/^wood(_\\d+)?$/i"`) or `"*"`. `"*"` applies first, then
  the rest in order (later wins).
- `textureOverrides` `TextureOverride[]` = `[]`: replace material textures with any image or video (below)

### Name patterns
Used by `materialOverrides` keys and `textureOverrides[].match`: exact name, glob (`*` `?` `[ab]`,
case-insensitive), or `/regex/flags` (flags `i m s x`). Material, object and texture names come from the file
(glTF/USD/FBX/.blend object + material names; OBJ `o`/`usemtl`; three.js object/material names; COLLADA
material names). A rule that matches nothing adds a job warning listing what was there.

### TextureOverride
- `source` asset id or uri, **required**: any image — png jpg webp avif heic tiff (16-bit) bmp tga gif exr hdr
  svg (rasterised at 2048 px) psd (flattened) — or video — mp4 mov webm mkv avi, animated gif/png/webp.
  Converted automatically; extensionless uploads are sniffed.
- `match` = `{}` (every material): `material`, `object` (scopes to objects whose name matches; shared
  materials are copied so other objects keep the original), `texture` (file name of a texture the material
  already uses, e.g. `"*_diffuse.png"`), `slot` `baseColor | normal | roughness | metallic | emission | opacity`
  (default: the slot `texture` matched, else `baseColor`)
- `uv` = `{ scale: 1, offset: [0,0], rotationDeg: 0 }`: `scale` number or [u,v]; applied as
  translate · rotate · scale (KHR_texture_transform order)
- `video` = `{ loop: true, startSec: 0, speed: 1 }`; `frame` (stills: show this source frame). Render frame
  `f` shows source time `startSec + (f − animation.frames[0]) / fps × speed`, looped or held on the last frame.
- `colorSpace` `auto | srgb | linear` = `auto` (sRGB for baseColor/emission, linear for data maps)
- `strength` ≥ 0, optional: normal-map strength or emission strength
- Meshes with no UVs (STL, scans) get box-projected UVs automatically. Later overrides win. Gaussian splats
  have no materials: overrides apply to meshes only.

### MaterialOverride (all optional)
`object` (pattern: only materials used by matching objects), `baseColor` (#rrggbb | #rrggbbaa | [r,g,b] linear
0–1), `metallic` 0–1, `roughness` 0–1, `emission` colour, `emissionStrength` ≥ 0, `opacity` 0–1,
`transmission` 0–1, `ior` ≥ 1.

### Transform
- `translate` [x,y,z] = [0,0,0]
- `rotateDeg` [x,y,z] = [0,0,0]: XYZ Euler in degrees
- `scale` number | [x,y,z] = 1

## camera (discriminated by `mode`)
- `auto`: `fovDeg` = 35, `elevationDeg` = 20, `azimuthDeg` = 35, `padding` = 0.1 (fraction of the bounds)
- `scene`: `name`, optional (default: the active or first camera in the file)
- `lookat`: `position` [x,y,z] **required**, `target` = [0,0,0], `up`, optional, `fovDeg` = 40
- every mode takes an optional `lens` (physical camera):
  - `focalLengthMm`, optional: overrides `fovDeg`, measured across `sensorWidthMm` = 36 (the larger image side)
  - `fStop`, optional: setting it turns **depth of field** on
  - `focusDistance` (scene units) or `focusTarget` = `"auto"` (the camera target) | `[x,y,z]` | object name
  - `apertureBlades` = 0 (round; 3+ = polygonal bokeh), `apertureRotationDeg` = 0, `anamorphicRatio` = 1

## environment
- `hdri`: asset id/uri of an equirectangular `.hdr`/`.exr`, or the built-ins `"studio"`, `"sunset"`, `"night"`
- `intensity` ≥ 0 = 1
- `rotationDeg` = 0: rotates the HDRI around the up axis
- `background` `"hdri" | "transparent" | colour` = `"hdri"`. The HDRI still lights the scene when the background is a colour.

## lights (discriminated by `type`)
- `sun`: `direction` [x,y,z] (direction light travels), `intensity` = 3, `color`
- `point`: `position`, `intensity` = 100, `color`, `radius` = 0.05
- `spot`: `position`, `target`, `intensity` = 100, `angleDeg` = 45, `color`
- `area`: `position`, `target`, `size` [w,h] = [1,1], `intensity` = 50, `color`

## render
- `engine` = `"auto"`: `auto blender-cycles blender-eevee blender-workbench mitsuba threejs gsplat` (`gsplat`: splats only, GPU)
- `resolution` [w,h] = [1920,1080]: each 16–16384
- `quality` = `"standard"`: `draft | standard | final` (maps to samples, denoise and bounces per engine)
- `samples` int | null = null: overrides the quality preset
- `denoise` bool = true
- `seed` int = 0
- `hardware` = `"auto"`: `auto | cpu | gpu` (auto picks the cheapest class that fits)
- `antialias`, optional: `{ filter = "gaussian" | "box" | "blackman-harris" | "mitchell", filterWidth = 1.5 }` (pixels)
- `motionBlur`, optional: `{ shutter = 0.5 (fraction of a frame; 0.5 = 180°), position = "center" | "start" | "end",
  subframes = 8 }`. Needs an animation; `subframes` is used where the engine emulates it.

## animation
- `frames` [start, end] inclusive, **required**
- `fps` = 30
- `step` = 1: render every Nth frame
- `turntable`, optional: `{ revolutions = 1, axis = "up" | "X" | "Y" | "Z" }`: orbits the camera around the bounds

## output
- `format` = `"png"`: images `png jpg webp exr`; video `mp4 webm gif mov`
- `passes` = `["beauty"]`: `beauty alpha depth normal albedo mask`
- `transparent` = false: not allowed with jpg/mp4/gif
- `video` `{ codec?: h264 | h265 | vp9 | prores | gif, crf = 18 }`
- `keepFrames` = false: also return individual frames for video
- `format: "splat"` bakes the scene into a 3D Gaussian splat instead of images:
  - `splatFormat` = `"ply"`: `ply | spz | splat` (spz ≈ 10x smaller; .ply/.splat are written Y-down like captures, .spz Y-up)
  - `splatBake` `{ preset = "standard", views?, resolution?, steps?, shDegree?, maxGaussians?, engine = "auto", coverage = "auto", init = "surface", dataset = false }` — `init: "lit"` starts training from a path-traced lit splat: ~2/3 of the steps, sharper result
    - `preset`: `fast` (mesh → Gaussians directly; albedo only, seconds on CPU), `standard` (96 views at 768px + 7k
      gsplat steps, SH2, L4), `high` (200 views at 1024px + 30k steps, SH3, L40S)
    - `engine`: renders the training views (any mesh engine, not `gsplat`); `coverage`: `auto` (−30°…90°), `sphere`, `hemisphere`
    - `dataset: true` also returns the views + an exact COLMAP sparse model (`sparse/0`, text + binary) as a zip

## post (optional, all fields optional)
- `exposure` = 0 (stops), `tonemap` = `"agx"`: `agx filmic aces neutral standard` (run inside the engine where it can)
- grade: `contrast` −1…1 = 0, `saturation` 0…2 = 1, `temperature` −1 (cool)…1 (warm) = 0, `tint` −1…1 = 0
- lens: `vignette` 0…1, `chromaticAberration` 0…1, `lensDistortion` −1 (barrel)…1 (pincushion)
- `bloom` `{ intensity = 0.3, threshold = 0.8, radius = 0.4 }`, `filmGrain` 0…1, `sharpen` 0…1
- Everything except exposure/tonemap is a shared post stage applied identically to every engine's frames.

## Cross-field rules
- Video `format` requires `animation`.
- `format: "splat"` must not have `animation`; `splatFormat`/`splatBake` require `format: "splat"`; the scene must contain meshes.
- `transparent: true` requires png/webp/exr/webm/mov.
- `render.motionBlur` requires an animation range (something has to move: a turntable or scene animation).
