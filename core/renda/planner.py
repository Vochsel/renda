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
    "filament": {"cpu": 0.06, "gpu": 0.01},  # real-time rasteriser (EGL; llvmpipe on CPU)
    "pbrt": {"cpu": 0.15, "gpu": 0.15},  # CPU-only; measured 2026-10-20: 640×480×64 spp ≈ 2 s on 8 cores
}

# Seconds per million splats per frame for the splat rasteriser (sorting/projection dominate on GPU;
# the numpy fallback scales with coverage). Measured on Modal 2026-10-19: L4 ≈ 180 ms per 1M splats at 1080p
# (gsplat 1.5.3); L40S assumed ~2x faster; numpy ≈ 8 s for 79k splats at 720p on an M-series CPU.
SPLAT_SECONDS_PER_M: dict[str, float] = {"cpu": 150.0, "gpu-l4": 0.18, "gpu-l40s": 0.09}
# Host-side work per gsplat frame (GPU readback, background, composite, PNG), per 1080p of pixels.
# Measured 2026-10-20 on an L4: a 48-frame 720×720 turntable of a 79k-splat capture spent ~0.30 s/frame
# here versus ~0.01 s rasterising.
GSPLAT_HOST_SECONDS_PER_1080P = 1.2
DEFAULT_SPLATS = 1_000_000

# Engine start + scene load (seconds); .blend/.fbx/.abc via IR add an extraction pass.
# Calibrated 2026-10-20 from production usage (20th-percentile billed seconds of single-frame chunks,
# where load dominates): Eevee 5.5, Workbench 6.6, Mitsuba GPU 4.1 / CPU 5.9, three.js 5.0, Cycles CPU 5.2.
LOAD_SECONDS: dict[str, float] = {
    "blender-cycles": 7.0, "blender-eevee": 5.5, "blender-workbench": 6.0, "mitsuba": 4.5, "threejs": 5.0,
    "gsplat": 6.0,  # torch + CUDA init + file load
    "filament": 4.0, "pbrt": 4.0,
}
GPU_ONLY = {"blender-eevee", "blender-workbench", "gsplat"}
CPU_ONLY = {"filament", "pbrt"}  # their worker images are CPU containers
MIN_FRAME_SECONDS = {"filament": 0.3, "threejs": 0.15, "blender-workbench": 0.2, "blender-eevee": 0.5, "gsplat": 0.05}

# Fan-out economics. Each extra container costs its load overhead o (start + scene load, billed);
# N containers on W seconds of work: cost ≈ N·o + W, wall ≈ o + W/N.
#   economy  — fewest containers: one, unless a container would exceed ECONOMY_CHUNK_SECONDS of work
#   standard — split only while load stays ≤ 10% of each chunk (chunk work ≥ 9·o)
#   rush     — as wide as possible while load stays ≤ 50% of each chunk (chunk work ≥ o)
# Shared with web/src/lib/estimate.ts via spec/fixtures/estimator/fanout.json.
OVERHEAD_TARGET = {"economy": 0.05, "standard": 0.10, "rush": 0.5}
MAX_CONTAINERS = {"economy": 10, "standard": 50, "rush": 50}
ECONOMY_CHUNK_SECONDS = 1800.0

# Light baking (output.format "glb" + output.bake): samples per texel by quality, per baker.
BAKE_SAMPLES: dict[str, dict[str, int]] = {
    "blender-cycles": {"draft": 64, "standard": 256, "final": 1024},
    "mitsuba": {"draft": 64, "standard": 256, "final": 1024},
    "threejs": {"draft": 64, "standard": 192, "final": 384},  # shadow-mapped light directions accumulated
}
BAKE_ENGINES = ("blender-cycles", "mitsuba", "threejs")

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
    est_wall_seconds: tuple[float, float] = (0.0, 0.0)
    splats: bool = False  # the scene has Gaussian splats (the worker routes these to the splat-capable image)
    splat_bake: dict | None = None  # output.format "splat": resolved splatbake.BakeSettings (frames = training views)

    def to_json(self) -> dict:
        d = asdict(self)
        d["est_seconds"] = list(self.est_seconds)
        d["est_wall_seconds"] = list(self.est_wall_seconds)
        return d

    @classmethod
    def from_json(cls, d: dict) -> "RenderPlan":
        d = dict(d)
        d["est_seconds"] = tuple(d.get("est_seconds", (0, 0)))
        d["est_wall_seconds"] = tuple(d.get("est_wall_seconds", (0, 0)))
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
    # Draft previews of mesh formats go to three.js on CPU first: measured 7.3 s billed at the CPU rate vs
    # ~9.5 s on an L4 for Workbench/Eevee (≈2.7× cheaper), and CPU capacity never waits for a free GPU.
    elif fmt in ("stl", "obj", "ply", "3mf", "dae", "step", "iges"):
        c = {"draft": ["threejs", "blender-workbench", "mitsuba"],
             "standard": ["mitsuba", "blender-cycles", "threejs"],
             "final": ["blender-cycles", "mitsuba"]}[quality]
    else:  # gltf, usd, rir
        c = ["threejs", "blender-eevee", "mitsuba"] if quality == "draft" else ["blender-cycles", "mitsuba"]
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
        host = GSPLAT_HOST_SECONDS_PER_1080P * w * h / 2.07e6
        return max((splat_s or 0.0) + host, MIN_FRAME_SECONDS["gsplat"])
    eff_samples = samples
    if engine == "blender-cycles" and pkg.render.denoise:
        # adaptive sampling stops early on clean regions; with OIDN the low-sample threshold is loose
        # (measured 2026-10-20: 72-frame 800² draft turntable ≈ 0.5 s/frame on an L4)
        eff_samples = samples * 0.45
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
    if engine in CPU_ONLY:
        if req == "gpu":
            warnings.append(f"{engine} runs on CPU containers; ignoring render.hardware=gpu")
        return "cpu"
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


def fanout_count(n_frames: int, load_s: float, frame_s: float, priority: str) -> int:
    """How many containers to split `n_frames` over (see the fan-out economics above)."""
    if n_frames <= 1:
        return 1
    work = n_frames * max(frame_s, 1e-3)
    if priority == "economy":
        n = math.ceil(work / ECONOMY_CHUNK_SECONDS)
    else:
        target = OVERHEAD_TARGET[priority]
        min_chunk_work = load_s * (1 - target) / target
        n = math.floor(work / max(min_chunk_work, 1e-3))
    return int(min(max(n, 1), MAX_CONTAINERS[priority], n_frames))


def chunk_frames(frames: list[int], load_s: float, frame_s: float, priority: str) -> list[list[int]]:
    n = fanout_count(len(frames), load_s, frame_s, priority)
    per = math.ceil(len(frames) / n)
    return [frames[i : i + per] for i in range(0, len(frames), per)]


def wall_seconds(chunks: list[list[int]], load_s: float, frame_s: float, extra: float = 0.0) -> float:
    """Wall time with every chunk running in parallel: one load + the longest chunk + serial extras."""
    longest = max((len(c) for c in chunks), default=1)
    return load_s + longest * frame_s + extra


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
    if pkg.output.is_splat:
        return _plan_splat_bake(pkg, asset_paths, fmt, priority, available, triangles, asset_hashes,
                                engine_versions, prices)
    quality = pkg.render.quality
    include_fmts = include_formats(pkg, asset_paths)
    has_splats = fmt == "splat" or "splat" in include_fmts
    mixed = has_splats and (fmt != "splat" or any(f != "splat" for f in include_fmts))
    splats = (splat_count or DEFAULT_SPLATS) if has_splats else None

    if pkg.output.is_model:
        return _plan_model(pkg, asset_paths, fmt, priority, available, triangles, asset_hashes, engine_versions, warnings)

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

    load_s = LOAD_SECONDS[engine] + (6.0 if fmt in ("blend", "fbx", "abc") and not engine.startswith("blender") else 0.0) \
        + (5.0 if fmt in ("step", "iges") else 0.0)  # OpenCASCADE tessellation (cached afterwards)
    if has_splats and engine in ("blender-cycles", "blender-eevee", "blender-workbench", "mitsuba"):
        load_s += 4.0  # splat file + rasteriser init for the composited layer
    from . import effects, post as post_mod

    frame_s = frame_seconds(engine, hardware, pkg, samples, complexity, splats) * effects.frame_cost_factor(pkg, engine)
    if post_mod.is_active(pkg.post, True):
        frame_s += 0.35 * pkg.render.resolution[0] * pkg.render.resolution[1] / 2.07e6  # numpy post stage, per 1080p frame
    warnings += effects.warnings_for(pkg, engine)
    chunks = chunk_frames(frames, load_s, frame_s, priority)
    encode_s = 0.05 * len(frames) * (pkg.render.resolution[0] * pkg.render.resolution[1] / 2.07e6) if pkg.output.is_video else 0.0
    total = len(chunks) * load_s + len(frames) * frame_s + encode_s
    # one chunk encodes in its own container; several need a separate encode container (start + download)
    encode_wall = encode_s + (5.0 if pkg.output.is_video and len(chunks) > 1 else 0.0)
    wall = wall_seconds(chunks, load_s, frame_s, encode_wall)

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
        est_wall_seconds=(round(wall * 0.6, 1), round(wall * 1.8, 1)),
    )


def _plan_model(pkg, asset_paths, fmt, priority, available, triangles, asset_hashes, engine_versions, warnings) -> RenderPlan:
    """GLB output: a plain conversion (engine "export", CPU) or a light bake (one chunk, usually on a GPU)."""
    bake = pkg.output.bake
    tris = triangles or 200_000
    extract_s = 6.0 if fmt in ("blend", "fbx", "abc") else 0.0
    if fmt == "splat" and not include_formats(pkg, asset_paths):
        raise RuntimeError("Gaussian splats can't be exported or baked to GLB (they have no surfaces); "
                           "use a mesh scene, or render the splats to an image instead")
    if bake is None:
        engine, hardware, samples = "export", "cpu", 0
        load_s, frame_s = 3.0 + extract_s, 1.0 + tris / 150_000
    else:
        if pkg.render.engine == "auto":
            cands = [e for e in BAKE_ENGINES if available is None or e in available]
            if not cands:
                raise RuntimeError("no baker available here (needs Blender, Mitsuba or three.js); run with --cloud")
            engine = cands[0]
        else:
            engine = pkg.render.engine
            if engine not in BAKE_ENGINES:
                raise RuntimeError(f"{engine} can't bake; use auto, blender-cycles, mitsuba or threejs")
            if available is not None and engine not in available:
                raise RuntimeError(f"engine {engine} isn't available here; run `renda engines` or bake with --cloud")
        samples = bake.samples or BAKE_SAMPLES[engine][pkg.render.quality]
        if engine == "threejs" or pkg.render.hardware == "cpu":
            hardware = "cpu"
        else:
            hardware = "gpu-l40s" if priority == "rush" and bake.size >= 4096 else "gpu-l4"
        texels = bake.size * bake.size * 0.6  # typical atlas coverage
        unwrap = 2.0 + tris / 60_000
        if engine == "threejs":
            frame_s = unwrap + samples * (0.04 + bake.size * bake.size / 4e7)
        else:
            kind = "gpu" if hardware.startswith("gpu") else "cpu"
            ao = 0.25 if bake.mode == "ao" else 1.0
            work = texels * samples * ENGINE_COST[engine][kind] * ao * complexity_factor(triangles)
            frame_s = unwrap + work / THROUGHPUT[hardware]
        load_s = LOAD_SECONDS[engine] + extract_s
    total = load_s + frame_s
    key = ""
    if asset_hashes is not None or asset_paths:
        from .cache import cache_key, sha256_file

        hashes = dict(asset_hashes or {})
        if not hashes and asset_paths:
            hashes = {k: sha256_file(p) for k, p in asset_paths.items() if Path(p).is_file()}
        key = cache_key(pkg, hashes, engine_versions or {engine: "cloud"})
    return RenderPlan(
        engine=engine, hardware=hardware, frames=[1], chunks=[[1]],
        est_seconds=(round(total * 0.6, 1), round(total * 1.8, 1)), warnings=warnings, cache_key=key,
        scene_format=fmt, samples=samples, priority=priority,
        est_load_seconds=round(load_s, 2), est_frame_seconds=round(frame_s, 3), splats=False,
    )


# Splat-bake refinement: seconds per 1000 steps at 768² with ~450k Gaussians (gsplat MCMC; production
# 2026-10-20: 7k steps ≈ 190–230 s on an L4 → ~30 s/1k incl. eval), scaled by view resolution, plus a fixed
# start (torch/CUDA init, scene build, view decode, geometry targets, export). Training usually stops early
# on the PSNR plateau, so `steps` is an upper bound.
TRAIN_SECONDS_PER_K_STEPS: dict[str, float] = {"gpu-l4": 30.0, "gpu-l40s": 12.0, "cpu": 0.0}
TRAIN_START_SECONDS = 15.0
# Fan the training views out to parallel containers only when rendering them inside the trainer would add
# more than this much wall time; economy never fans out, rush fans out past 20 s of view rendering.
SINGLE_CONTAINER_MAX_VIEW_SECONDS = {"economy": math.inf, "standard": 240.0, "rush": 20.0}
TRAINER_ENGINES = {"blender-cycles", "blender-eevee", "blender-workbench", "mitsuba"}  # in the splat image


def train_seconds(settings: dict) -> float:
    if not settings.get("views"):
        return 0.0
    res = (settings["resolution"] / 768) ** 2
    hw = settings["train_hardware"]
    return TRAIN_START_SECONDS + settings["steps"] / 1000 * TRAIN_SECONDS_PER_K_STEPS[hw] * max(res, 0.25)


def _plan_splat_bake(pkg: RendaPackage, asset_paths, fmt: str, priority: str, available, triangles,
                     asset_hashes, engine_versions, prices) -> RenderPlan:
    """output.format "splat": the training views (frames), rendered either inside the training container
    (small jobs: no extra container overheads or Volume round trip) or fanned out to parallel chunks that
    stream into a trainer that starts as soon as the first views land; preset fast is one CPU chunk."""
    from . import splatbake

    warnings: list[str] = []
    if fmt == "splat":
        raise ValueError('the scene is already a Gaussian splat; use `renda convert` to change its format')
    settings = splatbake.resolve_settings(pkg)
    ignored = splatbake.ignored_view_settings(pkg)
    if ignored:
        warnings.append(f"splat bake: {', '.join(ignored)} ignored — training views are sharp pinhole renders "
                        "(lens/blur/screen-space effects would bake in as view-inconsistent noise)")
    if settings.cameras and settings.preset == "fast" and not settings.views:
        warnings.append("splat bake: preset fast converts the mesh directly; splatBake.cameras need preset standard or high")
    key = ""
    if asset_hashes is not None or asset_paths:
        from .cache import cache_key, sha256_file

        hashes = dict(asset_hashes or {})
        if not hashes and asset_paths:
            hashes = {k: sha256_file(p) for k, p in asset_paths.items() if Path(p).is_file()}
        key = cache_key(pkg, hashes, engine_versions or {"splatbake": "cloud"})
    if settings.direct:
        load_s = LOAD_SECONDS["mitsuba"] + (6.0 if fmt in ("blend", "fbx", "abc") else 0.0)
        est = load_s + settings.init_points / 1e5 * 1.5
        return RenderPlan(engine=splatbake.FAST_ENGINE, hardware="cpu", frames=[1], chunks=[[1]],
                          est_seconds=(round(est * 0.6, 1), round(est * 1.8, 1)), warnings=warnings, cache_key=key,
                          scene_format=fmt, priority=priority, est_load_seconds=load_s, est_frame_seconds=est,
                          est_wall_seconds=(round(est * 0.6, 1), round(est * 1.8, 1)), splat_bake=settings.to_json())

    vpkg = splatbake.view_package(pkg, settings)
    if settings.view_engine_auto:
        # Cycles or Eevee is decided from the scene's materials once it's loaded (both run in the Blender
        # image): plan, route and estimate as Cycles, the conservative choice.
        if available is None or "blender-cycles" in available:
            engine = "blender-cycles"
        else:
            cands = [e for e in choose_engine(fmt, vpkg.render.quality, available) if e != "gsplat"]
            if not cands:
                raise RuntimeError(f"no mesh engine available for .{fmt} here; render with --cloud")
            engine = cands[0]
            settings.view_engine_auto = False
    else:
        engine = vpkg.render.engine
    settings.engine = engine
    frames = list(range(1, settings.views + 1))
    samples = samples_for(engine, vpkg.render.quality, vpkg.render.samples)
    if engine == "threejs":
        samples = min(samples, 4)
    complexity = complexity_factor(triangles)
    hardware = choose_hardware(engine, vpkg, priority, samples, complexity, len(frames), warnings, prices)
    load_s = LOAD_SECONDS[engine] + (6.0 if fmt in ("blend", "fbx", "abc") and not engine.startswith("blender") else 0.0)
    frame_s = frame_seconds(engine, hardware, vpkg, samples, complexity)
    # inside the trainer the views render on the training GPU
    train_hw = settings.train_hardware
    frame_s_single = frame_seconds(engine, train_hw, vpkg, samples, complexity) if engine != "threejs" else frame_s
    if settings.fan_out is True:
        single = False
    elif settings.fan_out is False:
        single = True
    else:
        single = len(frames) * frame_s_single <= SINGLE_CONTAINER_MAX_VIEW_SECONDS[priority] and settings.preset != "high"
    if single and engine not in TRAINER_ENGINES:
        if settings.fan_out is False:
            warnings.append(f"splat bake: {engine} views can't render inside the training container; fanning out")
        single = False
    settings.single_container = single
    settings.stream = not single
    data = settings.to_json()
    t_train = train_seconds(data)
    if single:
        chunks = [frames]
        # views render on the training GPU, sharing its one scene load
        frame_s = frame_s_single
        view_s = len(frames) * frame_s
        total = view_s + t_train
        wall = view_s + t_train
        hardware = train_hw
    else:
        view_s = len(frames) * frame_s
        # an explicit fanOut asks for parallelism: split as wide as rush would
        chunks = chunk_frames(frames, load_s, frame_s, "rush" if settings.fan_out is True else priority)
        total = len(chunks) * load_s + view_s + t_train
        # the trainer starts once the first views land, so rendering overlaps training's start
        wall = wall_seconds(chunks, load_s, frame_s) + t_train * 0.9
    return RenderPlan(
        engine=engine, hardware=hardware, frames=frames, chunks=chunks,
        est_seconds=(round(total * 0.6, 1), round(total * 1.8, 1)), warnings=warnings, cache_key=key,
        scene_format=fmt, samples=samples, priority=priority, est_load_seconds=round(load_s, 2),
        est_frame_seconds=round(frame_s, 3), splat_bake=data,
        est_wall_seconds=(round(wall * 0.6, 1), round(wall * 1.8, 1)),
    )


def priority_estimates(pkg: RendaPackage, asset_paths: dict[str, Path] | None, prices: dict[str, float] | None = None,
                       **kw) -> dict[str, dict]:
    """Cost and wall-time ranges for each priority, so callers can trade money for latency."""
    out = {}
    for pr in ("economy", "standard", "rush"):
        p = plan(pkg, asset_paths, pr, prices=prices, **kw)
        single = bool((p.splat_bake or {}).get("single_container"))
        out[pr] = {"hardware": p.hardware, "containers": 1 if single else len(p.chunks),
                   "estCostUsd": list(estimate_cost(p, prices)), "estWallSeconds": list(p.est_wall_seconds),
                   "estSeconds": list(p.est_seconds)}
    return out


def estimate_cost(plan: RenderPlan, prices: dict[str, float] | None = None) -> tuple[float, float]:
    """USD range for a plan: per-second billing at the hardware class's per-minute price (a splat bake's
    refinement step is priced at its own class)."""
    prices = prices or DEFAULT_PRICES
    p = prices.get(plan.hardware, DEFAULT_PRICES.get(plan.hardware, 0.06))
    lo, hi = plan.est_seconds
    if plan.splat_bake and plan.splat_bake.get("views") and plan.splat_bake.get("single_container"):
        return (round(lo / 60 * p, 4), round(hi / 60 * p, 4))
    if plan.splat_bake and plan.splat_bake.get("views"):
        t = train_seconds(plan.splat_bake)
        pt = prices.get(plan.splat_bake["train_hardware"], DEFAULT_PRICES["gpu-l4"])
        return (round((lo - t * 0.6) / 60 * p + t * 0.6 / 60 * pt, 4), round((hi - t * 1.8) / 60 * p + t * 1.8 / 60 * pt, 4))
    return (round(lo / 60 * p, 4), round(hi / 60 * p, 4))
