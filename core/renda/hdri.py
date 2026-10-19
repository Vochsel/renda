"""Built-in environment maps, generated procedurally so we ship no binaries.

Equirectangular, Y-up, written as Radiance RGBE (.hdr) which Blender, Mitsuba and
three.js (RGBELoader) all read.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

BUILTINS = ("studio", "sunset", "night", "white")


def cache_dir() -> Path:
    d = Path(os.environ.get("RENDA_CACHE_DIR", Path.home() / ".cache" / "renda")) / "hdri"
    d.mkdir(parents=True, exist_ok=True)
    return d


def builtin_path(name: str, width: int = 1024) -> Path:
    if name not in BUILTINS:
        raise ValueError(f"unknown built-in HDRI {name!r}; choose one of {BUILTINS}")
    p = cache_dir() / f"{name}-{width}.hdr"
    if not p.exists():
        write_hdr(p, generate(name, width))
    return p


def _dirs(width: int) -> np.ndarray:
    h = width // 2
    u = (np.arange(width) + 0.5) / width
    v = (np.arange(h) + 0.5) / h
    phi = (u - 0.5) * 2 * np.pi  # longitude
    theta = v * np.pi  # 0 = up
    ph, th = np.meshgrid(phi, theta)
    return np.stack([np.sin(th) * np.sin(ph), np.cos(th), -np.sin(th) * np.cos(ph)], axis=-1)


def _blob(d: np.ndarray, center, size: float, power: float) -> np.ndarray:
    c = np.asarray(center, dtype=np.float64)
    c /= np.linalg.norm(c)
    cos = np.clip((d * c).sum(-1), -1, 1)
    return power * np.exp(-((1 - cos) / size) ** 2)[..., None]


def generate(name: str, width: int = 1024) -> np.ndarray:
    d = _dirs(width)
    y = d[..., 1:2]
    if name == "white":
        return np.ones(d.shape, dtype=np.float32)
    if name == "studio":
        # neutral grey dome, darker floor, a big key softbox, a fill and a rim strip
        sky = 0.55 + 0.25 * np.clip(y, 0, 1)
        floor = 0.18 + 0.05 * np.clip(-y, 0, 1)
        img = np.where(y > 0, sky, floor) * np.ones(3)
        img = img + _blob(d, (-0.6, 0.6, 0.5), 0.04, 9.0) * np.array([1.0, 0.98, 0.95])
        img = img + _blob(d, (0.8, 0.3, 0.4), 0.06, 2.5) * np.array([0.95, 0.97, 1.0])
        img = img + _blob(d, (0.2, 0.5, -0.9), 0.03, 4.0)
        return img.astype(np.float32)
    if name == "sunset":
        horizon = np.exp(-np.abs(y) * 6)
        sky = np.array([0.25, 0.35, 0.7]) * np.clip(y, 0, 1) + np.array([1.4, 0.6, 0.25]) * horizon
        ground = np.array([0.08, 0.06, 0.05]) * np.ones_like(y)
        img = np.where(y > 0, sky, ground + 0.3 * horizon * np.array([1.0, 0.5, 0.2]))
        img = img + _blob(d, (0.0, 0.08, -1.0), 0.0008, 400.0) * np.array([1.0, 0.7, 0.4])
        return img.astype(np.float32)
    if name == "night":
        sky = np.array([0.01, 0.015, 0.04]) + np.array([0.02, 0.03, 0.08]) * np.exp(-np.abs(y) * 4)
        img = np.where(y > 0, sky, np.array([0.004, 0.004, 0.006])) * np.ones(3)
        img = img + _blob(d, (0.4, 0.6, -0.7), 0.0006, 60.0) * np.array([0.8, 0.85, 1.0])
        return img.astype(np.float32)
    raise ValueError(name)


def write_hdr(path: Path, img: np.ndarray) -> None:
    """Minimal flat (non-RLE) Radiance RGBE writer."""
    img = np.asarray(img, dtype=np.float64)
    h, w, _ = img.shape
    m = img.max(axis=-1)
    e = np.zeros_like(m)
    mant = np.zeros_like(m)
    nz = m > 1e-32
    mant[nz], e[nz] = np.frexp(m[nz])
    scale = np.zeros_like(m)
    scale[nz] = mant[nz] * 256.0 / m[nz]
    rgbe = np.zeros((h, w, 4), dtype=np.uint8)
    rgbe[..., :3] = np.clip(img * scale[..., None], 0, 255).astype(np.uint8)
    rgbe[..., 3] = np.where(nz, e + 128, 0).astype(np.uint8)
    with open(path, "wb") as f:
        f.write(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n")
        f.write(f"-Y {h} +X {w}\n".encode())
        f.write(rgbe.tobytes())


def read_hdr(path: Path) -> np.ndarray:
    """Reader for files written by write_hdr (flat RGBE) — used in tests."""
    data = Path(path).read_bytes()
    head_end = data.index(b"\n\n") + 2
    line_end = data.index(b"\n", head_end)
    parts = data[head_end:line_end].split()
    h, w = int(parts[1]), int(parts[3])
    rgbe = np.frombuffer(data[line_end + 1 :], dtype=np.uint8)[: h * w * 4].reshape(h, w, 4).astype(np.float64)
    f = np.where(rgbe[..., 3] > 0, np.ldexp(1.0, (rgbe[..., 3] - 136).astype(int)), 0)
    return (rgbe[..., :3] + 0.5) * f[..., None] * (rgbe[..., 3:4] > 0)
