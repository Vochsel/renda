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


def _rfilter(pkg) -> dict:
    """render.antialias → Mitsuba reconstruction filter (default: Gaussian, Mitsuba's own default)."""
    aa = pkg.render.antialias
    if aa is None:
        return {"type": "gaussian"}
    if aa.filter == "box":
        return {"type": "box"}
    if aa.filter == "mitchell":
        return {"type": "mitchell"}
    if aa.filter == "blackman-harris":
        return {"type": "lanczos", "lobes": 3}
    # width 1.5 px (Blender's default) ↔ σ = 0.5 (Mitsuba's default), scaled linearly
    return {"type": "gaussian", "stddev": max(aa.filterWidth / 3, 0.1)}


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

        tex_plan = None
        if pkg.scene.textureOverrides:
            from ..textures import TexturePlan, resolve_texture_overrides

            tex_plan = TexturePlan(stage, resolve_texture_overrides(pkg, ctx.asset_paths, frames, ctx.cache_dir))
            warnings += tex_plan.warnings()
            tex_plan.apply(stage, frames[0])
        tex_cache = TextureCache(stage.base_dir)
        mesh_dir = (ctx.cache_dir or ctx.out_dir) / "mi-meshes"
        scene = stage_to_dict(stage, mi, tex_cache, mesh_dir)
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

        from ..ir.camera import resolve_subframes

        subs = resolve_subframes(pkg, stage, frames, warnings, prefer_scene=use_scene_cam,
                                 emulate_motion_blur=pkg.render.motionBlur is not None)
        cams = {f: s[len(s) // 2] for f, s in subs.items()}
        first = cams[frames[0]]
        n_sub = len(subs[frames[0]])
        spp_sub = max(1, round(spp / n_sub))  # same total samples, spread over the shutter
        thin_lens = first.focus_distance is not None and first.ortho_height is None
        sensor: dict = {
            "type": "thinlens" if thin_lens else "perspective" if first.ortho_height is None else "orthographic",
            "near_clip": float(max(first.near, 1e-4)), "far_clip": float(first.far),
            "to_world": mi.ScalarTransform4f().look_at(origin=first.position.tolist(), target=first.target.tolist(), up=first.up.tolist()),
            "film": {"type": "hdrfilm", "width": width, "height": height, "pixel_format": "rgba",
                     "rfilter": _rfilter(pkg), "sample_border": True},
            "sampler": {"type": "independent", "sample_count": spp_sub, "seed": pkg.render.seed},
        }
        if thin_lens:
            sensor["aperture_radius"] = float(first.aperture_radius)
            sensor["focus_distance"] = float(first.focus_distance)
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

        post = pkg.post
        tm_mode, exposure = (post.tonemap, post.exposure) if post is not None else ("agx", 0.0)
        depths: dict[int, np.ndarray] = {}
        for f in frames:
            cam = cams[f]
            if tex_plan is not None and tex_plan.animated and f != frames[0]:
                # video textures: swap in this frame's images and rebuild the scene (simpler and sturdier
                # than poking nested bitmap parameters; meshes are small next to the render)
                tex_plan.apply(stage, f)
                rebuilt = stage_to_dict(stage, mi, tex_cache, mesh_dir)
                rebuilt.update({k: v for k, v in scene.items() if not k.startswith(("shape_", "bsdf_"))})
                mi_scene = mi.load_dict(rebuilt)
                params = mi.traverse(mi_scene)
            acc = None
            channels: dict = {}
            for i, sub in enumerate(subs[f]):
                if first.ortho_height is None:
                    params["camera.to_world"] = mi.Transform4f().look_at(origin=sub.position.tolist(), target=sub.target.tolist(), up=sub.up.tolist())
                    params.update()
                mi.render(mi_scene, params, seed=(pkg.render.seed + f) * 64 + i, spp=spp_sub)
                bmp = mi_scene.sensors()[0].film().bitmap(raw=False)
                sub_rgba = np.array(bmp, copy=True)[..., :4].astype(np.float32)  # R,G,B,A first
                acc = sub_rgba if acc is None else acc + sub_rgba
                if i == len(subs[f]) // 2:
                    channels = {k: np.array(v, copy=True) for k, v in bmp.split()}
            rgba = acc / len(subs[f])
            if hybrid and "depth" in channels:
                depths[f] = _planar_depth(channels["depth"], cam, width, height)
            if denoiser is not None:
                rgb = np.array(denoiser(mi.TensorXf(rgba[..., :3].copy())))
                rgba = np.concatenate([rgb, rgba[..., 3:4]], axis=-1)
            if hybrid:  # mesh layer with alpha; composite_files adds splats + background and the final format
                save_beauty(rgba, ctx.out_dir / frame_filename("beauty", f, "png"), "png", True, None, tm_mode, exposure)
            else:
                files.append(save_beauty(rgba, ctx.out_dir / frame_filename("beauty", f, ext), ext, pkg.output.transparent, bgc,
                                         tm_mode, exposure))
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



def bake_atlas(ctx: RenderContext, stage, layout, texels, warnings: list[str]) -> np.ndarray:
    """Texel-space bake with Dr.Jit through the same Mitsuba scene the renderer uses (path integrator,
    env + lights + GI). Returns (linear RGB atlas, valid mask), v-up rows.

    unlit: radiance leaving each texel along its normal, from a view ray just above the surface — full
    BSDF (glossy, metal, textures), like Cycles' combined bake. lightmap: E/π from cosine-weighted rays.
    ao: cosine-weighted sky visibility within a quarter of the scene diagonal."""
    import shutil
    import tempfile

    import drjit as dr
    import mitsuba as mi

    from ..ir.exporters.mitsuba import TextureCache, light_dict, stage_to_dict
    from ..scene import env_map_path, package_lights

    pkg, plan = ctx.pkg, ctx.plan
    bake = pkg.output.bake
    variant = pick_variant(_gpu_wanted(plan.hardware))
    if plan.hardware.startswith("gpu") and not variant.startswith(("cuda", "metal")):
        warnings.append(f"mitsuba: no GPU backend available, baked with {variant}")
    scratch = Path(tempfile.mkdtemp(prefix="mi-bake-", dir=ctx.cache_dir or ctx.out_dir))
    try:
        scene = stage_to_dict(stage, mi, TextureCache(stage.base_dir), scratch / "mi-meshes")
        for i, lt in enumerate(package_lights(pkg, stage)):
            d = light_dict(lt, stage.up_vector(), mi)
            if d:
                scene[f"pkg_light_{i}"] = d
        env = pkg.environment
        dome = next((l for l in stage.lights if l.type == "dome" and l.texture), None)
        env_path = env_map_path(pkg, ctx.asset_paths) or (Path(dome.texture) if dome is not None else None)
        if env_path is not None:
            rot = mi.ScalarTransform4f().rotate(stage.up_vector().tolist(), env.rotationDeg)
            if stage.up_axis == "Z":  # Mitsuba envmaps are Y-up
                rot = rot @ mi.ScalarTransform4f().rotate([1, 0, 0], 90)
            scene["environment"] = {"type": "envmap", "filename": str(env_path), "scale": float(env.intensity), "to_world": rot}
        elif not any(k.startswith(("light_", "pkg_light_")) for k in scene):
            scene["environment"] = {"type": "constant", "radiance": {"type": "rgb", "value": [0.8, 0.8, 0.8]}}
        scene["integrator"] = {"type": "path", "max_depth": 6}
        mi_scene = mi.load_dict(scene)
        integrator = mi_scene.integrator()

        lo, hi = stage.bounds()
        diag = float(np.linalg.norm(hi - lo)) or 1.0
        eps = 1e-4 * diag
        ao_dist = 0.25 * diag
        pos = texels.interpolate(layout, "points").astype(np.float32)
        nrm = texels.interpolate(layout, "normals").astype(np.float32)
        K = len(texels)
        spp = int(plan.samples)
        acc = np.zeros((K, 3), dtype=np.float32)
        valid = np.ones(K, dtype=bool)
        batch = (1 << 21) if variant.startswith("cuda") else (1 << 17)
        for start in range(0, K, batch):
            end = min(K, start + batch)
            n = end - start
            o = pos[start:end] + nrm[start:end] * eps
            N = mi.Vector3f(nrm[start:end, 0], nrm[start:end, 1], nrm[start:end, 2])
            O = mi.Point3f(o[:, 0], o[:, 1], o[:, 2])
            frame = mi.Frame3f(N)
            sampler = mi.load_dict({"type": "independent", "sample_count": 1})
            sampler.seed(int(pkg.render.seed) * 7919 + start, n)
            total = dr.zeros(mi.Color3f, n)
            if bake.mode == "unlit":
                off = 1e-3 * diag
                Ov = mi.Point3f(*(pos[start:end] + nrm[start:end] * off).T)
                view = mi.Ray3f(Ov, -N)
                si = mi_scene.ray_intersect(view)
                ok = si.is_valid() & (si.t < 2.5 * off)
                valid[start:end] = np.array(ok, dtype=bool)
            for _ in range(spp):
                if bake.mode == "unlit":
                    L, _, _ = integrator.sample(mi_scene, sampler, mi.Ray3f(Ov, -N))
                    total += mi.Color3f(L)
                    sampler.schedule_state()
                    dr.eval(total)
                    continue
                d = frame.to_world(mi.warp.square_to_cosine_hemisphere(sampler.next_2d()))
                ray = mi.Ray3f(O, d)
                if bake.mode == "ao":
                    ray.maxt = ao_dist
                    vis = dr.select(mi_scene.ray_test(ray), 0.0, 1.0)
                    total += mi.Color3f(vis, vis, vis)
                else:
                    L, _, _ = integrator.sample(mi_scene, sampler, ray)
                    total += mi.Color3f(L)
                sampler.schedule_state()
                dr.eval(total)
            arr = np.array(total, dtype=np.float32)
            acc[start:end] = arr.T if arr.shape == (3, n) else arr.reshape(n, 3)
        acc /= max(spp, 1)
        out = np.zeros((layout.size, layout.size, 3), dtype=np.float32)
        out[texels.rows, texels.cols] = acc
        mask = np.zeros((layout.size, layout.size), dtype=bool)
        mask[texels.rows, texels.cols] = valid  # texels whose view ray hit something else get dilated over
        if bake.mode == "unlit" and valid.mean() < 0.9:
            warnings.append(f"mitsuba: {100 * (1 - valid.mean()):.0f}% of texels were occluded at the surface and filled from neighbours")
        return out, mask
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
