"""Lit initialisation for splat-bake training (`splatBake.init: "lit"`).

Instead of starting the trainer from albedo-coloured surface Gaussians, start it from a splat whose lighting is
already path-traced in: Gaussians are placed evenly on the surface a camera can actually see, the radiance
leaving each one is traced in DIRECTIONS directions through the same Mitsuba scene the texel baker uses, and
fitted to spherical harmonics. Training then only has to fix edges and the renderer differences, so it reaches
the surface init's final quality in about half the steps (Porsche, Modal L4, 16 held-out Cycles views: 4000
steps 34.1 dB vs 6000 steps 33.5 dB for the surface init).

1. Oversample the surface, drop transparent texels and what no outward ray escapes from, thin evenly (one sample
   per voxel) to the budget.
2. Radiance per Gaussian and visible direction (view rays from just outside it). Base-colour alpha is applied
   as Mitsuba opacity for this trace (the glTF path drops it), so shadow planes and decals don't render opaque.
3. Semi-transparent Gaussians: over other geometry (clearcoat on paint, glass on a cabin) they stay opaque and
   carry the traced mix; free-standing (shadow planes, decals over nothing) they keep their alpha and their own
   colour, un-premultiplied with a second ray traced past them.
4. Joint bilateral denoise over neighbours (similar normal and albedo) with the Gaussian's own albedo detail
   multiplied back in, tonemap like the views (AgX by default), per-Gaussian ridge fit to SH, on the GPU.

Needs Mitsuba, torch and scipy (the worker's splat image has all three).
"""

from __future__ import annotations

import contextlib
import math
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np

from ..ir.splats import SH_COUNT, Splats, logit, sh_basis
from ..ir.stage import Stage
from . import SurfaceSamples, _linear_to_srgb, _load_texture, _srgb_to_linear, init_gaussians, sample_surface

DIRECTIONS = 64  # radiance samples per Gaussian (about a third are visible)
SPP = 32  # paths per sample
OVERSAMPLE = 3.75  # candidates per kept Gaussian (about 70% of a mesh's surface is visible)
RADIUS_SCALE = 1.15  # disc radius / even-coverage radius


def fibonacci_sphere(n: int) -> np.ndarray:
    i = np.arange(n) + 0.5
    z = 1 - 2 * i / n
    r = np.sqrt(1 - z * z)
    phi = math.pi * (1 + 5 ** 0.5) * i
    return np.stack([r * np.cos(phi), r * np.sin(phi), z], 1).astype(np.float32)


def subset(s: SurfaceSamples, m) -> SurfaceSamples:
    return SurfaceSamples(s.points[m], s.normals[m], s.colors[m], s.radius[m],
                          None if s.glossy is None else s.glossy[m], None if s.alpha is None else s.alpha[m])


def thin_even(points: np.ndarray, target: int, seed: int = 0) -> np.ndarray:
    """Sorted indices of ~target points, at most one per voxel: even coverage instead of random clumps and holes."""
    if len(points) <= target:
        return np.arange(len(points))
    order = np.random.default_rng(seed).permutation(len(points))
    lo = points.min(0)

    def pick(cell: float) -> np.ndarray:
        key = np.floor((points[order] - lo) / cell).astype(np.int64)
        key = (key[:, 0] * 73856093) ^ (key[:, 1] * 19349663) ^ (key[:, 2] * 83492791)
        return order[np.unique(key, return_index=True)[1]]

    span = float(np.ptp(points, 0).max()) or 1.0
    a, b = span * 1e-5, span * 0.1  # bisect the cell size for the target count
    best = pick(b)
    for _ in range(30):
        mid = (a * b) ** 0.5
        best = pick(mid)
        if abs(len(best) - target) < 0.01 * target:
            break
        a, b = (mid, b) if len(best) > target else (a, mid)
    return np.sort(best)


@contextlib.contextmanager
def alpha_as_opacity(stage: Stage, scratch: Path):
    """Base-colour texture alpha as each material's opacity texture, for the duration of the trace."""
    from PIL import Image

    patched = []
    for i, m in enumerate(stage.materials.values()):
        if m.opacity_tex or not m.base_color_tex:
            continue
        tex = _load_texture(stage, m.base_color_tex)
        if tex is None or tex[..., 3].min() > 0.98:
            continue
        path = Path(scratch) / f"alpha-{i}.png"
        Image.fromarray((np.clip(tex[..., 3] * m.opacity, 0, 1) * 255).astype(np.uint8), "L").save(path)
        m.opacity_tex = str(path)
        patched.append(m)
    try:
        yield [m.name for m in patched]
    finally:
        for m in patched:
            m.opacity_tex = None


def _escapes(mi_scene, points: np.ndarray, normals: np.ndarray | None, dirs: np.ndarray, off: float,
             batch: int) -> np.ndarray:
    """(N,K): an outward ray from just off the point escapes the scene along each direction (and, with normals,
    the direction isn't tangent to the surface). Either side counts: splats are two-sided and plenty of meshes
    have flipped normals; the far side of a closed surface fails the escape test anyway."""
    import mitsuba as mi

    N, K = len(points), len(dirs)
    ok = np.zeros((N, K), bool)
    per = max(1, batch // K)
    for s in range(0, N, per):
        e = min(N, s + per)
        w = np.tile(dirs, (e - s, 1))
        cand = np.ones(len(w), bool) if normals is None else np.abs((w * np.repeat(normals[s:e], K, 0)).sum(1)) > 0.02
        o = (np.repeat(points[s:e], K, 0) + w * off)[cand].astype(np.float32)
        hit = np.array(mi_scene.ray_test(mi.Ray3f(mi.Point3f(*o.T), mi.Vector3f(*w[cand].T))), dtype=bool)
        blk = cand.copy()
        blk[cand] = ~hit
        ok[s:e] = blk.reshape(e - s, K)
    return ok


def _trace(mi_scene, origins: np.ndarray, dirs: np.ndarray, spp: int, seed: int, batch: int):
    """Mean path-traced radiance (n,3) along rays, and the first-hit distance (n,), inf on a miss."""
    import drjit as dr
    import mitsuba as mi

    integrator = mi_scene.integrator()
    L_out = np.zeros((len(origins), 3), np.float32)
    t_out = np.full(len(origins), np.inf, np.float32)
    for s in range(0, len(origins), batch):
        e = min(len(origins), s + batch)
        n = e - s
        ray = mi.Ray3f(mi.Point3f(*origins[s:e].astype(np.float32).T), mi.Vector3f(*dirs[s:e].astype(np.float32).T))
        si = mi_scene.ray_intersect(ray)
        t_out[s:e] = np.where(np.array(si.is_valid(), dtype=bool), np.array(si.t, dtype=np.float32), np.inf)
        sampler = mi.load_dict({"type": "independent", "sample_count": 1})
        sampler.seed(seed * 7919 + s, n)
        total = dr.zeros(mi.Color3f, n)
        for _ in range(spp):
            L, _, _ = integrator.sample(mi_scene, sampler, ray)
            total += mi.Color3f(L)
            sampler.schedule_state()
            dr.eval(total)
        arr = np.array(total, dtype=np.float32)
        L_out[s:e] = (arr.T if arr.shape == (3, n) else arr.reshape(n, 3)) / spp
    return L_out, t_out


def trace_radiance(mi_scene, points: np.ndarray, ok: np.ndarray, dirs: np.ndarray, spp: int, off: float, seed: int,
                   batch: int, behind: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Radiance leaving each point towards each visible direction (N,K,3 float16), from a view ray just outside it.
    behind=True continues that view ray past the point instead: what shows through it, and how far (N,K)."""
    n_idx, k_idx = np.nonzero(ok)
    w = dirs[k_idx]
    sign = -1.0 if behind else 1.0
    L, t = _trace(mi_scene, points[n_idx] + sign * w * off, -w, spp, seed + (99 if behind else 0), batch)
    L_out = np.zeros(ok.shape + (3,), np.float16)
    t_out = np.full(ok.shape, np.inf, np.float32)
    L_out[n_idx, k_idx] = np.minimum(L, 6e4)
    t_out[n_idx, k_idx] = t
    return L_out, t_out


def denoise_fit(L: np.ndarray, ok: np.ndarray, samples: SurfaceSamples, spacing: float, dirs: np.ndarray, degree: int,
                tonemap: str = "agx", exposure: float = 0.0, device: str = "cuda", k: int = 24):
    """Joint bilateral denoise of the radiance over neighbouring Gaussians (same direction index, similar normal and
    albedo; own albedo detail multiplied back in), tonemap + sRGB, then a per-Gaussian weighted ridge fit to SH
    (3DGS convention: rgb = B(camera → point)·c + 0.5). Hidden directions are pulled softly to the visible mean,
    higher bands are damped. Returns (coeffs (N,nc,3), visible direction count (N,))."""
    import torch
    from scipy.spatial import cKDTree

    from ..images import _AGX_IN, _AGX_MAX_EV, _AGX_MIN_EV, _AGX_OUT, tonemap as tonemap_np

    dev = torch.device(device)
    N = len(L)
    Lg = torch.from_numpy(L).to(dev)
    okg = torch.from_numpy(ok).to(dev)
    nrm = torch.from_numpy(samples.normals.astype(np.float32)).to(dev)
    a = torch.from_numpy(_srgb_to_linear(samples.colors.astype(np.float64)).astype(np.float32)).to(dev)
    d, idx = cKDTree(samples.points).query(samples.points, min(k, N))
    idx = torch.from_numpy(np.asarray(idx).reshape(N, -1)).to(dev)
    d = torch.from_numpy(np.asarray(d, np.float32).reshape(N, -1)).to(dev)
    out = torch.empty_like(Lg)
    for s in range(0, N, 16384):
        e = min(N, s + 16384)
        I = idx[s:e]
        nd = (nrm[s:e, None] * nrm[I]).sum(-1)
        rel = (a[s:e, None] - a[I]).abs().sum(-1) / (a[s:e, None].sum(-1) + a[I].sum(-1) + 0.03)
        w = torch.exp(-(d[s:e] / (2.0 * spacing)) ** 2) * ((nd - 0.75) / 0.2).clamp(0, 1) * torch.exp(-(rel / 0.12) ** 2)
        Wk = w[:, :, None] * okg[I].float()
        Ls = torch.einsum("nkd,nkdc->ndc", Wk, Lg[I].float()) / Wk.sum(1).clamp_min(1e-6)[..., None]
        a_s = (w[..., None] * a[I]).sum(1) / w.sum(1).clamp_min(1e-6)[:, None]
        detail = ((a[s:e] + 0.03) / (a_s + 0.03)).clamp(0, 4)  # offset: black/metallic albedo carries no detail
        out[s:e] = torch.where(okg[s:e, :, None], Ls * detail[:, None, :], Lg[s:e].float()).half()

    ain = torch.tensor(_AGX_IN, dtype=torch.float32, device=dev)
    aout = torch.tensor(_AGX_OUT, dtype=torch.float32, device=dev)
    A = torch.from_numpy(sh_basis(-dirs.astype(np.float64), degree).astype(np.float32)).to(dev)
    nc, K = A.shape[1], A.shape[0]
    band = np.concatenate([[0], np.repeat([1, 2, 3], [3, 5, 7])])[:nc]
    reg = torch.diag(torch.tensor(np.array([0, 2e-3, 1e-2, 3e-2])[band] * K, dtype=torch.float32, device=dev))
    fb = torch.from_numpy(samples.colors.astype(np.float32)).to(dev)
    coeffs = torch.empty((N, nc, 3), device=dev)
    vis_n = torch.empty(N, dtype=torch.int64, device=dev)
    for s in range(0, N, 65536):
        e = min(N, s + 65536)
        if tonemap == "agx":
            v = (out[s:e].float() * 2.0 ** exposure).clamp_min(1e-10) @ ain
            v = (torch.log2(v).clamp(_AGX_MIN_EV, _AGX_MAX_EV) - _AGX_MIN_EV) / (_AGX_MAX_EV - _AGX_MIN_EV)
            x2 = v * v
            x4 = x2 * x2
            v = 15.5 * x4 * x2 - 40.14 * x4 * v + 31.96 * x4 - 6.868 * x2 * v + 0.4298 * x2 + 0.1191 * v - 0.00232
            v = (v @ aout).clamp(0, 1) ** 2.2
        else:
            v = torch.from_numpy(tonemap_np(out[s:e].float().cpu().numpy(), tonemap, exposure).astype(np.float32)).to(dev)
        col = torch.where(v <= 0.0031308, v * 12.92, 1.055 * v.clamp_min(0) ** (1 / 2.4) - 0.055)
        o = okg[s:e]
        vis = o.sum(1)
        mean = torch.where(vis[:, None] > 0, (col * o[..., None]).sum(1) / vis.clamp_min(1)[:, None], fb[s:e])
        tgt = torch.where(o[..., None], col, mean[:, None, :])
        wt = torch.where(o, 1.0, 0.05)
        Aw = (wt[:, :, None] * A[None]).transpose(1, 2)
        coeffs[s:e] = torch.linalg.solve(Aw @ A + reg[None], Aw @ (tgt - 0.5))
        vis_n[s:e] = vis
    return coeffs.cpu().numpy(), vis_n.cpu().numpy()


def lit_gaussians(stage: Stage, pkg, asset_paths: dict, points: int, sh_degree: int, seed: int = 0,
                  cache_dir: Path | None = None, batch: int = 1 << 21, device: str = "cuda") -> tuple[Splats, dict]:
    """The lit starting splat (stage coordinates) and timing/metrics."""
    from ..engines.mitsuba import baking_scene, pick_variant

    t0 = time.time()
    metrics: dict = {}
    pick_variant(device != "cpu")
    lo, hi = stage.bounds()
    off = 1e-3 * (float(np.linalg.norm(hi - lo)) or 1.0)
    scratch = Path(tempfile.mkdtemp(prefix="lit-init-", dir=cache_dir))
    try:
        with alpha_as_opacity(stage, scratch) as patched:
            mi_scene = baking_scene(pkg, stage, asset_paths, scratch)
        metrics["alpha_materials"] = patched

        # 1. visible, solid surface, thinned evenly to the budget
        cand = sample_surface(stage, int(points * OVERSAMPLE), seed)
        area = math.pi * len(cand.points) * (float(cand.radius[0]) / 1.3) ** 2
        keep = cand.alpha >= 0.03
        keep[keep] = _escapes(mi_scene, cand.points[keep], None, fibonacci_sphere(32), off, batch).any(1)
        visible_frac = float(keep.mean())
        cand = subset(cand, keep)
        samples = subset(cand, thin_even(cand.points, points, seed))
        spacing = math.sqrt(area * visible_frac / max(len(samples.points), 1))
        samples.radius[:] = spacing / math.sqrt(math.pi) * RADIUS_SCALE
        metrics["sample_s"] = round(time.time() - t0, 1)

        # 2. radiance; for semi-transparent Gaussians also what shows through them
        t1 = time.time()
        dirs = fibonacci_sphere(DIRECTIONS)
        ok = _escapes(mi_scene, samples.points, samples.normals, dirs, off, batch)
        L, _ = trace_radiance(mi_scene, samples.points, ok, dirs, SPP, off, seed, batch)
        semi = np.nonzero(samples.alpha < 0.98)[0]
        Lb, tb = trace_radiance(mi_scene, samples.points[semi], ok[semi], dirs, SPP, off, seed, batch, behind=True)
        metrics["trace_s"] = round(time.time() - t1, 1)
        del mi_scene
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        with contextlib.suppress(Exception):  # hand the GPU memory Dr.Jit cached back for torch (training)
            import drjit as dr

            dr.flush_malloc_cache()

    # 3. layered (something behind) → opaque with the traced mix; free-standing → own alpha, un-premultiplied
    opacity = np.full(len(samples.points), 0.9, np.float32)
    okb = ok[semi]
    free = (np.isinf(tb) & okb).sum(1) / np.maximum(okb.sum(1), 1) > 0.5
    fa = samples.alpha[semi][free]
    Lf = (L[semi][free].astype(np.float32) - (1 - fa[:, None, None]) * Lb[free].astype(np.float32)) / np.maximum(fa, 0.05)[:, None, None]
    L[semi[free]] = np.clip(Lf, 0, 6e4).astype(np.float16)
    opacity[semi[free]] = np.minimum(0.9, 1 - (1 - fa) ** 0.75)  # neighbouring discs overlap ~1.3x

    # 4. denoise, tonemap like the views, fit SH
    t2 = time.time()
    post = pkg.post
    coeffs, vis = denoise_fit(L, ok, samples, spacing, dirs, sh_degree, post.tonemap if post is not None else "agx",
                              post.exposure if post is not None else 0.0, device)
    metrics["fit_s"] = round(time.time() - t2, 1)
    keep = vis > 0
    init = init_gaussians(subset(samples, keep), sh_degree)
    splats = Splats("lit", init.means, init.scales, init.rotations, logit(opacity[keep]).astype(np.float32),
                    coeffs[keep, 0], coeffs[keep, 1:] if sh_degree else None, antialiased=True)
    metrics.update(gaussians=len(splats), spacing_mm=round(spacing * 1000, 1), visible=round(visible_frac, 3),
                   seconds=round(time.time() - t0, 1))
    return splats, metrics
