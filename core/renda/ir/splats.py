"""3D Gaussian splats in the IR.

Stored the way 3DGS trains and ships them (the .ply layout): log scales, unnormalised
quaternions (w, x, y, z), logit opacities and spherical-harmonic colour coefficients
(degree 0–3). Colours are display-referred sRGB (3DGS fits SH against sRGB photos), so
engines composite them in display space and never tonemap them.

`transform` maps the stored (file) coordinates to stage coordinates; `baked()` applies it
to positions, covariances and the view-dependent SH terms so rasterisers only ever see
world-space splats.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .stage import Mat4, identity

SH_C0 = 0.28209479177387814
SH_C1 = 0.4886025119029199
SH_C2 = (1.0925484305920792, -1.0925484305920792, 0.31539156525252005, -1.0925484305920792, 0.5462742152960396)
SH_C3 = (-0.5900435899266435, 2.890611442640554, -0.4570457994644658, 0.3731763325901154,
         -0.4570457994644658, 1.445305721320277, -0.5900435899266435)

# coefficients per degree, including the DC term
SH_COUNT = {0: 1, 1: 4, 2: 9, 3: 16}


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def sh_basis(dirs: np.ndarray, degree: int) -> np.ndarray:
    """Real SH basis in the 3DGS ordering: (N,3) unit directions → (N, (degree+1)²)."""
    x, y, z = dirs[:, 0], dirs[:, 1], dirs[:, 2]
    out = [np.full_like(x, SH_C0)]
    if degree >= 1:
        out += [-SH_C1 * y, SH_C1 * z, -SH_C1 * x]
    if degree >= 2:
        xx, yy, zz, xy, yz, xz = x * x, y * y, z * z, x * y, y * z, x * z
        out += [SH_C2[0] * xy, SH_C2[1] * yz, SH_C2[2] * (2 * zz - xx - yy), SH_C2[3] * xz, SH_C2[4] * (xx - yy)]
    if degree >= 3:
        out += [
            SH_C3[0] * y * (3 * xx - yy),
            SH_C3[1] * xy * z,
            SH_C3[2] * y * (4 * zz - xx - yy),
            SH_C3[3] * z * (2 * zz - 3 * xx - 3 * yy),
            SH_C3[4] * x * (4 * zz - xx - yy),
            SH_C3[5] * z * (xx - yy),
            SH_C3[6] * x * (xx - 3 * yy),
        ]
    return np.stack(out, axis=1)


def _fibonacci_sphere(n: int) -> np.ndarray:
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = math.pi * (1 + 5**0.5) * i
    return np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], axis=1)


def sh_rotation_matrix(rot: np.ndarray, degree: int) -> np.ndarray:
    """(K-1)x(K-1) matrix rotating the view-dependent SH coefficients (bands 1..degree) by `rot`.

    Solved numerically (rotate sample directions, least-squares refit), which is exact for
    band-limited functions and avoids hand-written Wigner-D matrices for bands 2–3.
    """
    dirs = _fibonacci_sphere(256)
    b = sh_basis(dirs, degree)[:, 1:]
    b_rot = sh_basis(dirs @ rot, degree)[:, 1:]  # f'(d) = f(R^T d)
    return np.linalg.lstsq(b, b_rot, rcond=None)[0]


def quat_wxyz_to_mat(q: np.ndarray) -> np.ndarray:
    """(N,4) quaternions (w,x,y,z), any norm → (N,3,3) rotation matrices."""
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    w, x, y, z = q[:, 0], q[:, 1], q[:, 2], q[:, 3]
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], -1),
        np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], -1),
        np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], -1),
    ], axis=1)


def mat_to_quat_wxyz(m: np.ndarray) -> np.ndarray:
    """(N,3,3) rotation matrices → (N,4) unit quaternions (w,x,y,z)."""
    m00, m11, m22 = m[:, 0, 0], m[:, 1, 1], m[:, 2, 2]
    q = np.empty((len(m), 4))
    t = m00 + m11 + m22
    # pick the numerically safest branch per element
    w = np.sqrt(np.maximum(0, 1 + t)) / 2
    x = np.sqrt(np.maximum(0, 1 + m00 - m11 - m22)) / 2
    y = np.sqrt(np.maximum(0, 1 - m00 + m11 - m22)) / 2
    z = np.sqrt(np.maximum(0, 1 - m00 - m11 + m22)) / 2
    x = np.copysign(x, m[:, 2, 1] - m[:, 1, 2])
    y = np.copysign(y, m[:, 0, 2] - m[:, 2, 0])
    z = np.copysign(z, m[:, 1, 0] - m[:, 0, 1])
    q[:, 0], q[:, 1], q[:, 2], q[:, 3] = w, x, y, z
    return q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)


@dataclass
class Splats:
    name: str
    means: np.ndarray  # (N,3) float32, file coordinates
    scales: np.ndarray  # (N,3) log scale
    rotations: np.ndarray  # (N,4) quaternion w,x,y,z (unnormalised)
    opacities: np.ndarray  # (N,) logit
    sh_dc: np.ndarray  # (N,3) SH band 0 coefficient per channel
    sh_rest: np.ndarray | None = None  # (N,K-1,3) bands 1..degree
    transform: Mat4 = field(default_factory=identity)
    antialiased: bool = False  # trained with mip/antialiased rasterisation (spz flag)

    def __post_init__(self) -> None:
        self.means = np.asarray(self.means, dtype=np.float32).reshape(-1, 3)
        n = len(self.means)
        self.scales = np.asarray(self.scales, dtype=np.float32).reshape(n, 3)
        self.rotations = np.asarray(self.rotations, dtype=np.float32).reshape(n, 4)
        self.opacities = np.asarray(self.opacities, dtype=np.float32).reshape(n)
        self.sh_dc = np.asarray(self.sh_dc, dtype=np.float32).reshape(n, 3)
        if self.sh_rest is not None:
            self.sh_rest = np.asarray(self.sh_rest, dtype=np.float32).reshape(n, -1, 3)
            if self.sh_rest.shape[1] == 0:
                self.sh_rest = None
        self.transform = np.asarray(self.transform, dtype=np.float64).reshape(4, 4)

    def __len__(self) -> int:
        return len(self.means)

    @property
    def sh_degree(self) -> int:
        k = 0 if self.sh_rest is None else self.sh_rest.shape[1]
        return {0: 0, 3: 1, 8: 2, 15: 3}.get(k, 0)

    def colors(self) -> np.ndarray:
        """View-independent display colour (band 0 only), 0..1."""
        return np.clip(self.sh_dc * SH_C0 + 0.5, 0, 1)

    def alphas(self) -> np.ndarray:
        return sigmoid(self.opacities.astype(np.float64))

    def world_means(self) -> np.ndarray:
        m = self.transform
        return self.means.astype(np.float64) @ m[:3, :3].T + m[:3, 3]

    def robust_bounds(self, lo_pct: float = 2.0, hi_pct: float = 98.0) -> tuple[np.ndarray, np.ndarray] | None:
        """World AABB of the visible core: drops near-transparent splats and the floaters most
        captures have far from the subject, so auto-framing looks at the scene, not the outliers."""
        if len(self) == 0:
            return None
        pts = self.world_means()
        vis = self.alphas() > 0.2
        if vis.sum() >= 16:
            pts = pts[vis]
        if len(pts) < 50:
            return pts.min(axis=0), pts.max(axis=0)
        return np.percentile(pts, lo_pct, axis=0), np.percentile(pts, hi_pct, axis=0)

    def baked(self) -> "Splats":
        """Apply `transform` to the data itself (positions, covariance, SH) → identity transform.

        Non-uniform scale is approximated by its polar decomposition: rotation R and the
        mean axis stretch applied to every Gaussian, which is exact for uniform scale."""
        m = self.transform
        if np.allclose(m, np.eye(4)):
            return self
        lin = m[:3, :3]
        u, _, vt = np.linalg.svd(lin)
        rot = u @ vt  # orthogonal part; may include a reflection (e.g. an axis flip)
        uniform = float(np.cbrt(abs(np.linalg.det(lin)))) or 1.0
        means = self.world_means().astype(np.float32)
        r_new = np.einsum("ij,njk->nik", rot, quat_wxyz_to_mat(self.rotations.astype(np.float64)))
        if np.linalg.det(rot) < 0:
            # A reflected frame describes the same ellipsoid once one axis is negated back.
            r_new[:, :, 2] *= -1
        quats = mat_to_quat_wxyz(r_new).astype(np.float32)
        scales = self.scales + np.float32(math.log(uniform))
        rest = self.sh_rest
        if rest is not None:
            rmat = sh_rotation_matrix(rot, self.sh_degree)
            rest = np.einsum("ij,njc->nic", rmat, rest).astype(np.float32)
        return Splats(self.name, means, scales, quats, self.opacities, self.sh_dc, rest, identity(), self.antialiased)

    def subset(self, mask: np.ndarray) -> "Splats":
        return Splats(self.name, self.means[mask], self.scales[mask], self.rotations[mask], self.opacities[mask],
                      self.sh_dc[mask], None if self.sh_rest is None else self.sh_rest[mask], self.transform,
                      self.antialiased)

    @staticmethod
    def concat(items: list["Splats"], name: str = "splats") -> "Splats":
        """Merge (already baked) splat sets; SH degree drops to the lowest common one."""
        items = [s.baked() for s in items if len(s)]
        if not items:
            return Splats(name, np.zeros((0, 3)), np.zeros((0, 3)), np.zeros((0, 4)), np.zeros(0), np.zeros((0, 3)))
        deg = min(s.sh_degree for s in items)
        k = SH_COUNT[deg] - 1
        rest = None if k == 0 else np.concatenate([s.sh_rest[:, :k] for s in items])
        return Splats(name, np.concatenate([s.means for s in items]), np.concatenate([s.scales for s in items]),
                      np.concatenate([s.rotations for s in items]), np.concatenate([s.opacities for s in items]),
                      np.concatenate([s.sh_dc for s in items]), rest)
