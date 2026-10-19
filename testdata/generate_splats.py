"""Procedural Gaussian splat test scenes (license-clean, deterministic).

    uv run --project core python testdata/generate_splats.py          # → testdata/scenes/splats/
    uv run --project core python testdata/generate_splats.py --big    # + 200k-splat perf versions → testdata/out/

- garden.{ply,splat,spz}: a splat-only sculpture garden (ground disc, a knot of coloured blobs, pebbles)
  with SH degree 1 so view-dependent colour is exercised (.splat can't store it: band 0 only).
- environment.ply: a room-sized splat environment (floor, walls, plants, pillars) for the mixed scene,
  sized so the test table (testdata/scenes/scene.glb, floor at y = -1) stands in the middle of it.

Built in Y-up (three.js/RUB) coordinates. .ply and .splat are written in COLMAP/OpenCV axes (Y down)
like real captures; .spz stores RUB. All three therefore describe the same scene once Renda applies
each format's axis convention.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "core"))

from renda.ir.loaders.splat import write_ply, write_splat, write_spz  # noqa: E402
from renda.ir.splats import SH_C0, Splats, logit  # noqa: E402

FLIP = np.diag([1.0, -1.0, -1.0, 1.0])  # Y-up ↔ Y-down (180° about X)


def _quat_random(rng, n):
    q = rng.normal(size=(n, 4))
    return q / np.linalg.norm(q, axis=1, keepdims=True)


def _quat_align_y(normals):
    """Quaternions (w,x,y,z) rotating +Y onto each normal: flat splats lie on a surface."""
    y = np.array([0, 1.0, 0])
    n = normals / np.linalg.norm(normals, axis=1, keepdims=True)
    axis = np.cross(np.broadcast_to(y, n.shape), n)
    s = np.linalg.norm(axis, axis=1)
    c = n @ y
    ang = np.arctan2(s, c)
    axis = np.where(s[:, None] > 1e-9, axis / np.maximum(s, 1e-9)[:, None], [1.0, 0, 0])
    return np.concatenate([np.cos(ang / 2)[:, None], axis * np.sin(ang / 2)[:, None]], 1)


class Builder:
    def __init__(self, seed: int):
        self.rng = np.random.default_rng(seed)
        self.parts: list[dict] = []

    def add(self, means, scales, quats, rgb, alpha, sheen=0.0):
        n = len(means)
        self.parts.append(dict(means=means, scales=np.log(scales), quats=quats, rgb=np.clip(rgb, 0.02, 0.98),
                               alpha=np.broadcast_to(alpha, (n,)).astype(np.float64), sheen=np.broadcast_to(sheen, (n,))))

    def surface(self, pts, normals, rgb, size, thickness=0.15, alpha=0.95, jitter=0.25, sheen=0.0):
        n = len(pts)
        sz = size * (1 + jitter * self.rng.normal(size=(n, 1)).clip(-2, 2))
        scales = np.concatenate([sz, sz * thickness, sz], 1)
        self.add(pts, np.abs(scales) + 1e-4, _quat_align_y(normals), rgb, alpha, sheen)

    def blobs(self, pts, rgb, size, alpha=0.9, sheen=0.0):
        n = len(pts)
        sc = size * np.exp(0.3 * self.rng.normal(size=(n, 3)))
        self.add(pts, sc, _quat_random(self.rng, n), rgb, alpha, sheen)

    def build(self, name: str, sh_degree: int) -> Splats:
        cat = {k: np.concatenate([p[k] for p in self.parts]) for k in self.parts[0]}
        n = len(cat["means"])
        rest = None
        if sh_degree >= 1:
            # band 1 sheen: brighter when viewed from above/front (a stand-in for specular in captures)
            rest = np.zeros((n, 3, 3), np.float32)
            rest[:, 0, :] = -0.35 * cat["sheen"][:, None]  # -y basis → +y brightening
            rest[:, 1, :] = 0.15 * cat["sheen"][:, None]
        return Splats(name, cat["means"], cat["scales"], cat["quats"], logit(cat["alpha"]),
                      (cat["rgb"] - 0.5) / SH_C0, rest)


def garden(n_scale: float = 1.0, seed: int = 7) -> Splats:
    b = Builder(seed)
    rng = b.rng
    # ground disc with grass/soil noise
    n = int(16000 * n_scale)
    r = 2.2 * np.sqrt(rng.random(n))
    th = rng.random(n) * 2 * math.pi
    pts = np.stack([r * np.cos(th), -0.02 * rng.random(n), r * np.sin(th)], 1)
    patch = 0.5 + 0.5 * np.sin(pts[:, 0] * 3.1) * np.cos(pts[:, 2] * 2.3)
    rgb = np.stack([0.25 + 0.2 * patch, 0.45 + 0.15 * patch, 0.18 + 0.05 * patch], 1) + 0.05 * rng.normal(size=(n, 3))
    b.surface(pts, np.tile([0, 1.0, 0], (n, 1)), rgb, 0.045)
    # knot sculpture (trefoil tube) with a hue gradient and sheen
    n = int(16000 * n_scale)
    t = rng.random(n) * 2 * math.pi
    cx = np.sin(t) + 2 * np.sin(2 * t)
    cy = np.cos(t) - 2 * np.cos(2 * t)
    cz = -np.sin(3 * t)
    centre = np.stack([cx, cz, cy], 1) * 0.28 + [0, 1.05, 0]
    off = rng.normal(size=(n, 3))
    off /= np.linalg.norm(off, axis=1, keepdims=True)
    pts = centre + off * 0.11
    hue = t / (2 * math.pi)
    rgb = np.stack([0.5 + 0.45 * np.cos(2 * math.pi * hue), 0.5 + 0.45 * np.cos(2 * math.pi * (hue - 1 / 3)),
                    0.5 + 0.45 * np.cos(2 * math.pi * (hue - 2 / 3))], 1)
    b.blobs(pts, rgb, 0.022, sheen=1.0)
    # plinth
    n = int(4000 * n_scale)
    a = rng.random(n) * 2 * math.pi
    hgt = rng.random(n) * 0.45
    pts = np.stack([0.35 * np.cos(a), hgt, 0.35 * np.sin(a)], 1)
    b.surface(pts, np.stack([np.cos(a), np.zeros(n), np.sin(a)], 1), np.tile([0.82, 0.8, 0.76], (n, 1)), 0.03)
    n2 = int(1500 * n_scale)
    r = 0.35 * np.sqrt(rng.random(n2))
    a = rng.random(n2) * 2 * math.pi
    b.surface(np.stack([r * np.cos(a), np.full(n2, 0.45), r * np.sin(a)], 1), np.tile([0, 1.0, 0], (n2, 1)),
              np.tile([0.85, 0.83, 0.8], (n2, 1)), 0.03)
    # pebbles / coloured spheres around
    for i in range(7):
        n = int(500 * n_scale)
        ang = i / 7 * 2 * math.pi
        c = np.array([1.4 * math.cos(ang), 0.12, 1.4 * math.sin(ang)])
        d = rng.normal(size=(n, 3))
        d /= np.linalg.norm(d, axis=1, keepdims=True)
        col = np.array([[0.9, 0.35, 0.25], [0.95, 0.75, 0.2], [0.3, 0.55, 0.9], [0.85, 0.85, 0.88],
                        [0.6, 0.35, 0.75], [0.3, 0.75, 0.6], [0.95, 0.55, 0.65]][i])
        b.blobs(c + d * 0.12, np.tile(col, (n, 1)) + 0.03 * rng.normal(size=(n, 3)), 0.02, sheen=0.6)
    # a few faint floaters far away, like real captures have (must not ruin auto-framing)
    n = int(300 * n_scale)
    b.blobs(rng.normal(size=(n, 3)) * 12, np.full((n, 3), 0.6), 0.2, alpha=0.08)
    return b.build("garden", sh_degree=1)


def environment(n_scale: float = 1.0, seed: int = 11) -> Splats:
    """Room around the table: floor y=-1, walls at |x|,|z| = 4.5, height 3."""
    b = Builder(seed)
    rng = b.rng
    floor_y = -1.0
    n = int(26000 * n_scale)
    pts = np.stack([rng.uniform(-4.5, 4.5, n), np.full(n, floor_y) - 0.01 * rng.random(n), rng.uniform(-4.5, 4.5, n)], 1)
    plank = (np.floor((pts[:, 0] + 10) * 2.5) % 2).astype(float)
    rgb = np.stack([0.55 + 0.08 * plank, 0.42 + 0.06 * plank, 0.3 + 0.04 * plank], 1) + 0.03 * rng.normal(size=(n, 3))
    b.surface(pts, np.tile([0, 1.0, 0], (n, 1)), rgb, 0.06)
    walls = [([1, 0, 0], 4.5, 0), ([-1, 0, 0], -4.5, 0), ([0, 0, 1], 4.5, 2), ([0, 0, -1], -4.5, 2)]
    for normal, pos, axis in walls:
        n = int(9000 * n_scale)
        u = rng.uniform(-4.5, 4.5, n)
        v = rng.uniform(floor_y, floor_y + 3.2, n)
        pts = np.stack([np.full(n, pos), v, u], 1) if axis == 0 else np.stack([u, v, np.full(n, pos)], 1)
        stripe = 0.5 + 0.5 * np.sin(v * 9)
        rgb = np.stack([0.86 - 0.05 * stripe, 0.84 - 0.04 * stripe, 0.8 - 0.02 * stripe], 1)
        b.surface(pts, np.tile(-np.array(normal, float), (n, 1)), rgb, 0.07)
    for i, (x, z) in enumerate([(-3, -3), (3, -3), (-3, 3), (3, 3), (0, -3.6), (-3.6, 0)]):
        n = int(2200 * n_scale)
        d = rng.normal(size=(n, 3))
        d /= np.linalg.norm(d, axis=1, keepdims=True)
        crown = np.array([x, floor_y + 1.3, z]) + d * np.array([0.55, 0.6, 0.55]) * rng.random((n, 1)) ** 0.3
        green = np.array([0.18, 0.45 + 0.05 * i % 2, 0.2])
        b.blobs(crown, np.tile(green, (n, 1)) + 0.06 * rng.normal(size=(n, 3)), 0.04)
        m = int(600 * n_scale)
        a = rng.random(m) * 2 * math.pi
        hgt = rng.random(m) * 0.7
        pot = np.stack([x + 0.3 * np.cos(a), floor_y + hgt, z + 0.3 * np.sin(a)], 1)
        b.surface(pot, np.stack([np.cos(a), np.zeros(m), np.sin(a)], 1), np.tile([0.72, 0.38, 0.25], (m, 1)), 0.035)
    return b.build("environment", sh_degree=0)


def flipped(s: Splats) -> Splats:
    s.transform = FLIP
    out = s.baked()
    s.transform = np.eye(4)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--big", action="store_true", help="also write 200k-splat perf versions to testdata/out/")
    args = ap.parse_args()
    out = HERE / "scenes" / "splats"
    out.mkdir(parents=True, exist_ok=True)
    g = garden()
    write_ply(flipped(g), out / "garden.ply")
    write_splat(flipped(g), out / "garden.splat")
    write_spz(g, out / "garden.spz")
    e = environment()
    write_ply(flipped(e), out / "environment.ply")
    for p in sorted(out.iterdir()):
        print(f"{p.relative_to(HERE)}  {p.stat().st_size / 1e6:.2f} MB")
    print(f"garden: {len(g):,} splats (SH degree {g.sh_degree}); environment: {len(e):,} splats")
    if args.big:
        big = HERE / "out" / "splats-big"
        big.mkdir(parents=True, exist_ok=True)
        gb = garden(n_scale=200_000 / len(g), seed=8)
        write_ply(flipped(gb), big / "garden-200k.ply")
        write_spz(gb, big / "garden-200k.spz")
        print(f"big: {len(gb):,} splats → {big.relative_to(HERE)}")


if __name__ == "__main__":
    main()
