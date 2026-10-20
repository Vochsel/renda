"""Bake any scene into a 3D Gaussian splat (`output.format: "splat"`).

Because the source is a known 3D scene, no structure-from-motion is needed: camera poses, intrinsics
and surface points are exact. The pipeline:

1. **Mesh → Gaussians** (`init_gaussians`): area-weighted surface samples, oriented flat along the
   surface, coloured from base-colour textures (the Mesh2Splat idea, EA SEED 2025). The `fast`
   preset returns these directly — albedo only, seconds on a CPU.
2. **Training views** (`camera_rig`): cameras auto-fitted around the bounds (Fibonacci sphere or
   hemisphere, a far ring that frames everything plus close-ups aimed at surface points), rendered
   with any mesh engine through the normal chunk fan-out, transparent background.
3. **Ground-truth COLMAP model** (`write_colmap`): exact PINHOLE intrinsics, OpenCV-convention
   extrinsics, points3D from the surface samples — usable by any splatting/NeRF tool.
4. **Refinement** (`splatbake.train`, CUDA): gsplat with MCMC densification, antialiased
   rasterisation, photometric + alpha + depth/normal (from the source mesh) losses, initialised
   from step 1 so it converges in thousands rather than tens of thousands of steps.
"""

from __future__ import annotations

import math
import struct
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ..ir.camera import ResolvedCamera
from ..ir.splats import SH_C0, SH_COUNT, Splats, logit, mat_to_quat_wxyz
from ..ir.stage import Stage
from ..spec import RendaPackage, SplatBake

# Defaults per preset. views=0 → no rendering/training (direct conversion).
# Steps are an upper bound: training stops early once held-out PSNR plateaus. Mesh-initialised
# Gaussians with frozen positions for the first FREEZE_STEPS converge in a few thousand steps.
PRESETS: dict[str, dict] = {
    "fast": {"views": 0, "resolution": 0, "steps": 0, "sh_degree": 0, "init_points": 300_000,
             "max_gaussians": 300_000, "train_hardware": "cpu"},
    "standard": {"views": 96, "resolution": 768, "steps": 6000, "sh_degree": None, "init_points": 200_000,
                 "max_gaussians": 450_000, "train_hardware": "gpu-l4", "lit_points": 400_000},
    "high": {"views": 200, "resolution": 1024, "steps": 15000, "sh_degree": None, "init_points": 400_000,
             "max_gaussians": 2_000_000, "train_hardware": "gpu-l40s", "lit_points": 800_000},
}
# init "lit" (splatbake.lit): training starts from a path-traced lit splat, so it needs fewer steps. Porsche on an
# L4, 16 held-out Cycles views: surface init 6000 steps 33.5 dB; lit init 4000 steps 34.1 dB, 3000 steps 33.4 dB.
LIT_STEPS_FACTOR = 2 / 3
LIT_FREEZE_STEPS = 300
FREEZE_STEPS = 1000  # mesh-initialised positions are exact: optimise colour/opacity/shape first
VIEW_FOV_DEG = 40.0
HOLDOUT_EVERY = 8  # every 8th view is held out for PSNR/SSIM
FAST_ENGINE = "splatbake"  # pseudo-engine: direct conversion on CPU


@dataclass
class BakeSettings:
    preset: str
    views: int
    resolution: int
    steps: int
    sh_degree: int
    init_points: int
    max_gaussians: int
    engine: str
    coverage: str
    dataset: bool
    splat_format: str
    train_hardware: str
    seed: int = 0
    # what training starts from: "surface" (albedo surface samples) or "lit" (path-traced lit splat, splatbake.lit)
    init: str = "surface"
    # explicit training cameras ([{position, target, up?, fovDeg}]) instead of the automatic rig
    cameras: list | None = None
    # engine=auto for the views: Eevee for diffuse scenes, Cycles when glossy/glass/emissive (decided once the
    # scene is loaded; the planner routes and estimates as Cycles, both run in the Blender image)
    view_engine_auto: bool = False
    fan_out: str | bool = "auto"
    # planner decision: render the views inside the training container (no chunk fan-out)
    single_container: bool = False
    # fanned out: the trainer starts as soon as the first views land on the shared Volume
    stream: bool = False

    @property
    def direct(self) -> bool:
        return self.views == 0

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "BakeSettings":
        return cls(**d)


SH_AUTO = -1  # sh_degree placeholder: 3 for glossy scenes, 1 otherwise (decided from the loaded scene)


def resolve_settings(pkg: RendaPackage) -> BakeSettings:
    sb = pkg.output.splatBake or SplatBake()
    p = PRESETS[sb.preset]
    cameras = [c.model_dump() for c in sb.cameras] if sb.cameras else None
    explicit_views = isinstance(sb.views, int) and not isinstance(sb.views, bool)
    if cameras:
        views = len(cameras)
    elif explicit_views:
        views = int(sb.views)
    else:
        views = p["views"]
    if sb.preset == "fast" and not cameras and not explicit_views:
        views = 0
    max_g = sb.maxGaussians or p["max_gaussians"]
    sh = sb.shDegree if sb.shDegree is not None else p["sh_degree"]
    if views == 0:
        sh = 0 if sh is None else sh
    lit = sb.init == "lit" and views > 0  # the lit init seeds training; preset fast has none
    steps = sb.steps if sb.steps is not None else (p["steps"] or PRESETS["standard"]["steps"])
    if lit and sb.steps is None:
        steps = int(round(steps * LIT_STEPS_FACTOR, -2))
    std = PRESETS["standard"]
    points = (p.get("lit_points") or std["lit_points"]) if lit else (p["init_points"] or std["init_points"])
    return BakeSettings(
        preset=sb.preset,
        views=views,
        resolution=sb.resolution or p["resolution"] or 768,
        steps=steps,
        sh_degree=SH_AUTO if sh is None else sh,
        init_points=min(points, max_g),
        max_gaussians=max_g,
        engine=sb.engine,
        coverage=sb.coverage,
        dataset=sb.dataset,
        splat_format=pkg.output.splatFormat or "spz",
        train_hardware=(p["train_hardware"] if p["train_hardware"] != "cpu" else "gpu-l4") if views else "cpu",
        seed=pkg.render.seed,
        init="lit" if lit else "surface",
        cameras=cameras,
        view_engine_auto=sb.engine == "auto",
        fan_out=sb.fanOut,
    )


# ---------------------------------------------------------------------------- scene analysis


def material_is_glossy(mat) -> bool:
    """Materials whose look depends on the view (reflections, refraction) or that emit light: these
    need Cycles for the views and higher-order SH in the splat."""
    if mat is None or getattr(mat, "unlit", False):
        return False
    return (mat.metallic >= 0.5 or mat.roughness < 0.35 or mat.transmission > 0.1 or mat.opacity < 0.95
            or mat.emission_strength > 0)


def glossy_fraction(stage: Stage) -> float:
    """Share of surface area covered by view-dependent materials."""
    total = glossy = 0.0
    for m in stage.meshes:
        if m.indices is None or len(m.indices) == 0:
            continue
        pts = m.world_points() if hasattr(m, "world_points") else m.points
        t = pts[np.asarray(m.indices, dtype=np.int64)]
        a = float((0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1)).sum())
        total += a
        if material_is_glossy(stage.materials.get(m.material) if m.material else None):
            glossy += a
    return glossy / total if total else 0.0


GLOSSY_SCENE = 0.03  # ≥ 3% of the surface glossy/glass/emissive → treat the scene as view-dependent


def adapt_to_scene(settings: BakeSettings, stage: Stage) -> tuple[BakeSettings, list[str]]:
    """Resolve the scene-dependent choices (view engine, SH degree). Deterministic, so the view chunks
    and the trainer (separate containers) agree."""
    from dataclasses import replace

    notes: list[str] = []
    gf = glossy_fraction(stage)
    glossy = gf >= GLOSSY_SCENE
    if settings.sh_degree == SH_AUTO:
        # degree 3 has 16 coefficients per colour: worth it for hero (high) bakes of shiny scenes; degree 2
        # captures most highlights at 60% of the size and converges within the standard step budget
        settings = replace(settings, sh_degree=(3 if settings.preset == "high" else 2) if glossy else 1)
    if settings.view_engine_auto and settings.engine in ("blender-cycles", "blender-eevee"):
        engine = "blender-cycles" if glossy else "blender-eevee"
        if engine != settings.engine:
            settings = replace(settings, engine=engine)
        notes.append(f"splat bake: views rendered with {engine} ({gf:.0%} of the surface is glossy/glass/emissive; "
                     f"{'path tracing for reflections' if glossy else 'diffuse scene, rasterised for speed'})")
    return settings, notes


def view_package(pkg: RendaPackage, settings: BakeSettings) -> RendaPackage:
    """The package each training view renders with: same scene + lighting, square transparent PNG."""
    data = pkg.model_dump(mode="json", by_alias=True, exclude_unset=True)
    data["render"] = {**data.get("render", {}), "resolution": [settings.resolution, settings.resolution]}
    if settings.engine != "auto":
        data["render"]["engine"] = settings.engine
    # Training averages 90+ views and the splat smooths sensor noise anyway: denoised low-spp views
    # (draft) train as well as clean ones for a fraction of the render time.
    if "samples" not in data["render"] and data["render"].get("quality") != "final":
        data["render"]["quality"] = "draft"
        data["render"]["denoise"] = True
    data["output"] = {"format": "png", "transparent": True, "passes": ["beauty"]}
    data.pop("animation", None)
    data["render"].pop("motionBlur", None)
    data["camera"] = {"mode": "auto"}  # replaced per frame by the rig (see ir.camera.override_cameras)
    post = data.get("post")
    if post:  # colour grading is consistent across views and bakes in fine; screen-space lens effects don't
        kept = {k: v for k, v in post.items() if k in _VIEW_POST_KEEP}
        if kept:
            data["post"] = kept
        else:
            data.pop("post")
    return RendaPackage.model_validate(data)


# Post settings that are identical for every view (a colour grade), so baking them into the splat is fine.
_VIEW_POST_KEEP = frozenset({"exposure", "tonemap", "contrast", "saturation", "temperature", "tint"})


def ignored_view_settings(pkg: RendaPackage) -> list[str]:
    """Package settings the training views drop: lens/DoF, motion blur and screen-space post effects would
    be baked into the splat as view-inconsistent noise."""
    out: list[str] = []
    if getattr(pkg.camera, "lens", None) is not None:
        out.append("camera.lens")
    if pkg.render.motionBlur is not None:
        out.append("render.motionBlur")
    if pkg.post is not None:
        dumped = pkg.post.model_dump(exclude_unset=True)
        out += [f"post.{k}" for k in dumped if k not in _VIEW_POST_KEEP]
    return out


# --------------------------------------------------------------------------------- camera rig


def _fibonacci_dirs(n: int, up: np.ndarray, min_elev_deg: float, max_elev_deg: float = 90.0) -> np.ndarray:
    """n directions spread evenly over the band of elevations [min, max] around `up`."""
    if n <= 0:
        return np.zeros((0, 3))
    zmin, zmax = math.sin(math.radians(min_elev_deg)), math.sin(math.radians(max_elev_deg))
    i = np.arange(n) + 0.5
    z = zmin + (zmax - zmin) * i / n
    r = np.sqrt(np.clip(1 - z * z, 0, 1))
    theta = math.pi * (1 + 5**0.5) * i
    local = np.stack([r * np.cos(theta), r * np.sin(theta), z], axis=1)
    a = np.array([1.0, 0, 0]) if abs(up[0]) < 0.9 else np.array([0, 1.0, 0])
    a = a - up * (a @ up)
    a /= np.linalg.norm(a)
    b = np.cross(up, a)
    return local[:, :1] * a + local[:, 1:2] * b + local[:, 2:3] * up


def _explicit_cameras(stage: Stage, settings: BakeSettings) -> list[ResolvedCamera]:
    up_default = stage.up_vector().astype(float)
    lo, hi = stage.bounds()
    span = max(float(np.linalg.norm(hi - lo)), 1e-3)
    cams = []
    for c in settings.cameras or []:
        pos = np.asarray(c["position"], float)
        tgt = np.asarray(c["target"], float)
        up = np.asarray(c.get("up") or up_default, float)
        d = float(np.linalg.norm(tgt - pos)) or span
        cams.append(ResolvedCamera(pos, tgt, up, float(c.get("fovDeg") or VIEW_FOV_DEG), near=max(d * 0.005, 1e-4),
                                   far=d + span * 3))
    return cams


def _coverage_targets(samples: "SurfaceSamples", n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Close-up targets placed by coverage and importance: farthest-point sampling over surface samples
    (position + normal), weighted towards detailed (normals vary within a voxel) and glossy regions."""
    pts, nrm = samples.points.astype(float), samples.normals.astype(float)
    lo, hi = pts.min(0), pts.max(0)
    span = max(float(np.linalg.norm(hi - lo)), 1e-6)
    cell = span / 24
    keys = np.floor((pts - lo) / cell).astype(np.int64)
    _, inv = np.unique(keys, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    sums = np.zeros((inv.max() + 1, 3))
    np.add.at(sums, inv, nrm)
    counts = np.bincount(inv).astype(float)[:, None]
    detail = 1.0 - np.linalg.norm(sums / counts, axis=1)[inv]  # 0 flat … 1 normals cancel
    weight = 1.0 + 3.0 * detail + 2.0 * samples.glossy.astype(float)
    feat = np.concatenate([(pts - lo) / span, nrm * 0.35], axis=1)
    rng = np.random.default_rng(seed)
    chosen = [int(rng.choice(len(pts), p=weight / weight.sum()))]
    dist = np.full(len(pts), np.inf)
    for _ in range(1, min(n, len(pts))):
        dist = np.minimum(dist, np.linalg.norm(feat - feat[chosen[-1]], axis=1))
        chosen.append(int(np.argmax(dist * weight)))
    idx = np.array(chosen)
    return pts[idx], nrm[idx]


def camera_rig(stage: Stage, settings: BakeSettings, samples: "SurfaceSamples | None" = None) -> list[ResolvedCamera]:
    """Training cameras.

    - explicit `cameras` are used as given;
    - orbit: one ring at 20° elevation framing the bounds;
    - sphere/hemisphere/auto: a far Fibonacci shell framing everything (~55%) plus close-ups (~45%)
      placed by surface coverage (see `_coverage_targets`), looking at the surface along its normal so
      flat faces, crevices and glossy parts each get a dedicated view. auto spans −30°..90° elevation
      (objects usually rest on something), hemisphere 5°..85°, sphere everything."""
    if settings.cameras:
        return _explicit_cameras(stage, settings)
    n = settings.views
    up = stage.up_vector().astype(float)
    up /= np.linalg.norm(up) or 1
    lo, hi = stage.bounds()
    center = (lo + hi) / 2
    radius = max(float(np.linalg.norm(hi - lo)) / 2, 1e-3)
    fov = VIEW_FOV_DEG
    dist = radius / math.sin(math.radians(fov) / 2) * 1.05
    near_plane = max(dist - radius * 1.5, dist * 0.01)
    if settings.coverage == "orbit":
        return [ResolvedCamera(center + d * dist, center.copy(), up.copy(), fov, near=near_plane, far=dist + radius * 3)
                for d in _fibonacci_dirs(n, up, 20.0, 20.0 + 1e-6)]
    min_elev, max_elev = {"sphere": (-90.0, 90.0), "hemisphere": (5.0, 85.0), "auto": (-30.0, 90.0)}[settings.coverage]
    have_samples = samples is not None and len(samples.points) > 0
    n_far = n if not have_samples else max(int(round(n * 0.55)), min(n, 8))
    n_near = n - n_far
    cams: list[ResolvedCamera] = []
    for d in _fibonacci_dirs(n_far, up, min_elev, max_elev):
        cams.append(ResolvedCamera(center + d * dist, center.copy(), up.copy(), fov, near=near_plane, far=dist + radius * 3))
    if n_near:
        targets, normals = _coverage_targets(samples, n_near, settings.seed + 7)
        sin_min = math.sin(math.radians(min_elev))
        for t, nn in zip(targets, normals):
            out = t - center
            out = out / (np.linalg.norm(out) or 1)
            d = nn * 0.75 + out * 0.25  # mostly along the surface normal, biased outwards
            d = d / (np.linalg.norm(d) or 1)
            if float(d @ up) < sin_min:  # respect the coverage band (e.g. no views from under the floor)
                d = d - up * (float(d @ up) - sin_min)
                d = d / (np.linalg.norm(d) or 1)
            pos = t + d * dist * 0.45
            view_up = up if abs(float(d @ up)) < 0.98 else np.cross(d, np.array([1.0, 0, 0]))
            cams.append(ResolvedCamera(pos, t.astype(float), view_up.astype(float), fov, near=dist * 0.005,
                                       far=dist * 0.45 + radius * 3))
    # shared clip planes: some engines build one sensor per chunk from its first camera
    near, far = min(c.near for c in cams), max(c.far for c in cams)
    for c in cams:
        c.near, c.far = near, far
    return cams


def bake_rig(stage: Stage, settings: BakeSettings) -> dict[int, ResolvedCamera]:
    """The rig keyed by frame number (1..views). Deterministic for a given stage + settings, so view
    chunks and the trainer (separate containers) agree on every pose without passing them around."""
    samples = None if settings.cameras else sample_surface(stage, 6000, settings.seed + 1)
    return {i + 1: c for i, c in enumerate(camera_rig(stage, settings, samples))}


def view_image_name(frame: int) -> str:
    return f"frame_{frame:04d}.png"


# --------------------------------------------------------------------------------- mesh → gaussians


def _load_texture(stage: Stage, path: str | None):
    if not path:
        return None
    from PIL import Image

    p = Path(path)
    if not p.is_absolute() and stage.base_dir:
        p = Path(stage.base_dir) / p
    try:
        return np.asarray(Image.open(p).convert("RGBA"), dtype=np.float32) / 255.0
    except Exception:
        return None


def _sample_texture(tex: np.ndarray, uv: np.ndarray) -> np.ndarray:
    h, w = tex.shape[:2]
    u = np.mod(uv[:, 0], 1.0)
    v = np.mod(uv[:, 1], 1.0)
    x = np.clip((u * w).astype(int), 0, w - 1)
    y = np.clip(((1 - v) * h).astype(int), 0, h - 1)  # glTF/IR UVs: origin bottom-left
    return tex[y, x]


@dataclass
class SurfaceSamples:
    points: np.ndarray  # (N,3) world
    normals: np.ndarray  # (N,3) unit
    colors: np.ndarray  # (N,3) display sRGB 0..1
    radius: np.ndarray  # (N,) tangent radius covering the sample's share of area
    glossy: np.ndarray | None = None  # (N,) bool: sample lies on a view-dependent material
    alpha: np.ndarray | None = None  # (N,) opacity: material factor × base-colour texture alpha


def sample_surface(stage: Stage, n: int, seed: int = 0) -> SurfaceSamples:
    """Area-weighted random samples on every mesh (world space), with normals and base colours."""
    tris_p, tris_n, tris_uv, tris_c, mats = [], [], [], [], []
    for m in stage.meshes:
        if m.indices is None or len(m.indices) == 0:
            continue
        pts = m.world_points() if hasattr(m, "world_points") else m.points
        idx = np.asarray(m.indices, dtype=np.int64)
        tris_p.append(pts[idx])
        if m.normals is not None and len(m.normals) == len(m.points):
            rot = np.asarray(m.transform)[:3, :3]
            nrm = m.normals @ np.linalg.inv(rot)  # row-vector form of the normal matrix (M⁻¹)ᵀ
            tris_n.append(nrm[idx])
        else:
            tris_n.append(None)
        tris_uv.append(m.uvs[idx] if m.uvs is not None and len(m.uvs) == len(m.points) else None)
        tris_c.append(m.colors[idx, :, :3] if m.colors is not None and len(m.colors) == len(m.points) else None)
        mats.append(stage.materials.get(m.material) if m.material else None)
    if not tris_p:
        raise ValueError("splat bake needs at least one mesh in the scene")

    areas = [0.5 * np.linalg.norm(np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]), axis=1) for t in tris_p]
    total = float(sum(a.sum() for a in areas)) or 1.0
    rng = np.random.default_rng(seed)
    out_p, out_n, out_c, out_g, out_a = [], [], [], [], []
    for t, tn, tuv, tc, mat, a in zip(tris_p, tris_n, tris_uv, tris_c, mats, areas):
        k = int(round(n * a.sum() / total))
        if k == 0 or a.sum() == 0:
            continue
        face = rng.choice(len(t), size=k, p=a / a.sum())
        r1, r2 = rng.random(k), rng.random(k)
        s1 = np.sqrt(r1)
        bary = np.stack([1 - s1, s1 * (1 - r2), s1 * r2], axis=1)  # uniform on the triangle
        tri = t[face]
        p = np.einsum("nk,nkd->nd", bary, tri)
        geo_n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        geo_n /= np.maximum(np.linalg.norm(geo_n, axis=1, keepdims=True), 1e-12)
        if tn is not None:
            nn = np.einsum("nk,nkd->nd", bary, tn[face])
            nn /= np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-12)
            nn = np.where(np.isfinite(nn).all(axis=1, keepdims=True), nn, geo_n)
        else:
            nn = geo_n
        base = np.array(mat.base_color[:3] if mat else (0.8, 0.8, 0.8), dtype=np.float64)
        # IR base colours are linear; splats store display sRGB
        col = np.tile(base, (k, 1))
        alpha = np.full(k, float(mat.opacity) if mat else 1.0)
        tex = _load_texture(stage, mat.base_color_tex) if mat else None
        if tex is not None and tuv is not None:
            uv = np.einsum("nk,nkd->nd", bary, tuv[face])
            texel = _sample_texture(tex, uv)
            col = col * _srgb_to_linear(texel[:, :3])
            alpha = alpha * texel[:, 3]
        if tc is not None:
            col = col * np.einsum("nk,nkd->nd", bary, tc[face]).clip(0, 1)
        if mat is not None and mat.emission_strength > 0:
            col = col + np.array(mat.emission) * mat.emission_strength
        out_p.append(p)
        out_n.append(nn)
        out_c.append(_linear_to_srgb(np.clip(col, 0, 1)))
        out_g.append(np.full(k, material_is_glossy(mat)))
        out_a.append(alpha)
    points = np.concatenate(out_p).astype(np.float32)
    radius = np.full(len(points), math.sqrt(total / max(len(points), 1) / math.pi) * 1.3, dtype=np.float32)
    return SurfaceSamples(points, np.concatenate(out_n).astype(np.float32), np.concatenate(out_c).astype(np.float32), radius,
                          np.concatenate(out_g), np.concatenate(out_a).astype(np.float32))


def _srgb_to_linear(x: np.ndarray) -> np.ndarray:
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(x: np.ndarray) -> np.ndarray:
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(np.clip(x, 0, None), 1 / 2.4) - 0.055)


def _frames_from_normals(normals: np.ndarray) -> np.ndarray:
    """Rotation matrices whose local Z is the normal (columns = local axes in world)."""
    n = normals / np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    helper = np.where(np.abs(n[:, :1]) < 0.9, np.array([[1.0, 0, 0]]), np.array([[0, 1.0, 0]]))
    t = np.cross(helper, n)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
    b = np.cross(n, t)
    return np.stack([t, b, n], axis=2)


def init_gaussians(samples: SurfaceSamples, sh_degree: int = 0, flatness: float = 0.1, opacity: float = 0.9) -> Splats:
    """Surface-aligned flat Gaussians, one per sample (Mesh2Splat-style initialisation)."""
    n = len(samples.points)
    rot = _frames_from_normals(samples.normals.astype(np.float64))
    quats = mat_to_quat_wxyz(rot).astype(np.float32)
    r = samples.radius.astype(np.float64)
    scales = np.log(np.stack([r, r, np.maximum(r * flatness, 1e-7)], axis=1)).astype(np.float32)
    k = SH_COUNT[sh_degree] - 1
    return Splats(
        name="baked",
        means=samples.points,
        scales=scales,
        rotations=quats,
        opacities=np.full(n, logit(np.array([opacity]))[0], dtype=np.float32),
        sh_dc=((samples.colors - 0.5) / SH_C0).astype(np.float32),
        sh_rest=np.zeros((n, k, 3), dtype=np.float32) if k else None,
        antialiased=True,
    )


# --------------------------------------------------------------------------------- COLMAP


def colmap_pose(cam: ResolvedCamera, width: int, height: int) -> tuple[np.ndarray, np.ndarray, float]:
    """(qvec wxyz, tvec, focal px) of the world→camera transform in COLMAP/OpenCV convention."""
    from ..engines.splat_raster import camera_matrices

    view, k = camera_matrices(cam, width, height)
    q = mat_to_quat_wxyz(view[None, :3, :3])[0]
    if q[0] < 0:
        q = -q
    return q, view[:3, 3], float(k[0, 0])


def write_colmap(out_dir: Path, cams: list[ResolvedCamera], width: int, height: int, image_names: list[str],
                 points: np.ndarray, colors: np.ndarray, binary: bool = True, text: bool = True) -> Path:
    """sparse/0/{cameras,images,points3D}.{txt,bin}: one PINHOLE camera per image (shared when
    intrinsics match), exact poses, surface points with colours (empty tracks: poses are known)."""
    sparse = Path(out_dir) / "sparse" / "0"
    sparse.mkdir(parents=True, exist_ok=True)
    poses = [colmap_pose(c, width, height) for c in cams]
    focals = sorted({round(f, 6) for _, _, f in poses})
    cam_id = {f: i + 1 for i, f in enumerate(focals)}
    cols8 = np.clip(np.round(np.asarray(colors) * 255), 0, 255).astype(np.uint8)
    if text:
        with open(sparse / "cameras.txt", "w") as fh:
            fh.write("# Camera list with one line of data per camera:\n#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n")
            fh.write(f"# Number of cameras: {len(focals)}\n")
            for f, i in cam_id.items():
                fh.write(f"{i} PINHOLE {width} {height} {f!r} {f!r} {width / 2!r} {height / 2!r}\n")
        with open(sparse / "images.txt", "w") as fh:
            fh.write("# Image list with two lines of data per image:\n")
            fh.write("#   IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n#   POINTS2D[] as (X, Y, POINT3D_ID)\n")
            fh.write(f"# Number of images: {len(cams)}, mean observations per image: 0\n")
            for i, ((q, t, f), name) in enumerate(zip(poses, image_names), start=1):
                fh.write(f"{i} {' '.join(repr(float(x)) for x in q)} {' '.join(repr(float(x)) for x in t)} {cam_id[round(f, 6)]} {name}\n\n")
        with open(sparse / "points3D.txt", "w") as fh:
            fh.write("# 3D point list with one line of data per point:\n")
            fh.write("#   POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[] as (IMAGE_ID, POINT2D_IDX)\n")
            fh.write(f"# Number of points: {len(points)}, mean track length: 0\n")
            for i, (p, c) in enumerate(zip(points, cols8), start=1):
                fh.write(f"{i} {p[0]:.6f} {p[1]:.6f} {p[2]:.6f} {c[0]} {c[1]} {c[2]} 0\n")
    if binary:
        with open(sparse / "cameras.bin", "wb") as fh:
            fh.write(struct.pack("<Q", len(focals)))
            for f, i in cam_id.items():
                fh.write(struct.pack("<iiQQ", i, 1, width, height))  # model 1 = PINHOLE
                fh.write(struct.pack("<dddd", f, f, width / 2, height / 2))
        with open(sparse / "images.bin", "wb") as fh:
            fh.write(struct.pack("<Q", len(cams)))
            for i, ((q, t, f), name) in enumerate(zip(poses, image_names), start=1):
                fh.write(struct.pack("<i", i))
                fh.write(struct.pack("<7d", *[float(x) for x in q], *[float(x) for x in t]))
                fh.write(struct.pack("<i", cam_id[round(f, 6)]))
                fh.write(name.encode() + b"\x00")
                fh.write(struct.pack("<Q", 0))
        with open(sparse / "points3D.bin", "wb") as fh:
            fh.write(struct.pack("<Q", len(points)))
            for i, (p, c) in enumerate(zip(points, cols8), start=1):
                fh.write(struct.pack("<Q3d3Bd", i, float(p[0]), float(p[1]), float(p[2]), int(c[0]), int(c[1]), int(c[2]), 0.0))
                fh.write(struct.pack("<Q", 0))
    return sparse


def read_colmap_images_txt(path: Path) -> list[tuple[np.ndarray, np.ndarray, int, str]]:
    """Parse images.txt → [(qvec, tvec, camera_id, name)] (used by tests and the dataset check)."""
    out = []
    lines = [l for l in Path(path).read_text().splitlines() if l and not l.startswith("#")]
    for line in lines:
        parts = line.split()
        if len(parts) >= 10:
            out.append((np.array(parts[1:5], float), np.array(parts[5:8], float), int(parts[8]), parts[9]))
    return out


def zip_dataset(root: Path, out: Path) -> Path:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(Path(root).rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(root))
    return out


# --------------------------------------------------------------------------------- outputs


def output_name(pkg: RendaPackage, settings: BakeSettings) -> str:
    import re

    name = pkg.name
    if not name:
        uri = pkg.asset_uri(pkg.scene.root)
        name = "splat" if uri.startswith("asset://") else Path(uri.split("?")[0]).stem
    base = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "splat"
    return f"{base}.{settings.splat_format}"


def file_axes_matrix(stage_up: str, path: Path) -> np.ndarray:
    """Stage world → the file format's convention, so baked splats open upright here and in other
    viewers: .spz is Y-up; .ply/.splat captures are COLMAP/OpenCV Y-down (what our loaders assume)."""
    m = np.eye(4)
    if stage_up == "Z":  # Z-up → Y-up: (x, y, z) → (x, z, -y)
        m[:3, :3] = [[1, 0, 0], [0, 0, 1], [0, -1, 0]]
    if Path(path).suffix.lower() in (".ply", ".splat"):
        m = np.diag([1.0, -1.0, -1.0, 1.0]) @ m
    return m


def export_splats(splats: Splats, path: Path, stage_up: str = "Y") -> Path:
    from ..ir.loaders.splat import write_any

    path.parent.mkdir(parents=True, exist_ok=True)
    m = file_axes_matrix(stage_up, path)
    if not np.allclose(m, np.eye(4)):
        splats = Splats(splats.name, splats.means, splats.scales, splats.rotations, splats.opacities, splats.sh_dc,
                        splats.sh_rest, m, splats.antialiased).baked()
    return write_any(splats, path)


def direct_bake(stage: Stage, pkg: RendaPackage, settings: BakeSettings, out_dir: Path) -> tuple[list[Path], list[str]]:
    """`fast` preset: mesh → Gaussians, no rendering. Colours are albedo (unlit)."""
    samples = sample_surface(stage, settings.init_points, settings.seed)
    splats = init_gaussians(samples, settings.sh_degree)
    out = export_splats(splats, Path(out_dir) / output_name(pkg, settings), stage.up_axis)
    warnings = [f"splat bake (fast): {len(splats):,} Gaussians from mesh surfaces; colours are albedo without "
                f"lighting — use preset standard to bake lighting and reflections"]
    if settings.dataset:
        warnings.append("splat bake: dataset output needs rendered views; ignored for preset fast")
    return [out], warnings
