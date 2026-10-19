# @rendahq/cli

Render 3D files and scenes in the [Renda](https://renda.vochsel.com) cloud from your terminal or agent.
Installs as `renda` (and `rendahq`).

```bash
npx @rendahq/cli init                  # free trial (no signup) → MCP server + skills for Claude Code / Cursor / Codex
npx @rendahq/cli render model.glb      # uploads, renders, downloads to ./renders
```

| | |
|---|---|
| `init [--agent claude-code\|cursor\|codex\|all] [--yes] [--trial] [--remote]` | auth (trial if none), MCP config, skills, AGENTS.md note (with `--yes` or consent) |
| `mcp` · `mcp add\|remove [--agent …] [--remote]` | run the stdio MCP server, or (un)install it |
| `trial` · `claim` | anonymous trial (5 render-minutes, 1920px max, 7 days) and its claim link |
| `signup <email>` · `verify <code>` · `login` · `logout` | accounts (email code, or browser device flow) |
| `whoami` · `balance` · `usage` · `billing` | account, free minutes, spend, card link |
| `render <file\|pkg> [--res WxH] [--quality …] [--engine …] [--frames a-b] [--format …] [-o dir] [--no-wait] [--max-minutes N] [--local]` | cloud render; `--local` hands off to `uvx renda` (Blender/Mitsuba on this machine) |
| `estimate <file\|pkg>` · `jobs [id] [--cancel] [--download dir]` | cost before rendering; job status/outputs |
| `validate <pkg>` · `new <file>` | package tools |
| `video <hyperframes-dir> [--var key=value …] [--vars json] [--quality draft\|looks\|delivery]` | HyperFrames video; `--var clipUrl=./robot.glb` renders a turntable first and drops it into the composition, `--var clipUrl=job:<id>` reuses a render |

When stdout isn't a TTY (or with `--json`) every command prints one JSON object:
`{"status": "ok", …, "next": [...]}` or `{"status": "error", "reason", "message", "hint", "next"}`.
Exit codes: 0 ok, 1 error, 2 usage, 3 auth needed, 4 payment required. API keys are never printed.

Credentials: `~/.config/renda/credentials.json` (shared with the Python CLI `uvx renda` and `@rendahq/mcp`),
or `RENDA_API_KEY`. API: `RENDA_API_URL` (default `https://renda.vochsel.com`).
