"""Wall-time benchmark against an isolated worker app (never production).

Uploads inputs to R2 under bench/<tag>/, calls the app's `orchestrate` directly and prints wall time,
billed seconds per hardware (from the job's usage events, captured by a no-op callback) and phase timings.

    RENDA_ENV=../web/.env.local uv run --with boto3 python scripts/bench_wall.py --app renda-worker-w --tag w
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import boto3
import modal

ROOT = Path(__file__).resolve().parents[2]
BENCH = {
    "stl-draft": {"scene": {"root": "testdata/scenes/scene.stl"}, "render": {"quality": "draft", "resolution": [960, 720]}},
    "stl-draft-workbench": {"scene": {"root": "testdata/scenes/scene.stl"},
                            "render": {"engine": "blender-workbench", "quality": "draft", "resolution": [960, 720]}},
    "glb-cycles": {"scene": {"root": "testdata/scenes/scene.glb"},
                   "render": {"engine": "blender-cycles", "quality": "standard", "resolution": [960, 720]}},
    "turntable-24": {"scene": {"root": "testdata/scenes/scene.glb"}, "render": {"engine": "blender-eevee", "resolution": [640, 640]},
                     "animation": {"frames": [1, 24], "fps": 24, "turntable": {"revolutions": 1}}, "output": {"format": "mp4"}},
    "gsplat-still": {"scene": {"root": "testdata/scenes/splats/garden.spz"}, "render": {"engine": "gsplat", "resolution": [960, 720]}},
}


def _env() -> dict[str, str]:
    path = Path(os.environ.get("RENDA_ENV", ROOT / "web/.env.local"))
    return dict(ln.strip().split("=", 1) for ln in path.read_text().splitlines() if "=" in ln and not ln.startswith("#"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--tag", default="w")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--repeat", type=int, default=1)
    args = ap.parse_args()
    assert args.app != "renda-worker", "never benchmark production"
    env = _env()
    s3 = boto3.client("s3", endpoint_url=f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
                      aws_access_key_id=env["R2_ACCESS_KEY_ID"], aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"], region_name="auto")
    bucket = env.get("R2_BUCKET", "renda").strip('"')
    orchestrate = modal.Function.from_name(args.app, "orchestrate")
    rows = []
    for name, spec in BENCH.items():
        if args.only and name not in args.only:
            continue
        for rep in range(args.repeat):
            pkg = json.loads(json.dumps(spec))
            src = ROOT / pkg["scene"]["root"]
            sha = hashlib.sha256(src.read_bytes()).hexdigest()
            key = f"bench/{args.tag}/in/{sha}"
            s3.upload_file(str(src), bucket, key)
            uri = f"asset://{sha}"
            pkg["assets"] = {"scene": {"uri": uri, "format": "splat" if src.suffix in (".spz", ".splat") else None}}
            pkg["assets"]["scene"] = {k: v for k, v in pkg["assets"]["scene"].items() if v}
            pkg["scene"]["root"] = "scene"
            pkg.setdefault("render", {})["seed"] = int(time.time()) % 100000 + rep  # defeat the result cache
            job_id = f"bench_{args.tag}_{name}_{uuid.uuid4().hex[:6]}"
            body = {"jobId": job_id, "package": pkg, "priority": "standard",
                    "inputs": {uri: s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600)},
                    "outputPrefix": f"outputs/bench-{args.tag}/{job_id}/", "callbackUrl": "https://renda.vochsel.com/api/v1/internal/worker/events"}
            t = time.time()
            res = orchestrate.remote(body)
            wall = round(time.time() - t, 1)
            row = {"bench": name, "wall_s": wall, "status": res.get("status"), "engine": res.get("engine"),
                   "timings": res.get("timings"), "error": res.get("error")}
            rows.append(row)
            print(json.dumps(row), flush=True)
    print("SUMMARY", json.dumps([{k: r[k] for k in ("bench", "wall_s", "status")} for r in rows]))


if __name__ == "__main__":
    main()
