"""Splat-bake refinement on a CUDA GPU (gsplat).

Initialised from mesh-surface Gaussians (positions are exact, so they stay frozen for the first
FREEZE_STEPS while colour/opacity/shape fit), trained against the rendered views with:
- photometric loss: 0.8·L1 + 0.2·(1−SSIM), composited on a random background each step so alpha is learned;
- alpha loss vs the views' alpha (masks are exact: the source is a mesh);
- depth + normal losses against targets rasterised from the frozen mesh-surface Gaussians — geometry
  from the source mesh, independent of which engine rendered the views;
- MCMC densification (Kheradmand et al. 2024) capped at `max_gaussians`, antialiased (Mip-Splatting-style)
  rasterisation, coarse-to-fine resolution (half-res for the first 15% of steps), a surfel term that keeps
  Gaussians flat on the surface, progressive SH degree, exponential position-LR decay, early stop on the
  held-out PSNR plateau, and pruning of near-transparent Gaussians before export.

Imports torch/gsplat lazily: only the worker's splat image has them.
"""

from __future__ import annotations

import math
import shutil
import time
from pathlib import Path

import numpy as np

from . import FREEZE_STEPS, HOLDOUT_EVERY, BakeSettings, adapt_to_scene, bake_rig, export_splats, init_gaussians, \
    output_name, sample_surface, view_image_name, write_colmap, zip_dataset
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
          scene_radius: float, log=print, more_views=None) -> tuple[Splats, dict]:
    """Refine `init` against `images` (frame → (H,W,4) uint8 straight-alpha sRGB).

    `more_views()` (streaming) returns newly landed views as {frame: image}, or None once complete."""
    import torch
    import torch.nn.functional as F
    from gsplat import rasterization
    from gsplat.strategy import MCMCStrategy

    from ..engines.splat_raster import camera_matrices

    dev = torch.device("cuda")
    torch.manual_seed(settings.seed)
    all_frames = sorted(cams)
    hold = [f for f in all_frames if f % HOLDOUT_EVERY == 0] if len(all_frames) >= 16 else []
    h, w = next(iter(images.values())).shape[:2]
    imgs: dict = {}
    mats: dict = {}

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
    freeze = min(FREEZE_STEPS, steps // 3)
    gamma = 0.01 ** (1 / max(steps - freeze, 1))
    sched = torch.optim.lr_scheduler.ExponentialLR(opts["means"], gamma=gamma)
    # relocation (teleporting dead Gaussians onto live ones) is fine while positions are frozen; only the
    # MCMC position noise is held back (lr=0 below)
    strategy = MCMCStrategy(cap_max=settings.max_gaussians, refine_start_iter=500,
                            refine_stop_iter=int(steps * 0.8), refine_every=100, min_opacity=0.005)
    strategy.check_sanity(params, opts)
    state = strategy.initialize_state()
    nrm0 = _normals_of(params["quats"], params["scales"]).detach()
    means0 = params["means"].detach().clone()
    geo: dict = {}

    def add_views(new: dict):
        """Upload views and rasterise their geometry targets from the frozen mesh-surface Gaussians."""
        with torch.no_grad():
            for f, im in new.items():
                if f in imgs:
                    continue
                imgs[f] = torch.from_numpy(im).to(dev)  # uint8 on GPU
                view, k = camera_matrices(cams[f], w, h)
                mats[f] = (torch.tensor(view, dtype=torch.float32, device=dev)[None],
                           torch.tensor(k, dtype=torch.float32, device=dev)[None],
                           torch.tensor(cams[f].position, dtype=torch.float32, device=dev))
                v, kk, pos = mats[f]
                n = torch.where(((pos - means0) * nrm0).sum(-1, keepdim=True) < 0, -nrm0, nrm0)
                out, alpha, _ = rasterization(means0, params["quats"].detach(), torch.exp(params["scales"].detach()),
                                              torch.full_like(params["opacities"], 0.99), n, v, kk, w, h,
                                              render_mode="RGB+ED", packed=False)
                geo[f] = torch.cat([out[0], alpha[0]], -1).half()  # normal xyz, depth, alpha

    add_views(images)

    def render(f, bg, sh_deg, scale=1.0, with_normals=False):
        v, kk, pos = mats[f]
        ww, hh = w, h
        if scale != 1.0:
            kk = kk.clone()
            kk[:, :2, :] *= scale
            ww, hh = int(w * scale), int(h * scale)
        colors = params["sh0"] if not k_rest else torch.cat([params["sh0"], params["shN"]], 1)
        out, alpha, info = rasterization(params["means"], params["quats"], torch.exp(params["scales"]),
                                         torch.sigmoid(params["opacities"]), colors, v, kk, ww, hh,
                                         sh_degree=sh_deg, render_mode="RGB+ED", backgrounds=bg, packed=False,
                                         rasterize_mode="antialiased")
        nimg = None
        if with_normals:
            nn = _normals_of(params["quats"], params["scales"])
            nn = torch.where(((pos - params["means"]) * nn).sum(-1, keepdim=True) < 0, -nn, nn)
            nimg, _, _ = rasterization(params["means"], params["quats"], torch.exp(params["scales"]),
                                       torch.sigmoid(params["opacities"]), nn, v, kk, ww, hh, packed=False,
                                       rasterize_mode="antialiased")
            nimg = nimg[0]
        return out[0], alpha[0], info, nimg

    def target(f, bg, scale=1.0):
        im = imgs[f].float() / 255
        if scale != 1.0:
            im = F.interpolate(im.permute(2, 0, 1)[None], scale_factor=scale, mode="area")[0].permute(1, 2, 0)
        a = im[..., 3:4]
        return im[..., :3] * a + bg * (1 - a), a

    def geo_target(f, scale=1.0):
        g = geo[f].float()
        if scale != 1.0:
            g = F.interpolate(g.permute(2, 0, 1)[None], scale_factor=scale, mode="nearest")[0].permute(1, 2, 0)
        return g

    def evaluate():
        frames_h = [f for f in hold if f in imgs]
        if not frames_h:
            return None, None
        ps, ss = [], []
        with torch.no_grad():
            bg = torch.zeros(1, 3, device=dev)
            for f in frames_h:
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
    streaming = more_views is not None
    coarse_until = int(steps * 0.15)
    for step in range(steps):
        if streaming and step % 200 == 0:
            new = more_views()
            if new is None:
                streaming = False
            elif new:
                add_views(new)
        train_f = [f for f in imgs if f not in hold] or list(imgs)
        f = train_f[rng.integers(len(train_f))]
        bg = torch.rand(1, 3, device=dev)
        sh_deg = min(max(step - freeze, 0) // 1000, deg)
        scale = 0.5 if step < coarse_until else 1.0
        normals_now = step >= freeze and step % 4 == 0
        out, alpha, info, nimg = render(f, bg, sh_deg, scale, normals_now)
        tgt, ta = target(f, bg[0], scale)
        pred = out[..., :3]
        l1 = (pred - tgt).abs().mean()
        ssim = _ssim(pred.permute(2, 0, 1)[None], tgt.permute(2, 0, 1)[None])
        loss = 0.8 * l1 + 0.2 * (1 - ssim) + 0.1 * (alpha - ta).abs().mean()
        g = geo_target(f, scale)
        mask = (ta[..., 0] > 0.5) & (g[..., 4] > 0.5)
        if mask.any():
            loss = loss + 0.05 * ((out[..., 3] - g[..., 3]).abs()[mask]).mean() / s
            if nimg is not None:
                cos = torch.nn.functional.cosine_similarity(nimg, g[..., :3], dim=-1)
                loss = loss + 0.05 * (1 - cos[mask]).mean()
        sc = torch.exp(params["scales"])
        # surfels: the thinnest axis should stay much thinner than the others (flat on the surface)
        loss = loss + 0.01 * torch.sigmoid(params["opacities"]).mean() + 0.01 * sc.mean() / s \
            + 0.01 * (sc.min(-1).values / (sc.max(-1).values + 1e-8)).mean()
        loss.backward()
        frozen = step < freeze
        for name, o in opts.items():
            if frozen and name == "means":
                o.zero_grad(set_to_none=True)
                continue
            o.step()
            o.zero_grad(set_to_none=True)
        if not frozen:
            sched.step()
        strategy.step_post_backward(params, opts, state, step, info, lr=0.0 if frozen else sched.get_last_lr()[0])
        if (step + 1) % 500 == 0 and hold and step + 1 >= freeze:
            p, _ = evaluate()
            if p is not None:
                history.append(p)
                log(f"step {step + 1}/{steps}: {len(params['means']):,} gaussians, holdout PSNR {p:.2f} dB, {time.time() - t0:.0f}s")
                if (step + 1 >= max(steps * 0.5, freeze + 1500) and len(history) >= 3 and not streaming
                        and max(history[-2:]) - history[-3] < 0.05):
                    log("PSNR plateau → early stop")
                    break
    psnr, ssim_v = evaluate()
    with torch.no_grad():
        keep = torch.sigmoid(params["opacities"]) >= 0.01  # near-transparent Gaussians add bytes, not pixels
        out = Splats(
            name="baked",
            means=params["means"][keep].detach().cpu().numpy(),
            scales=params["scales"][keep].detach().cpu().numpy(),
            rotations=torch.nn.functional.normalize(params["quats"][keep], dim=-1).detach().cpu().numpy(),
            opacities=params["opacities"][keep].detach().cpu().numpy(),
            sh_dc=params["sh0"][keep][:, 0].detach().cpu().numpy(),
            sh_rest=params["shN"][keep].detach().cpu().numpy() if k_rest else None,
            antialiased=True,
        )
        pruned = int((~keep).sum())
    metrics = {"gaussians": len(out), "pruned": pruned, "steps": step + 1, "train_seconds": round(time.time() - t0, 1),
               "holdout_views": len([f for f in hold if f in imgs]), "views": len(imgs), "psnr": psnr, "ssim": ssim_v}
    return out, metrics


def _decode(path: Path) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(path).convert("RGBA"), dtype=np.uint8)


def load_views(views_dir: Path, frames: list[int], strict: bool = True) -> dict[int, np.ndarray]:
    """Decode the views that exist (in parallel: PNG decode is the bulk of trainer start-up)."""
    from concurrent.futures import ThreadPoolExecutor

    paths = {f: Path(views_dir) / view_image_name(f) for f in frames}
    present = [f for f, p in paths.items() if p.exists()]
    with ThreadPoolExecutor(max_workers=8) as pool:
        out = dict(zip(present, pool.map(lambda f: _decode(paths[f]), present)))
    if strict:
        if len(out) < max(8, len(frames) // 2):
            raise RuntimeError(f"splat bake: only {len(out)} of {len(frames)} training views were rendered")
        if len(out) < len(frames):
            print(f"splat bake: {len(frames) - len(out)} views missing; training on {len(out)}")
    return out


COMPLETE_MARKER = ".complete"  # written next to the views once every view chunk has finished


def wait_for_views(views_dir: Path, frames: list[int], refresh=None, minimum: int = 16, timeout: float = 900.0):
    """Streaming: block until enough views have landed on the shared Volume to start training."""
    need = min(minimum, len(frames))
    t0 = time.time()
    while True:
        if refresh:
            refresh()
        have = sum((Path(views_dir) / view_image_name(f)).exists() for f in frames)
        if have >= need or (Path(views_dir) / COMPLETE_MARKER).exists():
            return
        if time.time() - t0 > timeout:
            raise RuntimeError(f"splat bake: only {have} of {len(frames)} views after {timeout:.0f}s")
        time.sleep(2.0)


def train_bake(plan, pkg, asset_paths: dict[str, Path], views_dir: Path, out_dir: Path, cache_dir: Path | None = None,
               refresh=None):
    """Second stage of a splat bake: rebuild the scene, get the views (render them here in single-container
    mode, stream them from the shared Volume when fanned out, or read them), train, export.
    Returns a RenderResult with the splat file (+ dataset zip) and metrics as warnings."""
    from ..engines.base import RenderResult
    from ..scene import build_stage

    if not cuda_available():
        raise RuntimeError("splat-bake refinement needs a CUDA GPU with gsplat; run with --cloud or use preset fast")
    t0 = time.time()
    phases: dict[str, float] = {}

    def mark(name: str, since: float) -> float:
        now = time.time()
        phases[name] = round(now - since, 1)
        return now

    import torch  # noqa: F401  (CUDA init up front so the phase timings attribute it)

    t = mark("torch", t0)
    settings = BakeSettings.from_json(plan.splat_bake)
    stage, warnings = build_stage(pkg, asset_paths, cache_dir)
    settings, notes = adapt_to_scene(settings, stage)
    rig = bake_rig(stage, settings)
    frames = sorted(rig)
    t = mark("scene", t)
    views_dir = Path(views_dir)
    if settings.single_container:
        from ..render import _splat_bake_frames

        views_dir.mkdir(parents=True, exist_ok=True)
        r = _splat_bake_frames(plan, pkg, asset_paths, views_dir, frames, cache_dir)
        warnings += [w for w in r.warnings if w not in warnings]
        t = mark("views", t)
    more = None
    if settings.stream and not settings.single_container:
        wait_for_views(views_dir, frames, refresh)
        t = mark("wait_first_views", t)
        seen: set[int] = set()

        def more():
            if refresh:
                refresh()
            complete = (views_dir / COMPLETE_MARKER).exists()
            new = load_views(views_dir, [f for f in frames if f not in seen], strict=False)
            seen.update(new)
            if not new and complete:
                return None
            return new
        images = more() or {}
        if not images:
            raise RuntimeError("splat bake: no training views arrived")
    else:
        images = load_views(views_dir, frames)
    t = mark("decode", t)
    samples = sample_surface(stage, settings.init_points, settings.seed)
    init = init_gaussians(samples, settings.sh_degree)
    lo, hi = stage.bounds()
    radius = float(np.linalg.norm(hi - lo)) / 2
    t = mark("init", t)
    splats, m = train(init, images, rig, settings, radius, more_views=more)
    if more is not None:  # pick up anything that landed after training stopped streaming (dataset only)
        images.update(load_views(views_dir, frames, strict=False))
    t = mark("train", t)
    out_dir = Path(out_dir)
    files = [export_splats(splats, out_dir / output_name(pkg, settings), stage.up_axis)]
    if settings.dataset:
        ds = out_dir / "dataset"
        (ds / "images").mkdir(parents=True, exist_ok=True)
        names = []
        for f in sorted(images):
            shutil.copy(views_dir / view_image_name(f), ds / "images" / view_image_name(f))
            names.append(view_image_name(f))
        write_colmap(ds, [rig[f] for f in sorted(images)], settings.resolution, settings.resolution, names,
                     samples.points[:100_000], samples.colors[:100_000])
        files.append(zip_dataset(ds, out_dir / f"{Path(files[0]).stem}-colmap.zip"))
        shutil.rmtree(ds, ignore_errors=True)
    mark("export", t)
    warnings += [n for n in notes if n not in warnings]
    q = f", holdout PSNR {m['psnr']:.1f} dB / SSIM {m['ssim']:.3f} on {m['holdout_views']} views" if m["psnr"] else ""
    mode = "views rendered in the trainer" if settings.single_container else "views streamed from parallel chunks"
    warnings.append(f"splat bake ({settings.preset}): {m['gaussians']:,} Gaussians (SH {settings.sh_degree}, "
                    f"{m['pruned']:,} pruned), {m['steps']} steps in {m['train_seconds']:.0f}s from {m['views']} "
                    f"{settings.engine} views ({mode}){q}")
    warnings.append("splat bake timings: " + ", ".join(f"{k} {v:.1f}s" for k, v in phases.items()))
    return RenderResult([Path(f) for f in files], warnings, time.time() - t0)
