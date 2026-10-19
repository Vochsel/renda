"""Shared image stage that runs after any engine, so effects look the same everywhere:

- `apply` / `apply_file`: grade + lens post (white balance, bloom, contrast/saturation, lens distortion,
  chromatic aberration, vignette, sharpen, film grain) on a finished frame.
- `depth_of_field`: thin-lens depth of field from a depth buffer, for engines without a physical lens
  (three.js, gsplat). Gather blur with polygonal/anamorphic bokeh in a handful of radius layers.
- `accumulate`: average subframes in linear light (emulated motion blur).

Images are float arrays: display-referred sRGB 0..1 unless a name says linear. Pure numpy + Pillow.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .spec import Lens, Post


def srgb_to_linear(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0, None)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def luminance(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


# -- blurs ------------------------------------------------------------------------------------


def _box1d(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    if r <= 0:
        return a
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
    n = a.shape[axis]
    hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
    lo = np.take(c, np.arange(0, n), axis=axis)
    return ((hi - lo) / (2 * r + 1)).astype(a.dtype)


def gaussian_blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Three box passes ≈ Gaussian (O(1) per pixel regardless of sigma)."""
    if sigma < 0.3:
        return a
    r = max(1, int(round(math.sqrt(12 * sigma * sigma / 3 + 1) / 2)))  # box radius for 3 passes
    out = a
    for axis in (0, 1):
        for _ in range(3):
            out = _box1d(out, r, axis)
    return out


def _fft_convolve(img: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Same-size 2D convolution of (H,W[,C]) with a small (kh,kw) kernel, edge-padded."""
    kh, kw = kernel.shape
    py, px = kh // 2, kw // 2
    pad = [(py, py), (px, px)] + [(0, 0)] * (img.ndim - 2)
    p = np.pad(img, pad, mode="edge")
    H, W = p.shape[:2]
    fk = np.fft.rfft2(kernel, s=(H, W))
    if img.ndim == 2:
        out = np.fft.irfft2(np.fft.rfft2(p) * fk, s=(H, W))
    else:
        out = np.fft.irfft2(np.fft.rfft2(p, axes=(0, 1)) * fk[..., None], s=(H, W), axes=(0, 1))
    return out[2 * py:2 * py + img.shape[0], 2 * px:2 * px + img.shape[1]]


def bokeh_kernel(radius: float, blades: int = 0, rotation_deg: float = 0.0, ratio: float = 1.0) -> np.ndarray:
    """Normalised aperture shape: disc (blades < 3) or regular polygon; ratio > 1 stretches vertically."""
    rx, ry = radius, radius * ratio
    ex, ey = int(math.ceil(rx)) + 1, int(math.ceil(ry)) + 1
    y, x = np.mgrid[-ey:ey + 1, -ex:ex + 1].astype(np.float64)
    u, v = x / max(rx, 1e-6), y / max(ry, 1e-6)
    if blades >= 3:
        ang = np.arctan2(v, u) - math.radians(rotation_deg)
        sector = 2 * math.pi / blades
        a = np.mod(ang, sector) - sector / 2
        r_edge = math.cos(math.pi / blades) / np.cos(a)
        inside = np.hypot(u, v) - r_edge
    else:
        inside = np.hypot(u, v) - 1.0
    k = np.clip(0.5 - inside * max(rx, 1.0), 0, 1)  # ~1px anti-aliased edge
    s = k.sum()
    return k / s if s > 0 else k


def _resize(a: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Bilinear resize of (H,W[,C]) float arrays."""
    H, W = a.shape[:2]
    h, w = shape
    if (H, W) == (h, w):
        return a
    ys = (np.arange(h) + 0.5) * H / h - 0.5
    xs = (np.arange(w) + 0.5) * W / w - 0.5
    return _sample(a, *np.meshgrid(xs, ys))


def _downsample(a: np.ndarray, s: int) -> np.ndarray:
    if s <= 1:
        return a
    H, W = a.shape[:2]
    h, w = H // s, W // s
    a = a[: h * s, : w * s]
    return a.reshape(h, s, w, s, *a.shape[2:]).mean(axis=(1, 3))


def _sample(a: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Bilinear lookup with edge clamping; x, y in pixel coordinates (pixel centres at integers)."""
    H, W = a.shape[:2]
    x = np.clip(x, 0, W - 1)
    y = np.clip(y, 0, H - 1)
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    x1 = np.minimum(x0 + 1, W - 1)
    y1 = np.minimum(y0 + 1, H - 1)
    fx, fy = x - x0, y - y0
    if a.ndim == 3:
        fx, fy = fx[..., None], fy[..., None]
    top = a[y0, x0] * (1 - fx) + a[y0, x1] * fx
    bot = a[y1, x0] * (1 - fx) + a[y1, x1] * fx
    return top * (1 - fy) + bot * fy


# -- depth of field -----------------------------------------------------------------------------


def depth_of_field(rgba_lin: np.ndarray, coc: np.ndarray, blades: int = 0, rotation_deg: float = 0.0,
                   ratio: float = 1.0) -> np.ndarray:
    """Gather depth of field. rgba_lin: (H,W,4) premultiplied linear; coc: (H,W) blur radius in pixels.

    Each layer blurs only pixels at least that out of focus (so sharp pixels don't bleed into soft ones),
    then every pixel blends between the two layers bracketing its own radius. Large radii run at reduced
    resolution, which keeps a 1080p frame to roughly a second."""
    H, W = coc.shape
    cmax = float(min(np.nanmax(coc), max(H, W) * 0.04))
    if cmax < 0.6:
        return rgba_lin
    coc = np.clip(coc, 0, cmax)
    n = int(min(10, max(3, math.ceil(math.log2(cmax)) + 3)))
    radii = [0.0] + list(np.geomspace(0.75, cmax, n))
    layers = [rgba_lin]
    for r in radii[1:]:
        s = 1 if r < 4 else 2 if r < 12 else 4
        w = np.clip((coc - 0.5 * r) / max(0.5 * r, 1e-3), 0, 1)
        src = _downsample(rgba_lin * w[..., None], s)
        ws = _downsample(w, s)
        k = bokeh_kernel(r / s, blades, rotation_deg, ratio)
        num = _fft_convolve(src, k)
        den = _fft_convolve(ws, k)
        layer = num / np.maximum(den, 1e-4)[..., None]
        layer = np.where((den > 1e-3)[..., None], layer, _downsample(rgba_lin, s))
        layers.append(_resize(layer, (H, W)) if s > 1 else layer)
    idx = np.searchsorted(np.asarray(radii), coc, side="right") - 1
    idx = np.clip(idx, 0, len(radii) - 2)
    r0 = np.asarray(radii)[idx]
    r1 = np.asarray(radii)[idx + 1]
    t = np.clip((coc - r0) / np.maximum(r1 - r0, 1e-6), 0, 1)[..., None]
    stack = np.stack(layers)  # (L,H,W,4)
    yy, xx = np.mgrid[0:H, 0:W]
    a = stack[idx, yy, xx]
    b = stack[idx + 1, yy, xx]
    return a * (1 - t) + b * t


def dof_display(rgb: np.ndarray, alpha: np.ndarray, depth: np.ndarray, lens: "Lens", focal_mm: float,
                focus: float) -> tuple[np.ndarray, np.ndarray]:
    """Depth of field on a display-referred straight-alpha frame (gsplat/three.js outputs)."""
    from .lens import coc_pixels

    H, W = depth.shape
    coc = coc_pixels(depth, focal_mm, lens.fStop or 2.8, focus, lens.sensorWidthMm, W, H)
    a = np.clip(alpha, 0, 1)[..., None]
    rgba = np.concatenate([srgb_to_linear(rgb) * a, a], axis=-1)
    out = depth_of_field(rgba, coc, lens.apertureBlades, lens.apertureRotationDeg, lens.anamorphicRatio)
    oa = np.clip(out[..., 3], 0, 1)
    rgb_out = linear_to_srgb(out[..., :3] / np.maximum(oa, 1e-6)[..., None])
    return np.where(oa[..., None] > 1e-6, rgb_out, rgb), oa


# -- motion blur --------------------------------------------------------------------------------


def accumulate(frames: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """Average display-referred (rgb, alpha) subframes in linear, premultiplied light."""
    acc = None
    acc_a = None
    for rgb, a in frames:
        lin = srgb_to_linear(rgb) * a[..., None]
        acc = lin if acc is None else acc + lin
        acc_a = a.astype(np.float64) if acc_a is None else acc_a + a
    n = len(frames)
    acc, acc_a = acc / n, acc_a / n
    rgb = linear_to_srgb(acc / np.maximum(acc_a, 1e-6)[..., None])
    return np.where(acc_a[..., None] > 1e-6, rgb, 0.0), acc_a


# -- grade + lens post ---------------------------------------------------------------------------


def is_active(post: "Post | None", emulate_exposure: bool = False) -> bool:
    if post is None:
        return False
    return any([
        emulate_exposure and abs(post.exposure) > 1e-9, abs(post.contrast) > 1e-9, abs(post.saturation - 1) > 1e-9,
        abs(post.temperature) > 1e-9, abs(post.tint) > 1e-9, post.vignette > 0, post.chromaticAberration > 0,
        abs(post.lensDistortion) > 1e-9, post.bloom is not None and post.bloom.intensity > 0, post.filmGrain > 0,
        post.sharpen > 0,
    ])


def lens_magnification(post: "Post | None") -> float:
    """Worst-case factor by which the lens remap below pushes content away from the frame centre.

    Auto framing fits the subject inside 1/this of the frame so distortion and chromatic aberration
    can't push it out (pincushion magnifies the edges by up to 1/(1+k); barrel's corner-filling zoom
    magnifies everything by up to 1+k; CA samples the blue channel 1% further in at ca=1)."""
    if post is None:
        return 1.0
    k = -0.25 * post.lensDistortion
    m = 1.0 / (1.0 + k) if k < 0 else 1.0 + k
    if post.chromaticAberration:
        m /= 1.0 - 0.01 * post.chromaticAberration
    return m


def _lens_remap(lin: np.ndarray, distortion: float, ca: float) -> np.ndarray:
    """Radial distortion + lateral chromatic aberration as one per-channel remap."""
    H, W = lin.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
    cx, cy = (W - 1) / 2, (H - 1) / 2
    norm = math.hypot(cx, cy) or 1.0
    dx, dy = (xx - cx) / norm, (yy - cy) / norm
    r2 = dx * dx + dy * dy
    k = -0.25 * distortion  # <0 input = barrel: sample further out
    scale = 1 + k * r2
    if k > 0:  # barrel would pull in black from outside the frame: zoom so the corners stay filled
        scale = scale / (1 + k)
    out = np.empty_like(lin)
    for c, shift in enumerate((1 + 0.01 * ca, 1.0, 1 - 0.01 * ca)):
        s = scale * shift
        out[..., c] = _sample(lin[..., c], cx + dx * norm * s, cy + dy * norm * s)
    if lin.shape[-1] > 3:
        out[..., 3:] = _sample(lin[..., 3:], cx + dx * norm * scale, cy + dy * norm * scale)
    return out


def apply(rgb: np.ndarray, alpha: np.ndarray | None, post: "Post", seed: int = 0,
          emulate_exposure: bool = False) -> tuple[np.ndarray, np.ndarray | None]:
    """Grade + lens post on a display-referred straight-alpha frame. Returns (rgb, alpha)."""
    H, W = rgb.shape[:2]
    lin = srgb_to_linear(rgb.astype(np.float64))
    if emulate_exposure and post.exposure:
        lin = lin * 2.0 ** post.exposure
    if post.temperature or post.tint:
        t, m = post.temperature, post.tint
        lin = lin * np.array([1 + 0.12 * t + 0.04 * m, 1 - 0.08 * m, 1 - 0.12 * t + 0.04 * m])
    if post.bloom is not None and post.bloom.intensity > 0:
        disp_l = luminance(linear_to_srgb(lin))
        thr = post.bloom.threshold
        bright = lin * (np.clip((disp_l - thr) / max(1 - thr, 1e-3), 0, 1))[..., None]
        diag = math.hypot(H, W)
        glow = np.zeros_like(lin)
        for frac, w in ((0.003, 0.45), (0.01, 0.35), (0.03, 0.2)):  # tight core + wide halo
            sigma = diag * frac * (0.5 + 1.5 * post.bloom.radius)
            ds = 4 if sigma > 16 else 2 if sigma > 6 else 1
            g = gaussian_blur(_downsample(bright, ds), sigma / ds)
            glow += w * (_resize(g, (H, W)) if g.shape[:2] != (H, W) else g)
        # Add only the light that spills beyond where it came from: uniform bright areas (a white studio
        # background) stay as they are, highlights keep their value and light up their surroundings.
        lin = lin + post.bloom.intensity * np.maximum(glow - bright, 0)
    has_alpha = alpha is not None
    a = alpha if has_alpha else np.ones((H, W))
    if post.lensDistortion or post.chromaticAberration:
        stacked = _lens_remap(np.concatenate([lin * a[..., None], a[..., None]], axis=-1), post.lensDistortion,
                              post.chromaticAberration)
        a = np.clip(stacked[..., 3], 0, 1)
        lin = stacked[..., :3] / np.maximum(a, 1e-6)[..., None]
    if post.vignette > 0:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float64)
        r = np.hypot((xx - (W - 1) / 2) / ((W - 1) / 2 or 1), (yy - (H - 1) / 2) / ((H - 1) / 2 or 1)) / math.sqrt(2)
        lin = lin * (1 - post.vignette * np.clip(r, 0, 1) ** 2.2)[..., None]
    disp = linear_to_srgb(lin)
    if post.contrast:
        disp = 0.5 + (disp - 0.5) * (1 + post.contrast)
    if abs(post.saturation - 1) > 1e-9:
        lum = luminance(disp)[..., None]
        disp = lum + (disp - lum) * post.saturation
    if post.sharpen > 0:
        disp = disp + post.sharpen * 1.2 * (disp - gaussian_blur(disp, 1.0))
    if post.filmGrain > 0:
        rng = np.random.default_rng(seed)
        noise = rng.standard_normal((H, W))
        noise = gaussian_blur(noise, 0.6) * 1.6
        lum = np.clip(luminance(np.clip(disp, 0, 1)), 0, 1)
        amp = post.filmGrain * 0.09 * (0.35 + np.sqrt(lum * (1 - lum)) * 1.3)
        disp = disp + (noise * amp)[..., None]
    return np.clip(disp, 0, 1), (a if has_alpha else None)


def apply_file(path: Path, post: "Post", seed: int = 0, emulate_exposure: bool = False,
               warnings: list[str] | None = None) -> Path:
    """Run `apply` on an image file in place (PNG/JPEG/WebP; EXR when Mitsuba's Bitmap is available)."""
    from PIL import Image

    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".exr":
        try:
            import mitsuba as mi

            if not mi.variant():
                mi.set_variant("scalar_rgb")
            arr = np.array(mi.Bitmap(str(path)), copy=True).astype(np.float64)
        except ImportError:
            if warnings is not None and "post: EXR post effects need mitsuba; skipped" not in warnings:
                warnings.append("post: EXR post effects need mitsuba; skipped")
            return path
        a = arr[..., 3] if arr.shape[-1] > 3 else np.ones(arr.shape[:2])
        lin = arr[..., :3] / np.maximum(a, 1e-6)[..., None]
        peak = max(float(lin.max()), 1.0)
        rgb, a2 = apply(linear_to_srgb(lin / peak), a, post, seed, emulate_exposure)
        out = srgb_to_linear(rgb) * peak * (a2 if a2 is not None else a)[..., None]
        from .images import save_exr

        save_exr(np.dstack([out, a2 if a2 is not None else a]).astype(np.float32), path)
        return path
    img = Image.open(path)
    has_alpha = img.mode in ("RGBA", "LA")
    arr = np.asarray(img.convert("RGBA" if has_alpha else "RGB"), dtype=np.float64) / 255.0
    rgb = arr[..., :3]
    alpha = arr[..., 3] if has_alpha else None
    rgb, alpha = apply(rgb, alpha, post, seed, emulate_exposure)
    out8 = (np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)
    if alpha is not None:
        out = Image.fromarray(np.dstack([out8, (np.clip(alpha, 0, 1) * 255 + 0.5).astype(np.uint8)]), "RGBA")
    else:
        out = Image.fromarray(out8, "RGB")
    fmt = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}[ext]
    out.save(path, format=fmt, **({"quality": 92} if fmt in ("JPEG", "WEBP") else {}))
    return path
