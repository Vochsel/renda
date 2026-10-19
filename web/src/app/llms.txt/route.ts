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

> Renda renders 3D files in the cloud: .blend, .usd/.usda/.usdc/.usdz, .gltf/.glb, three.js JSON, .fbx, .obj, .stl, .ply, .dae, .3mf, .abc, Gaussian splats (3DGS .ply, .splat, .spz), or a .renda.json package → png/jpg/webp/exr stills, mp4/webm/gif/mov video, or a .glb with the lighting baked into its textures. It also renders HyperFrames HTML videos. Billed per render-second. You are reading this because the user asked you to set up Renda. Follow the steps in order, run the commands yourself, and report back.

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
Gaussian splats: splat-only scenes render with engine "gsplat"; put meshes inside a capture with scene.include (threejs renders both natively; blender-*/mitsuba composite the splats by depth, without engine lighting on them). Splat .ply/.splat captures are Y-down: scene.upAxis defaults to "-Y"; set "Y" if a render is upside down.
Identical re-renders are cached and free. Local paths are uploaded automatically (deduped by sha256).

Bake lighting into a model and get a GLB back (works in any glTF viewer / game engine with no lights):
    renda bake chair.blend                               # mode unlit (default), 2048² texture, Cycles on a GPU
    renda bake chair.blend --mode ao --size 1024         # ambient occlusion only, PBR materials kept
    renda bake chair.blend --mode lightmap               # lighting-only texture on TEXCOORD_1 (extras.renda.lightmap)
    renda bake scene.json --engine threejs               # cheapest (CPU): direct light + environment shadows, no bounces
    renda bake robot.fbx --glb                           # plain conversion to GLB, no baking
Package form: output: {"format": "glb", "bake": {"mode": "unlit", "size": 2048}}; engines blender-cycles | mitsuba | threejs. Splats can't be baked.

## 5. MCP (if you'd rather use tools than the CLI)
- Local stdio: \`${installSnippets.mcp.claudeCode}\` (or \`renda mcp add --agent cursor|codex|claude-code\`).
- Remote (streamable HTTP): ${base}/api/mcp with header \`Authorization: Bearer <apiKey>\`.
Tools: renda_trial, renda_claim, renda_signup, renda_verify, renda_render, renda_bake, renda_estimate, renda_get_job, renda_list_jobs, renda_render_video, renda_usage, renda_billing_link, …
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
- Estimate: POST /api/v1/estimate {package} · Submit: POST /api/v1/jobs {package, kind?, priority?, idempotencyKey?} · Poll: GET /api/v1/jobs/{id}
- Past renders: GET /api/v1/outputs?limit=30&kind=image|video|model → {items:[{jobId, name, url, thumbUrl, width, height, …}], nextCursor} (pass cursor=nextCursor for the next page)
- Send \`Authorization: Bearer <apiKey>\` on everything except auth routes.

## Links
- Package format: ${base}/docs · JSON Schema: ${base}/api/v1/schema.json · OpenAPI: ${base}/api/v1/openapi.json
- Auth for agents: ${base}/auth.md · MCP discovery: ${base}/.well-known/mcp/server.json · Pricing JSON: ${base}/api/v1/pricing
- npm: ${CLI_PACKAGE}, ${MCP_PACKAGE} · PyPI: renda · Source: https://github.com/${GITHUB_REPO}
`;
  return new Response(body, { headers: { "content-type": "text/plain; charset=utf-8" } });
}
