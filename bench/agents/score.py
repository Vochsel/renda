"""Score raw run records and write the committed results (JSONL + Markdown) and page thumbnails.

    uv run --no-project --with pillow --with imageio-ffmpeg --with trimesh python bench/agents/score.py \
        --records /tmp/renda-agent-eval/records.jsonl --workdir /tmp/renda-agent-eval \
        --judgments bench/agents/judgments/<date>.json

Validity is checked automatically (decodes / parses / loads). Whether the output actually does the task
("is this a product shot of the model?") is a human judgement recorded in the judgments file — the scorer
prints thumbnails' paths so a reviewer can look, and refuses to mark success without a verdict.
Renda billed cost per run comes from the production usage records of the jobs the run created
(needs DATABASE_URL; read-only).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRICE_PER_MIN = {"cpu": 0.015, "gpu-l4": 0.035, "gpu-l40s": 0.075}
THUMBS = REPO / "web" / "public" / "benchmarks" / "agents"
REFRESH = False
CORE_PY =os.environ.get("BENCH_CORE_PYTHON", str(REPO / "core" / ".venv" / "bin" / "python"))


def ffmpeg() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def check_image(p: Path) -> dict:
    from PIL import Image, ImageStat

    im = Image.open(p).convert("RGB")
    std = sum(ImageStat.Stat(im).stddev) / 3
    return {"valid": std > 2, "width": im.width, "height": im.height, "note": "blank image" if std <= 2 else ""}


def check_video(p: Path) -> dict:
    r = subprocess.run([ffmpeg(), "-i", str(p)], capture_output=True, text=True)
    import re

    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    return {"valid": dur > 0.5, "duration_s": round(dur, 2), "note": "" if 2.0 <= dur <= 4.5 else f"duration {dur:.1f}s (asked for 3 s)"}


def check_glb(p: Path) -> dict:
    import struct

    data = p.read_bytes()
    if data[:4] != b"glTF":
        return {"valid": False, "note": "not a GLB"}
    jlen = struct.unpack_from("<I", data, 12)[0]
    meta = json.loads(data[20:20 + jlen])
    n_img = len(meta.get("images", []))
    exts = sorted(set(meta.get("extensionsUsed", [])))
    return {"valid": bool(meta.get("meshes")), "meshes": len(meta.get("meshes", [])), "images": n_img, "extensions": exts,
            "note": "" if n_img else "no textures — lighting not baked into textures"}


def check_splat(p: Path) -> dict:
    r = subprocess.run([CORE_PY, "-c", (
        "import sys,json;from renda.ir.loaders import load_stage;s=load_stage(sys.argv[1]);"
        "print(json.dumps({'splats':sum(len(x.means) for x in s.splats),'meshes':len(s.meshes)}))")
        , str(p)], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(REPO / "core")})
    try:
        info = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"valid": False, "note": "not a loadable Gaussian splat: " + r.stderr.strip()[-200:]}
    ok = info["splats"] > 1000
    return {"valid": ok, **info, "note": "" if ok else f"only {info['splats']} splats" if info["splats"] else "a point cloud/mesh, not a Gaussian splat"}


def thumb(rec: dict, src: Path, kind: str, workdir: Path) -> str | None:
    THUMBS.mkdir(parents=True, exist_ok=True)
    out = THUMBS / f"{rec['task']}-{rec['agent']}-{rec['condition']}.webp"
    if out.exists() and not REFRESH:  # e.g. bake thumbnails made with preview_glb.py
        return f"/benchmarks/agents/{out.name}"
    tmp = workdir / "_thumbs" / (out.stem + ".png")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        if kind == "image":
            subprocess.run([ffmpeg(), "-loglevel", "error", "-y", "-i", str(src), str(tmp)], check=True)
        elif kind == "video":
            subprocess.run([ffmpeg(), "-loglevel", "error", "-y", "-ss", "1.5", "-i", str(src), "-frames:v", "1", str(tmp)], check=True)
        else:  # glb / splat: a quick local preview with Renda's own engines (CPU), same camera for every run
            engine = "mitsuba"  # renders meshes and splats on CPU; gsplat needs CUDA
            d = workdir / "_thumbs" / out.stem
            subprocess.run([CORE_PY, "-m", "renda.cli", "render", str(src), "--engine", engine, "--res", "480x360",
                            "--quality", "draft", "-o", str(d), "--json"], capture_output=True, check=True,
                           env={**os.environ, "PYTHONPATH": str(REPO / "core")})
            pngs = sorted(d.glob("*.png"))
            if not pngs:
                return None
            tmp = pngs[0]
        from PIL import Image

        im = Image.open(tmp).convert("RGB")
        im.thumbnail((480, 360))
        im.save(out, "WEBP", quality=82)
        return f"/benchmarks/agents/{out.name}"
    except Exception as e:  # noqa: BLE001 — a missing thumbnail must not lose the record
        print("thumb failed", rec["run"], e)
        return None


def renda_costs(job_ids: list[str]) -> dict[str, dict]:
    """Per-job billed seconds by hardware from production usage records (read-only)."""
    if not job_ids or not os.environ.get("DATABASE_URL"):
        return {}
    script = HERE / "renda_cost.mjs"
    r = subprocess.run(["node", str(script), json.dumps(job_ids)], capture_output=True, text=True, cwd=REPO / "web")
    if r.returncode:
        print("renda cost lookup failed:", r.stderr[-300:])
        return {}
    return json.loads(r.stdout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--records", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--judgments", required=True, help="JSON {run_id: {verdict: success|partial|fail, notes}}")
    ap.add_argument("--no-thumbs", action="store_true")
    ap.add_argument("--refresh-thumbs", action="store_true", help="regenerate thumbnails that already exist")
    args = ap.parse_args()
    global REFRESH
    REFRESH = args.refresh_thumbs
    tasks = {t["id"]: t for t in json.loads((HERE / "tasks.json").read_text())["tasks"]}
    workdir = Path(args.workdir)
    recs = [json.loads(l) for l in Path(args.records).read_text().splitlines() if l.strip()]
    judg = json.loads(Path(args.judgments).read_text()) if Path(args.judgments).exists() else {}
    costs = renda_costs(sorted({j for r in recs for j in r.get("renda_jobs", [])}))

    out = []
    for r in sorted(recs, key=lambda x: (list(tasks).index(x["task"]), x["agent"], x["condition"])):
        kind = tasks[r["task"]]["kind"]
        src = workdir / r["run"] / r["output_path"] if r.get("output_path") else None
        check = {"valid": False, "note": "no output file"}
        if src and src.exists():
            check = {"image": check_image, "video": check_video, "glb": check_glb, "splat": check_splat}[kind](src)
        jobs = r.get("renda_jobs", [])
        billed = {}
        for j in jobs:
            for hw, s in (costs.get(j) or {}).items():
                billed[hw] = billed.get(hw, 0) + s
        renda_cost = round(sum(s / 60 * PRICE_PER_MIN.get(hw, 0.035) for hw, s in billed.items()), 4) if jobs else 0.0
        j = judg.get(r["run"], {})
        verdict = j.get("verdict") or ("fail" if not check["valid"] else "unjudged")
        rec = {**r, "check": check, "renda_billed_seconds": {k: round(v, 1) for k, v in billed.items()},
               "renda_cost_usd": renda_cost, "verdict": verdict, "score": {"success": 1, "partial": 0.5}.get(verdict, 0),
               "notes": j.get("notes", check.get("note", ""))}
        rec.pop("final_message", None)
        if not args.no_thumbs and src and src.exists() and check["valid"]:
            rec["thumb"] = thumb(r, src, kind, workdir)
        out.append(rec)

    date = dt.date.today().isoformat()
    sha = out[0].get("git_sha", "nosha") if out else "nosha"
    res_dir = HERE / "results"
    res_dir.mkdir(exist_ok=True)
    jsonl = res_dir / f"{date}-{sha}.jsonl"
    jsonl.write_text("".join(json.dumps(x) + "\n" for x in out))
    (res_dir / f"{date}-{sha}.md").write_text(markdown(out, tasks, date, sha))
    unj = [x["run"] for x in out if x["verdict"] == "unjudged"]
    print(f"wrote {jsonl.relative_to(REPO)}; unjudged: {unj or 'none'}")


def markdown(rows: list[dict], tasks: dict, date: str, sha: str) -> str:
    def cell(r):
        return {"success": "✅", "partial": "🟡", "fail": "❌"}.get(r["verdict"], "?")

    lines = [f"# Agent benchmark — {date} ({sha})", "",
             "Each task run once per agent × condition, unattended, from an empty directory with only the input file.",
             "`bare` = no Renda (shell, file tools, network; Blender is installed on the machine). `renda` = Renda skills + MCP/CLI.", "",
             "| Task | Agent | Condition | Result | Wall | Tool calls | Agent cost | Renda cost | Notes |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {tasks[r['task']]['label']} | {r['agent']} ({r['model']}) | {r['condition']} | {cell(r)} {r['verdict']} | "
                     f"{r['wall_seconds']:.0f} s{' (' + r['killed'] + ')' if r.get('killed') else ''} | {r.get('tool_calls')} | "
                     f"${(r.get('agent_cost_usd') or 0):.3f} | ${r['renda_cost_usd']:.4f} | {r.get('notes') or ''} |")
    lines += ["", "## Totals", ""]
    for agent in sorted({r["agent"] for r in rows}):
        for cond in ("bare", "renda"):
            g = [r for r in rows if r["agent"] == agent and r["condition"] == cond]
            if not g:
                continue
            ok = sum(r["score"] for r in g)
            walls = sorted(r["wall_seconds"] for r in g)
            lines.append(f"- **{agent} / {cond}**: {ok:g}/{len(g)} tasks, median wall {walls[len(walls) // 2]:.0f} s, "
                         f"agent ${sum(r.get('agent_cost_usd') or 0 for r in g):.2f}, Renda ${sum(r['renda_cost_usd'] for r in g):.3f}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
