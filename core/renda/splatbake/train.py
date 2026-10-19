"""Splat-bake refinement on a CUDA GPU (gsplat).

Initialised from mesh-surface Gaussians, trained against the rendered views with:
- photometric loss: 0.8·L1 + 0.2·(1−SSIM), composited on a random background each step so alpha is learned;
- alpha loss vs the views' alpha (masks are exact: the source is a mesh);
- depth + normal losses against targets rasterised from the frozen mesh-surface Gaussians — geometry
  from the source mesh, independent of which engine rendered the views;
- MCMC densification (Kheradmand et al. 2024) capped at `max_gaussians`, antialiased (Mip-Splatting-style)
  rasterisation, progressive SH degree, exponential position-LR decay, early stop on held-out PSNR plateau.

Imports torch/gsplat lazily: only the worker's splat image has them.
"""

from __future__ import annotations

import math
import shutil
import time
from pathlib import Path

import numpy as np

from . import HOLDOUT_EVERY, BakeSettings, bake_rig, export_splats, init_gaussians, output_name, sample_surface, \
    view_image_name, write_colmap, zip_dataset
from ..ir.splats import Splats


def cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def _ssim(a, b, window: int = 11):
    """Mean SSIM of (B,3,H,W) tensors in [0,1] with a Gaussian window."""
    import torch
    import torch.nn.functional as F

    sigma = 1.5
    g = torch.exp(-((torch.arange(window, device=a.device) - window // 2) ** 2) / (2 * sigma**2))
    g = (g / g.sum()).float()
    w = (g[:, None] @ g[None, :])[None, None].repeat(a.shape[1], 1, 1, 1)
    pad = window // 2
    mu_a = F.conv2d(a, w, padding=pad, groups=a.shape[1])
    mu_b = F.conv2d(b, w, padding=pad, groups=a.shape[1])
    s_aa = F.conv2d(a * a, w, padding=pad, groups=a.shape[1]) - mu_a**2
    s_bb = F.conv2d(b * b, w, padding=pad, groups=a.shape[1]) - mu_b**2
    s_ab = F.conv2d(a * b, w, padding=pad, groups=a.shape[1]) - mu_a * mu_b
    c1, c2 = 0.01**2, 0.03**2
    m = ((2 * mu_a * mu_b + c1) * (2 * s_ab + c2)) / ((mu_a**2 + mu_b**2 + c1) * (s_aa + s_bb + c2))
    return m.mean()


def _normals_of(quats, scales):
    """World normal of each Gaussian: the axis of its smallest scale."""
    import torch

    q = torch.nn.functional.normalize(quats, dim=-1)
    w, x, y, z = q.unbind(-1)
    r = torch.stack([
        torch.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], -1),
        torch.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], -1),
        torch.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], -1),
    ], -2)
    idx = torch.argmin(scales, dim=-1)
    return torch.gather(r, 2, idx[:, None, None].expand(-1, 3, 1)).squeeze(-1)


def train(init: Splats, images: dict[int, np.ndarray], cams: dict[int, "object"], settings: BakeSettings,
          scene_radius: float, log=print) -> tuple[Splats, dict]:
    """Refine `init` against `images` (frame → (H,W,4) uint8 straight-alpha sRGB)."""
    import torch
    from gsplat import rasterization
    from gsplat.strategy import MCMCStrategy

    from ..engines.splat_raster import camera_matrices

    dev = torch.device("cuda")
    torch.manual_seed(settings.seed)
    frames = sorted(images)
    hold = [f for f in frames if f % HOLDOUT_EVERY == 0] if len(frames) >= 16 else []
    train_f = [f for f in frames if f not in hold]
    h, w = next(iter(images.values())).shape[:2]
    imgs = {f: torch.from_numpy(images[f]).to(dev) for f in frames}  # uint8 on GPU
    mats = {}
    for f in frames:
        view, k = camera_matrices(cams[f], w, h)
        mats[f] = (torch.tensor(view, dtype=torch.float32, device=dev)[None], torch.tensor(k, dtype=torch.float32, device=dev)[None],
                   torch.tensor(cams[f].position, dtype=torch.float32, device=dev))

    t = lambda a: torch.tensor(np.ascontiguousarray(a), dtype=torch.float32, device=dev)  # noqa: E731
    deg = settings.sh_degree
    k_rest = (deg + 1) ** 2 - 1
    params = torch.nn.ParameterDict({
        "means": torch.nn.Parameter(t(init.means)),
        "scales": torch.nn.Parameter(t(init.scales)),
        "quats": torch.nn.Parameter(t(init.rotations)),
        "opacities": torch.nn.Parameter(t(init.opacities)),
        "sh0": torch.nn.Parameter(t(init.sh_dc)[:, None, :]),
    })
    if k_rest:
        params["shN"] = torch.nn.Parameter(torch.zeros((len(init), k_rest, 3), device=dev))
    s = max(scene_radius, 1e-3)
    lrs = {"means": 1.6e-4 * s, "scales": 5e-3, "quats": 1e-3, "opacities": 5e-2, "sh0": 2.5e-3, "shN": 2.5e-3 / 20}
    opts = {k: torch.optim.Adam([{"params": params[k], "lr": lrs[k], "name": k}], eps=1e-15) for k in params}
    steps = max(settings.steps, 1)
    gamma = 0.01 ** (1 / steps)
    sched = torch.optim.lr_scheduler.ExponentialLR(opts["means"], gamma=gamma)
    strategy = MCMCStrategy(cap_max=settings.max_gaussians, refine_start_iter=500,
                            refine_stop_iter=int(steps * 0.8), refine_every=100, min_opacity=0.005)
    strategy.check_sanity(params, opts)
    state = strategy.initialize_state()

    # Geometry targets from the frozen mesh-surface Gaussians (exact surface, engine independent).
    geo = {}
    with torch.no_grad():
        nrm0 = _normals_of(params["quats"], params["scales"])
        for f in frames:
            v, kk, pos = mats[f]
            n = torch.where(((pos - params["means"]) * nrm0).sum(-1, keepdim=True) < 0, -nrm0, nrm0)
            out, alpha, _ = rasterization(params["means"], params["quats"], torch.exp(params["scales"]),
                                          torch.full_like(params["opacities"], 0.99), n, v, kk, w, h,
                                          render_mode="RGB+ED", packed=False)
            geo[f] = torch.cat([out[0], alpha[0]], -1).half()  # normal xyz, depth, alpha

    def render(f, bg, sh_deg, with_normals=False):
        v, kk, pos = mats[f]
        colors = params["sh0"] if not k_rest else torch.cat([params["sh0"], params["shN"]], 1)
        out, alpha, info = rasterization(params["means"], params["quats"], torch.exp(params["scales"]),
                                         torch.sigmoid(params["opacities"]), colors, v, kk, w, h,
                                         sh_degree=sh_deg, render_mode="RGB+ED", backgrounds=bg, packed=False,
                                         rasterize_mode="antialiased")
        nimg = None
        if with_normals:
            nn = _normals_of(params["quats"], params["scales"])
            nn = torch.where(((pos - params["means"]) * nn).sum(-1, keepdim=True) < 0, -nn, nn)
            nimg, _, _ = rasterization(params["means"], params["quats"], torch.exp(params["scales"]),
                                       torch.sigmoid(params["opacities"]), nn, v, kk, w, h, packed=False,
                                       rasterize_mode="antialiased")
            nimg = nimg[0]
        return out[0], alpha[0], info, nimg

    def target(f, bg):
        im = imgs[f].float() / 255
        a = im[..., 3:4]
        return im[..., :3] * a + bg * (1 - a), a

    def evaluate():
        if not hold:
            return None, None
        ps, ss = [], []
        with torch.no_grad():
            bg = torch.zeros(1, 3, device=dev)
            for f in hold:
                out, _, _, _ = render(f, bg, deg)
                tgt, _ = target(f, bg[0])
                pred = out[..., :3].clamp(0, 1)
                mse = ((pred - tgt) ** 2).mean()
                ps.append(float(-10 * torch.log10(mse.clamp_min(1e-10))))
                ss.append(float(_ssim(pred.permute(2, 0, 1)[None], tgt.permute(2, 0, 1)[None])))
        return float(np.mean(ps)), float(np.mean(ss))

    t0 = time.time()
    history: list[float] = []
    rng = np.random.default_rng(settings.seed)
    step = 0
    for step in range(steps):
        f = train_f[rng.integers(len(train_f))]
        bg = torch.rand(1, 3, device=dev)
        sh_deg = min(step // 1000, deg)
        normals_now = step >= 1000 and step % 4 == 0
        out, alpha, info, nimg = render(f, bg, sh_deg, normals_now)
        tgt, ta = target(f, bg[0])
        pred = out[..., :3]
        l1 = (pred - tgt).abs().mean()
        ssim = _ssim(pred.permute(2, 0, 1)[None], tgt.permute(2, 0, 1)[None])
        loss = 0.8 * l1 + 0.2 * (1 - ssim) + 0.1 * (alpha - ta).abs().mean()
        g = geo[f].float()
        mask = (ta[..., 0] > 0.5) & (g[..., 4] > 0.5)
        if mask.any():
            loss = loss + 0.05 * ((out[..., 3] - g[..., 3]).abs()[mask]).mean() / s
            if nimg is not None:
                cos = torch.nn.functional.cosine_similarity(nimg, g[..., :3], dim=-1)
                loss = loss + 0.05 * (1 - cos[mask]).mean()
        loss = loss + 0.01 * torch.sigmoid(params["opacities"]).mean() + 0.01 * torch.exp(params["scales"]).mean() / s
        loss.backward()
        for o in opts.values():
            o.step()
            o.zero_grad(set_to_none=True)
        sched.step()
        strategy.step_post_backward(params, opts, state, step, info, lr=sched.get_last_lr()[0])
        if (step + 1) % 1000 == 0 and hold:
            p, _ = evaluate()
            history.append(p)
            log(f"step {step + 1}/{steps}: {len(params['means']):,} gaussians, holdout PSNR {p:.2f} dB, {time.time() - t0:.0f}s")
            if step + 1 >= steps * 0.5 and len(history) >= 3 and max(history[-2:]) - history[-3] < 0.05:
                log("PSNR plateau → early stop")
                break
    psnr, ssim_v = evaluate()
    with torch.no_grad():
        out = Splats(
            name="baked",
            means=params["means"].detach().cpu().numpy(),
            scales=params["scales"].detach().cpu().numpy(),
            rotations=torch.nn.functional.normalize(params["quats"], dim=-1).detach().cpu().numpy(),
            opacities=params["opacities"].detach().cpu().numpy(),
            sh_dc=params["sh0"][:, 0].detach().cpu().numpy(),
            sh_rest=params["shN"].detach().cpu().numpy() if k_rest else None,
            antialiased=True,
        )
    metrics = {"gaussians": len(out), "steps": step + 1, "train_seconds": round(time.time() - t0, 1),
               "holdout_views": len(hold), "psnr": psnr, "ssim": ssim_v}
    return out, metrics


def load_views(views_dir: Path, frames: list[int]) -> dict[int, np.ndarray]:
    from PIL import Image

    out = {}
    for f in frames:
        p = Path(views_dir) / view_image_name(f)
        if p.exists():
            out[f] = np.asarray(Image.open(p).convert("RGBA"), dtype=np.uint8)
    missing = len(frames) - len(out)
    if len(out) < max(8, len(frames) // 2):
        raise RuntimeError(f"splat bake: only {len(out)} of {len(frames)} training views were rendered")
    if missing:
        print(f"splat bake: {missing} views missing; training on {len(out)}")
    return out


def train_bake(plan, pkg, asset_paths: dict[str, Path], views_dir: Path, out_dir: Path, cache_dir: Path | None = None):
    """Second stage of a splat bake: rebuild the scene, train from the rendered views, export.
    Returns a RenderResult with the splat file (+ dataset zip) and metrics as warnings."""
    from ..engines.base import RenderResult
    from ..scene import build_stage

    if not cuda_available():
        raise RuntimeError("splat-bake refinement needs a CUDA GPU with gsplat; run with --cloud or use preset fast")
    t0 = time.time()
    settings = BakeSettings.from_json(plan.splat_bake)
    stage, warnings = build_stage(pkg, asset_paths, cache_dir)
    rig = bake_rig(stage, settings)
    images = load_views(views_dir, sorted(rig))
    samples = sample_surface(stage, settings.init_points, settings.seed)
    init = init_gaussians(samples, settings.sh_degree)
    lo, hi = stage.bounds()
    radius = float(np.linalg.norm(hi - lo)) / 2
    splats, m = train(init, images, rig, settings, radius)
    out_dir = Path(out_dir)
    files = [export_splats(splats, out_dir / output_name(pkg, settings), stage.up_axis)]
    if settings.dataset:
        ds = out_dir / "dataset"
        (ds / "images").mkdir(parents=True, exist_ok=True)
        names = []
        for f in sorted(images):
            shutil.copy(Path(views_dir) / view_image_name(f), ds / "images" / view_image_name(f))
            names.append(view_image_name(f))
        write_colmap(ds, [rig[f] for f in sorted(images)], settings.resolution, settings.resolution, names,
                     samples.points[:100_000], samples.colors[:100_000])
        files.append(zip_dataset(ds, out_dir / f"{Path(files[0]).stem}-colmap.zip"))
        shutil.rmtree(ds, ignore_errors=True)
    q = f", holdout PSNR {m['psnr']:.1f} dB / SSIM {m['ssim']:.3f} on {m['holdout_views']} views" if m["psnr"] else ""
    warnings.append(f"splat bake ({settings.preset}): {m['gaussians']:,} Gaussians, {m['steps']} steps in "
                    f"{m['train_seconds']:.0f}s from {len(images)} {settings.engine} views{q}")
    return RenderResult([Path(f) for f in files], warnings, time.time() - t0)
