#!/usr/bin/env python3
"""Submit a test job.

Remote (against `modal serve` / deployed worker):
    MODAL_SUBMIT_URL=https://<ws>--renda-submit-dev.modal.run RENDA_WORKER_TOKEN=... \
    python scripts/submit_local.py model.glb --callback https://<tunnel>/api/v1/internal/worker/events

    The worker needs a presigned URL for every asset, so remote mode takes https:// asset URLs
    (--asset-url) or a package whose assets are already https://. Use the web app for real uploads.

Local (no Modal, no R2 — runs the same pipeline in-process with the installed `renda` core):
    python scripts/submit_local.py model.glb --local --out ./renders
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from renda_worker.events import verify  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_job(args) -> dict:
    job_id = args.job_id or f"local_{uuid.uuid4().hex[:10]}"
    src = Path(args.file)
    inputs: dict[str, str] = {}
    if src.name.endswith(".renda.json"):
        package = json.loads(src.read_text())
    else:
        package = {"version": 1, "name": src.stem, "assets": {"scene": {"uri": "./" + src.name}}, "scene": {"root": "scene"}}
        if args.format:
            package["output"] = {"format": args.format}
            if args.format in ("mp4", "webm", "gif", "mov"):
                package["animation"] = {"frames": [1, args.frames], "fps": 30, "turntable": {"revolutions": 1}}
    for asset in package.get("assets", {}).values():
        uri = asset["uri"]
        if uri.startswith(("asset://", "https://")):
            continue
        local = (src.parent / uri).resolve()
        sha = _sha(local)
        asset["uri"] = f"asset://{sha}"
        inputs[asset["uri"]] = args.asset_url or local.as_uri()
        asset["_local"] = str(local)
    return {"jobId": job_id, "kind": "scene", "package": package, "priority": args.priority, "inputs": inputs,
            "callbackUrl": args.callback or "https://localhost.invalid/events"}


def run_local(job: dict, out: Path) -> int:
    from renda_worker.core_api import RendaCore
    from renda_worker.events import EventSink
    from renda_worker.models import JobRequest
    from renda_worker.pipeline import Deps, run_chunk, run_encode, run_scene_job
    from renda_worker.storage import DirStorage

    local_files = {}
    for asset in job["package"].get("assets", {}).values():
        if "_local" in asset:
            local_files[job["inputs"][asset["uri"]]] = asset.pop("_local")
    for k in job["inputs"]:
        job["inputs"][k] = "https://local.invalid/" + k.removeprefix("asset://")

    def printer(url, body, headers):
        print("event", body.decode())
        return 200

    def download(url, dest):
        sha = url.rsplit("/", 1)[-1]
        src = next(p for u, p in local_files.items() if _sha(Path(p)) == sha)
        shutil.copyfile(src, dest)

    req = JobRequest.model_validate(job)
    deps = Deps(core=RendaCore(), storage=DirStorage(out), events=EventSink(req.jobId, str(req.callbackUrl), "local", poster=printer),
                asset_root=out / ".assets", download=download, work_root=out / ".work")
    deps.run_chunks = lambda runner, tasks: [run_chunk(t, deps) for t in tasks]
    deps.run_encode = lambda task: run_encode(task, deps)
    result = run_scene_job(req, deps)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "succeeded" else 1


def run_remote(job: dict) -> int:
    import requests

    for asset in job["package"].get("assets", {}).values():
        asset.pop("_local", None)
    bad = [u for u in job["inputs"].values() if not u.startswith("https://")]
    if bad:
        sys.exit("remote mode needs https asset urls: pass --asset-url (e.g. a presigned R2 GET) or use --local")
    r = requests.post(os.environ["MODAL_SUBMIT_URL"], json=job,
                      headers={"authorization": f"Bearer {os.environ['RENDA_WORKER_TOKEN']}"}, timeout=60)
    print(r.status_code, r.text)
    return 0 if r.ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="scene file or *.renda.json")
    ap.add_argument("--local", action="store_true", help="run in-process without Modal/R2")
    ap.add_argument("--out", type=Path, default=Path("renders"))
    ap.add_argument("--format", help="png|jpg|webp|exr|mp4|webm|gif|mov")
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--priority", default="standard", choices=["economy", "standard", "rush"])
    ap.add_argument("--callback")
    ap.add_argument("--asset-url")
    ap.add_argument("--job-id")
    ap.add_argument("--verify", nargs=2, metavar=("BODY_FILE", "SIGNATURE"), help="check an event signature with RENDA_WORKER_SECRET")
    args = ap.parse_args()
    if args.verify:
        ok = verify(Path(args.verify[0]).read_bytes(), os.environ["RENDA_WORKER_SECRET"], args.verify[1])
        print("valid" if ok else "INVALID")
        return 0 if ok else 1
    job = build_job(args)
    return run_local(job, args.out.resolve()) if args.local else run_remote(job)


if __name__ == "__main__":
    raise SystemExit(main())
