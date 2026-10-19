"""Camera resolution: auto-framing, look-at, scene cameras, turntables."""

from __future__ import annotations

import contextlib
import contextvars
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
    # physical lens (set when the package has camera.lens)
    focal_mm: float | None = None
    focus_distance: float | None = None  # plane of focus along the view axis; None = no depth of field
    aperture_radius: float = 0.0  # scene units

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
            "focalMm": self.focal_mm,
            "focusDistance": self.focus_distance,
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
    orbit: bool = False,
) -> ResolvedCamera:
    """Place a camera so the box [lo, hi] fills the frame from the given view direction.

    Fits the 8 box corners in perspective (not the bounding sphere, which leaves long or flat objects
    tiny) and re-centres on their projection. With orbit=True (turntables) the distance is the worst
    case over a full revolution about `up` and the target stays at the box centre, so nothing clips."""
    center = (lo + hi) / 2
    radius = max(float(np.linalg.norm(hi - lo)) / 2, 1e-4)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], float) - center
    tan_y = math.tan(math.radians(fov_y_deg) / 2) / (1 + padding)
    tan_x = tan_y * aspect
    side, front = _basis(up)
    el = math.radians(elevation_deg)

    def view(az_deg: float) -> np.ndarray:
        az = math.radians(az_deg)
        horiz = math.cos(az) * front + math.sin(az) * side
        return math.cos(el) * horiz + math.sin(el) * up

    def fit(direction: np.ndarray, offset=(0.0, 0.0)) -> tuple[float, np.ndarray, np.ndarray]:
        fwd = -direction
        right = np.cross(fwd, up)
        right = right / max(np.linalg.norm(right), 1e-9) if np.linalg.norm(right) > 1e-6 else side
        cam_up = np.cross(right, fwd)
        x = corners @ right - offset[0]
        y = corners @ cam_up - offset[1]
        z = corners @ direction  # towards the camera
        dist = float(np.max(np.maximum(z + np.abs(x) / tan_x, z + np.abs(y) / tan_y)))
        return dist, right, cam_up

    direction = view(azimuth_deg)
    if orbit:
        dist = max(fit(view(azimuth_deg + a))[0] for a in range(0, 360, 15))
        target = center
    else:
        # re-centre on the perspective projection of the corners and refit; converges in a few steps
        offset = (0.0, 0.0)
        for _ in range(4):
            dist, right, cam_up = fit(direction, offset)
            target = center + right * offset[0] + cam_up * offset[1]
            rel = corners - (target - center)
            depth = dist - rel @ direction
            px, py = (rel @ right) / depth, (rel @ cam_up) / depth
            offset = (offset[0] + (px.max() + px.min()) / 2 * dist, offset[1] + (py.max() + py.min()) / 2 * dist)
    dist = max(dist, radius * 1e-3)
    pos = target + direction * dist
    return ResolvedCamera(pos, target, up.astype(float), fov_y_deg, near=max(dist - radius * 2, dist * 0.001), far=dist + radius * 4)


# Explicit per-frame cameras (e.g. the splat-bake rig) that replace the package's camera for every engine.
_OVERRIDE: contextvars.ContextVar[dict[int, ResolvedCamera] | None] = contextvars.ContextVar("renda_cameras", default=None)


@contextlib.contextmanager
def override_cameras(cams: dict[int, ResolvedCamera]):
    """Within the block, resolve_cameras returns these cameras (keyed by frame) for any engine."""
    token = _OVERRIDE.set(cams)
    try:
        yield
    finally:
        _OVERRIDE.reset(token)


def active_override(frames: list[float]) -> dict[float, ResolvedCamera] | None:
    """The override cameras for `frames`, or None outside override_cameras()."""
    override = _OVERRIDE.get()
    return None if override is None else {f: override[f] for f in frames}


def resolve_cameras(pkg: RendaPackage, stage: Stage, frames: list[float], warnings: list[str] | None = None,
                    prefer_scene: bool = False) -> dict[float, ResolvedCamera]:
    """One camera per frame, applying the package's camera mode, lens and optional turntable.

    Frames may be fractional (motion-blur subframes). Turntable motion is a property of the whole
    animation, not of the chunk being rendered, so a one-frame chunk still gets its rotated view.
    prefer_scene: use the stage's camera when it has one (see scene.native_setup).
    """
    override = _OVERRIDE.get()
    if override is not None:
        # Explicit rig cameras (splat-bake training views) are used as-is: sharp pinholes, no lens/DoF,
        # since depth of field or blur would be baked into the splat. Callers warn if a lens was set.
        return {f: override[f] for f in frames}

    from ..lens import aperture_radius, effective_fov_y, focal_from_fov_y, focus_distance, focus_point

    warnings = warnings if warnings is not None else []
    w, h = pkg.render.resolution
    aspect = w / h
    up = stage.up_vector()
    lo, hi = stage.bounds()
    cam = pkg.camera
    lens = getattr(cam, "lens", None)
    if prefer_scene and stage.cameras:
        cam = SceneCamera(mode="scene", lens=lens)
    tt = pkg.animation.turntable if pkg.animation else None
    animated = bool(tt and len(pkg.frames()) > 1)

    if isinstance(cam, LookAtCamera):
        base = ResolvedCamera(np.array(cam.position, float), np.array(cam.target, float),
                              np.array(cam.up, float) if cam.up else up.astype(float), effective_fov_y(cam.fovDeg, lens, aspect))
    elif isinstance(cam, SceneCamera):
        sc = stage.camera(cam.name)
        if sc is None:
            warnings.append(
                f"camera: no camera named {cam.name!r} in scene; using auto framing" if cam.name
                else "camera: scene has no camera; using auto framing"
            )
            base = frame_bounds(lo, hi, up, effective_fov_y(35, lens, aspect), aspect)
        else:
            base = _from_scene_camera(sc, stage)
            if lens is not None and lens.focalLengthMm:
                base.fov_y_deg = effective_fov_y(base.fov_y_deg, lens, aspect)
    else:
        assert isinstance(cam, AutoCamera)
        from ..post import lens_magnification

        fov = effective_fov_y(cam.fovDeg, lens, aspect)
        # Post lens distortion / CA push content outwards after rendering: fit against a correspondingly
        # narrower view, then render with the real one, so the subject stays inside the padding.
        mag = lens_magnification(pkg.post)
        fit_fov = math.degrees(2 * math.atan(math.tan(math.radians(fov) / 2) / mag)) if mag > 1 else fov
        base = frame_bounds(lo, hi, up, fit_fov, aspect, cam.elevationDeg, cam.azimuthDeg, cam.padding, orbit=animated)
        base.fov_y_deg = fov

    if lens is not None and base.ortho_height is None:
        base.focal_mm = lens.focalLengthMm or focal_from_fov_y(base.fov_y_deg, lens.sensorWidthMm, aspect)
        if lens.fStop is not None:
            point = focus_point(lens, stage, warnings)
            base.focus_distance = focus_distance(lens, base.position, base.target, point)
            base.aperture_radius = aperture_radius(base.focal_mm, lens.fStop)

    if not animated:
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
                                base.ortho_height, base.near, base.far, base.focal_mm, base.focus_distance,
                                base.aperture_radius)
    return out


def resolve_subframes(pkg: RendaPackage, stage: Stage, frames: list[int], warnings: list[str] | None = None,
                      prefer_scene: bool = False, emulate_motion_blur: bool = True) -> dict[int, list[ResolvedCamera]]:
    """Cameras per frame for emulated motion blur: one per shutter subframe (just the frame's own camera
    when there's no motion blur or the engine does it natively)."""
    from ..effects import subframe_offsets

    offs = subframe_offsets(pkg) if emulate_motion_blur else [0.0]
    times = [f + o for f in frames for o in offs]
    cams = resolve_cameras(pkg, stage, times, warnings, prefer_scene)
    return {f: [cams[f + o] for o in offs] for f in frames}


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
