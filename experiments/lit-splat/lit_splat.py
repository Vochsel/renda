"""EXPERIMENT (not wired into renda): bake a lit Gaussian splat from a mesh scene without rendering views.

Mesh2Splat-style surfels (renda.splatbake.sample_surface / init_gaussians), then for every surfel the
radiance leaving it is path-traced in K directions (Mitsuba, same scene export as the texel baker:
env + lights + GI + full BSDF) and least-squares fitted to SH (degree 0-3). No training views, no trainer.

    uv run --project core --extra mitsuba python experiments/lit-splat/lit_splat.py PACKAGE.renda.json OUT_DIR
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import tempfile
import time
from pathlib import Path

import numpy as np

from renda import assets as assets_mod
from renda.images import tonemap
from renda.ir.splats import SH_COUNT, Splats, sh_basis
from renda.scene import build_stage, env_map_path, package_lights
from renda.spec import load_package
from renda.ir.splats import logit
from renda.splatbake import SurfaceSamples, _linear_to_srgb, _load_texture, _sample_texture, _srgb_to_linear, \
    export_splats, init_gaussians, material_is_glossy


def fibonacci_sphere(n: int) -> np.ndarray:
    i = np.arange(n) + 0.5
    z = 1 - 2 * i / n
    r = np.sqrt(1 - z * z)
    phi = math.pi * (1 + 5 ** 0.5) * i
    return np.stack([r * np.cos(phi), r * np.sin(phi), z], 1)


def mitsuba_scene(pkg, stage, asset_paths, scratch: Path, max_depth: int, extra: dict | None = None):
    """Same scene the texel baker (engines.mitsuba.bake_atlas) builds."""
    import mitsuba as mi

    from renda.engines.mitsuba import pick_variant
    from renda.ir.exporters.mitsuba import TextureCache, light_dict, stage_to_dict

    variant = pick_variant(True)
    scene = stage_to_dict(stage, mi, TextureCache(stage.base_dir), scratch / "mi-meshes")
    for i, lt in enumerate(package_lights(pkg, stage)):
        d = light_dict(lt, stage.up_vector(), mi)
        if d:
            scene[f"pkg_light_{i}"] = d
    env = pkg.environment
    dome = next((l for l in stage.lights if l.type == "dome" and l.texture), None)
    env_path = env_map_path(pkg, asset_paths) or (Path(dome.texture) if dome is not None else None)
    if env_path is not None:
        rot = mi.ScalarTransform4f().rotate(stage.up_vector().tolist(), env.rotationDeg)
        if stage.up_axis == "Z":
            rot = rot @ mi.ScalarTransform4f().rotate([1, 0, 0], 90)
        scene["environment"] = {"type": "envmap", "filename": str(env_path), "scale": float(env.intensity), "to_world": rot}
    elif not any(k.startswith(("light_", "pkg_light_")) for k in scene):
        scene["environment"] = {"type": "constant", "radiance": {"type": "rgb", "value": [0.8, 0.8, 0.8]}}
    scene["integrator"] = {"type": "path", "max_depth": max_depth}
    scene.update(extra(mi) if extra else {})
    return mi.load_dict(scene), variant


def trace_rays(mi_scene, O: np.ndarray, D: np.ndarray, spp: int, seed: int, batch: int) -> tuple[np.ndarray, np.ndarray]:
    """Mean path-traced radiance (n,3) along rays and their first-hit distance (n,), inf on a miss."""
    import drjit as dr
    import mitsuba as mi

    integrator = mi_scene.integrator()
    L_out = np.zeros((len(O), 3), np.float32)
    t_out = np.full(len(O), np.inf, np.float32)
    for s in range(0, len(O), batch):
        e = min(len(O), s + batch)
        n = e - s
        ray = mi.Ray3f(mi.Point3f(*O[s:e].astype(np.float32).T), mi.Vector3f(*D[s:e].astype(np.float32).T))
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


def visibility(mi_scene, points: np.ndarray, normals: np.ndarray, dirs: np.ndarray, off: float, batch: int) -> np.ndarray:
    """(N,K): direction not tangent to the surfel and an outward ray along it escapes the scene, so a camera
    out there sees the surfel (either side: splats are two-sided and plenty of meshes have flipped normals)."""
    import mitsuba as mi

    N, K = len(points), len(dirs)
    ok = np.zeros((N, K), bool)
    per = max(1, batch // K)
    for s in range(0, N, per):
        e = min(N, s + per)
        w = np.tile(dirs, (e - s, 1))
        front = np.abs((w * np.repeat(normals[s:e], K, 0)).sum(1)) > 0.02
        o = (np.repeat(points[s:e], K, 0) + w * off)[front].astype(np.float32)
        hit = np.array(mi_scene.ray_test(mi.Ray3f(mi.Point3f(*o.T), mi.Vector3f(*w[front].astype(np.float32).T))), dtype=bool)
        blk = front.copy()
        blk[front] = ~hit
        ok[s:e] = blk.reshape(e - s, K)
    return ok


def trace_radiance(mi_scene, points: np.ndarray, ok: np.ndarray, dirs: np.ndarray, spp: int, off: float, seed: int,
                   batch: int, behind: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Radiance leaving each point towards each visible direction (N,K,3), traced by a view ray from outside.
    behind=True instead continues that view ray past the point: what shows through a semi-transparent surfel,
    and how far away it is (N,K)."""
    n_idx, k_idx = np.nonzero(ok)
    w = dirs[k_idx]
    sign = -1.0 if behind else 1.0
    L, t = trace_rays(mi_scene, points[n_idx] + sign * w * off, -w, spp, seed + (99 if behind else 0), batch)
    L_out = np.zeros(ok.shape + (3,), np.float16)
    t_out = np.full(ok.shape, np.inf, np.float32)
    L_out[n_idx, k_idx] = L
    t_out[n_idx, k_idx] = t
    return L_out, t_out


def fit_sh(colors: np.ndarray, ok: np.ndarray, dirs: np.ndarray, degree: int, fallback: np.ndarray):
    """Per-surfel weighted ridge fit of display colours to SH (3DGS convention: rgb = B(view dir)·c + 0.5,
    view dir = camera → point = -dir). Hidden directions are softly pulled to the visible mean so the back
    of thin parts and silhouettes stay sane; higher bands are damped."""
    nc = SH_COUNT[degree]
    A = sh_basis(-dirs, degree)  # (K, nc)
    K = len(dirs)
    vis = ok.sum(1)
    mean = np.where(vis[:, None] > 0, (colors * ok[..., None]).sum(1) / np.maximum(vis, 1)[:, None], fallback)
    target = np.where(ok[..., None], colors, mean[:, None, :]) - 0.5
    w = np.where(ok, 1.0, 0.05)
    band = np.concatenate([[0], np.repeat([1, 2, 3], [3, 5, 7])])[:nc]
    reg = np.array([0, 2e-3, 1e-2, 3e-2])[band] * K
    out = np.empty((len(colors), nc, 3), np.float32)
    for s in range(0, len(colors), 65536):
        e = min(len(colors), s + 65536)
        Aw = (w[s:e, :, None] * A[None]).transpose(0, 2, 1)  # (n, nc, K)
        M = Aw @ A + np.diag(reg)[None]
        rhs = Aw @ target[s:e]
        out[s:e] = np.linalg.solve(M, rhs)
    resid = np.einsum("kj,njc->nkc", A, out, optimize=True) + 0.5 - np.where(ok[..., None], colors, mean[:, None, :])
    rmse = float(np.sqrt((resid[ok] ** 2).mean())) if ok.any() else 0.0
    return out, vis, rmse


def alpha_to_opacity(stage, scratch: Path) -> list[str]:
    """Base-colour alpha (shadow planes, decals, light lenses) as Mitsuba opacity textures. renda's glTF path
    drops it, so these surfaces render opaque (EXPERIMENT-ONLY patch on the in-memory stage)."""
    from PIL import Image

    names = []
    for name, m in stage.materials.items():
        if m.opacity_tex or not m.base_color_tex:
            continue
        tex = _load_texture(stage, m.base_color_tex)
        if tex is None or tex[..., 3].min() > 0.98:
            continue
        path = scratch / f"alpha-{len(names)}.png"
        Image.fromarray((np.clip(tex[..., 3] * m.opacity, 0, 1) * 255).astype(np.uint8), "L").save(path)
        m.opacity_tex = str(path)
        names.append(name)
    return names


def sample_surface_alpha(stage, n: int, seed: int = 0) -> tuple[SurfaceSamples, np.ndarray]:
    """renda.splatbake.sample_surface plus per-sample opacity (factor x base-colour alpha)."""
    tris_p, tris_n, tris_uv, mats = [], [], [], []
    for m in stage.meshes:
        if m.indices is None or len(m.indices) == 0:
            continue
        pts = m.world_points() if hasattr(m, "world_points") else m.points
        idx = np.asarray(m.indices, dtype=np.int64)
        tris_p.append(pts[idx])
        if m.normals is not None and len(m.normals) == len(m.points):
            tris_n.append((m.normals @ np.linalg.inv(np.asarray(m.transform)[:3, :3]))[idx])
        else:
            tris_n.append(None)
        tris_uv.append(m.uvs[idx] if m.uvs is not None and len(m.uvs) == len(m.points) else None)
        mats.append(stage.materials.get(m.material) if m.material else None)
    areas = [0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1) for t in tris_p]
    total = float(sum(a.sum() for a in areas)) or 1.0
    rng = np.random.default_rng(seed)
    P, Nn, C, A, G = [], [], [], [], []
    for t, tn, tuv, mat, a in zip(tris_p, tris_n, tris_uv, mats, areas):
        k = int(round(n * a.sum() / total))
        if k == 0 or a.sum() == 0:
            continue
        face = rng.choice(len(t), size=k, p=a / a.sum())
        s1, r2 = np.sqrt(rng.random(k)), rng.random(k)
        bary = np.stack([1 - s1, s1 * (1 - r2), s1 * r2], axis=1)
        tri = t[face]
        geo_n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        geo_n /= np.maximum(np.linalg.norm(geo_n, axis=1, keepdims=True), 1e-12)
        nn = geo_n
        if tn is not None:
            nn = np.einsum("nk,nkd->nd", bary, tn[face])
            nn /= np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-12)
            nn = np.where(np.isfinite(nn).all(axis=1, keepdims=True), nn, geo_n)
        col = np.tile(np.array(mat.base_color[:3] if mat else (0.8, 0.8, 0.8)), (k, 1))
        alpha = np.full(k, float(mat.opacity) if mat else 1.0)
        tex = _load_texture(stage, mat.base_color_tex) if mat else None
        if tex is not None and tuv is not None:
            texel = _sample_texture(tex, np.einsum("nk,nkd->nd", bary, tuv[face]))
            col = col * _srgb_to_linear(texel[:, :3])
            alpha = alpha * texel[:, 3]
        if mat is not None and mat.emission_strength > 0:
            col = col + np.array(mat.emission) * mat.emission_strength
        P.append(np.einsum("nk,nkd->nd", bary, tri)); Nn.append(nn); C.append(_linear_to_srgb(np.clip(col, 0, 1)))
        A.append(alpha); G.append(np.full(k, material_is_glossy(mat)))
    pts = np.concatenate(P).astype(np.float32)
    radius = np.full(len(pts), math.sqrt(total / max(len(pts), 1) / math.pi) * 1.3, np.float32)
    return (SurfaceSamples(pts, np.concatenate(Nn).astype(np.float32), np.concatenate(C).astype(np.float32), radius,
                           np.concatenate(G)), np.concatenate(A).astype(np.float32))


def visible_mask(mi_scene, points: np.ndarray, dirs: np.ndarray, off: float, batch_rays: int) -> np.ndarray:
    """Points an outward ray escapes from in at least one of `dirs` (either side of the surface)."""
    import mitsuba as mi

    K = len(dirs)
    out = np.zeros(len(points), bool)
    per = max(1, batch_rays // K)
    for s in range(0, len(points), per):
        e = min(len(points), s + per)
        w = np.tile(dirs, (e - s, 1)).astype(np.float32)
        o = (np.repeat(points[s:e], K, 0) + w * off).astype(np.float32)
        hit = np.array(mi_scene.ray_test(mi.Ray3f(mi.Point3f(*o.T), mi.Vector3f(*w.T))), dtype=bool)
        out[s:e] = (~hit).reshape(e - s, K).any(1)
    return out


def subset(s: SurfaceSamples, m) -> SurfaceSamples:
    return SurfaceSamples(s.points[m], s.normals[m], s.colors[m], s.radius[m], s.glossy[m])


def thin_even(points: np.ndarray, target: int, seed: int) -> np.ndarray:
    """Indices of ~target points, at most one per voxel: even coverage instead of random clumps and holes."""
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(points))
    lo = points.min(0)
    def pick(cell):
        key = np.floor((points[order] - lo) / cell).astype(np.int64)
        key = (key[:, 0] * 73856093) ^ (key[:, 1] * 19349663) ^ (key[:, 2] * 83492791)
        _, first = np.unique(key, return_index=True)
        return order[first]
    span = float(np.ptp(points, 0).max())
    a, b = span * 1e-5, span * 0.1  # bisect the cell size for the target count
    for _ in range(30):
        mid = (a * b) ** 0.5
        n = len(pick(mid))
        if abs(n - target) < 0.01 * target:
            break
        a, b = (mid, b) if n > target else (a, mid)
    return np.sort(pick(mid))


def denoise(L: np.ndarray, ok: np.ndarray, points: np.ndarray, normals: np.ndarray, albedo: np.ndarray,
            spacing: float, k: int = 24) -> np.ndarray:
    """Joint bilateral filter of the traced radiance over neighbouring surfels (same direction index, similar
    normal and albedo), then the surfel's own albedo detail is multiplied back in (texture demodulation):
    lighting is smooth, texture is sharp, so the noise goes and the texture stays."""
    from scipy.spatial import cKDTree

    d, idx = cKDTree(points).query(points, k)
    a = albedo.astype(np.float32)
    out = np.empty_like(L)
    for s in range(0, len(points), 4096):
        e = min(len(points), s + 4096)
        I = idx[s:e]
        nd = np.einsum("nc,nkc->nk", normals[s:e], normals[I])
        rel = np.abs(a[s:e, None] - a[I]).sum(-1) / (a[s:e, None].sum(-1) + a[I].sum(-1) + 0.03)
        w = np.exp(-(d[s:e] / (2.0 * spacing)) ** 2) * np.clip((nd - 0.75) / 0.2, 0, 1) * np.exp(-(rel / 0.12) ** 2)
        Wk = w[:, :, None] * ok[I]  # (n,k,K)
        num = np.einsum("nkd,nkdc->ndc", Wk, L[I].astype(np.float32), optimize=True)
        Ls = num / np.maximum(Wk.sum(1), 1e-6)[..., None]
        a_s = (w[..., None] * a[I]).sum(1) / np.maximum(w.sum(1), 1e-6)[:, None]
        detail = np.clip((a[s:e] + 0.03) / (a_s + 0.03), 0, 4)  # offset: black/metallic albedo carries no detail
        out[s:e] = np.where(ok[s:e, :, None], Ls * detail[:, None, :], L[s:e])
    return out


def cuda_available() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:
        return False


def denoise_fit_gpu(L, ok, points, normals, albedo, spacing, dirs, degree, fallback, exposure, do_denoise=True, k=24):
    """denoise() + AgX tonemap + fit_sh() in torch on the GPU (same maths; the CPU path takes minutes at 1M)."""
    import torch
    from scipy.spatial import cKDTree

    from renda.images import _AGX_IN, _AGX_MAX_EV, _AGX_MIN_EV, _AGX_OUT

    dev = os.environ.get("LIT_DEVICE", "cuda")
    N = len(points)
    Lg = torch.from_numpy(L).to(dev)
    okg = torch.from_numpy(ok).to(dev)
    if do_denoise:
        nrm = torch.from_numpy(normals.astype(np.float32)).to(dev)
        a = torch.from_numpy(albedo.astype(np.float32)).to(dev)
        d, idx = cKDTree(points).query(points, k)
        idx, d = torch.from_numpy(idx).to(dev), torch.from_numpy(d.astype(np.float32)).to(dev)
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
            detail = ((a[s:e] + 0.03) / (a_s + 0.03)).clamp(0, 4)
            out[s:e] = torch.where(okg[s:e, :, None], Ls * detail[:, None, :], Lg[s:e].float()).half()
        Lg = out
    ain = torch.tensor(_AGX_IN, dtype=torch.float32, device=dev)
    aout = torch.tensor(_AGX_OUT, dtype=torch.float32, device=dev)
    A = torch.from_numpy(sh_basis(-dirs.astype(np.float64), degree).astype(np.float32)).to(dev)
    nc, K = A.shape[1], A.shape[0]
    band = np.concatenate([[0], np.repeat([1, 2, 3], [3, 5, 7])])[:nc]
    reg = torch.diag(torch.tensor(np.array([0, 2e-3, 1e-2, 3e-2])[band] * K, dtype=torch.float32, device=dev))
    fb = torch.from_numpy(fallback.astype(np.float32)).to(dev)
    coeffs = torch.empty((N, nc, 3), device=dev)
    vis_n = torch.empty(N, dtype=torch.int64, device=dev)
    sq = torch.zeros((), device=dev, dtype=torch.float64)
    cnt = 0
    for s in range(0, N, 65536):
        e = min(N, s + 65536)
        v = (Lg[s:e].float() * 2.0 ** exposure).clamp_min(1e-10) @ ain
        v = (torch.log2(v).clamp(_AGX_MIN_EV, _AGX_MAX_EV) - _AGX_MIN_EV) / (_AGX_MAX_EV - _AGX_MIN_EV)
        x2 = v * v
        x4 = x2 * x2
        v = 15.5 * x4 * x2 - 40.14 * x4 * v + 31.96 * x4 - 6.868 * x2 * v + 0.4298 * x2 + 0.1191 * v - 0.00232
        v = (v @ aout).clamp(0, 1) ** 2.2
        col = torch.where(v <= 0.0031308, v * 12.92, 1.055 * v.clamp_min(0) ** (1 / 2.4) - 0.055)
        o = okg[s:e]
        vis = o.sum(1)
        mean = torch.where(vis[:, None] > 0, (col * o[..., None]).sum(1) / vis.clamp_min(1)[:, None], fb[s:e])
        tgt = torch.where(o[..., None], col, mean[:, None, :])
        w = torch.where(o, 1.0, 0.05)
        Aw = (w[:, :, None] * A[None]).transpose(1, 2)
        c = torch.linalg.solve(Aw @ A + reg[None], Aw @ (tgt - 0.5))
        coeffs[s:e], vis_n[s:e] = c, vis
        resid = A @ c + 0.5 - tgt
        sq += (resid[o] ** 2).sum().double()
        cnt += int(o.sum()) * 3
    return coeffs.cpu().numpy(), vis_n.cpu().numpy(), float((sq / max(cnt, 1)).sqrt())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("package", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--points", type=int, default=1_000_000, help="Gaussians on visible surfaces")
    ap.add_argument("--oversample", type=float, default=3.0)
    ap.add_argument("--dirs", type=int, default=64)
    ap.add_argument("--spp", type=int, default=32)
    ap.add_argument("--sh", type=int, default=3)
    ap.add_argument("--max-depth", type=int, default=6)
    ap.add_argument("--batch-rays", type=int, default=1 << 21)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--radius-scale", type=float, default=1.15, help="disc radius / even-coverage radius")
    ap.add_argument("--no-denoise", action="store_true")
    ap.add_argument("--tonemap", default=None, help="agx|filmic|aces|neutral|standard (default: the package's)")
    args = ap.parse_args()

    t0 = time.time()
    pkg = load_package(args.package)
    asset_paths = assets_mod.resolve(pkg, args.package.parent)
    stage, warnings = build_stage(pkg, asset_paths)
    args.out.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix="lit-splat-"))
    try:
        patched = alpha_to_opacity(stage, scratch)
        if patched:
            print(f"alpha → opacity: {', '.join(patched)}")
        mi_scene, variant = mitsuba_scene(pkg, stage, asset_paths, scratch, args.max_depth)
        lo, hi = stage.bounds()
        diag = float(np.linalg.norm(hi - lo)) or 1.0
        off = 1e-3 * diag

        # 1. oversample, drop transparent texels and what no camera can see, thin evenly to the budget
        cand, alpha = sample_surface_alpha(stage, int(args.points * args.oversample / 0.8), args.seed)
        area = math.pi * len(cand.points) * (float(cand.radius[0]) / 1.3) ** 2
        solid = alpha >= 0.03
        vis = np.zeros(len(alpha), bool)
        vis[solid] = visible_mask(mi_scene, cand.points[solid], fibonacci_sphere(32), off, args.batch_rays)
        cand, alpha = subset(cand, vis), alpha[vis]
        keep = thin_even(cand.points, args.points, args.seed)
        samples, alpha = subset(cand, keep), alpha[keep]
        spacing = math.sqrt(area * vis.mean() / len(samples.points))
        samples.radius[:] = spacing / math.sqrt(math.pi) * args.radius_scale
        print(f"surfels: {len(vis):,} candidates, {vis.mean() * 100:.0f}% visible+solid, {len(samples.points):,} kept, "
              f"spacing {spacing * 1000:.1f} mm, {(alpha < 0.98).sum():,} semi-transparent ({time.time() - t0:.1f}s)")
        export_splats(init_gaussians(samples, 0), args.out / "albedo.spz", stage.up_axis)

        # 2. radiance per surfel and direction; for semi-transparent surfels also what shows through them
        dirs = fibonacci_sphere(args.dirs).astype(np.float32)
        t2 = time.time()
        ok = visibility(mi_scene, samples.points, samples.normals, dirs, off, args.batch_rays)
        L, _ = trace_radiance(mi_scene, samples.points, ok, dirs, args.spp, off, args.seed, args.batch_rays)
        semi = np.nonzero(alpha < 0.98)[0]
        Lb, tb = trace_radiance(mi_scene, samples.points[semi], ok[semi], dirs, args.spp, off, args.seed, args.batch_rays,
                                behind=True)
        print(f"trace ({variant}): {time.time() - t2:.1f}s, {ok.mean() * 100:.0f}% of direction samples visible")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    # Semi-transparent surfels: over other geometry (clearcoat on paint, glass on the cabin) the surfel stands for
    # the whole stack, so it stays opaque with the traced mix (what's behind was culled as hidden). Free-standing
    # (shadow planes, decals over nothing) it keeps its alpha and its own colour: un-premultiply the traced mix.
    opacity = np.full(len(alpha), 0.9, np.float32)
    okb = ok[semi]
    escapes = (np.isinf(tb) & okb).sum(1) / np.maximum(okb.sum(1), 1)
    free = escapes > 0.5
    fa = alpha[semi][free][:, None, None]
    Lf = (L[semi][free].astype(np.float32) - (1 - fa) * Lb[free].astype(np.float32)) / np.maximum(fa, 0.05)
    L[semi[free]] = np.clip(Lf, 0, None).astype(np.float16)
    opacity[semi[free]] = np.minimum(0.9, 1 - (1 - alpha[semi][free]) ** 0.75)  # neighbours overlap ~1.3x
    print(f"alpha: {free.sum():,} free-standing surfels keep their alpha, {(~free).sum():,} layered stay opaque")

    # 3. denoise (bilateral over neighbours + albedo detail), 4. tonemap like renda's renders and fit SH
    t3 = time.time()
    post = pkg.post
    tm = args.tonemap or (post.tonemap if post is not None else "agx")
    exposure = post.exposure if post is not None else 0.0
    gpu = cuda_available() and tm == "agx"
    if gpu:
        coeffs, vis_n, rmse = denoise_fit_gpu(L, ok, samples.points, samples.normals,
                                              _srgb_to_linear(samples.colors.astype(np.float64)), spacing, dirs, args.sh,
                                              samples.colors, exposure, not args.no_denoise)
        sq = [rmse ** 2]
        print(f"denoise + fit (CUDA): {time.time() - t3:.1f}s")
    elif not args.no_denoise:
        L = denoise(L, ok, samples.points, samples.normals, _srgb_to_linear(samples.colors.astype(np.float64)), spacing)
        print(f"denoise: {time.time() - t3:.1f}s")
    t4 = time.time()
    nc = SH_COUNT[args.sh]
    if not gpu:
        coeffs = np.empty((len(L), nc, 3), np.float32)
        vis_n = np.empty(len(L), np.int64)
        sq = []
    for s in range(0, 0 if gpu else len(L), 100_000):
        e = min(len(L), s + 100_000)
        colors = _linear_to_srgb(tonemap(L[s:e].astype(np.float64), tm, exposure))
        coeffs[s:e], vis_n[s:e], rmse = fit_sh(colors, ok[s:e], dirs, args.sh, samples.colors[s:e].astype(np.float64))
        sq.append(rmse ** 2)
    keep = vis_n > 0
    init = init_gaussians(subset(samples, keep), args.sh)
    splats = Splats("lit", init.means, init.scales, init.rotations, logit(opacity[keep]).astype(np.float32),
                    coeffs[keep, 0], coeffs[keep, 1:] if args.sh else None, antialiased=True)
    out = export_splats(splats, args.out / f"lit-sh{args.sh}.spz", stage.up_axis)
    # full precision, stage coordinates: the starting point for training (experiments/lit-splat/modal_bench.py)
    np.savez(args.out / "lit.npz", means=splats.means, scales=splats.scales, rotations=splats.rotations,
             opacities=splats.opacities, sh_dc=splats.sh_dc, sh_rest=splats.sh_rest if args.sh else np.zeros(0))
    print(f"fit: {time.time() - t4:.1f}s, rmse {math.sqrt(np.mean(sq)):.4f} (display units)")
    print(f"wrote {out}: {len(splats):,} Gaussians, {out.stat().st_size / 1e6:.1f} MB — total {time.time() - t0:.1f}s")
    for w in warnings:
        print("warning:", w)


if __name__ == "__main__":
    main()
