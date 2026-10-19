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
- `materialOverrides` `{ [materialName | "*"]: MaterialOverride }` = `{}`

### Transform
- `translate` [x,y,z] = [0,0,0]
- `rotateDeg` [x,y,z] = [0,0,0]: XYZ Euler in degrees
- `scale` number | [x,y,z] = 1

### MaterialOverride (all optional)
`baseColor` (#rrggbb | #rrggbbaa | [r,g,b] linear 0–1), `metallic` 0–1, `roughness` 0–1,
`emission` colour, `emissionStrength` ≥ 0, `opacity` 0–1, `transmission` 0–1, `ior` ≥ 1.

## camera (discriminated by `mode`)
- `auto`: `fovDeg` = 35, `elevationDeg` = 20, `azimuthDeg` = 35, `padding` = 0.1 (fraction of the bounds)
- `scene`: `name`, optional (default: the active or first camera in the file)
- `lookat`: `position` [x,y,z] **required**, `target` = [0,0,0], `up`, optional, `fovDeg` = 40

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

## Cross-field rules
- Video `format` requires `animation`.
- `transparent: true` requires png/webp/exr/webm/mov.
