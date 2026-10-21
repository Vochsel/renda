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
from .hyperframes import build_render_command, localize_variables
from .models import HyperframesPackage, JobRequest
from .storage import Storage, content_type
from .telemetry import capture_exception

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

    def elapsed(self) -> float:
        """Active seconds so far, including the span in progress (for live metering)."""
        return self.total + (self.clock() - self._t if self._t is not None else 0.0)

    @contextlib.contextmanager
    def paused(self):
        """Exclude a span (e.g. blocking on another billed container) from the active total."""
        self.__exit__()
        try:
            yield
        finally:
            self.__enter__()


class Phases:
    """Wall seconds per named phase (fetch, render, upload, …), returned with results for overhead analysis."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.seconds: dict[str, float] = {}

    @contextlib.contextmanager
    def __call__(self, name: str):
        t = self.clock()
        try:
            yield
        finally:
            self.seconds[name] = round(self.seconds.get(name, 0.0) + self.clock() - t, 3)


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
    run_train: Callable[["dispatch.TrainTask"], dict[str, Any]] | None = None
    # streaming splat bakes: start training while chunks still render; spawn → handle, join(handle) → result
    spawn_train: Callable[["dispatch.TrainTask"], Any] | None = None
    join_train: Callable[[Any], dict[str, Any]] | None = None
    # re-read the shared Volume (new views from other containers)
    refresh_volume: Callable[[], None] | None = None
    download: Callable[[str, Path], None] | None = None
    # Fetcher for user-supplied https:// URLs; None → the SSRF-safe default in assets.py.
    download_untrusted: Callable[[str, Path], None] | None = None
    on_new_asset: Callable[[], None] | None = None
    clock: Callable[[], float] = time.monotonic
    work_root: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "renda-work")


def _asset_cache(deps: Deps, job_package: dict, inputs: dict, defer_commit: bool = False) -> AssetCache:
    kw = {"download": deps.download} if deps.download else {}
    if deps.download_untrusted:
        kw["download_untrusted"] = deps.download_untrusted
    return AssetCache(deps.asset_root, inputs, name_hints(job_package), on_new=deps.on_new_asset,
                      scratch=deps.work_root / ".downloads", defer_commit=defer_commit, clock=deps.clock, **kw)


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
    ph = Phases(deps.clock)
    t_start = deps.clock()
    meter = deps.events.start_meter("orchestrate", hw.Hardware.CPU.value, active.elapsed)
    billable = True
    cache: AssetCache | None = None
    try:
        with active:
            with ph("resolve"):
                ev.status("planning", progress=0.0)
                cache = _asset_cache(deps, job.package, {k: str(v) for k, v in job.inputs.items()}, defer_commit=True)
                pkg = deps.core.load_package(job.package)
                work = deps.work_root / job.jobId
                work.mkdir(parents=True, exist_ok=True)
                asset_paths = deps.core.resolve(pkg, work, cache.fetch)
            with ph("volume_commit"):
                cache.commit()  # one Volume commit for every newly cached asset, before chunks look for them
            _check_cancel(deps)
            with ph("plan"):
                plan = deps.core.plan(pkg, asset_paths, job.priority)
            cache_key = get(plan, "cache_key")

            with ph("result_cache"):
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
            tasks = dispatch.build_tasks(job, plan, dispatch.scene_formats(asset_paths), out_format, deps.asset_root)
            if dispatch.is_video(out_format) and len(tasks) == 1 and not dispatch.is_splat_training(plan):
                keep = bool(get(output_cfg, "keepFrames", False))
                tasks[0].encode = {
                    "fps": float(get(get(pkg, "animation"), "fps", 30) or 30),
                    "output": _plain(output_cfg),
                    "output_key": job.outputPrefix + dispatch.output_name(job.package, out_format),
                    "keep_frames_prefix": job.outputPrefix + "frames/" if keep else None,
                }
            total_frames = sum(len(t.frames) for t in tasks)
            hw_now = dispatch.runner_hardware(tasks[0].runner).value if tasks else \
                str(get(get(plan, "splat_bake"), "train_hardware", get(plan, "hardware")))
            ev.status("rendering", progress=0.05, engine=get(plan, "engine"), hardware=hw_now)
            train_handle = None
            if dispatch.is_streaming(plan) and deps.spawn_train and deps.join_train:
                # the trainer waits for the first views on the shared Volume, then trains while chunks render
                train_handle = deps.spawn_train(dispatch.build_train_task(job, plan, deps.asset_root))

        # Wait on chunks — not billed to the user (each chunk bills itself).
        results: list[ChunkResult] = []
        done_frames = 0
        assert deps.run_chunks is not None
        t_wait = deps.clock()
        for runner_name, group in dispatch.group_by_runner(tasks).items():
            for res in deps.run_chunks(runner_name, group):
                _check_cancel(deps)
                results.append(res)
                done_frames += len(res.frames)
                for w in res.warnings:
                    ev.warning(w)
                ev.status("rendering", progress=dispatch.render_progress(done_frames, total_frames))

        ph.seconds["wait_chunks"] = round(deps.clock() - t_wait, 3)
        t_fin = deps.clock()
        with active:
            objects = [o for r in sorted(results, key=lambda r: r.chunk) for o in r.objects]
            if get(plan, "tiles") and not dispatch.is_splat_training(plan):
                objects = [_stitch_tiles(job, plan, objects, deps)]
            if dispatch.is_splat_training(plan):
                _check_cancel(deps)
                ev.status("encoding", progress=0.9)  # "training" in the UI's terms; keeps the status enum stable
                with active.paused():  # the training container bills its own time
                    if train_handle is not None:
                        _mark_views_complete(deps, job.jobId)
                        trained = deps.join_train(train_handle)
                    else:
                        assert deps.run_train is not None
                        trained = deps.run_train(dispatch.build_train_task(job, plan, deps.asset_root))
                for w in trained.get("warnings", []):
                    ev.warning(w)
                outputs = trained["outputs"]
            elif dispatch.is_video(out_format) and results and all(r.encoded for r in results):
                outputs = objects  # encoded in the render container: [video, *kept frames]
            elif dispatch.is_video(out_format):
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
        if billable and _billed(active.total, cache) > 0:
            ev.usage(hw.Hardware.CPU.value, _billed(active.total, cache), "orchestrate")
            billable = False
        ev.status("succeeded", progress=1.0)
        ev.done(outputs, cached=False)
        ph.seconds["finalize"] = round(deps.clock() - t_fin, 3)
        ph.seconds["fetch"] = round(cache.fetch_seconds, 3) if cache is not None else 0.0
        ph.seconds["billed_orchestrate"] = round(_billed(active.total, cache), 3)
        ph.seconds["total"] = round(deps.clock() - t_start, 3)
        return {"status": "succeeded", "cached": False, "outputs": outputs,
                "timings": {"orchestrate": ph.seconds, "chunks": [r.timings for r in sorted(results, key=lambda r: r.chunk)]}}
    except Canceled:
        ev.status("canceled")
        ev.flush()
        return {"status": "canceled"}
    except Exception as exc:
        log.exception("job %s failed", job.jobId)
        capture_exception(exc, job_id=job.jobId, kind="scene", priority=job.priority)
        ev.failed(f"{type(exc).__name__}: {exc}")
        return {"status": "failed", "error": str(exc)}
    finally:
        meter.stop()
        # Orchestrator active time (plan, finalize) on a 0.25-core container; skip on cache hits.
        if billable and _billed(active.total, cache) > 0:
            ev.usage(hw.Hardware.CPU.value, _billed(active.total, cache), "orchestrate")


def _mark_views_complete(deps: Deps, job_id: str) -> None:
    """Tell a streaming trainer that every view chunk has finished (it stops waiting for more)."""
    d = Path(dispatch.bake_views_dir(deps.asset_root, job_id))
    d.mkdir(parents=True, exist_ok=True)
    (d / ".complete").write_text("1")
    if deps.on_new_asset:
        deps.on_new_asset()  # Volume commit, so the training container sees the marker


def _plain(cfg: Any) -> Any:
    if hasattr(cfg, "model_dump"):
        return cfg.model_dump()
    return cfg


def run_chunk(task: ChunkTask, deps: Deps, started: float | None = None) -> ChunkResult:
    """Render one chunk of frames and upload them. Bills its own wall time from `started`."""
    started = deps.clock() if started is None else started
    meter = deps.events.start_meter(task.chunk, dispatch.runner_hardware(task.runner).value, lambda: deps.clock() - started)
    cache: AssetCache | None = None
    ph = Phases(deps.clock)
    try:
        with ph("resolve"):
            cache = _asset_cache(deps, task.package, task.inputs)
            pkg = deps.core.load_package(task.package)
            work = Path(tempfile.mkdtemp(prefix=f"chunk-{task.chunk}-", dir=_ensure(deps.work_root)))
            asset_paths = deps.core.resolve(pkg, work, cache.fetch)
            out_dir = work / "out"
            out_dir.mkdir()
        with ph("render"):
            result = deps.core.render_frames(task.plan, pkg, asset_paths, out_dir, task.frames)
        def upload(f: Path, key: str | None = None, final: bool = task.final, poster: Path | None = None) -> dict[str, Any]:
            key = key or task.upload_prefix + f.name
            ctype = content_type(f)
            obj: dict[str, Any] = {"key": key, "contentType": ctype}
            # core names passes like frame_0001.depth.exr; anything with a 2nd suffix is a pass.
            if len(f.suffixes) > 1:
                obj["pass"] = f.suffixes[-2].lstrip(".")
            if final:
                # user-facing output: dimensions + thumbnail for the gallery, uploaded alongside the file
                from concurrent.futures import ThreadPoolExecutor

                with ThreadPoolExecutor(2) as pool:
                    put = pool.submit(deps.storage.put_file, f, key, ctype)
                    pool.submit(media.attach, obj, f, deps.storage, poster).result()
                    obj["bytes"] = put.result()
            else:
                obj["bytes"] = deps.storage.put_file(f, key, ctype)
            return obj

        files = [Path(f) for f in get(result, "files") or []]
        ph.seconds["upload"] = 0.0
        t_up = deps.clock()
        encoded = False
        if task.encode:
            # single-chunk video: encode right here — frames never leave the container
            enc = task.encode
            beauty = sorted(f for f in files if len(f.suffixes) <= 1)
            video = work / enc["output_key"].rsplit("/", 1)[-1]
            with ph("encode"):
                deps.core.encode(beauty, float(enc["fps"]), enc["output"], video)
            jobs = [(video, enc["output_key"], True, beauty[0] if beauty else None)]
            if enc.get("keep_frames_prefix"):
                jobs += [(f, enc["keep_frames_prefix"] + f.name, False, None) for f in beauty]
            objects = _parallel(lambda j: upload(j[0], j[1], j[2], j[3]), jobs)
            encoded = True
        elif task.local_dir:
            # splat-bake views stay on the shared Volume for the training container (no R2 round trip)
            dest = Path(task.local_dir)
            dest.mkdir(parents=True, exist_ok=True)
            objects = [{"path": str(shutil.copy(f, dest / f.name)), "bytes": f.stat().st_size} for f in files]
            if deps.on_new_asset:
                deps.on_new_asset()
        else:
            # R2 round-trips dominate small frames; sequential uploads cost ~40s of GPU time per 48 frames.
            objects = _parallel(upload, files)
        ph.seconds["upload"] = round(deps.clock() - t_up, 3)
        shutil.rmtree(work, ignore_errors=True)
        if cache is not None:
            ph.seconds["fetch"] = round(cache.fetch_seconds, 3)
        ph.seconds["total"] = round(deps.clock() - started, 3)
        return ChunkResult(task.chunk, task.frames, objects, [str(w) for w in get(result, "warnings") or []],
                           timings=ph.seconds, encoded=encoded)
    finally:
        meter.stop()
        deps.events.usage(dispatch.runner_hardware(task.runner).value, _billed(deps.clock() - started, cache), task.chunk)


def run_train(task: "dispatch.TrainTask", deps: Deps, started: float | None = None) -> dict[str, Any]:
    """Splat-bake refinement: train on the Volume views, upload the splat (+ dataset), clean up."""
    started = deps.clock() if started is None else started
    meter = deps.events.start_meter("train", task.hardware, lambda: deps.clock() - started)
    cache: AssetCache | None = None
    try:
        cache = _asset_cache(deps, task.package, task.inputs)
        pkg = deps.core.load_package(task.package)
        work = Path(tempfile.mkdtemp(prefix="train-", dir=_ensure(deps.work_root)))
        asset_paths = deps.core.resolve(pkg, work, cache.fetch)
        out_dir = work / "out"
        out_dir.mkdir()
        result = deps.core.train_splat(task.plan, pkg, asset_paths, Path(task.views_dir), out_dir,
                                       refresh=deps.refresh_volume)

        def upload(f: Path) -> dict[str, Any]:
            key = task.output_prefix + f.name
            ctype = content_type(f)
            return {"key": key, "contentType": ctype, "bytes": deps.storage.put_file(f, key, ctype)}

        outputs = _parallel(upload, [Path(f) for f in get(result, "files") or []])
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(Path(task.views_dir).parent, ignore_errors=True)  # bakes/<job>/
        if deps.on_new_asset:
            deps.on_new_asset()
        return {"outputs": outputs, "warnings": [str(w) for w in get(result, "warnings") or []]}
    finally:
        meter.stop()
        deps.events.usage(task.hardware, _billed(deps.clock() - started, cache), "train")


def run_encode(task: EncodeTask, deps: Deps, started: float | None = None) -> dict[str, Any]:
    started = deps.clock() if started is None else started
    meter = deps.events.start_meter("encode", hw.Hardware.CPU.value, lambda: deps.clock() - started)
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
        meter.stop()
        deps.events.usage(hw.Hardware.CPU.value, deps.clock() - started, "encode")


def _billed(seconds: float, cache: AssetCache | None) -> float:
    """Seconds to bill: filling the asset cache on a miss is our infrastructure cost, not the user's."""
    return max(0.0, seconds - (cache.fetch_seconds if cache is not None else 0.0))


def _parallel(fn: Callable[[Any], Any], items: Sequence[Any], workers: int = 16) -> list[Any]:
    """Map fn over items on a thread pool, preserving order (storage calls are I/O bound)."""
    if len(items) <= 1:
        return [fn(i) for i in items]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        return list(pool.map(fn, items))


def _stitch_tiles(job: JobRequest, plan: Any, objects: list[dict[str, Any]], deps: Deps) -> dict[str, Any]:
    """Fast-mode still: download each tile, paste the region it owns into the full frame, upload the result
    as the job's single output and drop the tile objects."""
    from PIL import Image

    tiles = get(plan, "tiles")
    overlap = int(get(plan, "tile_overlap", 0) or 0)
    width, height = (get(get(job.package, "render") or {}, "resolution") or [1920, 1080])
    by_tile: dict[int, dict[str, Any]] = {}
    for o in objects:
        parts = o["key"].split("/tiles/")
        if len(parts) == 2 and o.get("pass", "beauty") == "beauty":
            by_tile[int(parts[1].split("/")[0])] = o
    work = Path(tempfile.mkdtemp(prefix="stitch-", dir=_ensure(deps.work_root)))
    try:
        def fetch(i: int) -> Path:
            dest = work / f"tile{i:02d}{Path(by_tile[i]['key']).suffix}"
            deps.storage.get_file(by_tile[i]["key"], dest)
            return dest

        paths = dict(zip(sorted(by_tile), _parallel(fetch, sorted(by_tile))))
        first = Image.open(paths[min(paths)])
        canvas = Image.new(first.mode, (int(width), int(height)))
        for i, box in enumerate(tiles):
            own = dispatch.tile_owned(box, int(width), int(height), overlap)
            img = Image.open(paths[i])
            canvas.paste(img.crop((own[0] - box[0], own[1] - box[1], own[2] - box[0], own[3] - box[1])), (own[0], own[1]))
        name = Path(by_tile[min(by_tile)]["key"]).name
        out = work / name
        fmt = out.suffix.lower()
        if fmt in (".jpg", ".jpeg"):
            canvas.convert("RGB").save(out, quality=95)
        elif fmt == ".webp":
            canvas.save(out, quality=95)
        else:
            canvas.save(out, compress_level=4)
        key = job.outputPrefix + name
        ctype = content_type(out)
        obj = {"key": key, "contentType": ctype, "bytes": deps.storage.put_file(out, key, ctype)}
        media.attach(obj, out, deps.storage)
        if hasattr(deps.storage, "delete_many"):  # tiles live under work/, which has no lifecycle rule
            try:
                deps.storage.delete_many([o["key"] for o in objects if "/tiles/" in o["key"]])
            except Exception:  # noqa: BLE001 — cleanup must never fail a finished render
                log.warning("couldn't delete tile objects for %s", job.jobId)
        return obj
    finally:
        shutil.rmtree(work, ignore_errors=True)


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
    meter = deps.events.start_meter("hyperframes", hw.Hardware.CPU.value, lambda: deps.clock() - started)
    cache: AssetCache | None = None
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
        variables, warnings = localize_variables(hp.variables, project, cache.fetch)
        cmd, cmd_warnings = build_render_command(hp.model_copy(update={"variables": variables}), project, out)
        warnings += cmd_warnings
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
        ev.flush()
        return {"status": "canceled"}
    except Exception as exc:
        log.exception("hyperframes job %s failed", job.jobId)
        capture_exception(exc, job_id=job.jobId, kind="hyperframes")
        ev.failed(f"{type(exc).__name__}: {exc}")
        return {"status": "failed", "error": str(exc)}
    finally:
        meter.stop()
        ev.usage(hw.Hardware.CPU.value, _billed(deps.clock() - started, cache), "hyperframes")
