"""pbrt-v4 adapter: Pharr/Jakob/Humphreys' spectral path tracer — the reference renderer of the PBR book.

Every scene goes IR → a pbrt-v4 scene file: meshes as binary PLY (world transforms baked in), principled
materials mapped to pbrt's conductor / dielectric / coated-diffuse / diffuse, the environment HDRI
converted to pbrt's equal-area octahedral map with `imgtool makeequiarea`, and the package/scene lights as
pbrt lights. pbrt renders a linear EXR that goes through the same tonemap + background composite as
Mitsuba, so packages look alike across engines. CPU only (the GPU build needs the OptiX SDK).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from .base import Capabilities, Engine, RenderContext, RenderResult, frame_filename, samples_for

_Z_TO_Y = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=np.float64)


def _bin(name: str) -> str | None:
    env = os.environ.get(f"RENDA_{name.upper()}")
    return env if env and Path(env).exists() else shutil.which(name)


def _fmt(v) -> str:
    return " ".join(f"{float(x):.7g}" for x in np.ravel(v))


def write_ply(path: Path, points: np.ndarray, indices: np.ndarray, normals: np.ndarray | None,
              uvs: np.ndarray | None) -> None:
    """Binary little-endian PLY in the layout pbrt's plymesh reads (x y z [nx ny nz] [u v], int faces)."""
    n = len(points)
    cols = [points.astype("<f4")]
    props = ["x", "y", "z"]
    if normals is not None and len(normals) == n:
        cols.append(normals.astype("<f4"))
        props += ["nx", "ny", "nz"]
    if uvs is not None and len(uvs) == n:
        cols.append(uvs.astype("<f4"))
        props += ["u", "v"]
    verts = np.ascontiguousarray(np.hstack(cols), dtype="<f4")
    faces = np.empty(len(indices), dtype=[("n", "u1"), ("i", "<i4", (3,))])
    faces["n"] = 3
    faces["i"] = indices.astype("<i4")
    head = ["ply", "format binary_little_endian 1.0", f"element vertex {n}"]
    head += [f"property float {p}" for p in props]
    head += [f"element face {len(indices)}", "property list uchar int vertex_indices", "end_header"]
    with open(path, "wb") as f:
        f.write(("\n".join(head) + "\n").encode())
        f.write(verts.tobytes())
        f.write(faces.tobytes())


def _exr_channels(path: Path) -> dict[str, np.ndarray] | None:
    """Every channel of pbrt's gbuffer EXR as (H, W) float arrays, split out of OpenEXR's grouped
    layers; None if the OpenEXR module isn't installed."""
    try:
        import OpenEXR
    except ImportError:
        return None
    out: dict[str, np.ndarray] = {}
    with OpenEXR.File(str(path), separate_channels=True) as f:
        for name, ch in f.channels().items():
            px = np.asarray(ch.pixels, dtype=np.float32)
            if px.ndim == 2:
                out[name] = px
            else:  # grouped (e.g. "RGBA"): one name per letter
                for i, letter in enumerate(name):
                    out[letter] = px[..., i]
    return out


def _pick(ch: dict[str, np.ndarray], *candidates: tuple[str, str, str]) -> np.ndarray | None:
    for names in candidates:
        if all(n in ch for n in names):
            return np.dstack([ch[n] for n in names])
    return None


def _read_rgba(path: Path, fallback) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
    """(RGBA, albedo, shading normal) from pbrt's gbuffer EXR; the AOVs feed OIDN when present."""
    ch = _exr_channels(path)
    if ch is None:
        img = fallback(path)
        a = img[..., 3] if img.shape[-1] > 3 else np.ones(img.shape[:2], np.float32)
        return np.dstack([img[..., :3], a]), None, None
    rgb = np.dstack([ch["R"], ch["G"], ch["B"]])
    albedo = _pick(ch, ("Albedo.R", "Albedo.G", "Albedo.B"), ("albedo.R", "albedo.G", "albedo.B"))
    normal = _pick(ch, ("Ns.X", "Ns.Y", "Ns.Z"), ("Nsx", "Nsy", "Nsz"), ("N.X", "N.Y", "N.Z"), ("Nx", "Ny", "Nz"))
    if "A" in ch:
        a = ch["A"]
    else:
        # pbrt-v4's gbuffer has no alpha channel. Its normals are averaged over the pixel's samples and are
        # zero where a sample missed geometry, so their length is the pixel's geometric coverage.
        geo = _pick(ch, ("N.X", "N.Y", "N.Z"), ("Nx", "Ny", "Nz"))
        geo = geo if geo is not None else normal
        a = np.clip(np.linalg.norm(geo, axis=-1), 0, 1).astype(np.float32) if geo is not None else np.ones(rgb.shape[:2], np.float32)
    return np.dstack([rgb, a]), albedo, normal


class PbrtEngine(Engine):
    name = "pbrt"
    accepts = frozenset({"rir"})
    capabilities = Capabilities(
        gpu=False, passes=frozenset({"beauty", "alpha"}), animation=False, cost_cpu=0.15, cost_gpu=0.15, load_seconds=4.0,
    )

    def available(self) -> bool:
        return _bin("pbrt") is not None and _bin("imgtool") is not None

    def unavailable_reason(self) -> str | None:
        return "pbrt-v4 isn't installed (build https://github.com/mmp/pbrt-v4 and put pbrt + imgtool on PATH, or set RENDA_PBRT)"

    def version(self) -> str | None:
        return os.environ.get("RENDA_PBRT_VERSION", "pbrt-v4")

    def render(self, ctx: RenderContext, frames: list[int]) -> RenderResult:
        from ..images import read_hdr_image, save_beauty
        from ..ir.camera import resolve_cameras
        from ..ir.exporters.mitsuba import TextureCache
        from ..scene import build_stage, env_map_path, package_lights
        from ..spec import parse_color

        t0 = time.time()
        pkg, plan = ctx.pkg, ctx.plan
        warnings: list[str] = []
        stage, w = build_stage(pkg, ctx.asset_paths, ctx.cache_dir)
        warnings += w
        if stage.splats:
            warnings.append("pbrt: Gaussian splats aren't supported; rendering meshes only")
        if pkg.scene.textureOverrides:
            warnings.append("pbrt: texture overrides aren't applied by this engine yet")
        if pkg.animation and not pkg.animation.turntable:
            warnings.append("pbrt: scene animation isn't supported; rendering the rest pose (camera turntables work)")
        cams = resolve_cameras(pkg, stage, frames, warnings)
        # pbrt's infinite light is Z-up; keep the scene in its own axes and orient the environment instead.
        width, height = pkg.render.resolution
        spp = samples_for(self.name, pkg.render.quality, pkg.render.samples)
        env = pkg.environment
        bg = env.background
        transparent = pkg.output.transparent or bg == "transparent"
        out_fmt = pkg.output.format if not pkg.output.is_video else "png"
        nthreads = os.cpu_count() or 4

        work = Path(tempfile.mkdtemp(prefix="renda-pbrt-", dir=ctx.cache_dir or None))
        try:
            lines = self._world(stage, pkg, ctx, work, warnings, TextureCache(stage.base_dir), package_lights)
            hdri = env_map_path(pkg, ctx.asset_paths)
            env_lines: list[str] = []
            if hdri:
                eq = work / "env-equiarea.exr"
                r = subprocess.run([_bin("imgtool"), "makeequiarea", str(hdri), "--outfile", str(eq)],
                                   capture_output=True, text=True)
                if r.returncode != 0 or not eq.exists():
                    raise RuntimeError(f"pbrt: imgtool makeequiarea failed: {(r.stdout + r.stderr)[-400:]}")
                up = "Rotate -90 1 0 0" if stage.up_axis != "Z" else ""  # equirect top → scene up
                env_lines = ["AttributeBegin", f"  {up}" if up else "", f"  Rotate {float(env.rotationDeg):.4g} 0 0 1",
                             f'  LightSource "infinite" "string filename" "{eq.name}" "float scale" [{float(env.intensity):.6g}]',
                             "AttributeEnd"]
            elif not any(l.startswith("LightSource") or "AreaLightSource" in l for l in lines):
                env_lines = ['LightSource "infinite" "rgb L" [0.8 0.8 0.8]']
            lens = getattr(pkg.camera, "lens", None)
            from .. import denoise as oidn

            # pbrt-v4 has no CPU denoiser; use OIDN with the gbuffer's albedo/normal (box filter, so the noise
            # stays per-pixel — wide filters correlate it and OIDN leaves it in, as with Mitsuba).
            denoise = bool(pkg.render.denoise) and oidn.available()
            pixel_filter = 'PixelFilter "box"' if denoise else 'PixelFilter "gaussian"'
            files: list[Path] = []
            for f in frames:
                c = cams[f]
                fov = float(c.fov_y_deg)
                if width < height:  # pbrt's fov is for the shorter image axis
                    fov = float(np.degrees(2 * np.arctan(np.tan(np.radians(fov) / 2) * width / height)))
                cam_params = f'"float fov" [{fov:.6g}]'
                if lens is not None and lens.fStop is not None and c.focus_distance and c.focal_mm:
                    lens_radius = (c.focal_mm / 1000.0) / (2 * float(lens.fStop))
                    cam_params += f' "float lensradius" [{lens_radius:.6g}] "float focaldistance" [{float(c.focus_distance):.6g}]'
                exr = work / f"frame_{f:05d}.exr"
                head = [
                    # pbrt's camera space is left-handed: flip camera-space x (before LookAt, so it applies after
                    # the world→camera transform) so images match the other engines rather than mirroring.
                    'Scale -1 1 1',
                    f"LookAt {_fmt(c.position)}  {_fmt(c.target)}  {_fmt(c.up)}",
                    f'Camera "{"orthographic" if c.ortho_height else "perspective"}" '
                    + (f'"float screenwindow" [{_fmt(np.array([-1, 1, -1, 1]) * np.array([c.ortho_height / 2 * width / height] * 2 + [c.ortho_height / 2] * 2))}]'
                       if c.ortho_height else cam_params),
                    f'Sampler "zsobol" "integer pixelsamples" [{int(spp)}]',
                    f'Integrator "volpath" "integer maxdepth" [{12 if pkg.render.quality == "final" else 8}]',
                    pixel_filter,
                    f'Film "gbuffer" "integer xresolution" [{width}] "integer yresolution" [{height}] '
                    f'"string filename" "{exr.name}" "string coordinatesystem" "camera"',
                    "WorldBegin",
                ]
                scene = work / f"frame_{f:05d}.pbrt"
                scene.write_text("\n".join(head + env_lines + lines) + "\n")
                r = subprocess.run([_bin("pbrt"), "--nthreads", str(nthreads), "--quiet", scene.name],
                                   capture_output=True, text=True, cwd=work)
                if r.returncode != 0 or not exr.exists():
                    raise RuntimeError(f"pbrt failed (exit {r.returncode}): {(r.stdout + r.stderr)[-800:]}")
                img, albedo, normal = _read_rgba(exr, read_hdr_image)
                if denoise:
                    img = np.dstack([oidn.denoise_rgb(img[..., :3], albedo, normal), img[..., 3]])
                rgb = img[..., :3].astype(np.float32)
                alpha = img[..., 3].astype(np.float32)
                if bg != "hdri" or transparent:
                    rgb = rgb * alpha[..., None]  # drop the visible environment; keep premultiplied geometry
                rgba = np.dstack([rgb, alpha])
                background = None if bg in ("hdri", "transparent") or transparent else tuple(parse_color(bg)[:3])
                post = pkg.post
                dst = ctx.out_dir / frame_filename("beauty", f, out_fmt)
                save_beauty(rgba if bg != "hdri" or transparent else np.dstack([img[..., :3], np.ones_like(alpha)]),
                            dst, out_fmt, transparent, background,
                            tonemap_mode=post.tonemap if post is not None else "agx",
                            exposure=float(post.exposure) if post is not None else 0.0,
                            fast=pkg.output.is_video)
                files.append(dst)
                if "alpha" in pkg.output.passes:
                    from PIL import Image

                    a = ctx.out_dir / frame_filename("alpha", f, "png")
                    Image.fromarray((np.clip(alpha, 0, 1) * 255 + 0.5).astype(np.uint8), "L").save(a)
                    files.append(a)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        unsupported = [p for p in pkg.output.passes if p not in self.capabilities.passes]
        if unsupported:
            warnings.append(f"pbrt: passes {unsupported} unsupported, skipped")
        _ = plan
        return RenderResult(files, warnings, time.time() - t0)

    def _world(self, stage, pkg, ctx, work: Path, warnings: list[str], tex, package_lights) -> list[str]:
        """Materials, meshes (PLY) and lights as pbrt world statements."""
        lines: list[str] = []
        textures: dict[str, str] = {}

        def texture(path: str | None, encoding: str) -> str | None:
            src = tex.readable(path)
            if src is None:
                return None
            key = f"{src}|{encoding}"
            if key not in textures:
                name = f"tex{len(textures)}"
                dst = work / f"{name}{Path(src).suffix.lower()}"
                shutil.copyfile(src, dst)
                lines.append(f'Texture "{name}" "spectrum" "imagemap" "string filename" "{dst.name}" "string encoding" "{encoding}"')
                textures[key] = name
            return textures[key]

        mats: dict[str, str] = {}
        for name, m in stage.materials.items():
            base = np.clip(np.asarray(m.base_color[:3], dtype=np.float64), 0, 1)
            rough = float(np.clip(m.roughness, 0.0, 1.0))
            tname = texture(m.base_color_tex, "sRGB")
            refl = f'"texture reflectance" "{tname}"' if tname else f'"rgb reflectance" [{_fmt(base)}]'
            if m.transmission > 0.5:
                body = f'"dielectric" "float eta" [{float(m.ior):.4g}] "float roughness" [{rough * rough:.4g}]'
            elif m.metallic > 0.5:
                body = f'"conductor" {refl} "float roughness" [{max(rough * rough, 0.002):.4g}]'
            elif rough > 0.85 or m.unlit:
                body = f'"diffuse" {refl}'
            else:
                body = f'"coateddiffuse" {refl} "float roughness" [{rough * rough:.4g}]'
            mid = f"mat{len(mats)}"
            lines.append(f'MakeNamedMaterial "{mid}" "string type" {body}')
            mats[name] = mid
        default = 'MakeNamedMaterial "renda_default" "string type" "coateddiffuse" "rgb reflectance" [0.8 0.8 0.8] "float roughness" [0.25]'
        lines.append(default)

        for i, mesh in enumerate(stage.meshes):
            M = np.asarray(mesh.transform, dtype=np.float64)
            pts = (np.c_[mesh.points.astype(np.float64), np.ones(len(mesh.points))] @ M.T)[:, :3]
            nrm = None
            if mesh.normals is not None:
                nm = np.linalg.inv(M[:3, :3]).T
                nrm = mesh.normals.astype(np.float64) @ nm.T
                nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
            uvs = mesh.uvs
            ply = work / f"mesh{i:05d}.ply"
            write_ply(ply, pts, mesh.indices, nrm, uvs)
            m = stage.materials.get(mesh.material or "")
            lines.append("AttributeBegin")
            lines.append(f'  NamedMaterial "{mats.get(mesh.material or "", "renda_default")}"')
            if m is not None and m.emission_strength > 0 and max(m.emission) > 0:
                L = np.asarray(m.emission, dtype=np.float64) * float(m.emission_strength)
                lines.append(f'  AreaLightSource "diffuse" "rgb L" [{_fmt(L)}]')
            alpha = f' "float alpha" [{float(m.opacity):.4g}]' if m is not None and m.opacity < 0.999 else ""
            lines.append(f'  Shape "plymesh" "string filename" "{ply.name}"{alpha}')
            lines.append("AttributeEnd")

        for lt in list(stage.lights) + package_lights(pkg, stage):
            col = np.asarray(lt.color, dtype=np.float64)
            p, d = lt.position(), lt.direction()
            if lt.type == "sun":
                lines.append(f'LightSource "distant" "point3 from" [0 0 0] "point3 to" [{_fmt(d)}] "rgb L" [{_fmt(col * lt.intensity)}]')
            elif lt.type == "point":
                lines.append(f'LightSource "point" "point3 from" [{_fmt(p)}] "rgb I" [{_fmt(col * lt.intensity / (4 * np.pi))}]')
            elif lt.type == "spot":
                half = float(lt.angle_deg) / 2
                lines.append(f'LightSource "spot" "point3 from" [{_fmt(p)}] "point3 to" [{_fmt(p + d)}] '
                             f'"float coneangle" [{half:.4g}] "float conedelta" [{min(5.0, half):.4g}] '
                             f'"rgb I" [{_fmt(col * lt.intensity / (4 * np.pi))}]')
            elif lt.type == "area":
                sx, sy = (float(s) / 2 for s in lt.size)
                T = np.asarray(lt.transform, dtype=np.float64)
                corners = [T[:3, :3] @ np.array(v) + T[:3, 3] for v in ((-sx, -sy, 0), (sx, -sy, 0), (-sx, sy, 0), (sx, sy, 0))]
                area = max(4 * sx * sy, 1e-6)
                L = col * lt.intensity / (area * np.pi)
                lines += ["AttributeBegin", f'  AreaLightSource "diffuse" "rgb L" [{_fmt(L)}]',
                          '  Material "diffuse" "rgb reflectance" [0 0 0]',
                          f'  Shape "bilinearmesh" "point3 P" [{_fmt(np.ravel(corners))}]', "AttributeEnd"]
        return lines
