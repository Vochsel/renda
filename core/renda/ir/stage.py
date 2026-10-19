"""Renda scene IR: a deliberately small, flat scene description.

Loaders turn any format into a Stage; engine adapters consume a Stage. Conventions:
right-handed, metres (after unit_scale), `up_axis` records whether the source is
Y-up or Z-up (engines adapt — we don't rotate geometry on load). Transforms are
4x4 row-major matrices mapping local → world, column-vector convention
(`world = M @ [x, y, z, 1]`). Cameras look down their local -Z with +Y up
(glTF/three.js convention). Colours are linear RGB(A). UVs are OpenGL-style
(origin bottom-left, v up) — trimesh and three.js already use this.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

import numpy as np

if TYPE_CHECKING:
    from .splats import Splats

Mat4 = np.ndarray


def identity() -> Mat4:
    return np.eye(4, dtype=np.float64)


@dataclass
class Material:
    name: str
    base_color: tuple[float, float, float, float] = (0.8, 0.8, 0.8, 1.0)
    metallic: float = 0.0
    roughness: float = 0.5
    emission: tuple[float, float, float] = (0.0, 0.0, 0.0)
    emission_strength: float = 0.0
    opacity: float = 1.0
    transmission: float = 0.0
    ior: float = 1.5
    double_sided: bool = True
    unlit: bool = False
    # texture paths (absolute, or relative to the stage's base dir)
    base_color_tex: str | None = None
    metallic_roughness_tex: str | None = None  # glTF packing: G=roughness, B=metallic
    roughness_tex: str | None = None
    metallic_tex: str | None = None
    normal_tex: str | None = None
    emission_tex: str | None = None
    opacity_tex: str | None = None
    # per-slot texture transform (scale_u, scale_v, offset_u, offset_v, rotation_deg) and colour space
    # ("srgb"|"linear"), keyed by spec slot name (baseColor, normal, …); set by texture overrides
    tex_uv: dict = field(default_factory=dict)
    tex_colorspace: dict = field(default_factory=dict)
    normal_strength: float = 1.0

    TEX_FIELDS = (
        "base_color_tex", "metallic_roughness_tex", "roughness_tex", "metallic_tex",
        "normal_tex", "emission_tex", "opacity_tex",
    )


@dataclass
class Mesh:
    name: str
    points: np.ndarray  # (N,3) float32
    indices: np.ndarray  # (M,3) int32 triangles
    normals: np.ndarray | None = None  # (N,3)
    uvs: np.ndarray | None = None  # (N,2)
    colors: np.ndarray | None = None  # (N,3|4) linear RGB(A) vertex colours
    transform: Mat4 = field(default_factory=identity)
    material: str | None = None

    def __post_init__(self) -> None:
        self.points = np.asarray(self.points, dtype=np.float32).reshape(-1, 3)
        self.indices = np.asarray(self.indices, dtype=np.int32).reshape(-1, 3)
        if self.normals is not None:
            self.normals = np.asarray(self.normals, dtype=np.float32).reshape(-1, 3)
        if self.uvs is not None:
            self.uvs = np.asarray(self.uvs, dtype=np.float32).reshape(-1, 2)
        self.transform = np.asarray(self.transform, dtype=np.float64).reshape(4, 4)

    def world_points(self) -> np.ndarray:
        return transform_points(self.transform, self.points)

    def ensure_normals(self) -> np.ndarray:
        if self.normals is None or len(self.normals) != len(self.points):
            self.normals = vertex_normals(self.points, self.indices)
        return self.normals


LightType = Literal["sun", "point", "spot", "area", "dome", "ambient"]


@dataclass
class Light:
    name: str
    type: LightType
    color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    # sun: irradiance-ish strength (W/m²); point/spot/area: watts; dome/ambient: multiplier
    intensity: float = 1.0
    transform: Mat4 = field(default_factory=identity)  # lights emit down local -Z
    angle_deg: float = 45.0  # spot cone
    size: tuple[float, float] = (1.0, 1.0)  # area
    radius: float = 0.05
    texture: str | None = None  # dome HDRI

    def position(self) -> np.ndarray:
        return np.asarray(self.transform)[:3, 3].copy()

    def direction(self) -> np.ndarray:
        d = -np.asarray(self.transform)[:3, 2]
        n = np.linalg.norm(d)
        return d / n if n else np.array([0, 0, -1.0])


@dataclass
class Camera:
    name: str
    transform: Mat4 = field(default_factory=identity)  # camera → world, looks down -Z, +Y up
    fov_y_deg: float = 40.0
    ortho_height: float | None = None  # set for orthographic cameras
    near: float = 0.01
    far: float = 10000.0
    aspect: float | None = None

    def position(self) -> np.ndarray:
        return np.asarray(self.transform)[:3, 3].copy()

    def forward(self) -> np.ndarray:
        return -np.asarray(self.transform)[:3, 2]

    def up(self) -> np.ndarray:
        return np.asarray(self.transform)[:3, 1]


@dataclass
class Stage:
    meshes: list[Mesh] = field(default_factory=list)
    splats: list["Splats"] = field(default_factory=list)  # 3D Gaussian splats (see ir/splats.py)
    materials: dict[str, Material] = field(default_factory=dict)
    lights: list[Light] = field(default_factory=list)
    cameras: list[Camera] = field(default_factory=list)
    up_axis: Literal["Y", "Z", "-Y"] = "Y"  # "-Y": COLMAP/OpenCV (splat captures); build_stage normalises it
    unit_scale: float = 1.0
    source_format: str | None = None
    base_dir: str | None = None  # textures with relative paths resolve against this
    frame_range: tuple[int, int] | None = None
    fps: float | None = None
    meta: dict = field(default_factory=dict)

    # -- queries -------------------------------------------------------------
    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        """World-space AABB (min, max). Empty stage → unit box at origin. Splats contribute
        their robust (outlier-free) bounds."""
        lo = np.full(3, np.inf)
        hi = np.full(3, -np.inf)
        for sp in self.splats:
            b = sp.robust_bounds()
            if b is not None:
                lo, hi = np.minimum(lo, b[0]), np.maximum(hi, b[1])
        for m in self.meshes:
            if len(m.points) == 0:
                continue
            w = m.world_points()
            lo = np.minimum(lo, w.min(axis=0))
            hi = np.maximum(hi, w.max(axis=0))
        if not np.all(np.isfinite(lo)):
            return np.array([-0.5] * 3), np.array([0.5] * 3)
        return lo, hi

    def splat_count(self) -> int:
        return int(sum(len(s) for s in self.splats))

    def triangle_count(self) -> int:
        return int(sum(len(m.indices) for m in self.meshes))

    def vertex_count(self) -> int:
        return int(sum(len(m.points) for m in self.meshes))

    def material_for(self, mesh: Mesh) -> Material:
        if mesh.material and mesh.material in self.materials:
            return self.materials[mesh.material]
        return self.materials.setdefault("__default__", Material(name="__default__"))

    def camera(self, name: str | None = None) -> Camera | None:
        if name:
            return next((c for c in self.cameras if c.name == name), None)
        return self.cameras[0] if self.cameras else None

    def up_vector(self) -> np.ndarray:
        return {"Z": np.array([0, 0, 1.0]), "-Y": np.array([0, -1.0, 0])}.get(self.up_axis, np.array([0, 1.0, 0]))

    # -- edits ---------------------------------------------------------------
    def apply_transform(self, m: Mat4) -> None:
        m = np.asarray(m, dtype=np.float64)
        for mesh in self.meshes:
            mesh.transform = m @ mesh.transform
        for sp in self.splats:
            sp.transform = m @ sp.transform
        for light in self.lights:
            light.transform = m @ light.transform
        for cam in self.cameras:
            cam.transform = m @ cam.transform

    def merge(self, other: "Stage", transform: Mat4 | None = None, prefix: str = "") -> None:
        """Add another stage's contents (e.g. a package `include`)."""
        if transform is not None:
            other.apply_transform(transform)
        rename = {}
        for name, mat in other.materials.items():
            new = name if name not in self.materials else f"{prefix}{name}"
            rename[name] = new
            mat.name = new
            self.materials[new] = mat
        for mesh in other.meshes:
            if mesh.material:
                mesh.material = rename.get(mesh.material, mesh.material)
            self.meshes.append(mesh)
        self.splats.extend(other.splats)
        self.lights.extend(other.lights)
        self.cameras.extend(other.cameras)


# -- math helpers ---------------------------------------------------------------


def transform_points(m: Mat4, pts: np.ndarray) -> np.ndarray:
    pts = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
    return pts @ m[:3, :3].T + m[:3, 3]


def vertex_normals(points: np.ndarray, indices: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    n = np.zeros_like(pts)
    if len(indices):
        tri = pts[indices]
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])  # area-weighted
        for k in range(3):
            np.add.at(n, indices[:, k], fn)
    norm = np.linalg.norm(n, axis=1, keepdims=True)
    norm[norm == 0] = 1
    return (n / norm).astype(np.float32)


def auto_smooth(points: np.ndarray, indices: np.ndarray, angle_deg: float = 30.0, uvs: np.ndarray | None = None):
    """Normals that are smooth across soft edges and split at edges sharper than angle_deg.

    Returns (points, indices, normals, uvs) — un-indexed (one vertex per corner) only if
    some edge is actually sharp, so smooth meshes keep their vertex sharing.
    """
    pts = np.asarray(points, dtype=np.float64)
    idx = np.asarray(indices, dtype=np.int64)
    if len(idx) == 0:
        return points, indices, vertex_normals(points, indices), uvs
    tri = pts[idx]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    fl = np.linalg.norm(fn, axis=1, keepdims=True)
    fu = fn / np.where(fl == 0, 1, fl)
    # corner c belongs to face c//3 and vertex idx.flat[c]; pair each corner with every
    # face sharing its vertex, keep pairs within the angle, sum area-weighted normals.
    corner_v = idx.reshape(-1)
    corner_f = np.repeat(np.arange(len(idx)), 3)
    order = np.argsort(corner_v, kind="stable")
    sv = corner_v[order]
    starts = np.searchsorted(sv, np.arange(len(pts)))
    ends = np.searchsorted(sv, np.arange(len(pts)), side="right")
    deg = ends - starts
    if (deg.astype(np.int64) ** 2).sum() > 60_000_000:  # pathological fans: fall back to smooth
        return points, indices, vertex_normals(points, indices), uvs
    c_deg = deg[corner_v]
    pair_c = np.repeat(np.arange(len(corner_v)), c_deg)
    offs = np.arange(len(pair_c)) - np.repeat(np.cumsum(c_deg) - c_deg, c_deg)
    pair_f = corner_f[order[starts[corner_v[pair_c]] + offs]]
    cos = math.cos(math.radians(angle_deg))
    keep = (fu[corner_f[pair_c]] * fu[pair_f]).sum(1) >= cos
    cn = np.zeros((len(corner_v), 3))
    np.add.at(cn, pair_c[keep], fn[pair_f[keep]])
    cn /= np.maximum(np.linalg.norm(cn, axis=1, keepdims=True), 1e-20)
    smooth = vertex_normals(pts, idx).astype(np.float64)
    if np.all((cn * smooth[corner_v]).sum(1) > 0.9999):
        return points, indices, smooth.astype(np.float32), uvs
    new_uvs = None if uvs is None else np.asarray(uvs)[corner_v]
    return pts[corner_v].astype(np.float32), np.arange(len(corner_v)).reshape(-1, 3), cn.astype(np.float32), new_uvs


def look_at(position, target, up) -> Mat4:
    """Camera-to-world matrix looking from position at target (camera looks down -Z)."""
    p = np.asarray(position, dtype=np.float64)
    t = np.asarray(target, dtype=np.float64)
    u = np.asarray(up, dtype=np.float64)
    f = t - p
    f /= np.linalg.norm(f) or 1
    if abs(np.dot(f, u / (np.linalg.norm(u) or 1))) > 0.999:  # looking straight along up
        u = np.array([0, 0, 1.0]) if abs(f[2]) < 0.9 else np.array([0, 1.0, 0])
    r = np.cross(f, u)
    r /= np.linalg.norm(r) or 1
    true_up = np.cross(r, f)
    m = identity()
    m[:3, 0] = r
    m[:3, 1] = true_up
    m[:3, 2] = -f
    m[:3, 3] = p
    return m


def trs(translate=(0, 0, 0), rotate_deg=(0, 0, 0), scale=1.0) -> Mat4:
    """Translate · Rz · Ry · Rx · Scale (XYZ euler, degrees)."""
    rx, ry, rz = (math.radians(a) for a in rotate_deg)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    s = np.array(scale if not isinstance(scale, (int, float)) else (scale,) * 3, dtype=np.float64)
    m = identity()
    m[:3, :3] = Rz @ Ry @ Rx @ np.diag(s)
    m[:3, 3] = translate
    return m


def quat_to_mat3(x: float, y: float, z: float, w: float) -> np.ndarray:
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def compose(translation=(0, 0, 0), rotation_quat=(0, 0, 0, 1), scale=(1, 1, 1)) -> Mat4:
    m = identity()
    m[:3, :3] = quat_to_mat3(*rotation_quat) @ np.diag(scale)
    m[:3, 3] = translation
    return m
