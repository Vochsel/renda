"""Intel Open Image Denoise for engines without a built-in denoiser (Mitsuba on CPU, or on GPUs where the
OptiX denoiser's weights aren't available). Optional: `pyoidn` is in the default install, but every caller
falls back to the noisy image if it can't load."""

from __future__ import annotations

import numpy as np

_STATE: dict = {}


def available() -> bool:
    if "ok" not in _STATE:
        try:
            import pyoidn  # noqa: F401

            _STATE["ok"] = True
        except Exception:  # missing wheel, or the shared library won't load on this platform
            _STATE["ok"] = False
    return _STATE["ok"]


def denoise_rgb(rgb: np.ndarray, albedo: np.ndarray | None = None, normal: np.ndarray | None = None,
                hdr: bool = True) -> np.ndarray:
    """Denoise a linear (H, W, 3) image. Albedo/normal AOVs (same shape) sharpen edges and textures;
    pass them when the renderer produces them. Returns the input unchanged if OIDN is unavailable."""
    if not available():
        return rgb
    import pyoidn

    color = np.ascontiguousarray(np.nan_to_num(rgb[..., :3], nan=0.0, posinf=0.0, neginf=0.0), dtype=np.float32)
    color = suppress_fireflies(np.maximum(color, 0.0))
    aux = {}
    # OIDN wants albedo with normals, never normals alone
    if albedo is not None and albedo.shape[:2] == color.shape[:2]:
        aux["albedo"] = np.ascontiguousarray(np.clip(albedo[..., :3], 0.0, 1.0), dtype=np.float32)
        if normal is not None and normal.shape[:2] == color.shape[:2]:
            aux["normal"] = np.ascontiguousarray(np.clip(normal[..., :3], -1.0, 1.0), dtype=np.float32)
    try:
        return np.asarray(pyoidn.denoise(color, aux.get("albedo"), aux.get("normal"), hdr=hdr), dtype=np.float32)
    except Exception as exc:  # keep the render; callers surface last_error() as a job warning
        _STATE["error"] = f"{type(exc).__name__}: {exc}"
        return rgb


def suppress_fireflies(rgb: np.ndarray, k: float = 4.0) -> np.ndarray:
    """Clamp isolated hot pixels (low-sample caustics, e.g. light bouncing off gold onto a table) to k× the
    luminance of their 3×3 neighbourhood median. A denoiser keeps such outliers as detail; Cycles avoids
    them by disabling caustics, Mitsuba's path tracer doesn't, so tame them here."""
    lum = rgb[..., :3] @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    pad = np.pad(lum, 1, mode="edge")
    h, w = lum.shape
    stack = np.stack([pad[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)])
    limit = k * np.median(stack, axis=0) + 0.05
    scale = np.where(lum > limit, limit / np.maximum(lum, 1e-8), 1.0).astype(np.float32)
    return rgb * scale[..., None]


def last_error() -> str | None:
    """Why the most recent denoise_rgb fell back to the noisy image, if it did."""
    return _STATE.pop("error", None)
