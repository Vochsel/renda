"""Google Filament adapter: real-time PBR rasteriser, headless via `filly` (Python bindings for Filament).

Everything goes IR → .glb and is drawn offscreen over EGL (Mesa llvmpipe on CPU, the NVIDIA driver on
GPUs). Image-based lighting comes from the same equirect HDRI the other engines use; package and scene
lights map to Filament's photometric lights. Linux x86_64 only (filly ships no macOS wheels).
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from .base import Capabilities, Engine, RenderContext, RenderResult, frame_filename, samples_for

# Filament IBL intensity (lux) for an environment of radiance 1 — and the scale we use to turn the
# IR's radiometric light units into Filament's photometric ones, so a package looks the same here as in
# Cycles or Mitsuba (sun: W/m² × k lux; point/spot: W → candela via the same k over 4π).
ENV_LUX = 30_000.0
RADIOMETRIC_TO_LUX = ENV_LUX / np.pi
DEFAULT_EV100 = 15.0
TONEMAPS = {"agx": "agx", "filmic": "filmic", "aces": "aces", "neutral": "pbr_neutral", "standard": "linear"}
_Z_TO_Y = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=np.float64)


def _filly():
    import filly

    return filly


class FilamentEngine(Engine):
    name = "filament"
    accepts = frozenset({"gltf"})
    capabilities = Capabilities(
        gpu=False, passes=frozenset({"beauty", "alpha"}), animation=True, cost_cpu=0.06, cost_gpu=0.01, load_seconds=4.0,
    )

    def available(self) -> bool:
        try:
            _filly()
        except Exception:  # noqa: BLE001 - missing wheel, no EGL, unsupported OS
            return False
        return True

    def unavailable_reason(self) -> str | None:
        return "uv pip install 'renda[filament]' (Linux x86_64 with an EGL/OpenGL 4.1 driver; no macOS build)"

    def version(self) -> str | None:
        try:
            from importlib.metadata import version

            return f"filly@{version('filly')}"
        except Exception:  # noqa: BLE001
            return None

    def render(self, ctx: RenderContext, frames: list[int]) -> RenderResult:
        import tempfile

        from PIL import Image

        from ..ir.camera import resolve_cameras
        from ..ir.exporters import to_gltf
        from ..scene import build_stage, env_map_path, package_lights
        from ..spec import parse_color

        t0 = time.time()
        pkg = ctx.pkg
        warnings: list[str] = []
        filly = _filly()

        stage, w = build_stage(pkg, ctx.asset_paths, ctx.cache_dir)
        warnings += w
        if stage.splats:
            warnings.append("filament: Gaussian splats aren't supported; rendering meshes only")
        if pkg.scene.textureOverrides:
            warnings.append("filament: texture overrides aren't applied by this engine yet")
        cams = resolve_cameras(pkg, stage, frames, warnings)
        # Filament's environment is Y-up: rotate Z-up scenes (and their cameras) into Y-up.
        to_y = _Z_TO_Y if stage.up_axis == "Z" else np.eye(4)

        def P(v) -> tuple[float, float, float]:
            return tuple(float(x) for x in (to_y[:3, :3] @ np.asarray(v, dtype=np.float64)) + to_y[:3, 3])

        def D(v) -> tuple[float, float, float]:
            return tuple(float(x) for x in to_y[:3, :3] @ np.asarray(v, dtype=np.float64))

        width, height = pkg.render.resolution
        ss = max(1, min(samples_for(self.name, pkg.render.quality, pkg.render.samples), 3))
        while ss > 1 and max(width, height) * ss > 8192:
            ss -= 1
        env = pkg.environment
        bg = env.background
        transparent = pkg.output.transparent or bg == "transparent"
        files: list[Path] = []
        out_fmt = pkg.output.format if not pkg.output.is_video else "png"
        lens = getattr(pkg.camera, "lens", None)
        post = pkg.post

        from ..ir.loaders.cad import _native_stdout_to_stderr

        # Filament's C++ core prints its banner and backend info to fd 1, which would corrupt `--json`.
        with tempfile.TemporaryDirectory() as tmp, _native_stdout_to_stderr():
            glb = to_gltf(stage, Path(tmp) / "scene.glb")
            with filly.Renderer() as r:
                sc = r.create_scene()
                model = sc.load(str(glb))
                if stage.up_axis == "Z":
                    model.rotation_euler_deg = (-90.0, 0.0, 0.0)
                hdri = env_map_path(pkg, ctx.asset_paths)
                if hdri:
                    sc.environment = r.load_environment(str(hdri))
                    sc.environment_intensity = ENV_LUX * float(env.intensity)
                    sc.environment_rotation_deg = float(env.rotationDeg)
                    sc.environment_visible = bg == "hdri" and not transparent
                if transparent:
                    sc.transparent = True
                    sc.background = (0.0, 0.0, 0.0, 0.0)
                elif bg not in ("hdri", "transparent"):
                    c = parse_color(bg)
                    sc.background = (float(c[0]), float(c[1]), float(c[2]), 1.0)
                sc.tone_mapping = TONEMAPS.get(post.tonemap if post is not None else "agx", "agx")
                sc.msaa = 4 if pkg.render.quality != "draft" else 1
                sc.ssao = pkg.render.quality != "draft"
                sc.shadows = True
                for lt in list(stage.lights) + package_lights(pkg, stage):
                    color = tuple(float(x) for x in lt.color)
                    if lt.type == "sun":
                        li = sc.add_sun_light(direction=D(lt.direction()), intensity=lt.intensity * RADIOMETRIC_TO_LUX, color=color)
                    elif lt.type == "point":
                        li = sc.add_point_light(position=P(lt.position()), color=color,
                                                intensity=lt.intensity * RADIOMETRIC_TO_LUX / (4 * np.pi))
                    elif lt.type in ("spot", "area"):
                        if lt.type == "area":
                            warnings.append("filament: area lights are approximated as spot lights")
                        cone = float(lt.angle_deg if lt.type == "spot" else 120.0) / 2
                        li = sc.add_spot_light(position=P(lt.position()), direction=D(lt.direction()), color=color,
                                               intensity=lt.intensity * RADIOMETRIC_TO_LUX / (4 * np.pi),
                                               cone_deg=(cone * 0.8, cone))
                    else:
                        continue  # dome/ambient: covered by the environment
                    try:
                        li.casts_shadows = True
                    except AttributeError:
                        pass
                cam = r.create_camera()
                sc.camera = cam
                cam.exposure = DEFAULT_EV100 - (float(post.exposure) if post is not None else 0.0)
                want_dof = lens is not None and lens.fStop is not None
                if want_dof:
                    sc.depth_of_field = True
                    cam.aperture = float(lens.fStop)
                anim = pkg.animation
                target = r.create_render_target(width=width * ss, height=height * ss)
                for f in frames:
                    c = cams[f]
                    if anim and model.animations:
                        model.apply_animation(0, (f - anim.frames[0]) / anim.fps)
                    cam.position = P(c.position)
                    cam.look_at(P(c.target), up=D(c.up))
                    if c.ortho_height is not None:
                        cam.set_orthographic(height=float(c.ortho_height), near=float(c.near), far=float(c.far))
                    else:
                        cam.set_perspective(fov_y_deg=float(c.fov_y_deg), near=float(max(c.near, 1e-3)), far=float(c.far))
                    if want_dof and c.focus_distance:
                        cam.focus_distance = float(c.focus_distance)
                    r.render(sc, target)
                    img = Image.fromarray(target.read(), "RGBA")
                    if ss > 1:
                        img = img.resize((width, height), Image.LANCZOS)
                    alpha = img.getchannel("A")
                    if not transparent:
                        img = img.convert("RGB")
                    dst = ctx.out_dir / frame_filename("beauty", f, out_fmt)
                    if out_fmt == "exr":
                        from ..images import save_exr

                        arr = np.asarray(img, dtype=np.float32) / 255.0
                        save_exr(np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4), dst)
                    else:
                        img.save(dst, format={"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}[out_fmt],
                                 **({"quality": 92} if out_fmt in ("jpg", "webp") else {}))
                    files.append(dst)
                    if "alpha" in pkg.output.passes:
                        a = ctx.out_dir / frame_filename("alpha", f, "png")
                        alpha.save(a)
                        files.append(a)
        unsupported = [p for p in pkg.output.passes if p not in self.capabilities.passes]
        if unsupported:
            warnings.append(f"filament: passes {unsupported} unsupported, skipped")
        return RenderResult(files, warnings, time.time() - t0)
