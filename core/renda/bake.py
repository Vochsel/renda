"""Light baking shared by every baker: lightmap UVs, texel rasterisation, dilation, encoding, GLB output.

A baker (Cycles, Mitsuba, three.js) only has to fill a linear-RGB texture laid out on the atlas UVs this
module computes, so the same model gets the same UV layout whichever engine bakes it.

Conventions: the atlas array A has shape (S, S, C) with row r covering v ∈ [r/S, (r+1)/S) — OpenGL UVs,
v up, like the IR. GLB output flips both the image rows and v, because glTF puts (0, 0) at the top left.
"""

from __future__ import annotations

import io
import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .ir.stage import Material, Stage

# Z-up → Y-up: (x, y, z) → (x, z, -y). GLB is Y-up.
Z_TO_Y = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]], dtype=np.float64)


class BakeError(RuntimeError):
    pass


@dataclass
class BakeMesh:
    name: str
    points: np.ndarray  # (N,3) float32, stage world space
    normals: np.ndarray  # (N,3) float32, world space, unit
    indices: np.ndarray  # (M,3) int32
    uv1: np.ndarray  # (N,2) float32 lightmap atlas UVs in [0,1], v up
    uv0: np.ndarray | None  # (N,2) original UVs (textures), v up
    material: str | None
    colors: np.ndarray | None = None


@dataclass
class BakeLayout:
    meshes: list[BakeMesh]
    size: int
    up_axis: str
    warnings: list[str] = field(default_factory=list)

    def triangle_count(self) -> int:
        return int(sum(len(m.indices) for m in self.meshes))


def _world(mesh) -> tuple[np.ndarray, np.ndarray]:
    m = np.asarray(mesh.transform, dtype=np.float64)
    pts = (np.c_[mesh.points.astype(np.float64), np.ones(len(mesh.points))] @ m.T)[:, :3]
    nrm = mesh.ensure_normals().astype(np.float64)
    nm = np.linalg.inv(m[:3, :3]).T
    nrm = nrm @ nm.T
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    return pts.astype(np.float32), nrm.astype(np.float32)


def prepare(stage: Stage, size: int = 2048, uvs: str = "auto", margin: int = 4) -> BakeLayout:
    """World-space copies of the stage's meshes with non-overlapping lightmap UVs (one shared atlas)."""
    meshes = [m for m in stage.meshes if len(m.indices)]
    if not meshes:
        if stage.splats:
            raise BakeError("Gaussian splats can't be baked (they have no surfaces or UVs); bake a mesh scene instead")
        raise BakeError("nothing to bake: the scene has no meshes")
    warnings: list[str] = []
    if stage.splats:
        warnings.append("bake: Gaussian splats are left out of the GLB (they have no surfaces to bake onto)")
    world = [_world(m) for m in meshes]
    out: list[BakeMesh] = []
    if uvs == "existing":
        for m, (p, n) in zip(meshes, world):
            if m.uvs is None:
                raise BakeError(f'mesh {m.name!r} has no UVs; use bake.uvs "auto"')
            uv = m.uvs.astype(np.float32)
            if uv.min() < -1e-3 or uv.max() > 1 + 1e-3:
                warnings.append(f"bake: mesh {m.name!r} has UVs outside 0–1; they were wrapped (expect artefacts)")
                uv = np.mod(uv, 1.0)
            out.append(BakeMesh(m.name, p, n, m.indices.copy(), uv, m.uvs, m.material, m.colors))
        return BakeLayout(out, size, stage.up_axis, warnings)

    import xatlas

    atlas = xatlas.Atlas()
    for m, (p, n) in zip(meshes, world):
        atlas.add_mesh(p, m.indices.astype(np.uint32), n)
    po = xatlas.PackOptions()
    po.resolution = int(size)
    po.padding = int(max(margin, 1))
    po.bilinear = True
    po.blockAlign = False
    po.rotate_charts = True
    atlas.generate(xatlas.ChartOptions(), po)
    for i, (m, (p, n)) in enumerate(zip(meshes, world)):
        vmap, idx, uv = atlas[i]
        vmap = np.asarray(vmap, dtype=np.int64)
        uv0 = m.uvs[vmap].astype(np.float32) if m.uvs is not None and len(m.uvs) == len(m.points) else None
        cols = m.colors[vmap] if m.colors is not None and len(m.colors) == len(m.points) else None
        out.append(BakeMesh(m.name, p[vmap], n[vmap], np.asarray(idx, dtype=np.int32), np.asarray(uv, dtype=np.float32),
                            uv0, m.material, cols))
    return BakeLayout(out, size, stage.up_axis, warnings)


# -- texel rasterisation -------------------------------------------------------------------------------


@dataclass
class Texels:
    """Covered texels of the atlas and the surface point each one samples."""

    rows: np.ndarray  # (K,) int
    cols: np.ndarray  # (K,) int
    mesh: np.ndarray  # (K,) int — index into layout.meshes
    tri: np.ndarray  # (K,) int — triangle index within that mesh
    bary: np.ndarray  # (K,3) float32
    size: int

    def __len__(self) -> int:
        return len(self.rows)

    def mask(self) -> np.ndarray:
        m = np.zeros((self.size, self.size), dtype=bool)
        m[self.rows, self.cols] = True
        return m

    def interpolate(self, layout: BakeLayout, attr: str) -> np.ndarray:
        """Barycentric interpolation of a per-vertex attribute ("points", "normals", "uv0") at every texel."""
        out = None
        for mi, mesh in enumerate(layout.meshes):
            sel = np.nonzero(self.mesh == mi)[0]
            if not len(sel):
                continue
            data = getattr(mesh, attr)
            if data is None:
                continue
            tris = mesh.indices[self.tri[sel]]  # (k,3)
            b = self.bary[sel][..., None]
            val = (data[tris[:, 0]] * b[:, 0] + data[tris[:, 1]] * b[:, 1] + data[tris[:, 2]] * b[:, 2])
            if out is None:
                out = np.zeros((len(self), data.shape[1]), dtype=np.float32)
            out[sel] = val
        if out is None:
            raise BakeError(f"no mesh has {attr}")
        if attr == "normals":
            out /= np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-12)
        return out


def rasterize(layout: BakeLayout) -> Texels:
    """Which triangle covers each texel centre (u = (c+.5)/S, v = (r+.5)/S), with barycentrics.

    Small triangles (the vast majority after unwrapping) are tested in vectorised batches against a fixed
    pixel grid around their bounding box; large ones get their own pass.
    """
    S = layout.size
    owner = np.full((S, S), -1, dtype=np.int64)
    bary_img = np.zeros((S, S, 3), dtype=np.float32)
    tri_mesh: list[np.ndarray] = []
    tri_local: list[np.ndarray] = []
    corners: list[np.ndarray] = []
    for mi, m in enumerate(layout.meshes):
        uv = m.uv1.astype(np.float64) * S - 0.5  # pixel-centre coordinates
        corners.append(uv[m.indices])  # (T,3,2)
        tri_mesh.append(np.full(len(m.indices), mi, dtype=np.int32))
        tri_local.append(np.arange(len(m.indices), dtype=np.int64))
    if not corners:
        raise BakeError("nothing to rasterise")
    C = np.concatenate(corners)  # (T,3,2) in pixel space, x = col, y = row
    gid_mesh = np.concatenate(tri_mesh)
    gid_tri = np.concatenate(tri_local)
    lo = np.floor(C.min(axis=1)).astype(np.int64) + 0  # (T,2)
    hi = np.ceil(C.max(axis=1)).astype(np.int64)
    lo = np.clip(lo, 0, S - 1)
    hi = np.clip(hi, 0, S - 1)
    ext = (hi - lo + 1).max(axis=1)

    def edge_test(gids: np.ndarray, px: np.ndarray, py: np.ndarray):
        """px, py: (n, k) pixel coords for triangles gids (n). Returns inside mask (n,k) and barycentrics."""
        a, b, c = C[gids, 0], C[gids, 1], C[gids, 2]  # (n,2)
        v0 = b - a
        v1 = c - a
        den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]  # (n,)
        ok = np.abs(den) > 1e-12
        den = np.where(ok, den, 1.0)
        dx = px - a[:, :1]
        dy = py - a[:, 1:2]
        w1 = (dx * v1[:, 1:2] - v1[:, :1] * dy) / den[:, None]
        w2 = (v0[:, :1] * dy - dx * v0[:, 1:2]) / den[:, None]
        w0 = 1 - w1 - w2
        eps = -1e-6
        inside = (w0 >= eps) & (w1 >= eps) & (w2 >= eps) & ok[:, None]
        return inside, w0, w1, w2

    for grid in (4, 8, 16, 32):
        sel = np.nonzero((ext <= grid) & (ext > (grid // 2 if grid > 4 else 0)))[0]
        if not len(sel):
            continue
        oy, ox = np.mgrid[0:grid, 0:grid]
        ox, oy = ox.ravel(), oy.ravel()
        for start in range(0, len(sel), max(1, 2_000_000 // (grid * grid))):
            g = sel[start:start + max(1, 2_000_000 // (grid * grid))]
            px = lo[g, 0:1] + ox[None, :]
            py = lo[g, 1:2] + oy[None, :]
            valid = (px <= hi[g, 0:1]) & (py <= hi[g, 1:2])
            inside, w0, w1, w2 = edge_test(g, px.astype(np.float64), py.astype(np.float64))
            inside &= valid
            n_idx, k_idx = np.nonzero(inside)
            rr, cc = py[n_idx, k_idx], px[n_idx, k_idx]
            owner[rr, cc] = g[n_idx]
            bary_img[rr, cc] = np.stack([w0[n_idx, k_idx], w1[n_idx, k_idx], w2[n_idx, k_idx]], axis=1)
    for g in np.nonzero(ext > 32)[0]:
        ys, xs = np.mgrid[lo[g, 1]:hi[g, 1] + 1, lo[g, 0]:hi[g, 0] + 1]
        px = xs.reshape(1, -1).astype(np.float64)
        py = ys.reshape(1, -1).astype(np.float64)
        inside, w0, w1, w2 = edge_test(np.array([g]), px, py)
        k = np.nonzero(inside[0])[0]
        rr, cc = ys.ravel()[k], xs.ravel()[k]
        owner[rr, cc] = g
        bary_img[rr, cc] = np.stack([w0[0, k], w1[0, k], w2[0, k]], axis=1)
    rows, cols = np.nonzero(owner >= 0)
    gids = owner[rows, cols]
    return Texels(rows, cols, gid_mesh[gids].astype(np.int32), gid_tri[gids], bary_img[rows, cols], S)


def dilate(img: np.ndarray, mask: np.ndarray, iterations: int) -> np.ndarray:
    """Grow covered texels outward by `iterations` pixels (average of covered 8-neighbours), so bilinear
    filtering and mipmaps at UV island borders don't pull in the empty background."""
    img = img.astype(np.float32).copy()
    mask = mask.copy()
    for _ in range(max(0, iterations)):
        acc = np.zeros_like(img)
        cnt = np.zeros(mask.shape, dtype=np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                m = np.roll(np.roll(mask, dy, 0), dx, 1)
                v = np.roll(np.roll(img, dy, 0), dx, 1)
                acc += v * m[..., None]
                cnt += m
        grow = (~mask) & (cnt > 0)
        if not grow.any():
            break
        img[grow] = acc[grow] / cnt[grow][:, None]
        mask = mask | grow
    # anything still empty gets the mean colour (keeps mip levels from going dark)
    if (~mask).any() and mask.any():
        img[~mask] = img[mask].mean(axis=0)
    return img


# -- encoding ------------------------------------------------------------------------------------------


def _srgb(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def soft_clip(x: np.ndarray, knee: float = 0.8) -> np.ndarray:
    """Identity below `knee`, smooth roll-off to 1 above it (keeps bright bakes from hard-clipping)."""
    over = np.maximum(x - knee, 0)
    return np.where(x <= knee, x, knee + (1 - knee) * (1 - np.exp(-over / (1 - knee))))


def encode(mode: str, linear: np.ndarray) -> tuple[np.ndarray, float]:
    """Linear RGB atlas → 8-bit sRGB pixels (v-up rows) + a scale to multiply back in (lightmap mode).

    unlit: colour as seen (soft-clipped). ao: grey occlusion in R (glTF reads R). lightmap: lighting
    normalised so its 99th percentile fits, the scale goes into extras.renda.lightmap.intensity.
    """
    rgb = np.asarray(linear, dtype=np.float32)[..., :3]
    if mode == "ao":
        g = np.clip(rgb.mean(axis=-1, keepdims=True), 0, 1)
        return (np.repeat(g, 3, axis=-1) * 255 + 0.5).astype(np.uint8), 1.0
    if mode == "lightmap":
        p99 = float(np.percentile(rgb.max(axis=-1), 99)) if rgb.size else 1.0
        scale = max(p99, 1e-4)
        return (_srgb(soft_clip(rgb / scale, 0.9)) * 255 + 0.5).astype(np.uint8), scale
    return (_srgb(soft_clip(rgb)) * 255 + 0.5).astype(np.uint8), 1.0


def image_bytes(pixels_vup: np.ndarray, fmt: str) -> tuple[bytes, str]:
    """Encode a v-up atlas as a glTF image (rows flipped so the top row is v=1)."""
    from PIL import Image

    img = Image.fromarray(np.ascontiguousarray(pixels_vup[::-1]))
    buf = io.BytesIO()
    if fmt == "png":
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png"
    if fmt == "webp":
        img.save(buf, format="WEBP", quality=90)
        return buf.getvalue(), "image/webp"
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


# -- albedo for bakers that shade themselves (Mitsuba) --------------------------------------------------


def _load_texture(stage: Stage, path: str | None):
    if not path:
        return None
    from PIL import Image

    p = Path(path)
    if not p.is_absolute() and stage.base_dir:
        p = Path(stage.base_dir) / p
    try:
        im = Image.open(p).convert("RGBA")
    except Exception:
        return None
    a = np.asarray(im, dtype=np.float32) / 255.0
    a[..., :3] = np.where(a[..., :3] <= 0.04045, a[..., :3] / 12.92, ((a[..., :3] + 0.055) / 1.055) ** 2.4)
    return a


def sample_texture(tex: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Bilinear lookup, repeat wrap. tex: (H,W,C) top row first (image order); uv: (K,2) v up."""
    h, w = tex.shape[:2]
    x = np.mod(uv[:, 0], 1.0) * w - 0.5
    y = (1 - np.mod(uv[:, 1], 1.0)) * h - 0.5
    x0 = np.floor(x).astype(np.int64)
    y0 = np.floor(y).astype(np.int64)
    fx = (x - x0)[:, None]
    fy = (y - y0)[:, None]
    x0m, x1m = np.mod(x0, w), np.mod(x0 + 1, w)
    y0m, y1m = np.mod(y0, h), np.mod(y0 + 1, h)
    top = tex[y0m, x0m] * (1 - fx) + tex[y0m, x1m] * fx
    bot = tex[y1m, x0m] * (1 - fx) + tex[y1m, x1m] * fx
    return top * (1 - fy) + bot * fy


def albedo_and_emission(stage: Stage, layout: BakeLayout, texels: Texels) -> tuple[np.ndarray, np.ndarray]:
    """Linear base colour and emitted radiance at every texel (material factors × textures × vertex colours)."""
    K = len(texels)
    albedo = np.zeros((K, 3), dtype=np.float32)
    emission = np.zeros((K, 3), dtype=np.float32)
    uv0 = None
    try:
        uv0 = texels.interpolate(layout, "uv0")
    except BakeError:
        pass
    cache: dict[str, np.ndarray | None] = {}
    for mi, mesh in enumerate(layout.meshes):
        sel = np.nonzero(texels.mesh == mi)[0]
        if not len(sel):
            continue
        mat: Material = stage.materials.get(mesh.material or "", None) or Material("__default__")
        col = np.tile(np.asarray(mat.base_color[:3], dtype=np.float32), (len(sel), 1))
        if mat.base_color_tex and uv0 is not None and mesh.uv0 is not None:
            key = mat.base_color_tex
            if key not in cache:
                cache[key] = _load_texture(stage, key)
            if cache[key] is not None:
                col *= sample_texture(cache[key], uv0[sel])[:, :3]
        if mesh.colors is not None:
            tris = mesh.indices[texels.tri[sel]]
            b = texels.bary[sel][..., None]
            c = mesh.colors[:, :3].astype(np.float32)
            if c.max() > 1.0:
                c = c / 255.0
            col *= c[tris[:, 0]] * b[:, 0] + c[tris[:, 1]] * b[:, 1] + c[tris[:, 2]] * b[:, 2]
        albedo[sel] = col
        if mat.emission_strength > 0:
            emission[sel] = np.asarray(mat.emission, dtype=np.float32) * mat.emission_strength
    return albedo, emission


# -- GLB writer ----------------------------------------------------------------------------------------


def _pad4(b: bytes, fill: bytes = b"\x00") -> bytes:
    return b + fill * ((4 - len(b) % 4) % 4)


class _GLB:
    def __init__(self) -> None:
        self.bin = bytearray()
        self.gltf: dict = {"asset": {"version": "2.0", "generator": "renda bake"}, "buffers": [], "bufferViews": [],
                           "accessors": [], "meshes": [], "nodes": [], "materials": [], "scenes": [{"nodes": []}],
                           "scene": 0}

    def view(self, data: bytes, target: int | None = None) -> int:
        while len(self.bin) % 4:
            self.bin += b"\x00"
        bv = {"buffer": 0, "byteOffset": len(self.bin), "byteLength": len(data)}
        if target:
            bv["target"] = target
        self.bin += data
        self.gltf["bufferViews"].append(bv)
        return len(self.gltf["bufferViews"]) - 1

    def accessor(self, arr: np.ndarray, kind: str, target: int | None = None, minmax: bool = False) -> int:
        comp = {np.dtype(np.float32): 5126, np.dtype(np.uint32): 5125, np.dtype(np.uint16): 5123}[arr.dtype]
        acc = {"bufferView": self.view(np.ascontiguousarray(arr).tobytes(), target), "componentType": comp,
               "count": int(arr.shape[0]), "type": kind}
        if minmax:
            acc["min"] = [float(x) for x in arr.min(axis=0)]
            acc["max"] = [float(x) for x in arr.max(axis=0)]
        self.gltf["accessors"].append(acc)
        return len(self.gltf["accessors"]) - 1

    def image(self, data: bytes, mime: str) -> int:
        self.gltf.setdefault("images", []).append({"bufferView": self.view(data), "mimeType": mime})
        self.gltf.setdefault("samplers", [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}])
        tex = {"source": len(self.gltf["images"]) - 1, "sampler": 0}
        if mime == "image/webp":
            tex = {"sampler": 0, "extensions": {"EXT_texture_webp": {"source": len(self.gltf["images"]) - 1}}}
            self._ext("EXT_texture_webp", required=True)
        self.gltf.setdefault("textures", []).append(tex)
        return len(self.gltf["textures"]) - 1

    def _ext(self, name: str, required: bool = False) -> None:
        used = self.gltf.setdefault("extensionsUsed", [])
        if name not in used:
            used.append(name)
        if required:
            req = self.gltf.setdefault("extensionsRequired", [])
            if name not in req:
                req.append(name)

    def write(self, out: Path) -> Path:
        self.gltf["buffers"] = [{"byteLength": len(self.bin)}]
        js = _pad4(json.dumps(self.gltf, separators=(",", ":")).encode(), b" ")
        binc = _pad4(bytes(self.bin))
        total = 12 + 8 + len(js) + 8 + len(binc)
        with open(out, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, total))
            f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
            f.write(struct.pack("<II", len(binc), 0x004E4942) + binc)
        return out


def _texture_file_bytes(stage: Stage, path: str | None, max_size: int = 2048) -> tuple[bytes, str] | None:
    """Re-encode a source texture for embedding (≤ max_size, jpg unless it has alpha)."""
    if not path:
        return None
    from PIL import Image

    p = Path(path)
    if not p.is_absolute() and stage.base_dir:
        p = Path(stage.base_dir) / p
    try:
        im = Image.open(p)
        im.load()
    except Exception:
        return None
    if max(im.size) > max_size:
        im.thumbnail((max_size, max_size))
    buf = io.BytesIO()
    if im.mode in ("RGBA", "LA") or "transparency" in im.info:
        im.convert("RGBA").save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png"
    im.convert("RGB").save(buf, format="JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


def write_glb(
    layout: BakeLayout,
    stage: Stage,
    out: str | Path,
    mode: str | None,
    baked: tuple[bytes, str] | None = None,
    lightmap_scale: float = 1.0,
    extras: dict | None = None,
) -> Path:
    """Write the layout as GLB (Y-up, one node per mesh, vertices in world space, TEXCOORD_1 = atlas).

    mode None: plain export (original PBR materials, no bake, no TEXCOORD_1). "source": original materials +
    TEXCOORD_1 (the input handed to an external baker). "unlit": every mesh shares one unlit material
    showing the baked texture. "ao": original materials + occlusionTexture on TEXCOORD_1. "lightmap": original
    materials + `extras.renda.lightmap = {index, texCoord: 1, intensity}`.
    """
    out = Path(out)
    g = _GLB()
    to_y = Z_TO_Y if layout.up_axis == "Z" else (np.diag([1.0, -1.0, -1.0, 1.0]) if layout.up_axis == "-Y" else np.eye(4))
    rot = to_y[:3, :3]
    baked_tex = g.image(*baked) if baked else None
    tex_cache: dict[str, int | None] = {}

    def tex(path: str | None) -> int | None:
        if not path:
            return None
        if path not in tex_cache:
            data = _texture_file_bytes(stage, path)
            tex_cache[path] = g.image(*data) if data else None
        return tex_cache[path]

    mat_index: dict[str, int] = {}

    def material(name: str | None) -> int:
        if mode == "unlit":
            key = "__baked__"
            if key not in mat_index:
                g._ext("KHR_materials_unlit")
                g.gltf["materials"].append({
                    "name": "baked", "doubleSided": True,
                    "pbrMetallicRoughness": {"baseColorTexture": {"index": baked_tex, "texCoord": 1},
                                             "metallicFactor": 0.0, "roughnessFactor": 1.0},
                    "extensions": {"KHR_materials_unlit": {}},
                })
                mat_index[key] = len(g.gltf["materials"]) - 1
            return mat_index[key]
        key = name or "__default__"
        if key in mat_index:
            return mat_index[key]
        m = stage.materials.get(key) or Material(key)
        pbr: dict = {"baseColorFactor": [float(c) for c in m.base_color[:3]] + [float(m.opacity * m.base_color[3])],
                     "metallicFactor": float(m.metallic), "roughnessFactor": float(m.roughness)}
        if (t := tex(m.base_color_tex)) is not None:
            pbr["baseColorTexture"] = {"index": t}
        if (t := tex(m.metallic_roughness_tex)) is not None:
            pbr["metallicRoughnessTexture"] = {"index": t}
        md: dict = {"name": m.name, "doubleSided": bool(m.double_sided), "pbrMetallicRoughness": pbr}
        if (t := tex(m.normal_tex)) is not None:
            md["normalTexture"] = {"index": t}
        if m.emission_strength > 0:
            md["emissiveFactor"] = [float(min(c * m.emission_strength, 1.0)) for c in m.emission]
            if (t := tex(m.emission_tex)) is not None:
                md["emissiveTexture"] = {"index": t}
        if m.opacity < 1 or m.base_color[3] < 1:
            md["alphaMode"] = "BLEND"
        if mode == "ao" and baked_tex is not None:
            md["occlusionTexture"] = {"index": baked_tex, "texCoord": 1, "strength": 1.0}
        if mode == "lightmap" and baked_tex is not None:
            md["extras"] = {"renda": {"lightmap": {"index": baked_tex, "texCoord": 1, "intensity": round(lightmap_scale, 6)}}}
        g.gltf["materials"].append(md)
        mat_index[key] = len(g.gltf["materials"]) - 1
        return mat_index[key]

    for i, m in enumerate(layout.meshes):
        pos = (m.points.astype(np.float64) @ rot.T).astype(np.float32)
        nrm = (m.normals.astype(np.float64) @ rot.T).astype(np.float32)
        attrs = {
            "POSITION": g.accessor(pos, "VEC3", 34962, minmax=True),
            "NORMAL": g.accessor(nrm, "VEC3", 34962),
        }
        uv0 = m.uv0 if m.uv0 is not None else m.uv1
        attrs["TEXCOORD_0"] = g.accessor(np.c_[uv0[:, 0], 1 - uv0[:, 1]].astype(np.float32), "VEC2", 34962)
        if mode is not None:  # plain exports have no lightmap UVs
            attrs["TEXCOORD_1"] = g.accessor(np.c_[m.uv1[:, 0], 1 - m.uv1[:, 1]].astype(np.float32), "VEC2", 34962)
        if m.colors is not None and mode != "unlit":
            c = m.colors[:, :4].astype(np.float32)
            if c.max() > 1.0:
                c = c / 255.0
            if c.shape[1] == 3:
                c = np.c_[c, np.ones(len(c), dtype=np.float32)]
            attrs["COLOR_0"] = g.accessor(c, "VEC4", 34962)
        idx = m.indices.astype(np.uint32 if len(m.points) > 65535 else np.uint16).reshape(-1)
        prim = {"attributes": attrs, "indices": g.accessor(idx, "SCALAR", 34963), "material": material(m.material), "mode": 4}
        g.gltf["meshes"].append({"name": m.name, "primitives": [prim]})
        g.gltf["nodes"].append({"name": f"{m.name}_{i}", "mesh": len(g.gltf["meshes"]) - 1})
        g.gltf["scenes"][0]["nodes"].append(len(g.gltf["nodes"]) - 1)
    if extras:
        g.gltf["asset"]["extras"] = extras
    return g.write(out)


def prepare_plain(stage: Stage) -> BakeLayout:
    """World-space meshes without lightmap UVs, for plain GLB conversion."""
    meshes = [m for m in stage.meshes if len(m.indices)]
    if not meshes:
        raise BakeError("nothing to export: the scene has no meshes" + (" (Gaussian splats can't be written to GLB)" if stage.splats else ""))
    out = []
    for m in meshes:
        p, n = _world(m)
        uv0 = m.uvs if m.uvs is not None and len(m.uvs) == len(m.points) else None
        out.append(BakeMesh(m.name, p, n, m.indices.copy(), uv0 if uv0 is not None else np.zeros((len(p), 2), np.float32),
                            uv0, m.material, m.colors if m.colors is not None and len(m.colors) == len(m.points) else None))
    warnings = ["export: Gaussian splats were left out (GLB has no splat primitive)"] if stage.splats else []
    return BakeLayout(out, 0, stage.up_axis, warnings)


def finish(
    layout: BakeLayout,
    stage: Stage,
    linear: np.ndarray,
    mode: str,
    out: str | Path,
    texture_format: str = "jpg",
    margin: int = 4,
    mask: np.ndarray | None = None,
    extras: dict | None = None,
) -> Path:
    """Baked linear atlas (v-up rows) → dilated, encoded texture → GLB."""
    if mask is None:
        mask = rasterize(layout).mask()
    lin = np.nan_to_num(np.asarray(linear, dtype=np.float32)[..., :3], nan=0.0, posinf=0.0, neginf=0.0)
    lin = dilate(lin, mask, max(margin, 2) * 2)
    pixels, scale = encode(mode, lin)
    fmt = "png" if mode == "ao" and texture_format == "jpg" else texture_format
    return write_glb(layout, stage, out, mode, image_bytes(pixels, fmt), scale, extras)


def bake_seconds_estimate(size: int, samples: int, triangles: int, throughput: float) -> float:
    """Planner helper: texel-samples / throughput + unwrap time."""
    unwrap = 2.0 + triangles / 60_000
    return size * size * samples / throughput + unwrap


def cosine_hemisphere(n: np.ndarray, u1: np.ndarray, u2: np.ndarray) -> np.ndarray:
    """Cosine-weighted directions around unit normals n (K,3) (numpy reference used in tests)."""
    r = np.sqrt(u1)
    phi = 2 * math.pi * u2
    x, y, z = r * np.cos(phi), r * np.sin(phi), np.sqrt(np.maximum(0, 1 - u1))
    a = np.where(np.abs(n[:, :1]) > 0.9, np.array([[0, 1.0, 0]]), np.array([[1.0, 0, 0]]))
    t = np.cross(a, n)
    t /= np.linalg.norm(t, axis=1, keepdims=True)
    b = np.cross(n, t)
    return t * x[:, None] + b * y[:, None] + n * z[:, None]
