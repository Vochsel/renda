"""gsplat engine: renders Gaussian splat scenes directly (nerfstudio gsplat on CUDA, numpy reference
otherwise). The cheapest, fastest engine for splat-only scenes; meshes in the scene are ignored
(use threejs, blender-* or mitsuba for mixed scenes).

Also hosts the helpers the hybrid path uses: background images, display-space saving and the
"splats over a rendered frame" compositor.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from .base import Capabilities, Engine, RenderContext, RenderResult, frame_filename
from .splat_raster import backend_name, composite_over, env_background, gsplat_available, rasterize, read_env


def srgb_to_linear(x: np.ndarray) -> np.ndarray:
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def save_display(rgb: np.ndarray, alpha: np.ndarray, path: Path, fmt: str, transparent: bool) -> Path:
    """Display-space straight-alpha RGB → file (EXR gets linearised premultiplied RGBA)."""
    from PIL import Image

    path = Path(path)
    if fmt == "exr":
        from ..images import save_exr

        lin = srgb_to_linear(np.clip(rgb, 0, 1)) * alpha[..., None]
        return save_exr(np.dstack([lin, alpha]).astype(np.float32), path)
    rgb8 = (np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)
    if transparent and fmt in ("png", "webp"):
        img = Image.fromarray(np.dstack([rgb8, (np.clip(alpha, 0, 1) * 255 + 0.5).astype(np.uint8)]), "RGBA")
    else:
        img = Image.fromarray(rgb8, "RGB")
    img.save(path, format={"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}[fmt], **({"quality": 92} if fmt in ("jpg", "webp") else {}))
    return path


def background_for(ctx: RenderContext, stage, cam, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """What sits behind the splats when nothing else is rendered: (display RGB, alpha)."""
    from ..scene import env_map_path
    from ..spec import parse_color

    pkg = ctx.pkg
    bg = pkg.environment.background
    if pkg.output.transparent or bg == "transparent":
        return np.zeros((height, width, 3)), np.zeros((height, width))
    if bg == "hdri":
        path = env_map_path(pkg, ctx.asset_paths)
        if path is not None:
            env = _env_cache(str(path))
            rgb = env_background(env, cam, width, height, stage.up_axis, pkg.environment.rotationDeg, pkg.environment.intensity)
            return rgb, np.ones((height, width))
        return np.zeros((height, width, 3)), np.ones((height, width))
    from ..images import linear_to_srgb

    c = linear_to_srgb(np.asarray(parse_color(bg)[:3], dtype=np.float64))
    return np.broadcast_to(c, (height, width, 3)).copy(), np.ones((height, width))


_ENV: dict[str, np.ndarray] = {}


def _env_cache(path: str) -> np.ndarray:
    if path not in _ENV:
        _ENV[path] = read_env(path)
    return _ENV[path]


def splats_of(stage):
    from ..ir.splats import Splats

    return Splats.concat(stage.splats)


def write_frame_outputs(ctx: RenderContext, frame: int, rgb: np.ndarray, alpha: np.ndarray, depth: np.ndarray | None,
                        files: list[Path], passes_supported: set[str]) -> None:
    from ..images import save_pass

    pkg = ctx.pkg
    fmt = pkg.output.format if not pkg.output.is_video else "png"
    transparent = pkg.output.transparent or pkg.environment.background == "transparent"
    files.append(save_display(rgb, alpha, ctx.out_dir / frame_filename("beauty", frame, fmt), fmt, transparent and fmt != "jpg"))
    for p in pkg.output.passes:
        if p == "beauty" or p not in passes_supported:
            continue
        ext = "exr" if pkg.output.format == "exr" else "png"
        if p in ("alpha", "mask"):
            files.append(save_pass(alpha, ctx.out_dir / frame_filename(p, frame, ext), "alpha"))
        elif p == "depth" and depth is not None:
            d = np.where(np.isfinite(depth), depth, 0.0).astype(np.float32)
            files.append(save_pass(d, ctx.out_dir / frame_filename("depth", frame, ext), "depth"))


def render_splat_frames(ctx: RenderContext, stage, frames: list[int], warnings: list[str],
                        passes_supported: set[str] = frozenset({"beauty", "alpha", "mask", "depth"})) -> list[Path]:
    """Splats (no meshes) over the package background, one file set per frame."""
    from ..ir.camera import resolve_cameras

    pkg = ctx.pkg
    w, h = pkg.render.resolution
    cams = resolve_cameras(pkg, stage, frames, warnings)
    splats = splats_of(stage)
    if backend_name() == "numpy" and len(splats) * w * h > 2e11:
        warnings.append(f"splats: {len(splats):,} splats on the CPU rasteriser is slow; render on a GPU (cloud) for speed")
    files: list[Path] = []
    for f in frames:
        layer = rasterize(splats, cams[f], w, h)
        bg_rgb, bg_a = background_for(ctx, stage, cams[f], w, h)
        none = np.zeros((h, w))
        rgb, alpha = composite_over(layer, np.zeros((h, w, 3)), none, None, bg_rgb, bg_a)
        write_frame_outputs(ctx, f, rgb, alpha, layer.depth, files, set(passes_supported))
    return files


class GsplatEngine(Engine):
    name = "gsplat"
    accepts = frozenset({"splat"})
    capabilities = Capabilities(
        gpu=True, cpu=False, passes=frozenset({"beauty", "alpha", "mask", "depth"}), animation=False,
        scene_lights=False, textures=False, cost_cpu=40.0, cost_gpu=0.02, load_seconds=6.0,
    )

    def available(self) -> bool:
        import os

        return gsplat_available() or os.environ.get("RENDA_SPLAT_BACKEND") == "numpy"

    def unavailable_reason(self) -> str | None:
        return ("needs an NVIDIA GPU with torch + gsplat (render with --cloud), "
                "or set RENDA_SPLAT_BACKEND=numpy for the slow CPU rasteriser; engine=threejs renders splats locally")

    def version(self) -> str | None:
        try:
            import gsplat

            return f"gsplat {gsplat.__version__}"
        except Exception:
            return "renda splat raster (numpy)"

    def render(self, ctx: RenderContext, frames: list[int]) -> RenderResult:
        from ..scene import build_stage

        t0 = time.time()
        stage, warnings = build_stage(ctx.pkg, ctx.asset_paths, ctx.cache_dir)
        if not stage.splats:
            raise RuntimeError("the gsplat engine renders Gaussian splats; this scene has none (use another engine)")
        if stage.meshes:
            warnings.append(f"gsplat: {len(stage.meshes)} meshes ignored; render mixed mesh + splat scenes with "
                            "threejs, blender-cycles/eevee or mitsuba")
        if ctx.pkg.lights:
            warnings.append("gsplat: splats carry baked lighting; package lights don't affect them")
        if not gsplat_available():
            warnings.append("gsplat: no CUDA here, used the (slow) numpy rasteriser")
        unsupported = [p for p in ctx.pkg.output.passes if p not in self.capabilities.passes]
        if unsupported:
            warnings.append(f"gsplat: passes {unsupported} unsupported, skipped")
        files = render_splat_frames(ctx, stage, frames, warnings)
        return RenderResult(files, warnings, time.time() - t0)


# -- hybrid: mesh engine frame + splats -------------------------------------------------------

HYBRID_NOTE = ("splats are composited over the {engine} render by depth; they don't receive {engine} "
               "lighting or shadows and don't appear in reflections")


def composite_files(ctx: RenderContext, stage, frames: list[int], depth_for_frame, warnings: list[str],
                    engine: str, cams, src_ext: str = "png", out_fmt: str | None = None) -> dict[int, Path]:
    """Engine frames (mesh only, transparent background) + splats + package background → final beauty.

    Reads `beauty_<f>.<src_ext>` (straight-alpha PNG or premultiplied linear EXR), writes the composite
    as `beauty_<f>.<out_fmt>` (default: the package format) and returns {frame: path}.
    depth_for_frame(frame) → the engine's planar mesh depth (H,W) or None."""
    from PIL import Image

    pkg = ctx.pkg
    w, h = pkg.render.resolution
    splats = splats_of(stage)
    out_fmt = out_fmt or (pkg.output.format if not pkg.output.is_video else "png")
    transparent = pkg.output.transparent or pkg.environment.background == "transparent"
    warnings.append(HYBRID_NOTE.format(engine=engine))
    out: dict[int, Path] = {}
    for f in frames:
        src = ctx.out_dir / frame_filename("beauty", f, src_ext)
        if not src.exists():
            continue
        if src_ext == "exr":
            from ..images import linear_to_srgb

            import mitsuba as mi

            if not mi.variant():
                mi.set_variant("scalar_rgb")
            arr = np.array(mi.Bitmap(str(src)), copy=True)
            a = arr[..., 3] if arr.shape[-1] > 3 else np.ones(arr.shape[:2])
            mesh = linear_to_srgb(np.clip(arr[..., :3] / np.where(a > 1e-6, a, 1)[..., None], 0, None))
        else:
            arr = np.asarray(Image.open(src).convert("RGBA"), dtype=np.float64) / 255.0
            mesh, a = arr[..., :3], arr[..., 3]
        layer = rasterize(splats, cams[f], w, h)
        bg_rgb, bg_a = background_for(ctx, stage, cams[f], w, h)
        rgb, alpha = composite_over(layer, mesh, a, depth_for_frame(f), bg_rgb, bg_a)
        dst = ctx.out_dir / frame_filename("beauty", f, out_fmt)
        save_display(rgb, alpha, dst, out_fmt, transparent and out_fmt != "jpg")
        if dst != src:
            src.unlink(missing_ok=True)
        out[f] = dst
    return out


def splat_stage_for(ctx: RenderContext):
    """The full stage when the scene has splats anywhere (root or includes), else None."""
    from ..formats import UnknownFormat, detect
    from ..scene import asset_path, build_stage

    pkg = ctx.pkg
    refs = [pkg.scene.root] + [i.asset for i in pkg.scene.include]
    has = ctx.plan.scene_format == "splat"
    for ref in refs[1:]:
        try:
            has = has or detect(asset_path(pkg, ref, ctx.asset_paths)) == "splat"
        except (UnknownFormat, KeyError, OSError):
            pass
    if not has:
        return None
    stage, warnings = build_stage(pkg, ctx.asset_paths, ctx.cache_dir)
    stage.meta["build_warnings"] = warnings
    return stage if stage.splats else None

