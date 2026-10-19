"""Physical lens maths shared by every engine: focal length ↔ field of view, focus distance, aperture and
circle of confusion. Scene units are treated as metres (after scene.unitScale)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .ir.stage import Stage
    from .spec import Lens


def fov_y_from_focal(focal_mm: float, sensor_mm: float, aspect: float) -> float:
    """Vertical FOV (deg) for a focal length; the sensor width spans the larger image side (Blender's AUTO fit)."""
    wide = 2 * math.atan(sensor_mm / (2 * focal_mm))
    if aspect >= 1:
        return math.degrees(2 * math.atan(math.tan(wide / 2) / aspect))
    return math.degrees(wide)


def focal_from_fov_y(fov_y_deg: float, sensor_mm: float, aspect: float) -> float:
    """Inverse of fov_y_from_focal: the focal length (mm) that gives this vertical FOV."""
    half_y = math.tan(math.radians(fov_y_deg) / 2)
    half_wide = half_y * aspect if aspect >= 1 else half_y
    return sensor_mm / (2 * half_wide)


def effective_fov_y(fov_y_deg: float, lens: "Lens | None", aspect: float) -> float:
    if lens is not None and lens.focalLengthMm:
        return fov_y_from_focal(lens.focalLengthMm, lens.sensorWidthMm, aspect)
    return fov_y_deg


def focus_point(lens: "Lens", stage: "Stage | None", warnings: list[str] | None = None) -> np.ndarray | None:
    """World-space focus point for focusTarget, or None for "auto" (the camera target) / unknown objects."""
    ft = lens.focusTarget
    if ft == "auto":
        return None
    if isinstance(ft, (tuple, list)):
        return np.asarray(ft, dtype=float)
    if stage is not None:
        hits = [m for m in stage.meshes if ft in (getattr(m, "name", None), getattr(m, "material", None))]
        if hits:
            pts = np.concatenate([m.world_points() for m in hits])
            return (pts.min(axis=0) + pts.max(axis=0)) / 2
    if warnings is not None:
        warnings.append(f"lens: no object named {ft!r} to focus on; focusing on the camera target")
    return None


def focus_distance(lens: "Lens", position: np.ndarray, target: np.ndarray, point: np.ndarray | None) -> float:
    """Distance along the view axis to the plane of focus (scene units)."""
    if lens.focusDistance:
        return float(lens.focusDistance)
    fwd = target - position
    n = float(np.linalg.norm(fwd)) or 1.0
    fwd = fwd / n
    if point is None:
        return n
    return max(float(np.dot(point - position, fwd)), 1e-3)


def aperture_radius(focal_mm: float, f_stop: float) -> float:
    """Entrance pupil radius in scene units (metres)."""
    return focal_mm / f_stop / 2 / 1000.0


def coc_pixels(depth: np.ndarray, focal_mm: float, f_stop: float, focus: float, sensor_mm: float,
               width_px: int, height_px: int) -> np.ndarray:
    """Circle-of-confusion *radius* in pixels for planar depths (scene units). Background (inf/0) gets the
    far-field limit."""
    f = focal_mm / 1000.0
    a = f / f_stop  # aperture diameter (m)
    s = max(float(focus), f * 1.001)
    d = np.where(np.isfinite(depth) & (depth > 0), depth, 1e12)
    c = a * f * np.abs(d - s) / (d * (s - f))  # diameter on the sensor (m)
    px_per_m = max(width_px, height_px) / (sensor_mm / 1000.0)
    return c * px_per_m / 2
