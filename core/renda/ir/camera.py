"""Camera resolution: auto-framing, look-at, scene cameras, turntables."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..spec import AutoCamera, LookAtCamera, RendaPackage, SceneCamera
from .stage import Camera, Stage, look_at


@dataclass
class ResolvedCamera:
    """Engine-agnostic camera for one frame."""

    position: np.ndarray
    target: np.ndarray
    up: np.ndarray
    fov_y_deg: float
    ortho_height: float | None = None
    near: float = 0.01
    far: float = 10000.0

    def matrix(self) -> np.ndarray:
        return look_at(self.position, self.target, self.up)

    def to_json(self) -> dict:
        return {
            "position": [float(x) for x in self.position],
            "target": [float(x) for x in self.target],
            "up": [float(x) for x in self.up],
            "fovYDeg": float(self.fov_y_deg),
            "orthoHeight": self.ortho_height,
            "near": float(self.near),
            "far": float(self.far),
        }


def _basis(up: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Two horizontal axes perpendicular to up, forming a right-handed frame (a, b, up)."""
    if abs(up[2]) > 0.5:  # Z-up: front is -Y, like Blender
        return np.array([1.0, 0, 0]), np.array([0, -1.0, 0])
    return np.array([1.0, 0, 0]), np.array([0, 0, 1.0])  # Y-up: front is +Z, like three.js


def frame_bounds(
    lo: np.ndarray,
    hi: np.ndarray,
    up: np.ndarray,
    fov_y_deg: float,
    aspect: float,
    elevation_deg: float = 20,
    azimuth_deg: float = 35,
    padding: float = 0.1,
) -> ResolvedCamera:
    """Place a camera so the bounding sphere of [lo, hi] fills the frame."""
    center = (lo + hi) / 2
    radius = max(float(np.linalg.norm(hi - lo)) / 2, 1e-4)
    fov_y = math.radians(fov_y_deg)
    fov_x = 2 * math.atan(math.tan(fov_y / 2) * aspect)
    half = min(fov_y, fov_x) / 2
    dist = radius * (1 + padding) / math.sin(half)
    side, front = _basis(up)
    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    horiz = math.cos(az) * front + math.sin(az) * side
    direction = math.cos(el) * horiz + math.sin(el) * up
    pos = center + direction * dist
    return ResolvedCamera(pos, center, up.astype(float), fov_y_deg, near=max(dist - radius * 2, dist * 0.001), far=dist + radius * 4)


def resolve_cameras(pkg: RendaPackage, stage: Stage, frames: list[int], warnings: list[str] | None = None,
                    prefer_scene: bool = False) -> dict[int, ResolvedCamera]:
    """One camera per frame, applying the package's camera mode and optional turntable.

    prefer_scene: use the stage's camera when it has one (see scene.native_setup).
    """
    warnings = warnings if warnings is not None else []
    w, h = pkg.render.resolution
    aspect = w / h
    up = stage.up_vector()
    lo, hi = stage.bounds()
    cam = pkg.camera
    if prefer_scene and stage.cameras:
        cam = SceneCamera(mode="scene")

    if isinstance(cam, LookAtCamera):
        base = ResolvedCamera(np.array(cam.position, float), np.array(cam.target, float),
                              np.array(cam.up, float) if cam.up else up.astype(float), cam.fovDeg)
    elif isinstance(cam, SceneCamera):
        sc = stage.camera(cam.name)
        if sc is None:
            warnings.append(
                f"camera: no camera named {cam.name!r} in scene; using auto framing" if cam.name
                else "camera: scene has no camera; using auto framing"
            )
            base = frame_bounds(lo, hi, up, 35, aspect)
        else:
            base = _from_scene_camera(sc, stage)
    else:
        assert isinstance(cam, AutoCamera)
        base = frame_bounds(lo, hi, up, cam.fovDeg, aspect, cam.elevationDeg, cam.azimuthDeg, cam.padding)

    tt = pkg.animation.turntable if pkg.animation else None
    if not tt or len(frames) <= 1:
        return {f: base for f in frames}

    axis = up if tt.axis == "up" else {"X": np.array([1.0, 0, 0]), "Y": np.array([0, 1.0, 0]), "Z": np.array([0, 0, 1.0])}[tt.axis]
    a, b = pkg.animation.frames
    span = max(b - a + 1, 1)
    out = {}
    for f in frames:
        t = (f - a) / span
        ang = 2 * math.pi * tt.revolutions * t
        rel = base.position - base.target
        out[f] = ResolvedCamera(base.target + _rotate(rel, axis, ang), base.target, base.up, base.fov_y_deg,
                                base.ortho_height, base.near, base.far)
    return out


def _from_scene_camera(sc: Camera, stage: Stage) -> ResolvedCamera:
    pos = sc.position()
    fwd = sc.forward()
    lo, hi = stage.bounds()
    dist = float(np.linalg.norm((lo + hi) / 2 - pos)) or 1.0
    return ResolvedCamera(pos, pos + fwd * dist, sc.up(), sc.fov_y_deg, sc.ortho_height, sc.near, sc.far)


def _rotate(v: np.ndarray, axis: np.ndarray, angle: float) -> np.ndarray:
    """Rodrigues rotation."""
    k = axis / (np.linalg.norm(axis) or 1)
    return v * math.cos(angle) + np.cross(k, v) * math.sin(angle) + k * np.dot(k, v) * (1 - math.cos(angle))
