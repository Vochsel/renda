# @rendahq/mcp

MCP server for [Renda](https://renda.vochsel.com): cloud rendering of 3D files and scenes
(`.blend`, `.usd/.usdz`, `.glb/.gltf`, three.js JSON, `.fbx`, `.obj`, `.stl`, `.ply`,
`.dae`, `.3mf`, `.abc`, CAD `.step/.iges`, Gaussian splats) and HyperFrames videos, from any agent.
Layered packages (`extends`, `scene.include[].package`) are flattened automatically;
`renda_flatten_package` shows the composed result. Billed per render-second.

## Install

**One command** (detects Claude Code / Cursor / Codex, adds the server and the Renda skills, starts a free trial if needed):

```sh
npx @rendahq/cli init          # or: npx @rendahq/cli mcp add --agent cursor
uvx renda mcp add              # same, from Python/uv
```

**Claude Code**

```sh
claude mcp add renda -- npx -y @rendahq/mcp
# with an existing key:
claude mcp add renda -e RENDA_API_KEY=rnd_live_... -- npx -y @rendahq/mcp
```

**Claude Desktop / Cursor** (`claude_desktop_config.json` / `.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "renda": { "command": "npx", "args": ["-y", "@rendahq/mcp"] }
  }
}
```

**Remote MCP** (no local install, no local file access): `https://renda.vochsel.com/api/mcp`
over Streamable HTTP with `Authorization: Bearer rnd_live_...`.

```sh
claude mcp add --transport http renda https://renda.vochsel.com/api/mcp --header "Authorization: Bearer rnd_live_..."
```

No key yet? You don't need one to start. With no key, `renda_render` (or `renda_trial`) starts an
anonymous trial (about 5 free render-minutes) and gives you a claim link to keep the workspace. For a full
account the agent calls `renda_signup` with your email, asks you for the 6-digit code, then calls
`renda_verify`. Keys are saved to `~/.config/renda/credentials.json`, shared with both `renda` CLIs (npm and PyPI).

## Configuration

| Env | Default | |
|---|---|---|
| `RENDA_API_KEY` | from credentials file | API key |
| `RENDA_API_URL` | `https://renda.vochsel.com` | API base URL (point at `http://localhost:3000` for dev) |

## Tools

| Tool | |
|---|---|
| `renda_trial` / `renda_claim` | anonymous trial (no signup) and its claim link for the human |
| `renda_signup` / `renda_verify` | email + 6-digit code onboarding; saves the key locally |
| `renda_whoami`, `renda_pricing`, `renda_usage` | account, prices, usage |
| `renda_billing_link` | Stripe Checkout/portal URL for the human |
| `renda_validate_package` | local, free validation with JSON paths and fixes |
| `renda_init_package` | starter `.renda.json` for a file |
| `renda_upload` | local only; content-addressed upload → `asset://<sha256>` |
| `renda_estimate` | hardware, time and cost range |
| `renda_render` | render a file / package / `.renda.json`; local paths auto-uploaded; starts a trial if there's no key; waits with progress; returns URLs and an inline preview |
| `renda_render_video` | HyperFrames composition → mp4/webm/mov/gif |
| `renda_get_job`, `renda_list_jobs`, `renda_cancel_job` | jobs |
| `renda_download` | local only; save outputs to disk |

Resources: `renda://schema/v1` (JSON Schema), `renda://docs/package` (guide).
Prompts: `render-file`, `make-turntable`.

## As a library

The web app mounts the same tools for the remote endpoint:

```ts
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { RendaClient, registerRendaTools } from "@rendahq/mcp";

const server = new McpServer({ name: "renda", version: "0.1.0" });
registerRendaTools(server, { client: new RendaClient({ baseUrl, apiKey }), mode: "remote" });
```

`mode: "remote"` omits the filesystem tools (`renda_upload`, `renda_download`, `packagePath`, `projectDir`).

## Develop

```sh
pnpm --filter @rendahq/mcp build
pnpm --filter @rendahq/mcp test
RENDA_API_URL=http://localhost:3000 node mcp/dist/bin.js
```

## Registry

`server.json` (MCP registry) and `mcpName: io.github.vochsel/renda` are in this package; publish with
`mcp-publisher publish` after `npm publish`. Hosted entry point for remote servers: `@rendahq/mcp/remote`;
stdio entry point: `@rendahq/mcp/stdio`.
