"""Ground plane / shadow catcher shared by every engine (scene.ground in the spec).

Placement is computed once from the scene bounds; engines add the plane *after* cameras are resolved so it
never affects auto-framing. Engines without a native shadow catcher use `composite_catcher`: render the
scene with a white matte plane, render the plane alone, and turn the luminance ratio into shadow alpha.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Plane side length, in scene diagonals, when ground.size is null. A catcher only needs to hold the
# shadows; a visible floor should reach (close to) the horizon.
AUTO_SIZE = {"shadowCatcher": 10.0, "solid": 40.0, "reflective": 40.0}
# The plane sits this fraction of the diagonal below the lowest point so it never z-fights the object.
EPSILON = 2e-4
NOISE_FLOOR = 0.04
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


@dataclass
class GroundPlacement:
    mode: str
    up: np.ndarray  # unit up vector (scene axes)
    height: float  # signed distance of the plane along `up`
    center: np.ndarray  # a point on the plane under the scene
    size: float  # side length
    diag: float  # scene diagonal (scale for offsets/blur)

    def corners(self) -> np.ndarray:
        """The plane's 4 corners (CCW seen from above)."""
        u, v = tangents(self.up)
        h = self.size / 2
        return np.array([self.center + s * h * u + t * h * v for s, t in ((-1, -1), (1, -1), (1, 1), (-1, 1))])

    def matrix(self) -> np.ndarray:
        """4×4 placing a 2×2 XY quad (normal +Z) as the plane: Mitsuba `rectangle`, three.js PlaneGeometry."""
        u, v = tangents(self.up)
        h = self.size / 2
        m = np.eye(4)
        m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = u * h, v * h, self.up, self.center
        return m

    def to_json(self) -> dict:
        return {"mode": self.mode, "up": self.up.tolist(), "height": self.height, "center": self.center.tolist(),
                "size": self.size, "diag": self.diag, "matrix": self.matrix().tolist()}


def tangents(up: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Right-handed (u, v) with u × v = up."""
    a = np.array([1.0, 0, 0]) if abs(up[0]) < 0.9 else np.array([0, 0, 1.0])
    u = np.cross(a, up)
    u /= np.linalg.norm(u)
    return u, np.cross(up, u)


def placement(lo: np.ndarray, hi: np.ndarray, up: np.ndarray, ground) -> GroundPlacement:
    """Where the plane goes for bounds (lo, hi): under the lowest point along `up` unless ground.offset is set."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    up = np.asarray(up, float) / np.linalg.norm(up)
    diag = float(np.linalg.norm(hi - lo)) or 1.0
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    height = float(ground.offset) if ground.offset is not None else float((corners @ up).min()) - EPSILON * diag
    c = (lo + hi) / 2
    center = c - (float(c @ up) - height) * up
    size = float(ground.size) if ground.size else AUTO_SIZE[ground.mode] * diag
    return GroundPlacement(ground.mode, up, height, center, size, diag)


def for_stage(stage, ground) -> GroundPlacement | None:
    if ground is None or not ground.enabled:
        return None
    lo, hi = stage.bounds()
    return placement(lo, hi, stage.up_vector(), ground)


def cost_factor(pkg, engine: str) -> float:
    """Frame-time multiplier for scene.ground (mirrored in web/src/lib/estimate.ts groundFactor): an emulated
    shadow catcher renders the plane alone plus an object mask; reflections cost a second view."""
    g = pkg.scene.ground
    if g is None or not g.enabled:
        return 1.0
    if g.mode == "shadowCatcher":
        return {"blender-cycles": 1.1, "threejs": 1.15, "gsplat": 1.1}.get(engine, 2.3)
    if g.mode == "reflective":
        return {"gsplat": 2.0, "threejs": 1.6}.get(engine, 1.15)
    return 1.05


def plane_material(ground) -> dict:
    """Principled parameters for a visible floor (solid/reflective) or the catcher's white matte."""
    from .spec import parse_color

    if ground.mode == "shadowCatcher":
        return {"base_color": (0.8, 0.8, 0.8), "roughness": 1.0, "metallic": 0.0, "specular": 0.0}
    col = tuple(parse_color(ground.color, (0.85, 0.85, 0.85))[:3])
    if ground.mode == "reflective":
        rough = max(0.02, min(ground.roughness, 0.35) * (1.0 - ground.reflectivity))
        # dielectric floor at the standard 4% F0 (specular 0.5) in every engine; reflectivity sharpens it
        return {"base_color": col, "roughness": rough, "metallic": 0.0, "specular": 0.5}
    return {"base_color": col, "roughness": ground.roughness, "metallic": 0.0, "specular": 0.25}


def composite_catcher(main: np.ndarray, plane: np.ndarray, obj_alpha: np.ndarray, opacity: float = 1.0,
                      backdrop: np.ndarray | None = None) -> np.ndarray:
    """Differential shadow catcher.

    main:      H×W×4, the scene rendered with a white matte plane (transparent film).
    plane:     H×W×4, the plane alone, same lighting (its alpha is the plane's coverage).
    obj_alpha: H×W, coverage of the objects alone.
    All premultiplied. Shadow = 1 − luminance(main)/luminance(plane) where only the plane is visible.
    Returns premultiplied RGBA: objects keep their colour, shadows are black with alpha = shadow × opacity.
    With `backdrop` (H×W×3, what the camera sees without plane or objects) the result is composited onto it
    and fully opaque.
    """
    main = main.astype(np.float32)
    plane = plane.astype(np.float32)
    oa = np.clip(obj_alpha.astype(np.float32), 0, 1)
    pa = np.clip(plane[..., 3], 0, 1)
    lp = plane[..., :3] @ LUMA
    lm = main[..., :3] @ LUMA
    shadow = np.clip(1.0 - lm / np.maximum(lp, 1e-4), 0.0, 1.0)
    # noise floor: two independently sampled renders never match exactly; don't let that grey the whole plane
    shadow = np.clip((shadow - NOISE_FLOOR) / (1.0 - NOISE_FLOOR), 0.0, 1.0)
    shadow = np.where(lp > 1e-4, shadow, 0.0) * pa * float(opacity)
    # main = oa·obj + (1−oa)·(lit plane): peel the plane off object edges so silhouettes don't pick up a halo
    lit_plane = plane[..., :3] * (1.0 - shadow)[..., None]
    obj_premul = np.clip(main[..., :3] - (1.0 - oa)[..., None] * lit_plane * pa[..., None], 0, None)
    obj_premul = np.where(oa[..., None] > 1e-3, obj_premul, 0.0)
    alpha = oa + (1.0 - oa) * shadow
    if backdrop is not None:
        rgb = backdrop[..., :3].astype(np.float32) * (1.0 - alpha)[..., None] + obj_premul
        return np.concatenate([rgb, np.ones_like(alpha)[..., None]], axis=-1)
    return np.concatenate([obj_premul, alpha[..., None]], axis=-1)


def scale_shadow_alpha(rgba: np.ndarray, obj_alpha: np.ndarray, opacity: float) -> np.ndarray:
    """Engines with a native catcher (Cycles): lighten only the shadow part of the alpha (premultiplied RGBA;
    shadow pixels carry no colour, so only alpha changes)."""
    if opacity >= 1.0:
        return rgba
    out = rgba.astype(np.float32).copy()
    oa = np.clip(obj_alpha.astype(np.float32), 0, 1)
    shadow = np.clip((out[..., 3] - oa) / np.maximum(1.0 - oa, 1e-6), 0, 1)
    out[..., 3] = oa + (1.0 - oa) * shadow * opacity
    return out


def coverage_shadow(coverage: np.ndarray, plane_uv: np.ndarray, on_plane: np.ndarray, blur_px: float,
                    opacity: float = 1.0) -> np.ndarray:
    """Approximate shadow for engines with no lighting (splats): a top-down coverage map of the scene,
    blurred, looked up at each pixel's ground point. coverage: G×G in [0,1]; plane_uv: H×W×2 in [0,1];
    on_plane: H×W bool. Returns H×W shadow alpha."""
    from PIL import Image, ImageFilter

    img = Image.fromarray((np.clip(coverage, 0, 1) * 255).astype(np.uint8))
    soft = np.asarray(img.filter(ImageFilter.GaussianBlur(max(blur_px, 0.5))), dtype=np.float32) / 255.0
    g = soft.shape[0]
    ij = np.clip((plane_uv * (g - 1)).round().astype(int), 0, g - 1)
    s = soft[ij[..., 1], ij[..., 0]]
    return np.where(on_plane, np.clip(s * 1.25, 0, 1) * opacity, 0.0)


# -- splats (no lighting to cast shadows with): approximate ground from a top-down coverage map -------------

COVERAGE_RES = 512


def splat_coverage(splats, gp: GroundPlacement, contact: bool = True) -> tuple[np.ndarray, float]:
    """Top-down opacity map of the splats over the ground (G×G in [0,1]) and its half-extent. Splats near the
    floor weigh more (contact shadow); a dense surface saturates to ~1."""
    p = splats.world_means() - gp.center
    u, v = tangents(gp.up)
    a, b, hgt = p @ u, p @ v, p @ gp.up
    ext = max(float(np.percentile(np.abs(np.concatenate([a, b])), 98)) * 1.6, gp.diag * 0.25)
    w = splats.alphas() * (np.exp(-np.clip(hgt, 0, None) / (0.35 * gp.diag)) if contact else 1.0)
    w = np.where(hgt > -0.02 * gp.diag, w, 0.0)  # below the floor casts nothing
    hist, _, _ = np.histogram2d(b, a, bins=COVERAGE_RES, range=[[-ext, ext], [-ext, ext]], weights=w)
    nz = hist[hist > 0]
    k = 3.0 / max(float(np.percentile(nz, 90)) if nz.size else 1.0, 1e-9)
    return 1.0 - np.exp(-hist * k), ext


def ground_hits(cam, width: int, height: int, gp: GroundPlacement, ext: float) -> tuple[np.ndarray, np.ndarray]:
    """Per pixel: (hit on the plane, coverage-map uv in [0,1]). Perspective cameras."""
    f = cam.target - cam.position
    f = f / np.linalg.norm(f)
    r = np.cross(f, cam.up)
    r /= np.linalg.norm(r)
    up = np.cross(r, f)
    t = np.tan(np.radians(cam.fov_y_deg) / 2)
    xs = ((np.arange(width) + 0.5) / width * 2 - 1) * t * width / height
    ys = (1 - (np.arange(height) + 0.5) / height * 2) * t
    d = f[None, None, :] + xs[None, :, None] * r[None, None, :] + ys[:, None, None] * up[None, None, :]
    dn = d @ gp.up
    tt = (gp.height - float(cam.position @ gp.up)) / np.where(np.abs(dn) > 1e-9, dn, 1e-9)
    pts = cam.position[None, None, :] + tt[..., None] * d - gp.center
    u, v = tangents(gp.up)
    uv = np.stack([(pts @ u) / (2 * ext) + 0.5, (pts @ v) / (2 * ext) + 0.5], axis=-1)
    half = gp.size / 2
    on = (tt > 0) & (np.abs(pts @ u) <= half) & (np.abs(pts @ v) <= half)
    return on, uv


def mirror_camera(cam, gp: GroundPlacement):
    """The camera reflected in the ground plane (render it, then flip the image horizontally)."""
    import dataclasses

    def refl(p):
        return p - 2 * (float(p @ gp.up) - gp.height) * gp.up

    up = cam.up - 2 * float(cam.up @ gp.up) * gp.up
    return dataclasses.replace(cam, position=refl(cam.position), target=refl(cam.target), up=up)


def splat_ground_background(splats, gp: GroundPlacement, ground, cam, width: int, height: int,
                            bg_rgb: np.ndarray, bg_a: np.ndarray, cov=None, rasterize=None):
    """Ground under a splat-only render, drawn into the background layer (display RGB, alpha) before the splats
    go over it: catcher shadows, a solid floor, or a floor with a mirrored splat reflection."""
    from .images import linear_to_srgb

    cov, ext = cov if cov is not None else splat_coverage(splats, gp, ground.contactShadows)
    on, uv = ground_hits(cam, width, height, gp, ext)
    inside = on & (uv[..., 0] >= 0) & (uv[..., 0] <= 1) & (uv[..., 1] >= 0) & (uv[..., 1] <= 1)
    shadow = coverage_shadow(cov, np.clip(uv, 0, 1), inside, blur_px=COVERAGE_RES * 0.01, opacity=ground.shadowOpacity)
    bg_rgb, bg_a = bg_rgb.astype(np.float64).copy(), bg_a.astype(np.float64).copy()
    if ground.mode == "shadowCatcher":
        bg_rgb *= (1 - shadow)[..., None]
        return bg_rgb, bg_a + (1 - bg_a) * shadow
    floor = linear_to_srgb(np.asarray(plane_material(ground)["base_color"], dtype=np.float64))
    floor_img = np.broadcast_to(floor, bg_rgb.shape).copy()
    if ground.mode == "reflective" and rasterize is not None:
        refl = rasterize(splats, mirror_camera(cam, gp), width, height)
        k = 0.25 + 0.6 * ground.reflectivity
        rr, ra = refl.rgb[:, ::-1], refl.alpha[:, ::-1]
        floor_img = floor_img * (1 - k * ra[..., None]) + rr * k
    floor_img *= (1 - 0.85 * shadow)[..., None]
    return np.where(on[..., None], floor_img, bg_rgb), np.where(on, 1.0, bg_a)
