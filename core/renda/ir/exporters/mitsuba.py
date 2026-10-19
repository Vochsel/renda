"""Stage → Mitsuba 3 scene dict (for `mi.load_dict`). Requires a variant to be set."""

from __future__ import annotations

import hashlib
import math
import tempfile
from pathlib import Path

import numpy as np

from ..stage import Light, Material, Mesh, Stage

MITSUBA_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".exr", ".hdr", ".pfm", ".ppm", ".bmp", ".tga", ".rgbe"}


class TextureCache:
    """Converts textures Mitsuba can't read (webp, packed glTF channels) into files it can."""

    def __init__(self, base_dir: str | None):
        self.base_dir = Path(base_dir) if base_dir else None
        self.dir = Path(tempfile.mkdtemp(prefix="renda-mi-tex-"))
        self._memo: dict[tuple, str | None] = {}

    def resolve(self, path: str | None) -> Path | None:
        if not path:
            return None
        p = Path(path)
        if not p.is_absolute() and self.base_dir:
            p = self.base_dir / p
        return p if p.exists() else None

    def readable(self, path: str | None) -> str | None:
        p = self.resolve(path)
        if p is None:
            return None
        key = ("plain", str(p))
        if key not in self._memo:
            if p.suffix.lower() in MITSUBA_IMAGE_EXTS:
                self._memo[key] = str(p)
            else:
                from PIL import Image

                out = self.dir / f"{hashlib.sha1(str(p).encode()).hexdigest()[:12]}.png"
                Image.open(p).convert("RGBA" if Image.open(p).mode in ("RGBA", "LA", "P") else "RGB").save(out)
                self._memo[key] = str(out)
        return self._memo[key]

    def channel(self, path: str | None, ch: int) -> str | None:
        """Extract one channel (e.g. glTF metallicRoughness: G=roughness, B=metallic) as greyscale."""
        p = self.resolve(path)
        if p is None:
            return None
        key = ("ch", str(p), ch)
        if key not in self._memo:
            from PIL import Image

            img = Image.open(p).convert("RGB")
            out = self.dir / f"{hashlib.sha1(str(p).encode()).hexdigest()[:12]}_c{ch}.png"
            img.getchannel(ch).save(out)
            self._memo[key] = str(out)
        return self._memo[key]

    def scaled_linear(self, path: str, scale: tuple[float, float, float], srgb: bool) -> str:
        """An EXR of the texture in linear light times `scale` (emission textures × colour × strength)."""
        p = self.resolve(path) or Path(path)
        key = ("scaled", str(p), tuple(scale), srgb)
        if key not in self._memo:
            from PIL import Image

            from ...images import save_exr

            if p.suffix.lower() in (".exr", ".hdr"):
                from ...images import read_hdr_image

                arr = read_hdr_image(p)[..., :3]
            else:
                arr = np.asarray(Image.open(p).convert("RGB"), dtype=np.float32) / 255.0
                if srgb:
                    arr = np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4)
            arr = (arr * np.asarray(scale, dtype=np.float32)).astype(np.float32)
            out = self.dir / f"{hashlib.sha1(repr(key).encode()).hexdigest()[:12]}.exr"
            save_exr(arr, out)
            self._memo[key] = str(out)
        return self._memo[key]

    def normal_strength(self, path: str, strength: float) -> str:
        """Scale a tangent-space normal map's XY deviation (Mitsuba's normalmap has no strength)."""
        key = ("nstr", path, strength)
        if key not in self._memo:
            from PIL import Image

            n = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0 * 2 - 1
            n[..., :2] *= strength
            n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)
            out = self.dir / f"{hashlib.sha1(repr(key).encode()).hexdigest()[:12]}.png"
            Image.fromarray(np.clip((n + 1) * 127.5, 0, 255).astype(np.uint8)).save(out)
            self._memo[key] = str(out)
        return self._memo[key]


def _tex_or(value, tex: str | None, raw: bool = False, uv=None, mi=None):
    if tex:
        d = {"type": "bitmap", "filename": tex, "raw": raw}
        t = uv_transform(uv, mi)
        if t is not None:
            d["to_uv"] = t
        return d
    return value


def uv_transform(uv, mi):
    """Texture-override UV transform (scale_u, scale_v, offset_u, offset_v, rotation_deg) → Mitsuba
    `to_uv`, composed like KHR_texture_transform: translate(offset) · rotate(rotation) · scale."""
    if not uv or mi is None or tuple(uv) == (1.0, 1.0, 0.0, 0.0, 0.0):
        return None
    sx, sy, ox, oy, rot = uv
    return (mi.ScalarTransform4f().translate([ox, oy, 0]) @ mi.ScalarTransform4f().rotate([0, 0, 1], rot)
            @ mi.ScalarTransform4f().scale([sx, sy, 1]))


def _raw(mat: Material, slot: str, default: bool) -> bool:
    cs = mat.tex_colorspace.get(slot)
    return default if cs is None else cs == "linear"


def bsdf_for(mat: Material, textures: TextureCache, vertex_colors: bool = False, mi=None) -> dict:
    if mat.unlit:
        return {"type": "diffuse", "reflectance": {"type": "rgb", "value": [0.0, 0.0, 0.0]}}
    uv = mat.tex_uv.get
    base = _tex_or({"type": "rgb", "value": list(mat.base_color[:3])}, textures.readable(mat.base_color_tex),
                   _raw(mat, "baseColor", False), uv("baseColor"), mi)
    if vertex_colors and not mat.base_color_tex:
        base = {"type": "mesh_attribute", "name": "vertex_color"}
    rough_tex = textures.readable(mat.roughness_tex) or textures.channel(mat.metallic_roughness_tex, 1)
    metal_tex = textures.readable(mat.metallic_tex) or textures.channel(mat.metallic_roughness_tex, 2)
    bsdf: dict = {
        "type": "principled",
        "base_color": base,
        "metallic": _tex_or(float(mat.metallic), metal_tex, _raw(mat, "metallic", True), uv("metallic"), mi),
        "roughness": _tex_or(max(float(mat.roughness), 0.02), rough_tex, _raw(mat, "roughness", True), uv("roughness"), mi),
        "eta": float(mat.ior),
    }
    if mat.transmission > 0:
        bsdf["spec_trans"] = float(mat.transmission)
    normal = textures.readable(mat.normal_tex)
    if normal:
        nmap = _tex_or(None, normal, True, uv("normal"), mi)
        if mat.normal_strength != 1.0:
            nmap["filename"] = textures.normal_strength(normal, mat.normal_strength)
        bsdf = {"type": "normalmap", "normalmap": nmap, "bsdf": bsdf}
    if mat.double_sided and mat.transmission == 0:  # twosided can't wrap transmissive BSDFs
        bsdf = {"type": "twosided", "bsdf": bsdf}
    opacity_tex = textures.readable(mat.opacity_tex)
    if mat.opacity < 1 or opacity_tex:
        bsdf = {"type": "mask", "opacity": _tex_or(float(mat.opacity), opacity_tex, True, uv("opacity"), mi), "bsdf": bsdf}
    return bsdf


def write_ply(mesh: Mesh, path: Path) -> Path:
    """IR mesh → binary little-endian PLY in world space (normals, uvs, vertex colours)."""
    pts = mesh.world_points().astype(np.float32)
    normals = mesh.ensure_normals().astype(np.float64)
    m3 = mesh.transform[:3, :3]
    det = np.linalg.det(m3)
    nmat = np.linalg.inv(m3).T if abs(det) > 1e-12 else m3
    wn = normals @ nmat.T
    wn /= np.maximum(np.linalg.norm(wn, axis=1, keepdims=True), 1e-12)
    faces = mesh.indices.astype(np.int32)
    if det < 0:  # mirrored transform flips winding
        faces = faces[:, [0, 2, 1]]
    cols = [("x", pts[:, 0]), ("y", pts[:, 1]), ("z", pts[:, 2]),
            ("nx", wn[:, 0]), ("ny", wn[:, 1]), ("nz", wn[:, 2])]
    if mesh.uvs is not None and len(mesh.uvs) == len(pts):
        # IR UVs are OpenGL-style (v up); Mitsuba looks textures up with v down
        cols += [("u", mesh.uvs[:, 0]), ("v", 1.0 - mesh.uvs[:, 1])]
    colors = None
    if mesh.colors is not None and len(mesh.colors) == len(pts):
        colors = np.asarray(mesh.colors)[:, :3].astype(np.float32)
        cols += [("r", colors[:, 0]), ("g", colors[:, 1]), ("b", colors[:, 2])]
    vdtype = np.dtype([(n, "<f4") for n, _ in cols])
    verts = np.empty(len(pts), dtype=vdtype)
    for n, c in cols:
        verts[n] = c
    fdtype = np.dtype([("n", "u1"), ("i", "<i4", (3,))])
    fdata = np.empty(len(faces), dtype=fdtype)
    fdata["n"] = 3
    fdata["i"] = faces
    header = ["ply", "format binary_little_endian 1.0", f"element vertex {len(pts)}"]
    header += [f"property float {n}" for n, _ in cols]
    header += [f"element face {len(faces)}", "property list uchar int vertex_indices", "end_header"]
    with open(path, "wb") as f:
        f.write(("\n".join(header) + "\n").encode())
        f.write(verts.tobytes())
        f.write(fdata.tobytes())
    return path


def light_dict(light: Light, up: np.ndarray, mi) -> dict | None:
    c = np.asarray(light.color, dtype=np.float64)
    if light.type == "sun":
        return {"type": "directional", "direction": light.direction().tolist(),
                "irradiance": {"type": "rgb", "value": (c * light.intensity).tolist()}}
    if light.type == "point":
        # watts → radiant intensity (W/sr)
        return {"type": "point", "position": light.position().tolist(),
                "intensity": {"type": "rgb", "value": (c * light.intensity / (4 * math.pi)).tolist()}}
    if light.type == "spot":
        half = light.angle_deg / 2
        pos = light.position()
        tgt = pos + light.direction()
        upv = up if abs(np.dot(light.direction(), up)) < 0.99 else np.array([1.0, 0, 0])
        return {"type": "spot", "to_world": mi.ScalarTransform4f().look_at(origin=pos.tolist(), target=tgt.tolist(), up=upv.tolist()),
                "cutoff_angle": half, "beam_width": half * 0.8,
                "intensity": {"type": "rgb", "value": (c * light.intensity / (4 * math.pi)).tolist()}}
    if light.type == "area":
        w, h = light.size
        area = max(w * h, 1e-6)
        to_world = mi.ScalarTransform4f(np.asarray(light.transform, dtype=np.float64).tolist()) @ mi.ScalarTransform4f().scale([w / 2, h / 2, 1])
        # rectangle faces +Z in Mitsuba; IR lights emit down -Z → flip
        to_world = to_world @ mi.ScalarTransform4f().rotate([1, 0, 0], 180)
        return {"type": "rectangle", "to_world": to_world,
                "emitter": {"type": "area", "radiance": {"type": "rgb", "value": (c * light.intensity / (area * math.pi)).tolist()}}}
    if light.type == "ambient":
        return {"type": "constant", "radiance": {"type": "rgb", "value": (c * light.intensity * 0.3).tolist()}}
    return None


def stage_to_dict(stage: Stage, mi=None, textures: TextureCache | None = None, mesh_dir: Path | None = None) -> dict:
    """Shapes (as PLY files), materials and stage lights. Caller adds integrator/sensor/environment.

    `mi` is only needed for transforms of spot/area lights; pure data otherwise.
    """
    textures = textures or TextureCache(stage.base_dir)
    mesh_dir = Path(mesh_dir or tempfile.mkdtemp(prefix="renda-mi-ply-"))
    mesh_dir.mkdir(parents=True, exist_ok=True)
    scene: dict = {"type": "scene"}
    bsdf_ids: dict[str, str] = {}
    for i, mesh in enumerate(stage.meshes):
        if len(mesh.indices) == 0:
            continue
        mat = stage.material_for(mesh)
        vc = mesh.colors is not None
        key = mat.name + ("+vc" if vc else "")
        if key not in bsdf_ids:
            bid = f"bsdf_{len(bsdf_ids)}"
            scene[bid] = bsdf_for(mat, textures, vc, mi)
            bsdf_ids[key] = bid
        shape: dict = {
            "type": "ply",
            "filename": str(write_ply(mesh, mesh_dir / f"mesh_{i:05d}.ply")),
            "face_normals": False,
            "bsdf": {"type": "ref", "id": bsdf_ids[key]},
        }
        emit = None
        if mat.unlit:
            emit = list(mat.base_color[:3])
        elif mat.emission_strength > 0 and max(mat.emission) > 0:
            emit = [float(c) * mat.emission_strength for c in mat.emission]
        if emit is not None:
            radiance: dict = {"type": "rgb", "value": emit}
            tex = textures.readable(mat.emission_tex if not mat.unlit else mat.base_color_tex)
            if tex:
                slot = "baseColor" if mat.unlit else "emission"
                radiance = _tex_or(None, textures.scaled_linear(tex, tuple(emit), not _raw(mat, slot, False)), True,
                                   mat.tex_uv.get(slot), mi)
            shape["emitter"] = {"type": "area", "radiance": radiance}
        scene[f"shape_{i}"] = shape
    up = stage.up_vector()
    for j, light in enumerate(stage.lights):
        if light.type in ("dome", "ambient"):
            continue  # environment emitters: the engine picks one (Mitsuba allows only one)
        d = light_dict(light, up, mi)
        if d:
            scene[f"light_{j}"] = d
    return scene
