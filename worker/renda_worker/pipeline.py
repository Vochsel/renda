"""Job pipelines with injected dependencies. app.py wires these to Modal; tests wire them to fakes.

Billing rules (ARCHITECTURE.md §4): every container that does work for a job reports its own
wall seconds from function entry as one `usage` event (idempotent per jobId:chunk). The
orchestrator bills only its active phases (fetch/plan/finalize), never the time it spends
waiting on render chunks. Cache hits bill nothing.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import dispatch, hardware as hw, media
from .assets import AssetCache, name_hints
from .core_api import Core, get
from .dispatch import ChunkResult, ChunkTask
from .events import EventSink
from .hyperframes import build_render_command
from .models import HyperframesPackage, JobRequest
from .storage import Storage, content_type

log = logging.getLogger("renda.pipeline")

# Bump when worker/core output changes so stale cache entries are ignored.
CACHE_NAMESPACE = "v1"


class Canceled(Exception):
    pass


class Stopwatch:
    """Accumulates active seconds across start/stop spans."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.total = 0.0
        self._t: float | None = None

    def __enter__(self):
        self._t = self.clock()
        return self

    def __exit__(self, *exc):
        self.total += self.clock() - (self._t or self.clock())
        self._t = None

    @contextlib.contextmanager
    def paused(self):
        """Exclude a span (e.g. blocking on another billed container) from the active total."""
        self.__exit__()
        try:
            yield
        finally:
            self.__enter__()


@dataclass
class EncodeTask:
    job_id: str
    frame_keys: list[str]
    fps: float
    output: Any  # package output config (dict)
    output_key: str
    keep_frames_prefix: str | None
    callback_url: str


@dataclass
class Deps:
    core: Core
    storage: Storage
    events: EventSink
    asset_root: Path
    is_canceled: Callable[[], bool] = lambda: False
    # runner name, tasks -> ChunkResults in completion order
    run_chunks: Callable[[str, list[ChunkTask]], Iterable[ChunkResult]] | None = None
    run_encode: Callable[[EncodeTask], dict[str, Any]] | None = None
    download: Callable[[str, Path], None] | None = None
    on_new_asset: Callable[[], None] | None = None
    clock: Callable[[], float] = time.monotonic
    work_root: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "renda-work")


def _asset_cache(deps: Deps, job_package: dict, inputs: dict) -> AssetCache:
    kw = {"download": deps.download} if deps.download else {}
    return AssetCache(deps.asset_root, inputs, name_hints(job_package), on_new=deps.on_new_asset, **kw)


def _check_cancel(deps: Deps) -> None:
    if deps.is_canceled():
        raise Canceled()


def _cache_manifest_key(cache_key: str) -> str:
    return f"cache/{CACHE_NAMESPACE}/{cache_key}.json"


def _restore_from_cache(deps: Deps, job: JobRequest, cache_key: str | None) -> list[dict] | None:
    if not cache_key:
        return None
    manifest = deps.storage.get_json(_cache_manifest_key(cache_key))
    if not manifest:
        return None
    outputs = []
    try:
        for obj in manifest["outputs"]:
            dst = job.outputPrefix + obj["key"].rsplit("/", 1)[-1]
            deps.storage.copy(obj["key"], dst)
            restored = {**obj, "key": dst}
            if obj.get("thumbKey"):
                restored["thumbKey"] = media.thumb_key_for(dst)
                deps.storage.copy(obj["thumbKey"], restored["thumbKey"])
            outputs.append(restored)
    except Exception as exc:  # expired source objects etc. → just render again
        log.warning("cache restore failed for %s: %s", cache_key, exc)
        return None
    return outputs


def run_scene_job(job: JobRequest, deps: Deps) -> dict[str, Any]:
    """Orchestrate a scene job end to end. Never raises; reports failure via events."""
    ev = deps.events
    active = Stopwatch(deps.clock)
    billable = True
    try:
        with active:
            ev.status("planning", progress=0.0)
            cache = _asset_cache(deps, job.package, {k: str(v) for k, v in job.inputs.items()})
            pkg = deps.core.load_package(job.package)
            work = deps.work_root / job.jobId
            work.mkdir(parents=True, exist_ok=True)
            asset_paths = deps.core.resolve(pkg, work, cache.fetch)
            _check_cancel(deps)
            plan = deps.core.plan(pkg, asset_paths, job.priority)
            cache_key = get(plan, "cache_key")

            cached = _restore_from_cache(deps, job, cache_key)
            if cached is not None:
                billable = False
                ev.status("succeeded", progress=1.0, engine=get(plan, "engine"))
                ev.done(cached, cached=True)
                return {"status": "succeeded", "cached": True, "outputs": cached}

            for w in get(plan, "warnings") or []:
                ev.warning(str(w))
            output_cfg = get(pkg, "output")
            out_format = str(get(output_cfg, "format", "png"))
            tasks = dispatch.build_tasks(job, plan, dispatch.scene_formats(asset_paths), out_format)
            total_frames = sum(len(t.frames) for t in tasks)
            runner = tasks[0].runner
            ev.status("rendering", progress=0.05, engine=get(plan, "engine"), hardware=dispatch.runner_hardware(runner).value)

        # Wait on chunks — not billed to the user (each chunk bills itself).
        results: list[ChunkResult] = []
        done_frames = 0
        assert deps.run_chunks is not None
        for runner_name, group in dispatch.group_by_runner(tasks).items():
            for res in deps.run_chunks(runner_name, group):
                _check_cancel(deps)
                results.append(res)
                done_frames += len(res.frames)
                for w in res.warnings:
                    ev.warning(w)
                ev.status("rendering", progress=dispatch.render_progress(done_frames, total_frames))

        with active:
            objects = [o for r in sorted(results, key=lambda r: r.chunk) for o in r.objects]
            if dispatch.is_video(out_format):
                _check_cancel(deps)
                ev.status("encoding", progress=0.9)
                keep = bool(get(output_cfg, "keepFrames", False))
                assert deps.run_encode is not None
                enc = EncodeTask(
                    job_id=job.jobId,
                    frame_keys=[o["key"] for o in objects if o.get("pass", "beauty") == "beauty"],
                    fps=float(get(get(pkg, "animation"), "fps", 30) or 30),
                    output=_plain(output_cfg),
                    output_key=job.outputPrefix + dispatch.output_name(job.package, out_format),
                    keep_frames_prefix=job.outputPrefix + "frames/" if keep else None,
                    callback_url=str(job.callbackUrl),
                )
                with active.paused():  # the encode container bills its own time
                    video = deps.run_encode(enc)
                outputs = [video] + video.pop("frames", [])
            else:
                outputs = objects

            if cache_key:
                deps.storage.put_json(_cache_manifest_key(cache_key), {"outputs": outputs, "jobId": job.jobId})
        # Report usage before `done` so the job's billed total is final when clients see it succeed.
        if billable and active.total > 0:
            ev.usage(hw.Hardware.CPU.value, active.total, "orchestrate")
            billable = False
        ev.status("succeeded", progress=1.0)
        ev.done(outputs, cached=False)
        return {"status": "succeeded", "cached": False, "outputs": outputs}
    except Canceled:
        ev.status("canceled")
        return {"status": "canceled"}
    except Exception as exc:
        log.exception("job %s failed", job.jobId)
        ev.failed(f"{type(exc).__name__}: {exc}")
        return {"status": "failed", "error": str(exc)}
    finally:
        # Orchestrator active time (fetch, plan, finalize) on a 0.25-core container; skip on cache hits.
        if billable and active.total > 0:
            ev.usage(hw.Hardware.CPU.value, active.total, "orchestrate")


def _plain(cfg: Any) -> Any:
    if hasattr(cfg, "model_dump"):
        return cfg.model_dump()
    return cfg


def run_chunk(task: ChunkTask, deps: Deps, started: float | None = None) -> ChunkResult:
    """Render one chunk of frames and upload them. Bills its own wall time from `started`."""
    started = deps.clock() if started is None else started
    try:
        cache = _asset_cache(deps, task.package, task.inputs)
        pkg = deps.core.load_package(task.package)
        work = Path(tempfile.mkdtemp(prefix=f"chunk-{task.chunk}-", dir=_ensure(deps.work_root)))
        asset_paths = deps.core.resolve(pkg, work, cache.fetch)
        out_dir = work / "out"
        out_dir.mkdir()
        result = deps.core.render_frames(task.plan, pkg, asset_paths, out_dir, task.frames)
        def upload(f: Path) -> dict[str, Any]:
            key = task.upload_prefix + f.name
            ctype = content_type(f)
            obj = {"key": key, "contentType": ctype, "bytes": deps.storage.put_file(f, key, ctype)}
            # core names passes like frame_0001.depth.exr; anything with a 2nd suffix is a pass.
            if len(f.suffixes) > 1:
                obj["pass"] = f.suffixes[-2].lstrip(".")
            if task.final:  # user-facing output: dimensions + thumbnail for the dashboard gallery
                media.attach(obj, f, deps.storage)
            return obj

        # R2 round-trips dominate small frames; sequential uploads cost ~40s of GPU time per 48 frames.
        objects = _parallel(upload, [Path(f) for f in get(result, "files") or []])
        shutil.rmtree(work, ignore_errors=True)
        return ChunkResult(task.chunk, task.frames, objects, [str(w) for w in get(result, "warnings") or []])
    finally:
        seconds = deps.clock() - started
        deps.events.usage(dispatch.runner_hardware(task.runner).value, seconds, task.chunk)


def run_encode(task: EncodeTask, deps: Deps, started: float | None = None) -> dict[str, Any]:
    started = deps.clock() if started is None else started
    try:
        work = Path(tempfile.mkdtemp(prefix="encode-", dir=_ensure(deps.work_root)))
        def download(key: str) -> Path:
            dest = work / "frames" / key.rsplit("/", 1)[-1]
            deps.storage.get_file(key, dest)
            return dest

        frames = sorted(_parallel(download, task.frame_keys))
        out = work / task.output_key.rsplit("/", 1)[-1]
        deps.core.encode(frames, task.fps, task.output, out)
        ctype = content_type(out)
        size = deps.storage.put_file(out, task.output_key, ctype)
        result: dict[str, Any] = {"key": task.output_key, "contentType": ctype, "bytes": size}
        media.attach(result, out, deps.storage, poster_frame=frames[0] if frames else None)
        if task.keep_frames_prefix:
            def keep(key: str) -> dict[str, Any]:
                dst = task.keep_frames_prefix + key.rsplit("/", 1)[-1]
                deps.storage.copy(key, dst)
                return {"key": dst, "contentType": content_type(dst), "bytes": (work / "frames" / key.rsplit("/", 1)[-1]).stat().st_size}

            result["frames"] = _parallel(keep, task.frame_keys)
        shutil.rmtree(work, ignore_errors=True)
        return result
    finally:
        deps.events.usage(hw.Hardware.CPU.value, deps.clock() - started, "encode")


def _parallel(fn: Callable[[Any], Any], items: Sequence[Any], workers: int = 16) -> list[Any]:
    """Map fn over items on a thread pool, preserving order (storage calls are I/O bound)."""
    if len(items) <= 1:
        return [fn(i) for i in items]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(fn, items))


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_extract(zip_path: Path, dest: Path) -> None:
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():
            target = (dest / member.filename).resolve()
            if not target.is_relative_to(dest):
                raise ValueError(f"unsafe path in project zip: {member.filename}")
        z.extractall(dest)


def find_project_root(root: Path) -> Path:
    """Zips often wrap the project in one top-level folder; descend to where index.html/hyperframes.json lives."""
    if (root / "index.html").exists() or (root / "hyperframes.json").exists():
        return root
    entries = [p for p in root.iterdir() if not p.name.startswith((".", "__MACOSX"))]
    if len(entries) == 1 and entries[0].is_dir():
        return find_project_root(entries[0])
    return root


Runner = Callable[[list[str], Path, int], subprocess.CompletedProcess]


def _subprocess_runner(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def run_hyperframes_job(job: JobRequest, deps: Deps, run: Runner = _subprocess_runner, timeout: int = 2 * 60 * 60, started: float | None = None) -> dict[str, Any]:
    started = deps.clock() if started is None else started
    ev = deps.events
    try:
        hp = HyperframesPackage.model_validate(job.package)
        ev.status("planning", progress=0.0, engine="hyperframes", hardware="cpu")
        cache = _asset_cache(deps, {}, {k: str(v) for k, v in job.inputs.items()})
        zip_path = cache.fetch(hp.project)
        work = Path(tempfile.mkdtemp(prefix=f"hf-{job.jobId}-", dir=_ensure(deps.work_root)))
        _safe_extract(zip_path, work / "src")
        project = find_project_root(work / "src")
        _check_cancel(deps)
        out = work / dispatch.output_name({"name": hp.name or "video"}, hp.format)
        cmd, warnings = build_render_command(hp, project, out)
        for w in warnings:
            ev.warning(w)
        ev.status("rendering", progress=0.1, engine="hyperframes", hardware="cpu")
        proc = run(cmd, project, timeout)
        if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            tail = ((proc.stderr or "") + (proc.stdout or ""))[-2000:]
            raise RuntimeError(f"hyperframes render failed (exit {proc.returncode}): {tail}")
        ev.status("encoding", progress=0.95)
        key = job.outputPrefix + out.name
        size = deps.storage.put_file(out, key, content_type(out))
        outputs = [media.attach({"key": key, "contentType": content_type(out), "bytes": size}, out, deps.storage)]
        shutil.rmtree(work, ignore_errors=True)
        ev.status("succeeded", progress=1.0)
        ev.done(outputs, cached=False)
        return {"status": "succeeded", "outputs": outputs}
    except Canceled:
        ev.status("canceled")
        return {"status": "canceled"}
    except Exception as exc:
        log.exception("hyperframes job %s failed", job.jobId)
        ev.failed(f"{type(exc).__name__}: {exc}")
        return {"status": "failed", "error": str(exc)}
    finally:
        ev.usage(hw.Hardware.CPU.value, deps.clock() - started, "hyperframes")
