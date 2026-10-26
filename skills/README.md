# Renda agent skills

Skills that teach agents to render 3D scenes and videos with Renda, bundled as a
Claude Code plugin together with the `@rendahq/mcp` server.

| Skill | Use it for |
|---|---|
| `renda` | Entry point: rendering any 3D file, MCP vs CLI, draft→final workflow, reading results |
| `renda-onboarding` | Zero to first render: one-command setup, anonymous trial + claim link, email signup, buying credit, billing link |
| `renda-package` | Writing `.renda.json` packages: camera, lighting, materials, engines, per-format quirks |
| `renda-video` | Turntables, animations, HyperFrames compositions, 3D inside HyperFrames |
| `renda-cost` | Billing model, estimates, keeping renders cheap |

## Install

**Everything in one command** (MCP server + these skills for Claude Code / Cursor / Codex, plus a trial workspace):

```sh
npx @rendahq/cli init          # or: uvx renda trial && uvx renda mcp add
```

**skills.sh** (any agent the `skills` CLI supports):

```sh
npx skills add vochsel/renda                 # all skills
npx skills add vochsel/renda -s renda -a cursor
```

**Claude Code plugin** (skills + MCP server). The repo root has `.claude-plugin/marketplace.json`
pointing at this directory, which is the plugin root (`.claude-plugin/plugin.json`, `.mcp.json`):

```sh
/plugin marketplace add vochsel/renda
/plugin install renda@renda
```

**Skills only, by hand**: copy the folders into `~/.claude/skills/` (user) or `.claude/skills/` (project):
`cp -r skills/renda* ~/.claude/skills/`.

**MCP only**: `claude mcp add renda -- npx -y @rendahq/mcp` (see `mcp/README.md`).

## Layout

```
skills/
  .claude-plugin/plugin.json   plugin manifest (skills: "./", mcpServers: ./.mcp.json)
  .mcp.json                    runs npx -y @rendahq/mcp
  renda/SKILL.md
  renda-onboarding/SKILL.md
  renda-package/SKILL.md + references/
  renda-video/SKILL.md
  renda-cost/SKILL.md
```
