# Agent benchmark: coding agents with and without Renda

Gives coding agents the same 3D tasks twice — once **bare** (shell, file tools, network; whatever they can figure
out) and once **with Renda** (skills + MCP/CLI) — and logs what happened. Results feed the
[/benchmarks](https://renda.vochsel.com/benchmarks) page.

## Tasks

Defined in [`tasks.json`](tasks.json). Every run gets the same prompt (task prompt + a shared "unattended" suffix)
and a fresh empty directory containing only the input file(s):

| id | input | output |
|---|---|---|
| `still` | `model.glb` (testdata table scene) | `out/render.png` |
| `step` | `part.step` (testdata bracket) | `out/render.png` |
| `turntable` | `model.usdz` | `out/turntable.mp4` (3 s) |
| `bake` | `model.glb` | `out/baked.glb` (lighting baked into textures) |
| `splat` | `model.glb` | `out/model.ply` or `.spz` (Gaussian splat) |

GLB/STEP inputs get a per-run nonce (glTF `asset.extras` / a STEP comment; geometry untouched) so Renda's result
cache can't make a run free.

## Agents and conditions

| agent | default model | bare | renda |
|---|---|---|---|
| Claude Code (`claude -p`) | `opus` (`BENCH_CLAUDE_MODEL`) | `--strict-mcp-config` with no servers, `--setting-sources project` (no user plugins/settings) | + Renda MCP (local build) + Renda skills as a plugin |
| pi (`pi -p`) | `openai-codex/gpt-5.6-terra` (`BENCH_PI_MODEL`) | `--no-skills --no-extensions --no-context-files` | + Renda skills (`--skill`); pi has no MCP, so it uses the CLI |

Both conditions get the same built-in tools; the run record captures the MCP servers and skills the agent actually
loaded (`isolation`). In the renda condition, `renda`, `npx @rendahq/cli|mcp` and `uvx renda` resolve to the local
builds (the packages aren't published yet) and `RENDA_API_KEY` is set from `BENCH_RENDA_API_KEY`.

Guardrails: 15 min per run (`BENCH_TIMEOUT_S`), runs killed if free disk drops below `BENCH_MIN_FREE_GB` (3),
at most 2 in parallel. At the end, Homebrew packages installed during the suite are uninstalled and new `/tmp`
entries that a transcript mentions (scripts, venvs, frames) are deleted.

The renda condition runs share one benchmark account, so a run that lists jobs (`renda jobs`) can see another
run's. Only jobs created after a run started count towards its Renda cost, and outputs are checked against the
run's own jobs. In the 2026-10-20 suite one pi run downloaded a parallel run's bake after its own call hit pi's
120 s tool timeout; it was rerun alone (noted in its record).

## Run

```bash
pnpm install && pnpm -r --filter @rendahq/spec --filter @rendahq/mcp --filter @rendahq/cli build
export BENCH_RENDA_API_KEY=rnd_live_…        # a key with prepaid credit (renda condition only)
uv run --no-project python bench/agents/run.py --workdir /tmp/renda-agent-eval --records /tmp/renda-agent-eval/records.jsonl
```

Make the bake thumbnails in a real glTF viewer
(`uv run --no-project --with playwright --with pillow python bench/agents/preview_glb.py /tmp/renda-agent-eval`),
then look at every output (the scorer writes the other thumbnails) and record a verdict per run in
`judgments/<date>.json` (`{"<run>": {"verdict": "success|partial|fail", "notes": "…"}}`), and score:

```bash
set -a; source web/.env.local; set +a          # DATABASE_URL, read-only, for Renda billed cost per job
uv run --no-project --with pillow --with imageio-ffmpeg python bench/agents/score.py \
  --records /tmp/renda-agent-eval/records.jsonl --workdir /tmp/renda-agent-eval --judgments bench/agents/judgments/<date>.json
```

This writes `results/<date>-<sha>.jsonl` (one record per run: task, agent, condition, model, wall time, exit, tokens,
agent cost, Renda billed seconds + cost, tool calls, validity check, verdict, score, notes, timestamp, git sha),
`results/<date>-<sha>.md` (summary table) and `web/public/benchmarks/agents/*.webp` thumbnails. Commit those;
the page reads the newest results file at build time (`web/src/lib/agent-bench.ts`).

Transcripts stay in the workdir (secrets redacted) and are never committed.

## Caveats

One run per cell, so treat differences of a few tasks as noise. The benchmark machine has Blender installed, which
helps the bare condition. Agent cost is what each CLI reports for its own model calls.
