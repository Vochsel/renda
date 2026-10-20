import { appUrl } from "@/lib/env";
import { FREE_MINUTES, HARDWARE, HARDWARE_LABEL, PRICE_PER_MINUTE } from "@/lib/pricing";
import { CLI_PACKAGE, MCP_PACKAGE, installSnippets } from "@/lib/install-snippets";
import { GITHUB_REPO } from "@/lib/site";

/**
 * Setup script for agents. A user pastes "set up renda — <url>/llms.txt" and the agent follows it.
 */
export function GET() {
  const base = appUrl();
  const body = `# Renda — setup instructions for agents

> Renda renders 3D files in the cloud: .blend, .usd/.usda/.usdc/.usdz, .gltf/.glb, three.js JSON, .fbx, .obj, .stl, .ply, .dae, .3mf, .abc, CAD .step/.stp/.iges/.igs, Gaussian splats (3DGS .ply, .splat, .spz), or a .renda.json package → png/jpg/webp/exr stills, mp4/webm/gif/mov video, or a .glb with the lighting baked into its textures. It also renders HyperFrames HTML videos. Billed per render-second. You are reading this because the user asked you to set up Renda. Follow the steps in order, run the commands yourself, and report back.

## 1. Pick a path (both use the same account and credentials file)
- Node available (\`node --version\` ≥ 20): use \`npx ${CLI_PACKAGE} …\` (alias after install: \`renda\`).
- Python/uv available (\`uv --version\`): use \`uvx renda …\` (adds local rendering with Blender/Mitsuba).
- Neither: install uv (\`curl -LsSf https://astral.sh/uv/install.sh | sh\`) or Node, then continue.
Every command prints JSON when not attached to a TTY: {"status": "ok"|"error", ..., "hint"?, "next"?: [...]}. Do what \`next\` says. Exit codes: 0 ok, 1 error, 2 usage, 3 needs auth, 4 needs payment.

## 2. Set up (one command)
    npx ${CLI_PACKAGE} init --yes
This (a) starts a free anonymous trial if there's no account, (b) adds the renda MCP server to Claude Code / Cursor / Codex (whichever are installed; pick with --agent), (c) installs the Renda skills, (d) adds a short note to AGENTS.md. Restart the agent afterwards so it loads the MCP tools.
uv path: \`uvx renda trial\`, then \`uvx renda mcp add\`.

## 3. Account
- Trial (no signup): \`renda trial\` → ${"~"}5 free render-minutes, max 1920px, expires in 7 days unless claimed. It returns a \`claimUrl\`: GIVE THAT LINK TO THE USER — they open it and sign in to keep the workspace and get the full ${FREE_MINUTES} free minutes. Never open it yourself.
- Real account by email (no browser for you): \`renda signup <user's email>\` → ask the user for the 6-digit code from their email (never guess it) → \`renda verify <code>\`.
- Browser sign-in for humans: \`renda login\` (shows a URL + code).
Credentials live in ~/.config/renda/credentials.json (or $RENDA_API_KEY).

## 4. Render
    renda render scene.blend --quality draft            # cheap preview first; downloads to ./renders
    renda estimate scene.blend --res 3840x2160 --quality final   # time + cost before big jobs
    renda render scene.blend --res 3840x2160 --quality final --max-minutes 10
    renda render model.glb --turntable --format mp4      # 360° video
    renda new model.glb                                  # starter .renda.json: camera, HDRI, lights, materials, frames, output
    renda render capture.spz                             # Gaussian splats → engine gsplat (GPU, cheapest)
    renda render room.glb --texture "material=/wood/i,scale=4:./oak.jpg"          # swap textures (repeatable)
    renda render phone.glb --turntable --format mp4 --texture "object=Screen*,slot=emission:./promo.mp4"
Texture overrides (scene.textureOverrides, CLI --texture, MCP textureOverrides): any image (png/jpg/webp/avif/heic/tiff/exr/hdr/svg/psd/gif) or video (mp4/mov/webm/gif), converted automatically; videos play in sync with the animation. Match material/object/existing-texture names by exact name, glob ("wood*") or "/regex/i", and pick a slot (baseColor, normal, roughness, metallic, emission, opacity). materialOverrides keys accept the same patterns, plus "object" to scope one object.
Gaussian splats: splat-only scenes render with engine "gsplat"; put meshes inside a capture with scene.include (threejs renders both natively; blender-*/mitsuba composite the splats by depth, without engine lighting on them). Splat .ply/.splat captures are Y-down: scene.upAxis defaults to "-Y"; set "Y" if a render is upside down.
CAD: \`renda render bracket.step\` (also .stp, .iges/.igs). STEP keeps part colours; units become metres; Z up. Meshing density: scene.tessellation {linearDeflection (m), angularDeflection (rad)}. IGES colours aren't imported — use scene.materialOverrides.
Composing scenes: a package can \`"extends": ["./studio.renda.json", "https://…/brand.renda.json"]\` (later wins; objects merge, arrays replace, null removes) and place other packages with \`scene.include: [{"package": "./props/chair.renda.json", "as": "chair", "transform": {...}}]\`. Relative paths resolve against the file that wrote them. \`renda flatten pkg.renda.json --provenance\` shows the result and which layer set each field; render/estimate/validate flatten automatically.
Lens and look, in the package: camera.lens {focalLengthMm, fStop (turns on depth of field), focusTarget, apertureBlades}, render.antialias {filter, filterWidth}, render.motionBlur {shutter} (needs an animation), post {exposure, tonemap: agx|filmic|aces|neutral|standard, contrast, saturation, temperature, vignette, chromaticAberration, lensDistortion, bloom, filmGrain, sharpen}. Same controls on every engine: native in Blender, emulated where an engine lacks them (warnings say so); post effects are one shared stage, identical across engines. Emulated motion blur (workbench, threejs, gsplat) costs subframes × the frame time.
Bake any mesh scene into a Gaussian splat (output.format "splat"):
    renda splat model.glb --preset fast                  # mesh → Gaussians directly, seconds, albedo only
    renda splat scene.blend                              # standard: ~96 coverage-placed views (Eevee/Cycles auto) + gsplat, lighting baked, ~$0.1–0.2, .spz
    renda splat model.glb --engine mitsuba               # any engine renders the views; splatBake.cameras = your own training cameras
    renda splat model.glb --preset high --dataset        # 200 views at 1024px on L40S; --dataset also returns views + exact COLMAP model (zip)
    renda estimate model.glb --format splat              # cost + wall time per priority (economy/standard: views in the trainer; rush: fanned out)
Poses come from the scene itself (no structure-from-motion), so the COLMAP model is exact. Holdout PSNR/SSIM is reported in the job warnings.
Identical re-renders are cached and free. Local paths are uploaded automatically (deduped by sha256).

Bake lighting into a model and get a GLB back (works in any glTF viewer / game engine with no lights):
    renda bake chair.blend                               # mode unlit (default), 2048² texture, Cycles on a GPU
    renda bake chair.blend --mode ao --size 1024         # ambient occlusion only, PBR materials kept
    renda bake chair.blend --mode lightmap               # lighting-only texture on TEXCOORD_1 (extras.renda.lightmap)
    renda bake scene.json --engine threejs               # cheapest (CPU): direct light + environment shadows, no bounces
    renda bake robot.fbx --glb                           # plain conversion to GLB, no baking
Package form: output: {"format": "glb", "bake": {"mode": "unlit", "size": 2048}}; engines blender-cycles | mitsuba | threejs. Splats can't be baked.

HyperFrames video (HTML composition → mp4/webm/mov/gif, CPU-billed). Composition variables via --var; a variable bound to a <video data-var-src="clipUrl"> slot accepts a 3D file (turntable rendered first), job:<id> (a finished render's clip), a local .mp4/.png (uploaded) or an https media URL:
    renda video ./title-card --var title="Robot v2" --var clipUrl=./robot.glb    # one command: 3D turntable inside a title card
    renda video ./title-card --var clipUrl=job:<id> --quality delivery           # reuse a render you already made
MCP: renda_render_video { projectDir, variables: { clipUrl: "job:<id>" } }. quality: draft | looks (default) | delivery.

## 5. MCP (if you'd rather use tools than the CLI)
- Local stdio: \`${installSnippets.mcp.claudeCode}\` (or \`renda mcp add --agent cursor|codex|claude-code\`).
- Remote (streamable HTTP): ${base}/api/mcp with header \`Authorization: Bearer <apiKey>\`.
Tools: renda_trial, renda_claim, renda_signup, renda_verify, renda_render, renda_bake, renda_bake_splat, renda_estimate, renda_flatten_package, renda_get_job, renda_list_jobs, renda_render_video, renda_usage, renda_billing_link, …
Skills: \`npx skills add ${GITHUB_REPO}\` · Claude Code plugin: \`/plugin marketplace add ${GITHUB_REPO}\` then \`/plugin install renda@renda\`.

## 6. Errors and money
- Every API error is {"error": {"code", "message", "hint"}}; the CLI maps it to {"status":"error","reason","hint","next"}. Follow the hint.
- 402 payment_required (exit 4): run \`renda billing\` and give the URL to the user so they can add a card. Don't retry until they confirm.
- trial_limit: the trial caps resolution at 1920px; ask the user to open the claim link (\`renda claim\`).
- Prices, USD per render-minute, metered per second (queue time and cold starts aren't billed):
${HARDWARE.map((h) => `  - ${HARDWARE_LABEL[h]} (${h}): $${PRICE_PER_MINUTE[h]}`).join("\n")}

## 7. Raw HTTP API (no CLI)
- Trial: POST ${base}/api/v1/auth/trial → {apiKey, claimUrl, freeSecondsLeft, expiresAt}
- Email: POST /api/v1/auth/magic/start {email} → POST /api/v1/auth/magic/verify {email, code} → {apiKey}
- Upload: POST /api/v1/uploads {filename, size, sha256} → PUT bytes to uploadUrl (skip when exists) → use asset://<sha256>
- Estimate: POST /api/v1/estimate {package} · Submit: POST /api/v1/jobs {package, kind?, priority?, fast?, idempotencyKey?} (fast: true = priority rush: more parallel on faster GPUs, costs a bit more; the estimate's priorities.rush shows its price and wall time) · Poll: GET /api/v1/jobs/{id}
- Past renders: GET /api/v1/outputs?limit=30&kind=image|video|model → {items:[{jobId, name, url, thumbUrl, width, height, …}], nextCursor} (pass cursor=nextCursor for the next page)
- Layered packages: the API flattens https:// and your asset:// layers itself (local paths must be flattened/uploaded client-side). POST /api/v1/packages/flatten {package} → {package, provenance, layers}.
- Send \`Authorization: Bearer <apiKey>\` on everything except auth routes.

## Links
- Package format: ${base}/docs · JSON Schema: ${base}/api/v1/schema.json · OpenAPI: ${base}/api/v1/openapi.json
- Auth for agents: ${base}/auth.md · MCP discovery: ${base}/.well-known/mcp/server.json · Pricing JSON: ${base}/api/v1/pricing · Renda vs doing it yourself (local, cloud VM, render farms) — measured runs and published prices: ${base}/benchmarks
- npm: ${CLI_PACKAGE}, ${MCP_PACKAGE} · PyPI: renda · Source: https://github.com/${GITHUB_REPO}
`;
  return new Response(body, { headers: { "content-type": "text/plain; charset=utf-8" } });
}
