"""HyperFrames video jobs from the CLI: zip a project, resolve variables, submit.

Mirrors @rendahq/mcp's resolveVideoVariables so both CLIs behave the same:
- ``job:<id>``           → that job's video (or image) output URL — chain a 3D render into a video
- local 3D scene file    → renders a cloud turntable mp4 first, then uses its URL
- local media file       → uploaded, becomes asset://<sha256> (the worker copies it into the project)
- anything else          → unchanged (text, colours, numbers, https URLs, project-relative paths)
"""

from __future__ import annotations

import io
import json
import re
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..spec import EXTENSION_FORMATS
from .client import CloudError, RendaClient

MEDIA_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".gif", ".png", ".jpg", ".jpeg", ".webp", ".avif", ".svg",
              ".mp3", ".wav", ".m4a", ".aac", ".ogg"}
QUALITY_ALIASES = {"draft": "draft", "looks": "looks", "standard": "looks", "delivery": "delivery", "final": "delivery",
                   "high": "delivery"}
_SKIP = {".git", "node_modules", "renders", ".DS_Store", "__MACOSX"}


def hyperframes_quality(q: str | None) -> str | None:
    if q is None:
        return None
    if q not in QUALITY_ALIASES:
        raise CloudError("bad_option", f"--quality must be draft, looks or delivery (got {q!r})")
    return QUALITY_ALIASES[q]


def parse_vars(pairs: list[str] | None, vars_json: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if vars_json:
        parsed = json.loads(vars_json)
        if not isinstance(parsed, dict):
            raise CloudError("bad_option", "--vars must be a JSON object")
        out.update(parsed)
    for p in pairs or []:
        key, sep, value = p.partition("=")
        if not sep or not key:
            raise CloudError("bad_option", f"--var expects key=value (got {p!r})")
        out[key] = value
    return out


def zip_project(project: Path) -> Path:
    """Deterministic zip (sorted, fixed timestamps) so an unchanged project dedupes on upload."""
    project = Path(project)
    if not (project / "index.html").exists() and not (project / "hyperframes.json").exists():
        raise CloudError("not_a_project", f"{project} has no index.html or hyperframes.json",
                         "point at the HyperFrames project directory")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(project.rglob("*")):
            rel = p.relative_to(project)
            if p.is_file() and not any(part in _SKIP for part in rel.parts):
                info = zipfile.ZipInfo(rel.as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, p.read_bytes())
    dest = Path(tempfile.mkdtemp(prefix="renda-video-")) / f"{project.resolve().name or 'project'}.zip"
    dest.write_bytes(buf.getvalue())
    return dest


def _clip_url(job: dict) -> str:
    outs = job.get("outputs") or []
    o = next((o for o in outs if o.get("contentType", "").startswith("video/")), None) or \
        next((o for o in outs if o.get("contentType", "").startswith("image/")), None)
    if not o:
        raise CloudError("no_output", f"job {job['id']} has no video or image output to use")
    return o["url"]


def resolve_variables(client: RendaClient, variables: dict[str, Any], base_dir: Path,
                      wait: Callable[[dict], dict], turntable_seconds: float = 4) -> tuple[dict[str, Any], list[dict]]:
    """Return (variables the worker can fetch, info about jobs rendered/reused for them)."""
    out, jobs = dict(variables), []

    def finished(job: dict) -> dict:
        if job.get("status") not in ("succeeded", "failed", "canceled"):
            job = wait(job)
        if job.get("status") != "succeeded":
            raise CloudError("input_job_failed", f"job {job['id']} {job.get('status')}: {job.get('error') or ''}".strip())
        return job

    for key, value in variables.items():
        if not isinstance(value, str):
            continue
        m = re.fullmatch(r"job:([A-Za-z0-9_-]+)", value)
        if m:
            job = finished(client.get_job(m.group(1)))
            out[key] = _clip_url(job)
            jobs.append({"variable": key, "jobId": job["id"], "url": out[key]})
            continue
        if re.match(r"^(https?|asset|data):", value):
            continue
        path = (base_dir / value).expanduser()
        if not path.is_file():
            continue  # project-relative path or plain text
        ext = path.suffix.lower()
        fmt = None if ext == ".json" else EXTENSION_FORMATS.get(ext)
        if fmt:
            uri = client.upload(path)
            asset = {"uri": uri, **({"format": fmt} if fmt == "splat" else {})}
            pkg = {"version": 1, "name": f"{key}-turntable", "assets": {"scene": asset}, "scene": {"root": "scene"},
                   "environment": {"hdri": "studio", "background": "#111827"},
                   "render": {"resolution": [1080, 1080], "quality": "draft"},
                   "animation": {"frames": [1, round(turntable_seconds * 30)], "fps": 30, "turntable": {"revolutions": 1}},
                   "output": {"format": "mp4"}}
            job = finished(client.create_job(pkg))
            out[key] = _clip_url(job)
            jobs.append({"variable": key, "jobId": job["id"], "url": out[key]})
        elif ext in MEDIA_EXTS:
            out[key] = client.upload(path)
    return out, jobs
