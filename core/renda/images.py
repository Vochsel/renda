"""Float image → file, with tonemapping, background compositing and pass encoding."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def linear_to_srgb(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


# AgX (Blender 4 / three.js default view transform), polynomial approximation by B. Wrensch.
_AGX_IN = np.array([[0.842479062253094, 0.0423282422610123, 0.0423756549057051],
                    [0.0784335999999992, 0.878468636469772, 0.0784336],
                    [0.0792237451477643, 0.0791661274605434, 0.879142973793104]])
_AGX_OUT = np.array([[1.19687900512017, -0.0528968517574562, -0.0529716355144438],
                     [-0.0980208811401368, 1.15190312990417, -0.0980434501171241],
                     [-0.0990297440797205, -0.0989611768448433, 1.15107367264116]])
_AGX_MIN_EV, _AGX_MAX_EV = -12.47393, 4.026069


def agx(rgb: np.ndarray) -> np.ndarray:
    """Linear Rec.709 → linear display values (apply the sRGB OETF afterwards)."""
    v = np.clip(rgb, 1e-10, None) @ _AGX_IN
    v = (np.clip(np.log2(v), _AGX_MIN_EV, _AGX_MAX_EV) - _AGX_MIN_EV) / (_AGX_MAX_EV - _AGX_MIN_EV)
    x2 = v * v
    x4 = x2 * x2
    v = 15.5 * x4 * x2 - 40.14 * x4 * v + 31.96 * x4 - 6.868 * x2 * v + 0.4298 * x2 + 0.1191 * v - 0.00232
    v = v @ _AGX_OUT
    return np.power(np.clip(v, 0, 1), 2.2)


def tonemap(rgb: np.ndarray, mode: str = "agx") -> np.ndarray:
    """Linear HDR → display-referred linear 0..1 (before the sRGB OETF). AgX matches Blender and three.js."""
    if mode == "none":
        return np.clip(rgb, 0, 1)
    return agx(rgb)


def composite(rgba: np.ndarray, background: tuple[float, float, float] | None) -> np.ndarray:
    """Premultiplied-alpha composite over a solid linear colour."""
    if background is None or rgba.shape[-1] < 4:
        return rgba
    a = rgba[..., 3:4]
    rgb = rgba[..., :3] + (1 - a) * np.asarray(background, dtype=np.float32)
    return np.concatenate([rgb, np.ones_like(a)], axis=-1)


def save_beauty(rgba: np.ndarray, path: Path, fmt: str, transparent: bool,
                background: tuple[float, float, float] | None = None) -> Path:
    """rgba: (H,W,3|4) linear float, premultiplied alpha. `background` (linear RGB) is
    composited in display space after tonemapping, so solid colours come out exact."""
    path = Path(path)
    if fmt == "exr":
        return save_exr(composite(rgba, background) if not transparent else rgba, path)
    rgb = rgba[..., :3]
    alpha = rgba[..., 3] if rgba.shape[-1] > 3 else np.ones(rgb.shape[:2], dtype=np.float32)
    a = np.clip(alpha, 0, 1)[..., None]
    straight = rgb / np.where(a > 1e-6, a, 1)  # un-premultiply before the (non-linear) tonemap
    disp = linear_to_srgb(tonemap(straight))
    if background is not None and not transparent:
        bg = linear_to_srgb(np.asarray(background, dtype=np.float64))
        disp = disp * a + bg * (1 - a)
    elif not transparent:
        disp = disp * a  # premultiplied over black, as rendered
    disp8 = (np.clip(disp, 0, 1) * 255 + 0.5).astype(np.uint8)
    if transparent and fmt in ("png", "webp"):
        img = Image.fromarray(np.dstack([disp8, (a[..., 0] * 255 + 0.5).astype(np.uint8)]), "RGBA")
    else:
        img = Image.fromarray(disp8, "RGB")
    kwargs = {"quality": 92} if fmt in ("jpg", "webp") else {"optimize": False, "compress_level": 6}
    img.save(path, format={"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}[fmt], **kwargs)
    return path


def save_pass(data: np.ndarray, path: Path, kind: str) -> Path:
    """Depth → 16-bit PNG normalised near..far (or EXR raw); normals → 8-bit PNG (n*0.5+0.5)."""
    path = Path(path)
    if path.suffix == ".exr":
        return save_exr(data, path)
    if kind == "depth":
        d = data[..., 0] if data.ndim == 3 else data
        valid = (d > 0) & (d < 1e9) & np.isfinite(d)  # renderers mark background as 0 or 1e10
        if valid.any():
            lo, hi = d[valid].min(), d[valid].max()
            norm = np.where(valid, (d - lo) / max(hi - lo, 1e-9), 1.0)
        else:
            norm = np.ones_like(d)
        Image.fromarray((norm * 65535).astype(np.uint16)).save(path)
    elif kind in ("normal",):
        n = np.clip(data[..., :3] * 0.5 + 0.5, 0, 1)
        Image.fromarray((n * 255 + 0.5).astype(np.uint8), "RGB").save(path)
    elif kind in ("albedo",):
        Image.fromarray((linear_to_srgb(np.clip(data[..., :3], 0, 1)) * 255 + 0.5).astype(np.uint8), "RGB").save(path)
    else:  # alpha / mask
        d = data[..., 0] if data.ndim == 3 else data
        Image.fromarray((np.clip(d, 0, 1) * 255 + 0.5).astype(np.uint8), "L").save(path)
    return path


def save_exr(data: np.ndarray, path: Path) -> Path:
    """EXR via mitsuba's Bitmap (always present when the mitsuba extra is) or OpenEXR/imageio."""
    try:
        import mitsuba as mi

        if not mi.variant():
            mi.set_variant("scalar_rgb")
        mi.Bitmap(np.ascontiguousarray(data, dtype=np.float32)).write(str(path))
        return path
    except ImportError:
        pass
    try:
        import imageio.v3 as iio

        iio.imwrite(path, data.astype(np.float32))
        return path
    except Exception as e:  # pragma: no cover
        raise RuntimeError("writing EXR needs `uv tool install 'renda[mitsuba]'` or imageio with an EXR plugin") from e
