/**
 * Agent benchmark results for /benchmarks, read at build time from the newest committed results file in
 * bench/agents/results/*.jsonl (written by bench/agents/score.py). Rerun the benchmark, commit the new
 * results file, redeploy — the page picks it up. Server-only (node:fs).
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

export type AgentRun = {
  run: string;
  task: string;
  agent: string;
  condition: "bare" | "renda";
  model: string;
  started_at: string;
  wall_seconds: number;
  killed: string | null;
  tool_calls: number | null;
  agent_cost_usd: number | null;
  renda_cost_usd: number;
  verdict: "success" | "partial" | "fail" | "unjudged";
  score: number;
  notes: string;
  thumb?: string | null;
  git_sha?: string;
};

export type AgentGroup = {
  agent: string;
  condition: "bare" | "renda";
  model: string;
  runs: number;
  successes: number;
  medianWall: number;
  agentCost: number;
  rendaCost: number;
  medianTools: number;
};

export type AgentBench = { file: string; date: string; sha: string; runs: AgentRun[]; groups: AgentGroup[]; tasks: string[] };

const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? (s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2) : 0;
};

export function loadAgentBench(): AgentBench | null {
  const dir = join(process.cwd(), "..", "bench", "agents", "results");
  let files: string[];
  try {
    files = readdirSync(dir).filter((f) => f.endsWith(".jsonl")).sort();
  } catch {
    return null;
  }
  const file = files.at(-1);
  if (!file) return null;
  const runs = readFileSync(join(dir, file), "utf8")
    .split("\n")
    .filter(Boolean)
    .map((l) => JSON.parse(l) as AgentRun);
  const tasks = [...new Set(runs.map((r) => r.task))];
  const groups: AgentGroup[] = [];
  for (const agent of [...new Set(runs.map((r) => r.agent))]) {
    for (const condition of ["bare", "renda"] as const) {
      const g = runs.filter((r) => r.agent === agent && r.condition === condition);
      if (!g.length) continue;
      groups.push({
        agent,
        condition,
        model: g[0].model,
        runs: g.length,
        successes: g.reduce((n, r) => n + r.score, 0),
        medianWall: median(g.map((r) => r.wall_seconds)),
        agentCost: g.reduce((n, r) => n + (r.agent_cost_usd ?? 0), 0),
        rendaCost: g.reduce((n, r) => n + r.renda_cost_usd, 0),
        medianTools: median(g.map((r) => r.tool_calls ?? 0)),
      });
    }
  }
  const [date, sha] = file.replace(/\.jsonl$/, "").split(/-(?=[^-]+$)/);
  return { file: `bench/agents/results/${file}`, date, sha, runs, groups, tasks };
}
