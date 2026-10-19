---
name: renda
description: Render 3D files and scenes in the cloud with Renda: .blend, USD/USDZ, glTF/GLB, three.js JSON, FBX, OBJ, STL, PLY, DAE, 3MF, Alembic, CAD STEP/IGES (.step/.stp/.iges/.igs), and Gaussian splats (.ply/.splat/.spz captures, alone or mixed with meshes). Produces images (png/jpg/webp/exr) or video (mp4/webm/gif/mov) with Blender Cycles/Eevee, Mitsuba, three.js, or gsplat. Use this skill whenever the user wants to render, preview, visualise, thumbnail, screenshot or "see" a 3D model or scene, make product shots or turntables, batch-render assets, or mentions Renda, even if they don't say "render" (e.g. "what does this .glb look like?", "make a hero image of my STL", "render this STEP part").
---

# Renda: cloud rendering for agents

Renda takes a scene file or a **Renda package** (`*.renda.json`, typed render settings) and
returns images or video. It runs on cloud CPUs/GPUs and bills per second of render time,
so the habits below matter: they save the user money.

Related skills: `renda-onboarding` (no account / no key yet), `renda-package` (writing
packages), `renda-video` (turntables, animation, HyperFrames), `renda-cost` (pricing).

## Pick an interface

1. **MCP tools** (`renda_*`), if they're in your tool list. Best choice: auto-uploads
   local files, waits with progress, returns an inline preview image you can look at.
2. **`renda` CLI**, if you have a shell but no MCP: `npx @rendahq/cli …` (Node) or
   `uvx renda …` (Python; add `--cloud`, without it renders locally). Output is JSON when
   piped, with `next[]` hints; exit codes 0 ok, 1 error, 2 usage, 3 needs auth, 4 needs payment.
   Typical: `renda render scene.blend --quality draft`, `renda estimate scene.blend`, `renda jobs <id>`.
   Set it all up with `npx @rendahq/cli init --yes`.
3. **REST API** (`https://renda.vochsel.com/api/v1`, `Authorization: Bearer rnd_live_…`) for
   code integrations. Every error has `{error:{code,message,hint}}`; follow the `hint`.

No key? Use `renda-onboarding` first (fastest: a trial). Never ask the user to paste a key
you could get through a trial or signup.

## Workflow: draft, check, then final

1. **Draft first.** `renda_render {file: "./model.glb"}` defaults to `quality: "draft"`
   and an auto camera with studio lighting. Drafts cost cents and return in seconds.
2. **Look at the result.** The preview image is inline; check framing, orientation
   (wrong up-axis is the most common problem), scale, lighting and materials.
3. **Fix with a package**, not by re-rendering blindly. `renda_init_package` gives a
   starter; edit `camera`, `scene.upAxis`, `environment`, `scene.materialOverrides`
   (see `renda-package`). Validate with `renda_validate_package` (free, local).
4. **Estimate before expensive renders.** For `final`, high resolution, or any video, call
   `renda_estimate` and tell the user the range before submitting if it's more than about $1.
5. **Render final** and `renda_download` the outputs (URLs are presigned and expire).

Skip the draft only when the user already has a tuned package or explicitly wants final.

## Reading results

A job returns `status`, `outputs[]` (`name`, `url`, `contentType`, `bytes`), `billedSeconds`,
`costUsd`, `cached` and `warnings[]`.

- **Always read `warnings`.** They report features the engine couldn't honour (e.g. Mitsuba
  ignoring a pass, an FBX material approximated). Pass relevant ones on to the user.
- `cached: true` means an identical render already existed. It cost $0.
- `failed`: read `error`, fix the package, resubmit. Don't retry an identical package
  unless the error looks transient (network, timeout).
- Still running after the wait window: `renda_get_job {id, wait: true}`.

## Engine choice (short version)

Leave `render.engine: "auto"` unless there's a reason. Auto uses Blender for `.blend` and
picks a cheap engine for drafts. Override it when:
- **photoreal / final product shots** → `blender-cycles` (or `mitsuba` for spectral or physically exact work)
- **fast stylised or video previews** → `blender-eevee`
- **clay / geometry checks** → `blender-workbench` (cheapest, CPU)
- **three.js JSON scenes, or matching a web viewer** → `threejs`
- **Gaussian splat captures** → leave `auto` (picks `gsplat`, the cheapest); for a mesh inside a
  capture use `scene.include` and `threejs` (draft) or `blender-cycles` (final; splats composited by depth)

The full matrix is in `renda-package`.

## Errors you'll see

| code | do |
|---|---|
| `unauthenticated` | run the `renda-onboarding` flow |
| `payment_required` | `renda_billing_link`, then give the URL to the human. You can't pay. |
| `invalid_package` | apply the per-path fixes, then validate again |
| `unsupported_format` | convert, or check the extension (a three.js `.json` needs `metadata.type: "Object"`) |
| `asset_not_found` | a `uri` was wrong or a presigned URL expired; re-upload |
