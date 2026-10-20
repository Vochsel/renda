"""Run coding agents on the Renda tasks, with and without Renda, and log one JSON record per run.

    uv run --no-project python bench/agents/run.py --agents claude,pi --conditions bare,renda \
        --workdir /tmp/renda-agent-eval --results bench/agents/results

Each run gets a fresh directory outside the repo containing only the task's input files. Transcripts stay in
that directory (secrets redacted); only the per-run record goes into the results JSONL. See README.md.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TASKS = json.loads((HERE / "tasks.json").read_text())

AGENTS = {
    "claude": {"model": os.environ.get("BENCH_CLAUDE_MODEL", "opus")},
    "pi": {"model": os.environ.get("BENCH_PI_MODEL", "openai-codex/gpt-5.6-terra")},
}
TIMEOUT_S = int(os.environ.get("BENCH_TIMEOUT_S", 900))
MIN_FREE_GB = float(os.environ.get("BENCH_MIN_FREE_GB", 3))
SECRET_PATTERNS = [re.compile(p) for p in (r"rnd_live_[A-Za-z0-9_-]{8,}", r"sk-[A-Za-z0-9_-]{16,}", r"sk_(?:test|live)_[A-Za-z0-9]{8,}")]


def free_gb() -> float:
    st = os.statvfs("/System/Volumes/Data" if sys.platform == "darwin" else "/")
    return st.f_bavail * st.f_frsize / 1e9


def redact(text: str, extra: list[str]) -> str:
    for s in extra:
        if s:
            text = text.replace(s, "[REDACTED]")
    for p in SECRET_PATTERNS:
        text = p.sub("[REDACTED]", text)
    return text


def git_sha() -> str:
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()


# ---- Renda condition setup ---------------------------------------------------------------------------

def renda_setup(root: Path, api_key: str, api_url: str) -> dict:
    """Shims for the unpublished CLIs, a skills-only plugin dir (Claude), and an MCP config with the local build."""
    shims = root / "_renda" / "bin"
    shims.mkdir(parents=True, exist_ok=True)
    real_npx = shutil.which("npx") or "npx"
    real_uvx = shutil.which("uvx") or "uvx"
    node_cli = REPO / "cli" / "dist" / "bin.js"
    node_mcp = REPO / "mcp" / "dist" / "bin.js"
    (shims / "renda").write_text(f'#!/bin/sh\nexec node "{node_cli}" "$@"\n')
    (shims / "rendahq").write_text(f'#!/bin/sh\nexec node "{node_cli}" "$@"\n')
    # npx/uvx: route the not-yet-published Renda packages to the local builds, pass everything else through.
    (shims / "npx").write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do case "$a" in -*) ;; *) pkg="$a"; break;; esac; done\n'
        'case "$pkg" in\n'
        f'  @rendahq/cli*) shift_to=1; while [ "$1" != "$pkg" ]; do shift; done; shift; exec node "{node_cli}" "$@";;\n'
        f'  @rendahq/mcp*) while [ "$1" != "$pkg" ]; do shift; done; shift; exec node "{node_mcp}" "$@";;\n'
        f'  *) exec "{real_npx}" "$@";;\n'
        "esac\n"
    )
    (shims / "uvx").write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "renda" ]; then shift; exec uv run --quiet --project "{REPO / "core"}" renda "$@"; fi\n'
        f'exec "{real_uvx}" "$@"\n'
    )
    for f in shims.iterdir():
        f.chmod(0o755)

    plugin = root / "_renda" / "plugin"
    if plugin.exists():
        shutil.rmtree(plugin)
    (plugin / ".claude-plugin").mkdir(parents=True)
    src_manifest = json.loads((REPO / "skills" / ".claude-plugin" / "plugin.json").read_text())
    manifest = {k: v for k, v in src_manifest.items() if k not in ("mcpServers",)}
    manifest["skills"] = "./skills"
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest, indent=2))
    skill_dirs = []
    for d in sorted((REPO / "skills").iterdir()):
        if (d / "SKILL.md").exists():
            shutil.copytree(d, plugin / "skills" / d.name)
            skill_dirs.append(plugin / "skills" / d.name)

    mcp = root / "_renda" / "mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"renda": {
        "command": "node", "args": [str(node_mcp)], "env": {"RENDA_API_KEY": api_key, "RENDA_API_URL": api_url},
    }}}))
    mcp.chmod(0o600)
    return {"shims": shims, "plugin": plugin, "skills": skill_dirs, "mcp": mcp}


# ---- one run -----------------------------------------------------------------------------------------

def build_command(agent: str, condition: str, prompt: str, env_setup: dict | None, empty_mcp: Path) -> list[str]:
    model = AGENTS[agent]["model"]
    if agent == "claude":
        cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", model,
               "--dangerously-skip-permissions", "--setting-sources", "project", "--strict-mcp-config"]
        if condition == "renda":
            cmd += ["--mcp-config", str(env_setup["mcp"]), "--plugin-dir", str(env_setup["plugin"])]
        else:
            cmd += ["--mcp-config", str(empty_mcp)]
        return cmd
    cmd = ["pi", "-p", "--mode", "json", "--no-session", "--no-extensions", "--no-context-files", "--no-skills",
           "--model", model]
    if condition == "renda":
        for d in env_setup["skills"]:
            cmd += ["--skill", str(d)]
    return cmd + [prompt]


def parse_claude(lines: list[str]) -> dict:
    out = {"tool_calls": 0, "tokens": None, "agent_cost_usd": None, "model": None, "turns": None, "final": ""}
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "system" and d.get("subtype") == "init":
            out["model"] = d.get("model")
            out["init"] = {"tools": len(d.get("tools") or []), "mcp_servers": [s.get("name") for s in d.get("mcp_servers") or []],
                           "skills": d.get("skills") or d.get("slash_commands") or []}
        elif d.get("type") == "assistant":
            out["tool_calls"] += sum(1 for c in d.get("message", {}).get("content", []) if c.get("type") == "tool_use")
        elif d.get("type") == "result":
            u = d.get("usage") or {}
            out["tokens"] = {k: u.get(k) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
            out["agent_cost_usd"] = d.get("total_cost_usd")
            out["turns"] = d.get("num_turns")
            out["final"] = (d.get("result") or "")[-1500:]
            out["is_error"] = d.get("is_error")
    return out


def parse_pi(lines: list[str]) -> dict:
    out = {"tool_calls": 0, "tokens": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}, "agent_cost_usd": 0.0,
           "model": None, "turns": 0, "final": ""}
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "message_end" and d.get("message", {}).get("role") == "assistant":
            m = d["message"]
            out["model"] = m.get("model") or out["model"]
            out["turns"] += 1
            out["tool_calls"] += sum(1 for c in m.get("content", []) if c.get("type") in ("toolCall", "tool_use"))
            u = m.get("usage") or {}
            for k in out["tokens"]:
                out["tokens"][k] += u.get(k) or 0
            out["agent_cost_usd"] += (u.get("cost") or {}).get("total") or 0
            texts = [c.get("text", "") for c in m.get("content", []) if c.get("type") == "text"]
            if texts:
                out["final"] = texts[-1][-1500:]
            if m.get("stopReason") == "error":
                out["error"] = (m.get("errorMessage") or "")[:300]
    out["agent_cost_usd"] = round(out["agent_cost_usd"], 4)
    return out


def run_one(task: dict, agent: str, condition: str, root: Path, renda_env: dict | None, secrets: list[str]) -> dict:
    run_id = f"{task['id']}-{agent}-{condition}"
    rdir = root / run_id
    if rdir.exists():
        shutil.rmtree(rdir)
    (rdir / "out").mkdir(parents=True)
    nonce = f"{run_id}-{int(time.time())}"
    for src, dst in task["inputs"].items():
        unique_copy(REPO / src, rdir / dst, nonce)
    prompt = f"{task['prompt']} {TASKS['suffix']}"
    empty = root / "_empty_mcp.json"
    empty.write_text('{"mcpServers": {}}')

    env = {k: v for k, v in os.environ.items() if not k.startswith("RENDA_")}
    env["HOME"] = os.environ["HOME"]
    if condition == "renda":
        env["PATH"] = f"{renda_env['shims']}:{env['PATH']}"
        env["RENDA_API_KEY"] = secrets[0]
        env["RENDA_API_URL"] = os.environ.get("RENDA_API_URL", "https://renda.vochsel.com")
        env["XDG_CONFIG_HOME"] = str(rdir / ".config")  # isolate the CLIs' credentials file per run

    cmd = build_command(agent, condition, prompt, renda_env, empty)
    started = time.time()
    t0 = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=rdir, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            start_new_session=True)
    killed = {"why": None}

    def watchdog():
        while proc.poll() is None:
            if time.monotonic() - t0 > TIMEOUT_S:
                killed["why"] = "timeout"
            elif free_gb() < MIN_FREE_GB:
                killed["why"] = "disk_guard"
            if killed["why"]:
                try:
                    os.killpg(proc.pid, 9)
                except ProcessLookupError:
                    pass
                return
            time.sleep(5)

    threading.Thread(target=watchdog, daemon=True).start()
    stdout, stderr = proc.communicate()
    wall = round(time.monotonic() - t0, 1)
    lines = stdout.splitlines()
    (rdir / "transcript.jsonl").write_text(redact(stdout, secrets))
    (rdir / "stderr.txt").write_text(redact(stderr[-20000:], secrets))
    parsed = parse_claude(lines) if agent == "claude" else parse_pi(lines)

    outputs = task["output"] if isinstance(task["output"], list) else [task["output"]]
    found = [o for o in outputs if (rdir / o).exists() and (rdir / o).stat().st_size > 0]
    jobs = created_jobs(stdout, started)
    rec = {
        "run": run_id, "task": task["id"], "agent": agent, "condition": condition,
        "model": parsed.get("model") or AGENTS[agent]["model"],
        "started_at": dt.datetime.fromtimestamp(started, dt.timezone.utc).isoformat(timespec="seconds"),
        "wall_seconds": wall, "exit_code": proc.returncode, "killed": killed["why"],
        "tokens": parsed.get("tokens"), "agent_cost_usd": parsed.get("agent_cost_usd"),
        "tool_calls": parsed.get("tool_calls"), "turns": parsed.get("turns"),
        "output_path": found[0] if found else None, "renda_jobs": jobs,
        "agent_error": parsed.get("error") or (parsed.get("final") if parsed.get("is_error") else None),
        "final_message": redact(parsed.get("final") or "", secrets)[-600:],
    }
    if "init" in parsed:
        rec["isolation"] = parsed["init"]
    (rdir / "record.json").write_text(json.dumps(rec, indent=2))
    print(f"[{run_id}] {wall}s exit={proc.returncode} killed={killed['why']} output={rec['output_path']} tools={rec['tool_calls']}", flush=True)
    return rec


def created_jobs(stdout: str, started: float) -> list[str]:
    """Job ids this run created. Transcripts can also list older jobs (e.g. `renda jobs`), so keep only
    ids whose job was created after the run started (checked against the API with the bench key)."""
    ids = sorted(set(re.findall(r"job_[a-f0-9]{20}", stdout)))
    key = os.environ.get("BENCH_RENDA_API_KEY")
    if not ids or not key:
        return ids
    import urllib.request

    api = os.environ.get("RENDA_API_URL", "https://renda.vochsel.com")
    keep = []
    for j in ids:
        try:
            req = urllib.request.Request(f"{api}/api/v1/jobs/{j}", headers={"Authorization": f"Bearer {key}"})
            created = json.load(urllib.request.urlopen(req, timeout=20)).get("createdAt", "")
            if created and dt.datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp() >= started - 5:
                keep.append(j)
        except Exception:  # noqa: BLE001 — unknown ids (other orgs, typos) aren't this run's
            pass
    return keep


def unique_copy(src: Path, dst: Path, nonce: str) -> None:
    """Copy an input, making its bytes unique per run so Renda's result cache can't serve a $0 hit.
    GLB: nonce in asset.extras of the JSON chunk. STEP: a comment after DATA;. Geometry is untouched."""
    import struct

    data = src.read_bytes()
    if data[:4] == b"glTF":
        jlen = struct.unpack_from("<I", data, 12)[0]
        meta = json.loads(data[20:20 + jlen])
        meta.setdefault("asset", {}).setdefault("extras", {})["benchNonce"] = nonce
        j = json.dumps(meta, separators=(",", ":")).encode()
        j += b" " * (-len(j) % 4)
        rest = data[20 + jlen:]
        body = struct.pack("<II", len(j), 0x4E4F534A) + j + rest
        dst.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)
    elif data.lstrip().startswith(b"ISO-10303-21") and b"\nDATA;" in data:
        dst.write_bytes(data.replace(b"\nDATA;", f"\nDATA;\n/* bench nonce {nonce} */".encode(), 1))
    else:
        shutil.copy(src, dst)


def brew_list() -> set[str]:
    if not shutil.which("brew"):
        return set()
    r = subprocess.run(["brew", "list", "-1"], capture_output=True, text=True)
    return set(r.stdout.split())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", default="claude,pi")
    ap.add_argument("--conditions", default="bare,renda")
    ap.add_argument("--tasks", default=",".join(t["id"] for t in TASKS["tasks"]))
    ap.add_argument("--workdir", required=True, help="scratch dir outside the repo for run directories")
    ap.add_argument("--records", required=True, help="append raw run records (JSONL) here; score.py turns them into results")
    ap.add_argument("--parallel", type=int, default=2)
    args = ap.parse_args()

    root = Path(args.workdir).resolve()
    if REPO in root.parents or root == REPO:
        sys.exit("--workdir must be outside the repo")
    root.mkdir(parents=True, exist_ok=True)
    api_key = os.environ.get("BENCH_RENDA_API_KEY", "")
    conditions = args.conditions.split(",")
    if "renda" in conditions and not api_key:
        sys.exit("set BENCH_RENDA_API_KEY for the renda condition")
    renda_env = renda_setup(root, api_key, os.environ.get("RENDA_API_URL", "https://renda.vochsel.com")) if "renda" in conditions else None

    wanted = set(args.tasks.split(","))
    runs = [(t, a, c) for t in TASKS["tasks"] if t["id"] in wanted for a in args.agents.split(",") for c in conditions]
    before = brew_list()
    tmp_before = set(os.listdir("/tmp"))
    print(f"{len(runs)} runs, free disk {free_gb():.1f} GB, guard {MIN_FREE_GB} GB", flush=True)
    recs_path = Path(args.records)
    recs_path.parent.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futs = [pool.submit(run_one, t, a, c, root, renda_env, [api_key]) for t, a, c in runs]
        for f in cf.as_completed(futs):
            rec = f.result()
            rec["git_sha"] = git_sha()
            with recs_path.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
    # Bare agents often write scripts, venvs and frames to /tmp: remove new entries a transcript mentions.
    transcripts = " ".join((root / f"{t['id']}-{a}-{c}" / "transcript.jsonl").read_text(errors="ignore")
                           for t, a, c in runs if (root / f"{t['id']}-{a}-{c}" / "transcript.jsonl").exists())
    for name in sorted(set(os.listdir("/tmp")) - tmp_before):
        if f"/tmp/{name}" in transcripts:
            p = Path("/tmp") / name
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
            print("removed agent leftover", p, flush=True)
    added = sorted(brew_list() - before)
    if added:
        print("brew packages installed by agents (uninstalling):", added, flush=True)
        subprocess.run(["brew", "uninstall", "--ignore-dependencies", *added])
    (root / "_brew_added.json").write_text(json.dumps(added))


if __name__ == "__main__":
    main()
