"""Gaussian splat rasterisation shared by the gsplat engine and the hybrid (mesh engine + splats) path.

Two backends with the same maths (Kerbl et al. 3DGS: EWA projection, 0.3 px low-pass, front-to-back
alpha compositing, SH colour):

- ``gsplat``: nerfstudio's CUDA rasteriser via torch. Used whenever CUDA is available.
- ``numpy``: a vectorised reference implementation (tile loop, fragment sort). Exact but much slower;
  it keeps splat rendering working on CPU-only machines and in tests.

Output is a ``SplatLayer`` in display space (3DGS colours are fit to sRGB photos, so they are never
tonemapped): premultiplied RGB, alpha and the expected (alpha-weighted) planar depth along the
camera axis, which is what the compositor compares against mesh depth.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from ..ir.camera import ResolvedCamera
from ..ir.splats import SH_COUNT, Splats, quat_wxyz_to_mat, sh_basis, sigmoid

NEAR = 0.01


@dataclass
class SplatLayer:
    rgb: np.ndarray  # (H,W,3) premultiplied display RGB
    alpha: np.ndarray  # (H,W)
    depth: np.ndarray  # (H,W) planar camera-space depth; inf where alpha≈0

    def straight(self) -> np.ndarray:
        a = self.alpha[..., None]
        return np.clip(self.rgb / np.where(a > 1e-6, a, 1), 0, 1)


def camera_matrices(cam: ResolvedCamera, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """World→camera (OpenCV: +X right, +Y down, +Z forward) 4x4 and the 3x3 intrinsics."""
    f = cam.target - cam.position
    f = f / (np.linalg.norm(f) or 1)
    r = np.cross(f, cam.up)
    if np.linalg.norm(r) < 1e-9:
        r = np.cross(f, [0, 0, 1.0] if abs(f[2]) < 0.9 else [0, 1.0, 0])
    r = r / np.linalg.norm(r)
    d = np.cross(f, r)  # down
    view = np.eye(4)
    view[:3, :3] = np.stack([r, d, f])
    view[:3, 3] = -view[:3, :3] @ cam.position
    fy = 0.5 * height / math.tan(math.radians(cam.fov_y_deg) / 2)
    k = np.array([[fy, 0, width / 2], [0, fy, height / 2], [0, 0, 1.0]])
    return view, k


@lru_cache(maxsize=1)
def gsplat_available() -> bool:
    if os.environ.get("RENDA_SPLAT_BACKEND") == "numpy":
        return False
    try:
        import torch

        if not torch.cuda.is_available():
            return False
        import gsplat  # noqa: F401

        return True
    except Exception:
        return False


def backend_name() -> str:
    return "gsplat" if gsplat_available() else "numpy"


def rasterize(splats: Splats, cam: ResolvedCamera, width: int, height: int, backend: str | None = None) -> SplatLayer:
    s = splats.baked()
    backend = backend or backend_name()
    if len(s) == 0:
        return SplatLayer(np.zeros((height, width, 3)), np.zeros((height, width)), np.full((height, width), np.inf))
    if cam.ortho_height is not None:
        raise ValueError("splats need a perspective camera (orthographic isn't supported)")
    if backend == "gsplat":
        return _rasterize_gsplat(s, cam, width, height)
    return _rasterize_numpy(s, cam, width, height)


# -- gsplat (CUDA) ----------------------------------------------------------------------------


_GPU_SPLATS = None  # (splats, tensors) of the last splat set uploaded to the GPU


def _rasterize_gsplat(s: Splats, cam: ResolvedCamera, width: int, height: int) -> SplatLayer:
    import torch
    from gsplat import rasterization

    dev = torch.device("cuda")
    view, k = camera_matrices(cam, width, height)
    t = lambda a: torch.from_numpy(np.ascontiguousarray(a, dtype=np.float32)).to(dev)  # noqa: E731
    global _GPU_SPLATS
    if _GPU_SPLATS is None or _GPU_SPLATS[0] is not s:
        # Upload once per splat set, not once per frame (a turntable reuses the same tensors).
        coeffs = s.sh_dc[:, None, :] if s.sh_rest is None else np.concatenate([s.sh_dc[:, None, :], s.sh_rest], 1)
        with torch.no_grad():
            _GPU_SPLATS = (s, (t(s.means), t(s.rotations), torch.exp(t(s.scales)), torch.sigmoid(t(s.opacities)), t(coeffs)))
    means, quats, scales, opacities, colors_in = _GPU_SPLATS[1]
    with torch.no_grad():
        colors, alphas, _ = rasterization(
            means, quats, scales, opacities, colors_in,
            t(view)[None], t(k)[None], width, height, near_plane=NEAR, far_plane=1e10,
            sh_degree=s.sh_degree, render_mode="RGB+ED",
            rasterize_mode="antialiased" if s.antialiased else "classic",
        )
    out = colors[0].float().cpu().numpy()
    alpha = alphas[0, ..., 0].float().cpu().numpy()
    depth = np.where(alpha > 1e-4, out[..., 3], np.inf)
    return SplatLayer(np.clip(out[..., :3], 0, None), alpha, depth)


# -- numpy reference --------------------------------------------------------------------------


def _project(s: Splats, cam: ResolvedCamera, width: int, height: int):
    view, k = camera_matrices(cam, width, height)
    rot, trn = view[:3, :3], view[:3, 3]
    means = s.means.astype(np.float64)
    pc = means @ rot.T + trn
    z = pc[:, 2]
    keep = z > NEAR
    fx, fy, cx, cy = k[0, 0], k[1, 1], k[0, 2], k[1, 2]
    rs = quat_wxyz_to_mat(s.rotations.astype(np.float64))
    sc = np.exp(s.scales.astype(np.float64))
    m = rs * sc[:, None, :]
    sigma = m @ m.transpose(0, 2, 1)
    sigma_c = rot @ sigma @ rot.T
    zs = np.where(keep, z, 1.0)
    limx, limy = 1.3 * 0.5 * width / fx, 1.3 * 0.5 * height / fy
    tx = np.clip(pc[:, 0] / zs, -limx, limx) * zs
    ty = np.clip(pc[:, 1] / zs, -limy, limy) * zs
    j = np.zeros((len(s), 2, 3))
    j[:, 0, 0] = fx / zs
    j[:, 0, 2] = -fx * tx / zs**2
    j[:, 1, 1] = fy / zs
    j[:, 1, 2] = -fy * ty / zs**2
    cov = j @ sigma_c @ j.transpose(0, 2, 1)
    a0, b0, c0 = cov[:, 0, 0], cov[:, 0, 1], cov[:, 1, 1]
    det0 = a0 * c0 - b0 * b0
    a, b, c = a0 + 0.3, b0, c0 + 0.3
    det = a * c - b * b
    keep &= det > 0
    det = np.where(det > 0, det, 1)
    opac = sigmoid(s.opacities.astype(np.float64))
    if s.antialiased:  # mip-splatting style energy compensation for the low-pass filter
        opac = opac * np.sqrt(np.clip(det0 / det, 0, 1))
    mid = 0.5 * (a + c)
    lam = mid + np.sqrt(np.maximum(0.1, mid * mid - det))
    radius = np.ceil(3 * np.sqrt(lam))
    mx = fx * pc[:, 0] / zs + cx
    my = fy * pc[:, 1] / zs + cy
    keep &= (mx + radius > 0) & (mx - radius < width) & (my + radius > 0) & (my - radius < height)
    # view-dependent colour
    dirs = means - cam.position
    dirs /= np.maximum(np.linalg.norm(dirs, axis=1, keepdims=True), 1e-12)
    deg = s.sh_degree
    coeffs = s.sh_dc[:, None, :] if s.sh_rest is None else np.concatenate([s.sh_dc[:, None, :], s.sh_rest[:, : SH_COUNT[deg] - 1]], 1)
    rgb = np.einsum("nk,nkc->nc", sh_basis(dirs, deg), coeffs.astype(np.float64)) + 0.5
    rgb = np.clip(rgb, 0, None)
    idx = np.nonzero(keep)[0]
    idx = idx[np.argsort(z[idx], kind="stable")]  # front to back
    f32 = lambda x: x[idx].astype(np.float32)  # noqa: E731 - per-fragment maths in float32
    return dict(mx=f32(mx), my=f32(my), r=f32(radius), ca=f32(c / det), cb=f32(-b / det), cc=f32(a / det),
                op=f32(opac), rgb=f32(rgb), z=f32(z))


def _rasterize_numpy(s: Splats, cam: ResolvedCamera, width: int, height: int,
                     tile: int = 128, budget: int = 6_000_000) -> SplatLayer:
    p = _project(s, cam, width, height)
    rgb_out = np.zeros((height, width, 3))
    alpha_out = np.zeros((height, width))
    depth_w = np.zeros((height, width))
    x0 = np.clip(np.floor(p["mx"] - p["r"]), 0, width).astype(np.int64)
    x1 = np.clip(np.ceil(p["mx"] + p["r"]), 0, width).astype(np.int64)
    y0 = np.clip(np.floor(p["my"] - p["r"]), 0, height).astype(np.int64)
    y1 = np.clip(np.ceil(p["my"] + p["r"]), 0, height).astype(np.int64)
    for ty0 in range(0, height, tile):
        ty1 = min(ty0 + tile, height)
        rows = (y0 < ty1) & (y1 > ty0)
        for tx0 in range(0, width, tile):
            tx1 = min(tx0 + tile, width)
            sel = np.nonzero(rows & (x0 < tx1) & (x1 > tx0))[0]  # still depth-ordered
            if not len(sel):
                continue
            tw, th = tx1 - tx0, ty1 - ty0
            trans = np.ones(tw * th)  # transmittance carried across batches
            acc = np.zeros((tw * th, 3))
            accz = np.zeros(tw * th)
            sx0 = np.maximum(x0[sel], tx0)
            sy0 = np.maximum(y0[sel], ty0)
            wx = np.minimum(x1[sel], tx1) - sx0
            wy = np.minimum(y1[sel], ty1) - sy0
            counts = wx * wy
            bounds = np.concatenate([[0], np.cumsum(counts)])
            start = 0
            while start < len(sel):
                end = int(np.searchsorted(bounds, bounds[start] + budget, side="right")) - 1
                end = max(end, start + 1)
                b = slice(start, end)
                cnt = counts[b]
                total = int(cnt.sum())
                start = end
                if total == 0:
                    continue
                rep = np.repeat(np.arange(len(cnt)), cnt)
                local = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
                wxr = wx[b][rep]
                px = sx0[b][rep] + local % wxr
                py = sy0[b][rep] + local // wxr
                gi = sel[b][rep]
                dx = px.astype(np.float32) + np.float32(0.5) - p["mx"][gi]
                dy = py.astype(np.float32) + np.float32(0.5) - p["my"][gi]
                power = -0.5 * (p["ca"][gi] * dx * dx + p["cc"][gi] * dy * dy) - p["cb"][gi] * dx * dy
                alpha = np.minimum(np.float32(0.99), p["op"][gi] * np.exp(np.minimum(power, 0)))
                ok = (power <= 0) & (alpha >= np.float32(1 / 255))
                if not ok.any():
                    continue
                # int16 pixel ids (tiles ≤ 181²) make numpy's stable sort a radix sort
                pid = ((py[ok] - ty0) * tw + (px[ok] - tx0)).astype(np.int16 if tw * th < 32768 else np.int64)
                alpha, gi = alpha[ok].astype(np.float64), gi[ok]
                order = np.argsort(pid, kind="stable")  # keeps depth order within each pixel
                pid, alpha, gi = pid[order].astype(np.int64), alpha[order], gi[order]
                la = np.log1p(-alpha)
                cs = np.cumsum(la)
                seg_start = np.ones(len(pid), dtype=bool)
                seg_start[1:] = pid[1:] != pid[:-1]
                base = np.maximum.accumulate(np.where(seg_start, np.arange(len(pid)), 0))
                excl = cs - la - (cs[base] - la[base])
                wgt = alpha * trans[pid] * np.exp(excl)
                for ch in range(3):
                    acc[:, ch] += np.bincount(pid, weights=wgt * p["rgb"][gi, ch], minlength=tw * th)
                accz += np.bincount(pid, weights=wgt * p["z"][gi], minlength=tw * th)
                trans *= np.exp(np.bincount(pid, weights=la, minlength=tw * th))
            rgb_out[ty0:ty1, tx0:tx1] = acc.reshape(th, tw, 3)
            alpha_out[ty0:ty1, tx0:tx1] = (1 - trans).reshape(th, tw)
            depth_w[ty0:ty1, tx0:tx1] = accz.reshape(th, tw)
    depth = np.where(alpha_out > 1e-4, depth_w / np.maximum(alpha_out, 1e-12), np.inf)
    return SplatLayer(rgb_out, alpha_out, depth)


# -- backgrounds + compositing ----------------------------------------------------------------


def ray_directions(cam: ResolvedCamera, width: int, height: int) -> np.ndarray:
    """(H,W,3) unit world-space ray directions through pixel centres."""
    view, k = camera_matrices(cam, width, height)
    xs = (np.arange(width) + 0.5 - k[0, 2]) / k[0, 0]
    ys = (np.arange(height) + 0.5 - k[1, 2]) / k[1, 1]
    gx, gy = np.meshgrid(xs, ys)
    d_cam = np.stack([gx, gy, np.ones_like(gx)], -1)
    d = d_cam @ view[:3, :3]  # camera → world (rotation transpose)
    return d / np.linalg.norm(d, axis=-1, keepdims=True)


def env_background(env_rgb: np.ndarray, cam: ResolvedCamera, width: int, height: int, up_axis: str,
                   rotation_deg: float = 0.0, intensity: float = 1.0) -> np.ndarray:
    """Equirect (renda/hdri.py convention, Y-up) seen through `cam` → (H,W,3) display RGB."""
    d = ray_directions(cam, width, height).astype(np.float32)
    if up_axis == "Z":  # stage Z-up → env Y-up
        d = np.stack([d[..., 0], d[..., 2], -d[..., 1]], -1)
    a = math.radians(rotation_deg)
    x = d[..., 0] * math.cos(a) - d[..., 2] * math.sin(a)
    zz = d[..., 0] * math.sin(a) + d[..., 2] * math.cos(a)
    theta = np.arccos(np.clip(d[..., 1], -1, 1))
    phi = np.arctan2(x, -zz)
    h, w = env_rgb.shape[:2]
    u = ((phi / (2 * math.pi) + 0.5) * w).astype(np.int64) % w
    v = np.clip((theta / math.pi * h).astype(np.int64), 0, h - 1)
    # Tonemapping is per-pixel, so tonemap the (static) map once and index it per frame instead of
    # tonemapping every frame's full image (it dominated gsplat turntable frame time).
    return _display_env(env_rgb, intensity)[v, u]


_DISPLAY_ENV: dict = {}


def _display_env(env_rgb: np.ndarray, intensity: float) -> np.ndarray:
    from ..images import linear_to_srgb, tonemap

    key = (id(env_rgb), env_rgb.shape, float(intensity))
    if key not in _DISPLAY_ENV:
        if len(_DISPLAY_ENV) > 4:
            _DISPLAY_ENV.clear()
        _DISPLAY_ENV[key] = linear_to_srgb(tonemap(env_rgb[..., :3] * intensity))
    return _DISPLAY_ENV[key]


def read_env(path) -> np.ndarray:
    try:
        import mitsuba as mi

        if not mi.variant():
            mi.set_variant("scalar_rgb")
        return np.array(mi.Bitmap(str(path)).convert(mi.Bitmap.PixelFormat.RGB, mi.Struct.Type.Float32), copy=True)
    except Exception:
        from ..hdri import read_hdr

        return read_hdr(path)


def _over(c_a, a_a, c_b, a_b):
    """Straight-alpha A over B → (straight rgb, alpha)."""
    out_a = a_a + a_b * (1 - a_a)
    prem = c_a * a_a[..., None] + c_b * (a_b * (1 - a_a))[..., None]
    return prem / np.where(out_a > 1e-6, out_a, 1)[..., None], out_a


def composite_over(layer: SplatLayer, mesh_rgb: np.ndarray, mesh_alpha: np.ndarray, mesh_depth: np.ndarray | None,
                   bg_rgb: np.ndarray, bg_alpha: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """background → splats behind the mesh → mesh → splats in front of it (display space, straight alpha).

    `mesh_*` is the mesh engine's render on a transparent background (alpha = coverage) and its planar
    depth. Per pixel the splats go in front when their expected depth is nearer than the mesh, so
    semi-transparent splats straddling a surface are approximated; silhouettes blend exactly."""
    splat = layer.straight()
    a_s = layer.alpha
    if mesh_depth is None:
        front = np.zeros(a_s.shape, dtype=bool)
    else:
        front = layer.depth < mesh_depth
    behind_rgb, behind_a = _over(mesh_rgb, mesh_alpha, *_over(splat, a_s, bg_rgb, bg_alpha))
    front_rgb, front_a = _over(splat, a_s, *_over(mesh_rgb, mesh_alpha, bg_rgb, bg_alpha))
    rgb = np.where(front[..., None], front_rgb, behind_rgb)
    alpha = np.where(front, front_a, behind_a)
    return np.clip(rgb, 0, 1), np.clip(alpha, 0, 1)
