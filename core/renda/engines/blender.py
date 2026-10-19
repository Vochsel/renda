"""Blender adapter (Cycles, EEVEE, Workbench) — drives blender_scripts/render.py in a subprocess."""

from __future__ import annotations

import subprocess
import time
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..ir.loaders.blender import available as blender_available, blender_binary, run_blender_script
from .base import ADAPTIVE_THRESHOLD, Capabilities, Engine, RenderContext, RenderResult, frame_filename, samples_for

# Formats Blender's own importers handle; everything else is converted to .glb via the IR first.
NATIVE = frozenset({"blend", "usd", "gltf", "fbx", "obj", "stl", "ply", "abc"})


@lru_cache(maxsize=1)
def blender_version() -> str | None:
    b = blender_binary()
    if b:
        try:
            out = subprocess.run([b, "--version"], capture_output=True, text=True, timeout=60).stdout
            return out.splitlines()[0].replace("Blender", "").strip() if out else None
        except Exception:
            return None
    try:
        import bpy

        return bpy.app.version_string
    except ImportError:
        return None


class BlenderEngine(Engine):
    accepts = NATIVE

    def __init__(self, flavour: str):
        assert flavour in ("cycles", "eevee", "workbench")
        self.flavour = flavour
        self.name = f"blender-{flavour}"
        self.capabilities = Capabilities(
            gpu=True,
            cpu=flavour == "cycles",
            passes=frozenset({"beauty", "alpha", "depth", "normal", "albedo"}),
            cost_cpu={"cycles": 1.0, "eevee": 6.0, "workbench": 2.0}[flavour],
            cost_gpu={"cycles": 1.0, "eevee": 0.06, "workbench": 0.02}[flavour],
            load_seconds=8.0,
        )

    def available(self) -> bool:
        return blender_available()

    def unavailable_reason(self) -> str | None:
        return "install Blender 4.2+ (on PATH or $RENDA_BLENDER) or `uv tool install 'renda[blender]'`"

    def version(self) -> str | None:
        return blender_version()

    def build_job(self, ctx: RenderContext, frames: list[int], warnings: list[str], hybrid_stage=None,
                  cams: dict | None = None) -> dict:
        from ..scene import asset_path, build_stage, env_map_path, native_setup
        from ..spec import parse_color

        pkg, plan = ctx.pkg, ctx.plan
        fmt = plan.scene_format
        root = asset_path(pkg, pkg.scene.root, ctx.asset_paths)
        includes = []
        scene_lights: list[dict] = []
        overrides_applied_in_ir = False
        bridged_camera = None
        if fmt not in NATIVE:
            # three.js JSON, COLLADA, 3MF, .rir, splat scenes → IR → glTF (materials/overrides baked in;
            # splats are left out of the glTF and composited afterwards)
            if hybrid_stage is not None:
                stage = hybrid_stage
            else:
                stage, w = build_stage(pkg, ctx.asset_paths, ctx.cache_dir)
                warnings += w
            cache = _scratch(ctx, "blender-input")
            root = _gltf_for_blender(stage, cache / "scene.glb")
            src_fmt, source_up, unit_scale = "gltf", stage.up_axis, 1.0
            overrides_applied_in_ir = True
            scene_lights = ir_lights_as_package(stage)  # glTF export drops lights
            bridged_camera = _scene_camera_as_lookat(pkg, stage)  # …and cameras
        else:
            src_fmt, source_up, unit_scale = fmt, pkg.scene.upAxis, pkg.scene.unitScale
            from ..formats import detect

            for inc in pkg.scene.include:
                p = asset_path(pkg, inc.asset, ctx.asset_paths)
                ifmt = pkg.assets[inc.asset].format if inc.asset in pkg.assets and pkg.assets[inc.asset].format else detect(p)
                if ifmt == "splat":
                    continue  # composited after the Blender render
                if ifmt not in NATIVE or inc.upAxis or inc.localTransform or inc.materialOverrides:
                    # Through the IR: scoped overrides and the file-local transform are applied here;
                    # Blender's glTF importer then brings it into Blender's axes like any import, which
                    # is the up-axis conversion.
                    from ..ir import load_stage
                    from ..scene import apply_material_overrides, normalize_up

                    sub = load_stage(p, ifmt, ctx.cache_dir, tessellation=pkg.scene.tessellation)
                    if inc.upAxis:
                        sub.up_axis = inc.upAxis
                    normalize_up(sub)
                    if inc.materialOverrides:
                        apply_material_overrides(sub, inc.materialOverrides)
                    if inc.localTransform:
                        sub.apply_transform(inc.localTransform.to_matrix())
                    cache = _scratch(ctx, "blender-input")
                    p, ifmt = _gltf_for_blender(sub, cache / f"include_{len(includes)}.glb"), "gltf"
                m = inc.transform.to_matrix() if inc.transform else np.eye(4)
                includes.append({"path": str(p), "format": ifmt, "matrix": np.asarray(m).tolist()})

        use_scene_camera, keep_world = native_setup(pkg, fmt)
        env = pkg.environment
        hdri = env_map_path(pkg, ctx.asset_paths)
        bg = env.background
        transparent_film = pkg.output.transparent or bg != "hdri"
        if bg not in ("hdri", "transparent") and pkg.output.format == "exr":
            warnings.append("blender: solid background colours aren't composited into EXR; alpha is kept instead")

        def ov_json(ov) -> dict:
            d = {}
            if ov.baseColor is not None:
                d["baseColor"] = list(parse_color(ov.baseColor)[:3])
            if ov.emission is not None:
                d["emission"] = list(parse_color(ov.emission)[:3])
            for k in ("metallic", "roughness", "emissionStrength", "opacity", "transmission", "ior"):
                v = getattr(ov, k)
                if v is not None:
                    d[k] = v
            return d

        lights = list(scene_lights)
        for l in pkg.lights:
            d = l.model_dump()
            d["color"] = list(parse_color(l.color, (1.0, 1.0, 1.0))[:3])
            lights.append(d)

        tt = None
        if pkg.animation and pkg.animation.turntable:
            a, b = pkg.animation.frames
            tt = {"revolutions": pkg.animation.turntable.revolutions, "axis": pkg.animation.turntable.axis,
                  "frame_start": a, "frame_end": b}

        frame_cameras = None
        camera = bridged_camera or pkg.camera.model_dump()
        passes = [p for p in pkg.output.passes if p != "beauty"]
        if cams:
            frame_cameras = {str(f): c.to_json() for f, c in cams.items()}
            first = cams[frames[0]]
            camera = {"mode": "lookat", "position": first.position.tolist(), "target": first.target.tolist(),
                      "fovDeg": first.fov_y_deg}
            tt = None
            use_scene_camera = False
            transparent_film = True  # meshes only: the compositor adds splats and the background
            if "depth" not in passes:
                passes.append("depth")
        return {
            "input": str(root),
            "format": src_fmt,
            "source_up": source_up,
            "unit_scale": unit_scale,
            "includes": includes,
            "engine": self.flavour,
            "frames": frames,
            "resolution": list(pkg.render.resolution),
            "samples": samples_for(self.name, pkg.render.quality, pkg.render.samples),
            "adaptive_threshold": ADAPTIVE_THRESHOLD[pkg.render.quality],
            "denoise": pkg.render.denoise,
            "seed": pkg.render.seed,
            "gpu": plan.hardware.startswith("gpu"),
            "camera": camera,
            "frame_cameras": frame_cameras,
            "use_scene_camera_default": use_scene_camera,
            "turntable": tt,
            "environment": {"hdri": str(hdri) if hdri else None, "intensity": env.intensity,
                            "rotationDeg": env.rotationDeg, "keep_scene_world": keep_world},
            "transparent_film": transparent_film,
            "lights": lights,
            "material_overrides": {} if overrides_applied_in_ir else {k: ov_json(v) for k, v in pkg.scene.materialOverrides.items()},
            "passes": passes,
            "out_dir": str(ctx.out_dir),
            "image_format": "OPEN_EXR" if pkg.output.format == "exr" else "PNG",
        }

    def render(self, ctx: RenderContext, frames: list[int]) -> RenderResult:
        from .gsplat import composite_files, render_splat_frames, splat_stage_for

        t0 = time.time()
        warnings: list[str] = []
        stage = splat_stage_for(ctx)
        if stage is None:
            job = self.build_job(ctx, frames, warnings)
            try:
                result = run_blender_script("render.py", job)
            finally:
                _cleanup_scratch(job)
            warnings += result.get("warnings", [])
            files = postprocess(ctx, frames, warnings)
            return RenderResult(files, warnings, time.time() - t0)

        warnings += stage.meta.pop("build_warnings", [])
        if not stage.meshes:
            warnings.append(f"{self.name}: the scene is splats only, so Blender was skipped and the splats were "
                            "rasterised directly (engine=gsplat does the same, cheaper)")
            return RenderResult(render_splat_frames(ctx, stage, frames, warnings), warnings, time.time() - t0)
        from ..ir.camera import resolve_cameras

        cams = resolve_cameras(ctx.pkg, stage, frames, warnings)
        job = self.build_job(ctx, frames, warnings, hybrid_stage=stage, cams=cams)
        try:
            result = run_blender_script("render.py", job)
        finally:
            _cleanup_scratch(job)
        warnings += result.get("warnings", [])
        depths = {f: _read_exr_channel(ctx.out_dir / frame_filename("depth", f, "exr")) for f in frames}
        if "depth" not in ctx.pkg.output.passes:
            for f in frames:
                (ctx.out_dir / frame_filename("depth", f, "exr")).unlink(missing_ok=True)
        ext = "exr" if ctx.pkg.output.format == "exr" else "png"
        composite_files(ctx, stage, frames, depths.get, warnings, self.name, cams, src_ext=ext, out_fmt=ext)
        files = postprocess(ctx, frames, warnings)  # format conversion, alpha/passes
        return RenderResult(files, warnings, time.time() - t0)


def _scratch(ctx: RenderContext, name: str) -> Path:
    """Per-render scratch dir. cache_dir can be shared between concurrent jobs (the worker's assets
    Volume), so fixed names there would let renders overwrite each other's inputs."""
    import tempfile

    base = Path(ctx.cache_dir or ctx.out_dir) / name
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=base))


def _cleanup_scratch(job: dict) -> None:
    import shutil

    for p in [job.get("input")] + [i["path"] for i in job.get("includes", [])]:
        if p and "blender-input" in Path(p).parts:
            shutil.rmtree(Path(p).parent, ignore_errors=True)


def ir_lights_as_package(stage) -> list[dict]:
    """IR lights → the package light dicts render.py understands (in the stage's coordinates)."""
    out = []
    for l in stage.lights:
        pos = l.position().tolist()
        d = l.direction()
        color = list(l.color)
        if l.type == "sun":
            out.append({"type": "sun", "direction": d.tolist(), "intensity": l.intensity, "color": color})
        elif l.type == "point":
            out.append({"type": "point", "position": pos, "intensity": l.intensity, "color": color, "radius": l.radius})
        elif l.type == "spot":
            out.append({"type": "spot", "position": pos, "target": (l.position() + d).tolist(), "intensity": l.intensity,
                        "angleDeg": l.angle_deg, "color": color})
        elif l.type == "area":
            out.append({"type": "area", "position": pos, "target": (l.position() + d).tolist(), "intensity": l.intensity,
                        "size": list(l.size), "color": color})
    return out


def postprocess(ctx: RenderContext, frames: list[int], warnings: list[str]) -> list[Path]:
    """Blender writes RGBA PNG/EXR; convert to the requested format, composite solid backgrounds, encode passes."""
    from PIL import Image

    from ..spec import parse_color

    pkg = ctx.pkg
    fmt = pkg.output.format if not pkg.output.is_video else "png"
    bg = pkg.environment.background
    bg_rgb = None
    if bg not in ("hdri", "transparent") and not pkg.output.transparent:
        lin = parse_color(bg)[:3]
        bg_rgb = tuple(int(round(255 * (12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055))) for c in lin)
    wants_alpha = any(p in ("alpha", "mask") for p in pkg.output.passes)
    files: list[Path] = []
    for f in frames:
        if fmt == "exr":
            p = ctx.out_dir / frame_filename("beauty", f, "exr")
            if p.exists():
                files.append(p)
        else:
            src = ctx.out_dir / frame_filename("beauty", f, "png")
            if not src.exists():
                warnings.append(f"blender: frame {f} produced no image")
                continue
            img = Image.open(src)
            img.load()
            if wants_alpha and "A" in img.getbands():
                img.getchannel("A").save(ctx.out_dir / frame_filename("alpha", f, "png"))
                files.append(ctx.out_dir / frame_filename("alpha", f, "png"))
            if bg_rgb is not None:
                base = Image.new("RGBA", img.size, bg_rgb + (255,))
                img = Image.alpha_composite(base, img.convert("RGBA"))
            if not pkg.output.transparent:
                img = img.convert("RGB")
            dst = ctx.out_dir / frame_filename("beauty", f, fmt)
            save_kw = {"quality": 92} if fmt in ("jpg", "webp") else {}
            img.save(dst, format={"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}[fmt], **save_kw)
            if dst != src:
                src.unlink(missing_ok=True)
            files.append(dst)
        for p in pkg.output.passes:
            if p == "beauty":
                continue
            if p in ("alpha", "mask"):
                continue  # written from the beauty alpha above
            exr = ctx.out_dir / frame_filename(p, f, "exr")
            if not exr.exists():
                continue
            if pkg.output.format == "exr":
                files.append(exr)
            else:
                files.append(_exr_pass_to_png(exr, p, warnings))
    return files


def _read_exr_channel(path: Path) -> np.ndarray | None:
    """First channel of an EXR as (H,W) float (Blender's Z pass: planar camera depth)."""
    if not path.exists():
        return None
    try:
        import mitsuba as mi

        if not mi.variant():
            mi.set_variant("scalar_rgb")
        arr = np.array(mi.Bitmap(str(path)), copy=True)
    except Exception:
        return None
    d = arr[..., 0] if arr.ndim == 3 else arr
    return np.where((d > 0) & (d < 1e9), d, np.inf).astype(np.float64)


def _exr_pass_to_png(exr: Path, kind: str, warnings: list[str]) -> Path:
    try:
        import mitsuba as mi

        from ..images import save_pass

        if not mi.variant():
            mi.set_variant("scalar_rgb")
        arr = np.array(mi.Bitmap(str(exr)), copy=False)
        out = save_pass(arr, exr.with_suffix(".png"), kind)
        exr.unlink(missing_ok=True)
        return out
    except Exception:
        warnings.append(f"blender: pass {kind} kept as EXR (install renda[mitsuba] to convert to PNG)")
        return exr


def _scene_camera_as_lookat(pkg, stage) -> dict | None:
    """A file camera that didn't survive the IR → glTF bridge, as an equivalent look-at camera
    (in the stage's own axes, which is what the Blender script expects for package cameras)."""
    if pkg.camera.mode != "scene" or not stage.cameras:
        return None
    from ..ir.camera import _from_scene_camera

    sc = next((c for c in stage.cameras if c.name == pkg.camera.name), None) if pkg.camera.name else stage.cameras[0]
    if sc is None:
        return None
    rc = _from_scene_camera(sc, stage)
    return {"mode": "lookat", "position": rc.position.tolist(), "target": rc.target.tolist(),
            "up": rc.up.tolist(), "fovDeg": rc.fov_y_deg}


# Z-up → Y-up: (x, y, z) → (x, z, -y)
_Z_TO_Y = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=np.float64)


def _gltf_for_blender(stage, out: Path) -> Path:
    """IR → .glb for Blender's importer, which always treats glTF as Y-up (per spec) and rotates
    it to Z-up. to_gltf keeps the stage's own axes (the three.js engine relies on that), so rotate
    Z-up stages (COLLADA, 3MF, STL includes…) to Y-up first or they land on their side."""
    import copy

    from ..ir.exporters import to_gltf

    if stage.up_axis == "Z":
        stage = copy.copy(stage)
        stage.meshes = [copy.copy(m) for m in stage.meshes]
        for m in stage.meshes:
            m.transform = _Z_TO_Y @ np.asarray(m.transform, dtype=np.float64)
        stage.up_axis = "Y"
    return to_gltf(stage, out)

