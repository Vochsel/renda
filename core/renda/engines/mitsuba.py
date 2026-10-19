"""Mitsuba 3 adapter. Everything goes through the IR (Mitsuba has no .blend/glTF loaders)."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from .base import Capabilities, Engine, RenderContext, RenderResult, frame_filename, samples_for

_VARIANT: str | None = None


def _gpu_wanted(hardware: str) -> bool:
    return hardware.startswith("gpu")


def _planar_depth(d: np.ndarray, cam, width: int, height: int) -> np.ndarray:
    """Mitsuba's depth AOV is the distance along each camera ray; the compositor wants planar depth."""
    from .splat_raster import ray_directions

    d = d[..., 0] if d.ndim == 3 else d
    fwd = cam.target - cam.position
    fwd = fwd / (np.linalg.norm(fwd) or 1)
    cos = ray_directions(cam, width, height) @ fwd
    return np.where(d > 0, d * cos, np.inf)


def _optix_denoiser_weights() -> bool:
    import glob

    return any(glob.glob(p) for p in ("/usr/share/nvidia/nvoptix.bin", "/usr/lib/x86_64-linux-gnu/nvoptix.bin"))


def pick_variant(prefer_gpu: bool) -> str:
    """cuda (Linux GPU) > metal (Apple) > llvm (vectorised CPU) > scalar."""
    import mitsuba as mi

    have = set(mi.variants())
    order = (["cuda_ad_rgb"] if prefer_gpu else []) + ["metal_ad_rgb", "llvm_ad_rgb", "scalar_rgb"]
    for v in order:
        if v not in have:
            continue
        try:
            mi.set_variant(v)
            # a variant can import but have no backend (no CUDA driver / no libLLVM): probe it
            mi.load_dict({"type": "scene"})
            return v
        except Exception:
            continue
    raise RuntimeError("no usable Mitsuba variant")


class MitsubaEngine(Engine):
    name = "mitsuba"
    accepts = frozenset({"rir"})
    capabilities = Capabilities(
        gpu=True, passes=frozenset({"beauty", "alpha", "depth", "normal", "albedo"}),
        animation=False, cost_cpu=2.5, cost_gpu=0.9, load_seconds=4.0,
    )

    def available(self) -> bool:
        try:
            import mitsuba  # noqa: F401
        except ImportError:
            return False
        return True

    def unavailable_reason(self) -> str | None:
        return "uv tool install 'renda[mitsuba]'  (or: uvx --from 'renda[mitsuba]' renda …)"

    def version(self) -> str | None:
        try:
            import mitsuba as mi

            return mi.__version__
        except ImportError:
            return None

    def render(self, ctx: RenderContext, frames: list[int]) -> RenderResult:
        global _VARIANT
        import mitsuba as mi

        from ..ir.camera import resolve_cameras
        from ..ir.exporters.mitsuba import TextureCache, stage_to_dict
        from ..scene import build_stage, env_map_path, native_setup, package_lights
        from ..spec import parse_color

        t0 = time.time()
        pkg, plan = ctx.pkg, ctx.plan
        warnings: list[str] = []
        variant = pick_variant(_gpu_wanted(plan.hardware))
        _VARIANT = variant
        if plan.hardware.startswith("gpu") and not variant.startswith(("cuda", "metal")):
            warnings.append(f"mitsuba: no GPU backend available, rendered with {variant}")

        stage, w = build_stage(pkg, ctx.asset_paths, ctx.cache_dir)
        warnings += w
        hybrid = bool(stage.splats)
        if hybrid and not stage.meshes:
            from .gsplat import render_splat_frames

            warnings.append("mitsuba: the scene is splats only, so they were rasterised directly "
                            "(engine=gsplat does the same, cheaper)")
            return RenderResult(render_splat_frames(ctx, stage, frames, warnings), warnings, time.time() - t0)
        if pkg.animation and not pkg.animation.turntable:
            warnings.append("mitsuba: scene animation isn't supported; rendering the rest pose (camera turntables work)")

        width, height = pkg.render.resolution
        spp = samples_for(self.name, pkg.render.quality, pkg.render.samples)
        passes = [p for p in pkg.output.passes if p != "beauty"]
        unsupported = [p for p in passes if p not in self.capabilities.passes]
        if unsupported:
            warnings.append(f"mitsuba: passes {unsupported} unsupported, skipped")
        env = pkg.environment
        bg = env.background
        hide_env = bg != "hdri" or pkg.output.transparent or hybrid  # hybrid: compositor draws the background

        scene = stage_to_dict(stage, mi, TextureCache(stage.base_dir), (ctx.cache_dir or ctx.out_dir) / "mi-meshes")
        from ..ir.exporters.mitsuba import light_dict

        for i, lt in enumerate(package_lights(pkg, stage)):
            d = light_dict(lt, stage.up_vector(), mi)
            if d:
                scene[f"pkg_light_{i}"] = d

        use_scene_cam, keep_world = native_setup(pkg, plan.scene_format)
        dome = next((l for l in stage.lights if l.type == "dome" and l.texture), None)
        env_path = None if keep_world else env_map_path(pkg, ctx.asset_paths)
        if (env_path is None or keep_world) and dome is not None:
            env_path = Path(dome.texture)
        if env_path is not None:
            rot = mi.ScalarTransform4f().rotate(stage.up_vector().tolist(), env.rotationDeg)
            if stage.up_axis == "Z":  # Mitsuba envmaps are Y-up
                rot = rot @ mi.ScalarTransform4f().rotate([1, 0, 0], 90)
            scene["environment"] = {"type": "envmap", "filename": str(env_path), "scale": float(env.intensity), "to_world": rot}
        else:
            ambient = next((l for l in stage.lights if l.type == "ambient"), None)
            if ambient is not None:
                scene["environment"] = light_dict(ambient, stage.up_vector(), mi)
            elif not any(k.startswith(("light_", "pkg_light_")) for k in scene):
                scene["environment"] = {"type": "constant", "radiance": {"type": "rgb", "value": [0.8, 0.8, 0.8]}}

        if pkg.render.denoise and variant.startswith("cuda") and not _optix_denoiser_weights() and pkg.render.samples is None:
            # No OptiX denoiser here: GPU samples are cheap, so buy the noise down with spp instead.
            spp = min(spp * 4, 1024)
            warnings.append(f"mitsuba: OptiX denoiser unavailable on this GPU; rendered at {spp} spp instead")
        integrator: dict = {"type": "path", "max_depth": 8 if pkg.render.quality != "draft" else 5, "hide_emitters": hide_env}
        aovs = []
        if "depth" in passes or hybrid:  # hybrid: mesh depth for compositing splats
            aovs.append("depth:depth")
        if "normal" in passes:
            aovs.append("normal:sh_normal")
        if "albedo" in passes:
            aovs.append("albedo:albedo")
        if aovs:
            integrator = {"type": "aov", "aovs": ",".join(aovs), "integrator": integrator}
        scene["integrator"] = integrator

        cams = resolve_cameras(pkg, stage, frames, warnings, prefer_scene=use_scene_cam)
        first = cams[frames[0]]
        sensor: dict = {
            "type": "perspective" if first.ortho_height is None else "orthographic",
            "near_clip": float(max(first.near, 1e-4)), "far_clip": float(first.far),
            "to_world": mi.ScalarTransform4f().look_at(origin=first.position.tolist(), target=first.target.tolist(), up=first.up.tolist()),
            "film": {"type": "hdrfilm", "width": width, "height": height, "pixel_format": "rgba",
                     "rfilter": {"type": "gaussian"}, "sample_border": True},
            "sampler": {"type": "independent", "sample_count": spp, "seed": pkg.render.seed},
        }
        if first.ortho_height is None:
            sensor["fov"] = float(first.fov_y_deg)
            sensor["fov_axis"] = "y"
        else:
            sensor["to_world"] = sensor["to_world"] @ mi.ScalarTransform4f().scale([first.ortho_height / 2 * width / height, first.ortho_height / 2, 1])
        scene["camera"] = sensor

        mi_scene = mi.load_dict(scene)
        params = mi.traverse(mi_scene)
        denoiser = None
        if pkg.render.denoise:
            # OptixDenoiser aborts the whole process (uncatchable) when the driver's weights file is absent,
            # as on Modal's GPUs, so check for it first.
            if variant.startswith("cuda") and hasattr(mi, "OptixDenoiser") and _optix_denoiser_weights():
                denoiser = mi.OptixDenoiser(input_size=[width, height], albedo=False, normals=False, temporal=False)
            elif spp < 128 and not variant.startswith("cuda"):
                warnings.append(f"mitsuba: denoising needs a CUDA GPU; {variant} output is undenoised at {spp} spp")

        bgc = None if bg in ("hdri", "transparent") or pkg.output.transparent else tuple(parse_color(bg)[:3])
        ext = pkg.output.format if not pkg.output.is_video else "png"
        files: list[Path] = []
        from ..images import save_beauty, save_pass

        depths: dict[int, np.ndarray] = {}
        for f in frames:
            cam = cams[f]
            if cam is not first:
                params["camera.to_world"] = mi.Transform4f().look_at(origin=cam.position.tolist(), target=cam.target.tolist(), up=cam.up.tolist()) \
                    if first.ortho_height is None else params["camera.to_world"]
                params.update()
            mi.render(mi_scene, params, seed=pkg.render.seed + f, spp=spp)
            film = mi_scene.sensors()[0].film()
            bmp = film.bitmap(raw=False)
            channels = dict(bmp.split())
            if hybrid and "depth" in channels:
                depths[f] = _planar_depth(np.array(channels["depth"], copy=True), cam, width, height)
            rgba = np.array(bmp, copy=False)[..., :4].astype(np.float32)  # R,G,B,A first
            if denoiser is not None:
                rgb = np.array(denoiser(mi.TensorXf(rgba[..., :3].copy())))
                rgba = np.concatenate([rgb, rgba[..., 3:4]], axis=-1)
            if hybrid:  # mesh layer with alpha; composite_files adds splats + background and the final format
                save_beauty(rgba, ctx.out_dir / frame_filename("beauty", f, "png"), "png", True, None)
            else:
                files.append(save_beauty(rgba, ctx.out_dir / frame_filename("beauty", f, ext), ext, pkg.output.transparent, bgc))
            pass_ext = "exr" if pkg.output.format == "exr" else "png"
            for p in passes:
                if p == "alpha":
                    files.append(save_pass(rgba[..., 3], ctx.out_dir / frame_filename(p, f, "png"), "alpha"))
                elif p in channels:
                    files.append(save_pass(np.array(channels[p], copy=False), ctx.out_dir / frame_filename(p, f, pass_ext), p))
        if hybrid:
            from .gsplat import composite_files

            files = list(composite_files(ctx, stage, frames, depths.get, warnings, self.name, cams).values()) + files
        return RenderResult(files, warnings, time.time() - t0)

