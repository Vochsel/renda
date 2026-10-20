"""Render entrypoints shared by the CLI and the cloud worker."""

from __future__ import annotations

import re

import shutil
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from .engines import RenderContext, RenderResult, available_engines, engine_versions, get_engine
from .planner import RenderPlan, plan
from .spec import RendaPackage

__all__ = ["RenderResult", "render_frames", "run_local", "LocalRun"]


def render_frames(
    plan: RenderPlan,
    pkg: RendaPackage,
    asset_paths: dict[str, Path],
    out_dir: Path,
    frames: list[int],
    cache_dir: Path | None = None,
) -> RenderResult:
    """Render `frames` (usually one chunk of plan.chunks) into out_dir with plan.engine."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if pkg.output.is_model:  # GLB export / light bake: one output file, no frames
        from .bakers import run_model_job

        named = with_extensions({k: Path(v) for k, v in asset_paths.items()}, Path(cache_dir or out_dir) / "named")
        t0 = time.time()
        result = run_model_job(RenderContext(plan, pkg, named, out_dir, cache_dir))
        result.seconds = time.time() - t0
        return result
    from .splatbake import FAST_ENGINE

    engine = None if plan.engine == FAST_ENGINE else get_engine(plan.engine)
    if engine is not None and not engine.available():
        raise RuntimeError(f"engine {plan.engine} is not available: {engine.unavailable_reason()}")
    named = with_extensions({k: Path(v) for k, v in asset_paths.items()}, Path(cache_dir or out_dir) / "named")
    t0 = time.time()
    if pkg.output.is_splat:  # splat bake: direct conversion, or one chunk of sharp training views (no post)
        result = _splat_bake_frames(plan, pkg, named, out_dir, frames, cache_dir)
    else:
        result = engine.render(RenderContext(plan, pkg, named, out_dir, cache_dir), frames)
        if plan.engine == "gsplat" and pkg.scene.textureOverrides:
            result.warnings.append("gsplat: Gaussian splats have no material textures; textureOverrides were ignored "
                                   "(they apply to meshes in the other engines)")
        apply_post(pkg, plan.engine, result)
    result.seconds = time.time() - t0
    return result


def apply_post(pkg: RendaPackage, engine: str, result: RenderResult) -> None:
    """The shared post stage (renda.post) on every beauty frame, so it looks the same on every engine.
    (Effect support warnings come from the planner, once per job.)"""
    from . import effects, post

    emulate_exposure = effects.status(engine, "exposure") == effects.EMULATED
    if not post.is_active(pkg.post, emulate_exposure):
        return
    for f in result.files:
        m = re.match(r"frame_(\d+)\.(png|jpg|jpeg|webp|exr)$", Path(f).name)
        if m:
            post.apply_file(Path(f), pkg.post, seed=pkg.render.seed * 100003 + int(m.group(1)),
                            emulate_exposure=emulate_exposure, warnings=result.warnings)


def _splat_bake_frames(plan: RenderPlan, pkg: RendaPackage, asset_paths: dict[str, Path], out_dir: Path,
                       frames: list[int], cache_dir: Path | None) -> RenderResult:
    """`output.format: "splat"`: the direct (fast) bake, or one chunk of training views."""
    from . import splatbake
    from .ir.camera import override_cameras
    from .scene import build_stage

    settings = splatbake.BakeSettings.from_json(plan.splat_bake) if plan.splat_bake else splatbake.resolve_settings(pkg)
    stage, warnings = build_stage(pkg, asset_paths, cache_dir)
    if plan.engine == splatbake.FAST_ENGINE:
        files, w = splatbake.direct_bake(stage, pkg, settings, out_dir)
        return RenderResult(files, warnings + w, 0.0)
    settings, notes = splatbake.adapt_to_scene(settings, stage)  # Cycles vs Eevee from the materials
    rig = splatbake.bake_rig(stage, settings)
    vpkg = splatbake.view_package(pkg, settings)
    vplan = replace(plan, engine=settings.engine) if settings.engine != plan.engine else plan
    with override_cameras(rig):
        result = get_engine(vplan.engine).render(RenderContext(vplan, vpkg, asset_paths, out_dir, cache_dir), frames)
    beauty = {splatbake.view_image_name(f) for f in frames}
    result.files = [Path(f) for f in result.files if Path(f).name in beauty]  # views only; no passes
    result.warnings += [n for n in notes if n not in result.warnings]
    return result


_MAGIC_EXT = [
    (b"BLENDER", ".blend"), (b"\x28\xb5\x2f\xfd", ".blend"), (b"glTF", ".glb"), (b"PXR-USDC", ".usdc"),
    (b"#usda", ".usda"), (b"PK\x03\x04", None), (b"ply", None), (b"Kaydara FBX Binary", ".fbx"), (b"Ogawa", ".abc"),
    (b"#?RADIANCE", ".hdr"), (b"#?RGBE", ".hdr"), (b"\x76\x2f\x31\x01", ".exr"), (b"\x89PNG", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
]


def _extension_for(path: Path) -> str | None:
    """Best file extension for a content-addressed blob, from magic bytes / format sniffing."""
    from .formats import UnknownFormat, _zip_format, detect

    with open(path, "rb") as f:
        head = f.read(32)
    for magic, ext in _MAGIC_EXT:
        if head.startswith(magic):
            if ext is None and magic == b"ply":  # mesh/point .ply or a 3DGS splat .ply: both .ply
                return ".ply"
            if ext is None:  # zip: usdz or 3mf
                return {"usd": ".usdz", "3mf": ".3mf"}.get(_zip_format(path) or "")
            return ext
    try:
        fmt = detect(path)
    except (UnknownFormat, OSError):
        return None
    if fmt == "splat":
        from .ir.loaders.splat import kind_of

        return "." + kind_of(path)
    return {"threejs": ".json", "gltf": ".gltf", "obj": ".obj", "stl": ".stl", "ply": ".ply", "dae": ".dae"}.get(fmt)


def with_extensions(asset_paths: dict[str, Path], link_dir: Path) -> dict[str, Path]:
    """Engines (Blender's USD/PLY importers, Mitsuba's image loaders) pick readers by extension, but the
    cloud asset cache stores blobs by sha256 with none. Give such files a correctly-named symlink."""
    from .formats import EXTENSION_FORMATS

    known = set(EXTENSION_FORMATS) | {".hdr", ".exr", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
    out: dict[str, Path] = {}
    for key, path in asset_paths.items():
        if path.suffix.lower() in known or not path.is_file():
            out[key] = path
            continue
        ext = _extension_for(path)
        if not ext:
            out[key] = path
            continue
        link_dir.mkdir(parents=True, exist_ok=True)
        link = link_dir / f"{path.parent.name if path.name == 'blob' else path.stem}{ext}"
        if not link.exists():
            try:
                link.symlink_to(path.resolve())
            except OSError:
                shutil.copy(path, link)
        out[key] = link
    return out


@dataclass
class LocalRun:
    plan: RenderPlan
    outputs: list[Path] = field(default_factory=list)
    frames: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0


def _run_local_splat_bake(p: RenderPlan, pkg: RendaPackage, asset_paths, out_dir: Path, t0: float, progress) -> LocalRun:
    """Local standard/high splat bake: render the views, then refine (CUDA + gsplat required)."""
    from .splatbake.train import cuda_available, train_bake

    if not cuda_available():
        raise RuntimeError("splat bakes with preset standard/high train on a CUDA GPU (torch + gsplat): "
                           "add --cloud, or use --preset fast for a direct mesh → splat conversion")
    run = LocalRun(plan=p, warnings=list(p.warnings))
    cache_dir = Path(tempfile.mkdtemp(prefix="renda-work-"))
    views_dir = cache_dir / "views"
    done = 0
    # single-container bakes render their views inside train_bake (the same path the cloud trainer uses)
    for chunk in ([] if (p.splat_bake or {}).get("single_container") else p.chunks):
        r = render_frames(p, pkg, asset_paths, views_dir, chunk, cache_dir)
        run.warnings += [w for w in r.warnings if w not in run.warnings]
        done += len(chunk)
        if progress:
            progress(done, len(p.frames))
    r = train_bake(p, pkg, asset_paths, views_dir, out_dir, cache_dir)
    run.outputs = list(r.files)
    run.warnings += r.warnings
    shutil.rmtree(cache_dir, ignore_errors=True)
    run.seconds = time.time() - t0
    return run


def run_local(pkg: RendaPackage, base_dir: Path, out_dir: Path, priority: str = "standard", progress=None) -> LocalRun:
    """Full local pipeline: resolve assets → plan (local engines only) → render chunks → encode."""
    from . import assets as assets_mod
    from .video import encode

    t0 = time.time()
    asset_paths = assets_mod.resolve(pkg, base_dir)
    p = plan(pkg, asset_paths, priority, available=set(available_engines()), engine_versions=engine_versions())
    if p.splat_bake and p.splat_bake.get("views"):
        return _run_local_splat_bake(p, pkg, asset_paths, Path(out_dir), t0, progress)
    out_dir = Path(out_dir)
    frames_dir = out_dir / "frames" if pkg.output.is_video else out_dir
    run = LocalRun(plan=p, warnings=list(p.warnings))
    cache_dir = Path(tempfile.mkdtemp(prefix="renda-work-"))
    done = 0
    for chunk in p.chunks:
        r = render_frames(p, pkg, asset_paths, frames_dir, chunk, cache_dir)
        run.frames += r.files
        run.warnings += [w for w in r.warnings if w not in run.warnings]
        done += len(chunk)
        if progress:
            progress(done, len(p.frames))
    if pkg.output.is_video:
        beauty = sorted(f for f in run.frames if f.name.startswith("frame_"))
        fps = pkg.animation.fps if pkg.animation else 30
        # `name` is free text ("Turntable MP4 from .glb"); make it a safe file stem.
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", pkg.name or "").strip("-")[:80] or "render"
        run.outputs.append(encode(beauty, fps, pkg.output, out_dir / f"{name}.{pkg.output.format}"))
        if pkg.output.keepFrames:
            run.outputs += run.frames
    else:
        run.outputs = run.frames
    shutil.rmtree(cache_dir, ignore_errors=True)
    run.seconds = time.time() - t0
    return run
