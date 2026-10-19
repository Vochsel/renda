"""Render planning: pick engine + hardware, split frames into chunks, estimate time and cost.

The same planner runs in the CLI (local renders, `renda estimate`) and in the cloud
worker, so estimates shown to users are what the worker actually does.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .engines.base import samples_for
from .spec import RendaPackage, format_from_path

HARDWARE = ("cpu", "gpu-l4", "gpu-l40s")

# Default $/render-minute (keep in sync with web/src/lib/pricing.ts)
DEFAULT_PRICES: dict[str, float] = {"cpu": 0.015, "gpu-l4": 0.035, "gpu-l40s": 0.075}

# Pixel-samples per second for Cycles-equivalent work on each class (calibrated on a
# mid-complexity product shot). Engine cost factors scale this.
THROUGHPUT: dict[str, float] = {"cpu": 1.2e6, "gpu-l4": 13e6, "gpu-l40s": 38e6}

# Relative cost per pixel-sample vs Cycles, per engine and hardware kind.
ENGINE_COST: dict[str, dict[str, float]] = {
    "blender-cycles": {"cpu": 1.0, "gpu": 1.0},
    "blender-eevee": {"cpu": 6.0, "gpu": 0.06},  # EEVEE needs a GPU; CPU means software GL
    "blender-workbench": {"cpu": 2.0, "gpu": 0.02},
    "mitsuba": {"cpu": 0.8, "gpu": 0.7},
    "threejs": {"cpu": 0.05, "gpu": 0.01},
    "gsplat": {"cpu": 40.0, "gpu": 0.02},  # CPU = the numpy reference rasteriser
}

# Seconds per million splats per frame for the splat rasteriser (sorting/projection dominate on GPU;
# the numpy fallback scales with coverage). Measured on Modal 2026-10-19: L4 ≈ 180 ms per 1M splats at 1080p
# (gsplat 1.5.3); L40S assumed ~2x faster; numpy ≈ 8 s for 79k splats at 720p on an M-series CPU.
SPLAT_SECONDS_PER_M: dict[str, float] = {"cpu": 150.0, "gpu-l4": 0.18, "gpu-l40s": 0.09}
DEFAULT_SPLATS = 1_000_000

# Engine start + scene load (seconds); .blend/.fbx/.abc via IR add an extraction pass.
LOAD_SECONDS: dict[str, float] = {
    "blender-cycles": 8.0, "blender-eevee": 7.0, "blender-workbench": 5.0, "mitsuba": 4.0, "threejs": 3.0,
    "gsplat": 6.0,  # torch + CUDA init + file load
}
GPU_ONLY = {"blender-eevee", "blender-workbench", "gsplat"}
MIN_FRAME_SECONDS = {"threejs": 0.15, "blender-workbench": 0.2, "blender-eevee": 0.5, "gsplat": 0.05}

# Chunk overhead targets: share of a chunk spent loading the scene
OVERHEAD_TARGET = {"economy": 0.05, "standard": 0.10, "rush": 0.25}
MAX_CONTAINERS = {"economy": 10, "standard": 50, "rush": 50}

NATIVE_BLENDER = {"blend", "usd", "gltf", "fbx", "obj", "stl", "ply", "abc", "rir", "dae", "3mf"}


@dataclass
class RenderPlan:
    engine: str
    hardware: str
    frames: list[int]
    chunks: list[list[int]]
    est_seconds: tuple[float, float]
    warnings: list[str] = field(default_factory=list)
    cache_key: str = ""
    scene_format: str = ""
    samples: int = 0
    priority: str = "standard"
    est_load_seconds: float = 0.0
    est_frame_seconds: float = 0.0
    splats: bool = False  # the scene has Gaussian splats (the worker routes these to the splat-capable image)

    def to_json(self) -> dict:
        d = asdict(self)
        d["est_seconds"] = list(self.est_seconds)
        return d

    @classmethod
    def from_json(cls, d: dict) -> "RenderPlan":
        d = dict(d)
        d["est_seconds"] = tuple(d.get("est_seconds", (0, 0)))
        return cls(**d)


def scene_format_of(pkg: RendaPackage, asset_paths: dict[str, Path] | None) -> str:
    root = pkg.scene.root
    if root in pkg.assets and pkg.assets[root].format:
        return pkg.assets[root].format  # type: ignore[return-value]
    if asset_paths:
        from .formats import UnknownFormat, detect
        from .scene import asset_path

        try:
            return detect(asset_path(pkg, root, asset_paths))
        except (KeyError, UnknownFormat, OSError):
            pass
    return format_from_path(pkg.asset_uri(root)) or "unknown"


def include_formats(pkg: RendaPackage, asset_paths: dict[str, Path] | None) -> list[str]:
    out = []
    for inc in pkg.scene.include:
        fmt = pkg.assets[inc.asset].format if inc.asset in pkg.assets and pkg.assets[inc.asset].format else None
        if fmt is None and asset_paths:
            from .formats import UnknownFormat, detect
            from .scene import asset_path

            try:
                fmt = detect(asset_path(pkg, inc.asset, asset_paths))
            except (KeyError, UnknownFormat, OSError):
                fmt = None
        out.append(fmt or format_from_path(pkg.asset_uri(inc.asset)) or "unknown")
    return out


def choose_engine(fmt: str, quality: str, available: set[str] | None, mixed: bool = False) -> list[str]:
    """Ordered candidates for engine=auto. `mixed`: a splat scene that also includes meshes."""
    if fmt == "splat":
        c = (["threejs", "blender-eevee", "blender-cycles"] if quality == "draft" else ["blender-cycles", "threejs"]) if mixed \
            else ["gsplat", "threejs"]
    elif fmt in ("blend", "fbx", "abc"):
        c = ["blender-eevee", "blender-cycles"] if quality == "draft" else ["blender-cycles", "blender-eevee"]
    elif fmt == "threejs":
        c = ["threejs", "blender-eevee", "mitsuba"] if quality != "final" else ["blender-cycles", "mitsuba", "threejs"]
    elif fmt in ("stl", "obj", "ply", "3mf", "dae"):
        c = {"draft": ["blender-workbench", "mitsuba", "threejs"],
             "standard": ["mitsuba", "blender-cycles", "threejs"],
             "final": ["blender-cycles", "mitsuba"]}[quality]
    else:  # gltf, usd, rir
        c = ["blender-eevee", "mitsuba", "threejs"] if quality == "draft" else ["blender-cycles", "mitsuba"]
    if available is not None:
        c = [e for e in c if e in available] + [e for e in ("mitsuba", "blender-cycles", "threejs") if e in available and e not in c]
    return c


def frame_seconds(engine: str, hardware: str, pkg: RendaPackage, samples: int, complexity: float,
                  splats: int | None = None) -> float:
    w, h = pkg.render.resolution
    kind = "gpu" if hardware.startswith("gpu") else "cpu"
    splat_s = 0.0
    if splats:
        mp = w * h / 2.07e6  # relative to 1080p
        splat_s = splats / 1e6 * SPLAT_SECONDS_PER_M[hardware] * (mp if hardware == "cpu" else max(mp, 0.5))
    if engine == "gsplat":
        return max(splat_s or 0.0, MIN_FRAME_SECONDS["gsplat"])
    eff_samples = samples
    if engine == "blender-cycles" and pkg.render.denoise:
        eff_samples = samples * 0.6  # adaptive sampling usually stops early on clean regions
    work = w * h * eff_samples * ENGINE_COST[engine][kind] * complexity
    # threejs renders splats with Spark in the same pass; other engines add a composited splat layer
    extra = splat_s if engine != "threejs" else splat_s * 0.2
    return max(work / THROUGHPUT[hardware], MIN_FRAME_SECONDS.get(engine, 0.3)) + extra


def complexity_factor(triangles: int | None) -> float:
    if not triangles:
        return 1.0
    return float(min(max(1.0 + 0.35 * math.log10(max(triangles, 1) / 2e5), 0.7), 3.0))


def choose_hardware(engine: str, pkg: RendaPackage, priority: str, samples: int, complexity: float,
                    n_frames: int, warnings: list[str], prices: dict[str, float], splats: int | None = None) -> str:
    req = pkg.render.hardware
    if engine in GPU_ONLY:
        if req == "cpu":
            warnings.append(f"{engine} needs a GPU; ignoring render.hardware=cpu")
        return "gpu-l40s" if priority == "rush" and n_frames > 200 else "gpu-l4"
    candidates = list(HARDWARE)
    if req == "cpu":
        candidates = ["cpu"]
    elif req == "gpu":
        candidates = ["gpu-l4", "gpu-l40s"]
    if engine == "threejs" and req != "gpu" and not splats:
        return "cpu"  # SwiftShader is fine for raster previews and far cheaper
    if priority != "rush" and "gpu-l40s" in candidates and len(candidates) > 1:
        candidates.remove("gpu-l40s")

    # cheapest class whose total time meets the priority's latency target
    deadline = {"economy": 3600.0, "standard": 900.0, "rush": 240.0}[priority]
    best, best_cost = candidates[0], math.inf
    for hw in candidates:
        ft = frame_seconds(engine, hw, pkg, samples, complexity, splats)
        total = LOAD_SECONDS[engine] + ft * n_frames
        cost = total / 60 * prices.get(hw, DEFAULT_PRICES[hw])
        wall = LOAD_SECONDS[engine] + ft * math.ceil(n_frames / MAX_CONTAINERS[priority])
        if wall > deadline:
            cost *= 10  # strongly prefer classes that meet the deadline
        if cost < best_cost:
            best, best_cost = hw, cost
    return best


def chunk_frames(frames: list[int], load_s: float, frame_s: float, priority: str) -> list[list[int]]:
    target = OVERHEAD_TARGET[priority]
    per = max(1, math.ceil(load_s * (1 - target) / (target * max(frame_s, 1e-3))))
    per = max(per, math.ceil(len(frames) / MAX_CONTAINERS[priority]))
    return [frames[i : i + per] for i in range(0, len(frames), per)]


def plan(
    pkg: RendaPackage,
    asset_paths: dict[str, Path] | None,
    priority: str = "standard",
    *,
    available: set[str] | None = None,
    triangles: int | None = None,
    asset_hashes: dict[str, str] | None = None,
    engine_versions: dict[str, str] | None = None,
    prices: dict[str, float] | None = None,
    splat_count: int | None = None,
) -> RenderPlan:
    """Plan a render. `available=None` assumes every engine (the cloud); pass a set for local renders."""
    if priority not in OVERHEAD_TARGET:
        raise ValueError(f"priority must be one of {list(OVERHEAD_TARGET)}")
    prices = prices or DEFAULT_PRICES
    warnings: list[str] = []
    fmt = scene_format_of(pkg, asset_paths)
    quality = pkg.render.quality
    include_fmts = include_formats(pkg, asset_paths)
    has_splats = fmt == "splat" or "splat" in include_fmts
    mixed = has_splats and (fmt != "splat" or any(f != "splat" for f in include_fmts))
    splats = (splat_count or DEFAULT_SPLATS) if has_splats else None

    if pkg.render.engine == "auto":
        cands = choose_engine(fmt, quality, available, mixed)
        if not cands:
            raise RuntimeError(f"no render engine available for .{fmt} here; run `renda engines` or render with --cloud")
        engine = cands[0]
    else:
        engine = pkg.render.engine
        if available is not None and engine not in available:
            raise RuntimeError(f"engine {engine} isn't available here; run `renda engines` or render with --cloud")
    if engine.startswith("blender") and fmt == "threejs":
        warnings.append("three.js JSON is converted to glTF for Blender; ShaderMaterials are approximated")
    if engine == "threejs" and fmt not in ("threejs", "gltf", "splat"):
        warnings.append(f"threejs engine renders .{fmt} via IR → glTF conversion")

    frames = pkg.frames()
    samples = samples_for(engine, quality, pkg.render.samples)
    if engine == "threejs":
        samples = min(samples, 4)  # supersampling factor, not path-tracing samples
    complexity = complexity_factor(triangles)
    hardware = choose_hardware(engine, pkg, priority, samples, complexity, len(frames), warnings, prices, splats)

    load_s = LOAD_SECONDS[engine] + (6.0 if fmt in ("blend", "fbx", "abc") and not engine.startswith("blender") else 0.0)
    if has_splats and engine in ("blender-cycles", "blender-eevee", "blender-workbench", "mitsuba"):
        load_s += 4.0  # splat file + rasteriser init for the composited layer
    frame_s = frame_seconds(engine, hardware, pkg, samples, complexity, splats)
    chunks = chunk_frames(frames, load_s, frame_s, priority)
    encode_s = 0.05 * len(frames) * (pkg.render.resolution[0] * pkg.render.resolution[1] / 2.07e6) if pkg.output.is_video else 0.0
    total = len(chunks) * load_s + len(frames) * frame_s + encode_s

    key = ""
    if asset_hashes is not None or asset_paths:
        from .cache import cache_key, sha256_file

        hashes = dict(asset_hashes or {})
        if not hashes and asset_paths:
            hashes = {k: sha256_file(p) for k, p in asset_paths.items() if Path(p).is_file()}
        key = cache_key(pkg, hashes, engine_versions or {engine: "cloud"})

    return RenderPlan(
        engine=engine, hardware=hardware, frames=frames, chunks=chunks,
        est_seconds=(round(total * 0.6, 1), round(total * 1.8, 1)), warnings=warnings, cache_key=key,
        scene_format=fmt, samples=samples, priority=priority,
        est_load_seconds=round(load_s, 2), est_frame_seconds=round(frame_s, 3), splats=has_splats,
    )


def estimate_cost(plan: RenderPlan, prices: dict[str, float] | None = None) -> tuple[float, float]:
    """USD range for a plan: per-second billing at the hardware class's per-minute price."""
    p = (prices or DEFAULT_PRICES).get(plan.hardware, DEFAULT_PRICES.get(plan.hardware, 0.06))
    lo, hi = plan.est_seconds
    return (round(lo / 60 * p, 4), round(hi / 60 * p, 4))
