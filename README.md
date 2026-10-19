# Renda

**Open-source, agent-first cloud rendering for 3D files and scenes.**

Hand Renda a `.blend`, `.usdz`, `.glb`, `.fbx`, `.obj`, `.stl`, `.ply`, `.dae`, `.3mf`, `.abc`,
a three.js JSON scene or a Gaussian splat capture (`.ply`, `.splat`, `.spz`) — on its own or with
meshes placed inside it — and get back a still, a turntable, or a video. Renders run in
the cloud on [Modal](https://modal.com) and are billed per second of render time
(shown in render-minutes). Or run the same CLI locally, for free.

Node or Python, same account, same commands:

```bash
# Node
npx @rendahq/cli init                      # free trial (no signup) + MCP + skills for your agent
npx @rendahq/cli render chair.blend --res 2048x2048

# Python (uv) — also renders locally with Blender/Mitsuba
uvx renda trial
uvx renda render chair.blend --cloud --engine mitsuba --res 2048x2048
```

Agents: paste this and they do the rest themselves (trial → render → claim link for you):

```
set up renda — https://renda.vochsel.com/llms.txt
```

Or wire it up directly:

```bash
claude mcp add renda -- npx -y @rendahq/mcp     # MCP server
npx skills add vochsel/renda                   # skills (skills.sh)
/plugin marketplace add vochsel/renda          # Claude Code plugin: skills + MCP
```

A trial gives about 5 free render-minutes and a claim link. Open it and sign in to keep the workspace and
get 30 free minutes. A full account by email works from a terminal too: `renda signup you@example.com`, then
`renda verify <code>`.

## Why

- **One input, many engines.** Blender (Cycles / Eevee / Workbench), Mitsuba 3, three.js and
  gsplat behind one CLI. Gaussian splats render in all of them (natively in three.js and gsplat,
  depth-composited over Blender and Mitsuba renders). A lightweight scene IR (Hydra-lite) means a `.blend` can be rendered in
  Mitsuba, or a three.js scene in Cycles.
- **A tiny typed package format** (`*.renda.json`) for what scene files can't say: resolution,
  camera, HDRI, lights, material overrides, frame range, output format. See [`spec/`](spec/).
- **Cheap.** Per-second billing, automatic CPU vs GPU selection, result + asset caching,
  chunked fan-out, denoising so fewer samples are needed. See [ARCHITECTURE.md §5](ARCHITECTURE.md#5-cost-optimisation-the-workers-job).
- **Agent-first.** Signup, billing, rendering and results all work without a browser.
  Every error tells an agent what to do next.
- **Video.** Turntables and animations encode to MP4/WebM/GIF/ProRes; HyperFrames HTML
  compositions render on the same infrastructure.

## Repo

| Dir | |
|---|---|
| [`spec/`](spec/) | `@rendahq/spec`: package format (Zod + JSON Schema) |
| [`core/`](core/) | `renda` Python package + CLI: IR, loaders, engine adapters, cloud client |
| [`worker/`](worker/) | Modal app that renders jobs |
| [`web/`](web/) | Next.js dashboard, REST API, remote MCP (WorkOS, Stripe, Postgres, R2) |
| [`mcp/`](mcp/) | `@rendahq/mcp` stdio MCP server (+ hosted entry for the remote MCP) |
| [`cli/`](cli/) | `@rendahq/cli` Node CLI: `init`, trial/claim, cloud render, `mcp add` |
| [`skills/`](skills/) | Agent skills + Claude Code plugin |

Read [ARCHITECTURE.md](ARCHITECTURE.md) for how it fits together.

## License

Renda is open source under two licenses:

| Part | License |
|---|---|
| `spec/`, `core/`, `cli/`, `mcp/`, `skills/`, `testdata/` — the format, CLI, engines, MCP server, skills | [Apache-2.0](LICENSE) |
| `web/`, `worker/` — the hosted service (API, dashboard, Modal worker) | [AGPL-3.0-only](web/LICENSE) |

Use the CLI, engines, MCP server and package format anywhere, including in closed-source products.
If you run a modified copy of the hosted service for others, the AGPL requires you to publish your changes.
