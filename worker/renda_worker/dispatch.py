"""Pure planning helpers: which container class runs which frames, and where results go.

No Modal imports here so it's unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import hardware as hw
from .core_api import get

VIDEO_FORMATS = {"mp4", "webm", "gif", "mov"}

# Formats only Blender can read; any job touching them needs the Blender image.
BLENDER_ONLY_FORMATS = {"blend", "fbx", "abc"}

# Runner names → Modal function names in app.py.
RUNNERS = {
    "cpu": "render_chunk_cpu",            # slim image: mitsuba-llvm, trimesh, usd, ffmpeg
    "blender-cpu": "render_chunk_blender_cpu",
    "l4": "render_chunk_l4",
    "l40s": "render_chunk_l40s",
    "threejs": "render_chunk_threejs",
    "splat-l4": "render_chunk_splat_l4",      # + torch/gsplat: Gaussian splat jobs on GPU
    "splat-l40s": "render_chunk_splat_l40s",
}


def needs_blender(engine: str, scene_formats: Iterable[str]) -> bool:
    return engine.startswith("blender") or bool(BLENDER_ONLY_FORMATS & set(scene_formats))


def runner_for(engine: str, hardware: hw.Hardware, scene_formats: Iterable[str], splats: bool = False) -> str:
    if engine == "threejs":
        return "threejs"  # headless Chromium (Spark renders splats there too)
    if engine == "gsplat" or (splats and hardware is not hw.Hardware.CPU):
        # CUDA splat rasteriser lives in the splat image; CPU splat jobs use core's numpy rasteriser
        return "splat-l40s" if hardware is hw.Hardware.GPU_L40S else "splat-l4"
    if hardware is hw.Hardware.GPU_L40S:
        return "l40s"
    if hardware is hw.Hardware.GPU_L4:
        return "l4"
    return "blender-cpu" if needs_blender(engine, scene_formats) else "cpu"


def runner_hardware(runner: str) -> hw.Hardware:
    return {"l4": hw.Hardware.GPU_L4, "l40s": hw.Hardware.GPU_L40S, "splat-l4": hw.Hardware.GPU_L4,
            "splat-l40s": hw.Hardware.GPU_L40S}.get(runner, hw.Hardware.CPU)


def is_video(output_format: str) -> bool:
    return output_format in VIDEO_FORMATS


@dataclass
class ChunkTask:
    job_id: str
    chunk: int
    frames: list[int]
    runner: str
    package: dict[str, Any]
    inputs: dict[str, str]
    plan: Any
    callback_url: str
    upload_prefix: str  # outputs/<id>/ for stills, work/<id>/frames/ for video
    final: bool         # True → files are user-facing outputs


@dataclass
class ChunkResult:
    chunk: int
    frames: list[int]
    objects: list[dict[str, Any]] = field(default_factory=list)  # {key, contentType, bytes}
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0


def plan_chunks(plan: Any) -> list[list[int]]:
    """Chunks from the planner, or one chunk of all frames."""
    chunks = get(plan, "chunks")
    if chunks:
        return [list(c) for c in chunks]
    frames = get(plan, "frames") or [1]
    return [list(frames)]


def build_tasks(job: Any, plan: Any, scene_formats: Sequence[str], output_format: str) -> list[ChunkTask]:
    engine = str(get(plan, "engine", "auto"))
    hardware = hw.parse(str(get(plan, "hardware", "cpu")))
    runner = runner_for(engine, hardware, scene_formats, splats=bool(get(plan, "splats", False)))
    video = is_video(output_format)
    prefix = f"work/{job.jobId}/frames/" if video else job.outputPrefix
    return [
        ChunkTask(
            job_id=job.jobId,
            chunk=i,
            frames=frames,
            runner=runner,
            package=job.package,
            inputs={k: str(v) for k, v in job.inputs.items()},
            plan=plan,
            callback_url=str(job.callbackUrl),
            upload_prefix=prefix,
            final=not video,
        )
        for i, frames in enumerate(plan_chunks(plan))
    ]


def group_by_runner(tasks: Iterable[ChunkTask]) -> dict[str, list[ChunkTask]]:
    groups: dict[str, list[ChunkTask]] = {}
    for t in tasks:
        groups.setdefault(t.runner, []).append(t)
    return groups


# Progress budget: 0-5% planning, 5-90% rendering, 90-100% encoding/upload.
def render_progress(done_frames: int, total_frames: int) -> float:
    if total_frames <= 0:
        return 0.9
    return 0.05 + 0.85 * min(done_frames / total_frames, 1.0)


def output_name(package: dict[str, Any], ext: str) -> str:
    name = (package.get("name") or "render").strip() or "render"
    safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in name)[:80]
    return f"{safe}.{ext.lstrip('.')}"


def scene_formats(asset_paths: dict[str, Path] | Any) -> list[str]:
    from .assets import FORMAT_EXT

    ext_to_fmt = {v: k for k, v in FORMAT_EXT.items()}
    ext_to_fmt.update({".usd": "usd", ".usda": "usd", ".usdc": "usd", ".gltf": "gltf"})
    paths = asset_paths.values() if hasattr(asset_paths, "values") else asset_paths
    return sorted({ext_to_fmt[Path(p).suffix.lower()] for p in paths if Path(p).suffix.lower() in ext_to_fmt})
