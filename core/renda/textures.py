"""Texture overrides: turn `scene.textureOverrides` into per-frame image files every engine can read,
and apply them to an IR Stage.

- Any image goes in (png/jpg/webp/avif/heic/tiff/bmp/tga/gif/psd/svg/exr/hdr); what comes out is a
  PNG (or the original EXR/HDR) that Blender, Mitsuba and three.js all load.
- Videos (mp4/mov/webm/mkv/avi, animated gif/png/webp) become one still per rendered frame, synced to
  the animation: frame f shows source time `startSec + (f - frameStart) / fps * speed` (looped or held).
- Matching uses renda.engines.blender_scripts._match so Blender (native .blend path) and the IR agree.
"""

from __future__ import annotations

import hashlib
import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .engines.blender_scripts._match import basename, matches
from .spec import RendaPackage, TextureOverride

# IR material field per spec slot.
SLOT_FIELDS = {
    "baseColor": "base_color_tex",
    "normal": "normal_tex",
    "roughness": "roughness_tex",
    "metallic": "metallic_tex",
    "emission": "emission_tex",
    "opacity": "opacity_tex",
}
COLOR_SLOTS = {"baseColor", "emission"}

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mpg", ".mpeg", ".ogv"}
ANIMATED_IMAGE_EXTS = {".gif", ".png", ".apng", ".webp"}
PASSTHROUGH_HDR = {".exr", ".hdr"}
_PLAIN = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG"}


class TextureError(ValueError):
    pass


@dataclass
class ResolvedTexture:
    index: int
    spec: TextureOverride
    is_video: bool
    still: Path | None = None
    frames: dict[int, Path] = field(default_factory=dict)

    def path(self, frame: int) -> Path:
        if self.is_video:
            return self.frames.get(frame) or next(iter(self.frames.values()))
        assert self.still is not None
        return self.still

    def colorspace(self, slot: str) -> str:
        cs = self.spec.colorSpace
        if cs != "auto":
            return cs
        return "srgb" if slot in COLOR_SLOTS else "linear"

    def uv(self) -> tuple[float, float, float, float, float]:
        """(scale_u, scale_v, offset_u, offset_v, rotation_deg)."""
        u = self.spec.uv
        sx, sy = (u.scale, u.scale) if isinstance(u.scale, (int, float)) else u.scale
        return float(sx), float(sy), float(u.offset[0]), float(u.offset[1]), float(u.rotationDeg)

    def to_job(self, frames: list[int], path_map=lambda p: str(p)) -> dict:
        """JSON for engines that apply overrides themselves (Blender, three.js)."""
        m = self.spec.match
        return {
            "index": self.index,
            "match": {"material": m.material, "object": m.object, "texture": m.texture},
            "slot": m.slot,
            "frames": {str(f): path_map(self.path(f)) for f in frames},
            "uv": list(self.uv()),
            "colorSpace": self.spec.colorSpace,
            "strength": self.spec.strength,
        }


# -- ingestion ----------------------------------------------------------------------------------

def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:20]


def _sniff_ext(path: Path) -> str:
    """Extension from content (content-addressed blobs have none)."""
    ext = path.suffix.lower()
    if ext and ext not in (".bin", ".blob"):
        return ext
    with open(path, "rb") as f:
        head = f.read(64)
    sigs = [
        (b"\x89PNG", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"GIF8", ".gif"), (b"BM", ".bmp"),
        (b"II*\x00", ".tif"), (b"MM\x00*", ".tif"), (b"8BPS", ".psd"), (b"\x76\x2f\x31\x01", ".exr"),
        (b"#?RADIANCE", ".hdr"), (b"#?RGBE", ".hdr"), (b"\x1a\x45\xdf\xa3", ".webm"),
    ]
    for sig, e in sigs:
        if head.startswith(sig):
            return e
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return ".avif"
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
            return ".heic"
        if brand == b"qt  ":
            return ".mov"
        return ".mp4"
    text = head.lstrip().lower()
    if text.startswith(b"<svg") or text.startswith(b"<?xml"):
        return ".svg"
    return ext


def _open_image(path: Path, ext: str):
    from PIL import Image

    if ext in (".heic", ".heif"):
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except ImportError as e:
            raise TextureError("HEIC textures need pillow-heif (uv pip install pillow-heif)") from e
    if ext == ".psd":
        try:
            from psd_tools import PSDImage

            return PSDImage.open(path).composite()
        except ImportError:
            pass  # Pillow reads the flattened PSD image
    return Image.open(path)


def _svg_to_png(path: Path, dest: Path, size: int = 2048) -> Path:
    try:
        import resvg_py
    except ImportError as e:
        raise TextureError("SVG textures need resvg-py (uv pip install resvg-py)") from e
    import io

    from PIL import Image

    # rasterise at the SVG's own size first to learn its aspect, then at `size` on the long edge
    probe = Image.open(io.BytesIO(bytes(resvg_py.svg_to_bytes(svg_path=str(path)))))
    w, h = probe.size
    scale = size / max(w, h, 1)
    data = bytes(resvg_py.svg_to_bytes(svg_path=str(path), width=max(1, round(w * scale)), height=max(1, round(h * scale))))
    dest.write_bytes(data)
    return dest


def ingest_image(path: Path, cache: Path, frame_index: int | None = None) -> Path:
    """Any still image (or one frame of an animated image) → a file every engine reads."""
    ext = _sniff_ext(path)
    digest = _digest(path)
    if ext in PASSTHROUGH_HDR:
        dest = cache / f"{digest}{ext}"
        if not dest.exists():
            shutil.copyfile(path, dest)
        return dest
    dest = cache / f"{digest}{'' if frame_index is None else f'_f{frame_index}'}.png"
    if dest.exists():
        return dest
    if ext == ".svg":
        return _svg_to_png(path, dest)
    img = _open_image(path, ext)
    if frame_index is not None and getattr(img, "n_frames", 1) > 1:
        img.seek(min(frame_index, img.n_frames - 1))
    if ext in _PLAIN and frame_index is None and img.mode in ("RGB", "RGBA", "L", "LA") and img.format in _PLAIN.values():
        # already something every engine reads: use the file as-is
        dest = cache / f"{digest}{'.jpg' if img.format == 'JPEG' else '.png'}"
        if not dest.exists():
            shutil.copyfile(path, dest)
        return dest
    if img.mode == "I;16" or img.mode == "I":
        img.save(dest, format="PNG")  # 16-bit greyscale (height/roughness maps) stays 16-bit
        return dest
    if img.mode == "CMYK":
        img = img.convert("RGB")
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")
    img.save(dest, format="PNG")
    return dest


def is_video_source(path: Path) -> bool:
    ext = _sniff_ext(path)
    if ext in VIDEO_EXTS:
        return True
    if ext in ANIMATED_IMAGE_EXTS:
        try:
            from PIL import Image

            with Image.open(path) as im:
                return getattr(im, "n_frames", 1) > 1
        except Exception:
            return False
    return False


# -- video ---------------------------------------------------------------------------------------

def source_times(spec: TextureOverride, pkg: RendaPackage, frames: list[int]) -> dict[int, float | None]:
    """Render frame → source time in seconds (None = use video.frame / first frame for stills)."""
    v = spec.video
    if pkg.animation is None:
        return {f: (None if v.frame is not None else v.startSec) for f in frames}
    fps, start = pkg.animation.fps, pkg.animation.frames[0]
    return {f: v.startSec + (f - start) / fps * v.speed for f in frames}


def _wrap(t: float, duration: float, loop: bool, frame_dt: float) -> float:
    if duration <= 0:
        return 0.0
    if loop:
        return t % duration
    return min(max(t, 0.0), max(duration - frame_dt, 0.0))


def _animated_image_frames(path: Path, times: dict[int, float | None], spec: TextureOverride, cache: Path) -> dict[int, Path]:
    from PIL import Image

    with Image.open(path) as im:
        durations = []
        for i in range(im.n_frames):
            im.seek(i)
            durations.append(max(im.info.get("duration", 100), 10) / 1000.0)
    starts = [sum(durations[:i]) for i in range(len(durations))]
    total = sum(durations)
    out: dict[int, Path] = {}
    for f, t in times.items():
        if t is None:
            idx = min(spec.video.frame or 0, len(durations) - 1)
        else:
            tt = _wrap(t, total, spec.video.loop, durations[-1])
            idx = max(i for i, s in enumerate(starts) if s <= tt + 1e-9)
        out[f] = ingest_image(path, cache, frame_index=idx)
    return out


def _video_meta(path: Path) -> tuple[float, float]:
    """(fps, duration seconds) via ffmpeg."""
    import imageio_ffmpeg

    gen = imageio_ffmpeg.read_frames(str(path))
    try:
        meta = next(gen)
    finally:
        gen.close()
    fps = float(meta.get("fps") or 30.0)
    duration = float(meta.get("duration") or 0.0)
    return fps, duration


def _video_frames(path: Path, times: dict[int, float | None], spec: TextureOverride, cache: Path) -> dict[int, Path]:
    """Decode once, sequentially, keeping only the source frames the render needs."""
    import imageio_ffmpeg
    import numpy as np
    from PIL import Image

    fps, duration = _video_meta(path)
    if duration <= 0:
        duration = 1.0 / fps
    n_src = max(1, int(round(duration * fps)))
    want: dict[int, list[int]] = {}
    for f, t in times.items():
        if t is None:
            idx = spec.video.frame or 0
        else:
            idx = int(math.floor(_wrap(t, duration, spec.video.loop, 1.0 / fps) * fps + 1e-6))
        idx = min(max(idx, 0), n_src - 1)
        want.setdefault(idx, []).append(f)
    digest = _digest(path)
    out: dict[int, Path] = {}
    todo = {i for i in want if not (cache / f"{digest}_v{i:06d}.png").exists()}
    if todo:
        gen = imageio_ffmpeg.read_frames(str(path))  # rgb24
        meta = next(gen)
        w, h = meta["size"]
        last = max(todo)
        prev = None
        for i, buf in enumerate(gen):
            prev = buf
            if i in todo:
                Image.fromarray(np.frombuffer(buf, np.uint8).reshape(h, w, 3)).save(cache / f"{digest}_v{i:06d}.png")
            if i >= last:
                break
        gen.close()
        # the container's duration can overstate the frame count: fill any missing tail frames with the last one
        for i in todo:
            p = cache / f"{digest}_v{i:06d}.png"
            if not p.exists() and prev is not None:
                Image.fromarray(np.frombuffer(prev, np.uint8).reshape(h, w, 3)).save(p)
    for idx, fs in want.items():
        for f in fs:
            out[f] = cache / f"{digest}_v{idx:06d}.png"
    return out


# -- resolution ----------------------------------------------------------------------------------

def texture_cache(cache_dir: Path | None) -> Path:
    import os

    root = Path(cache_dir) if cache_dir else Path(os.environ.get("RENDA_CACHE_DIR", Path.home() / ".cache" / "renda"))
    d = root / "textures"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_texture_overrides(pkg: RendaPackage, asset_paths: dict[str, Path], frames: list[int],
                              cache_dir: Path | None = None) -> list[ResolvedTexture]:
    """Ingest every override's source for the frames being rendered."""
    from .scene import asset_path

    cache = texture_cache(cache_dir)
    out: list[ResolvedTexture] = []
    for i, ov in enumerate(pkg.scene.textureOverrides):
        src = asset_path(pkg, ov.source, asset_paths)
        if is_video_source(src):
            times = source_times(ov, pkg, frames)
            if _sniff_ext(src) in ANIMATED_IMAGE_EXTS:
                fr = _animated_image_frames(src, times, ov, cache)
            else:
                fr = _video_frames(src, times, ov, cache)
            if pkg.animation is None:  # a still render of a video texture: one frame
                out.append(ResolvedTexture(i, ov, False, still=next(iter(fr.values()))))
            else:
                out.append(ResolvedTexture(i, ov, True, frames=fr))
        else:
            out.append(ResolvedTexture(i, ov, False, still=ingest_image(src, cache)))
    return out


# -- applying to an IR stage --------------------------------------------------------------------

def _scope_materials(stage, object_pattern: str | None, candidates: set[str], tag: str) -> dict[str, str]:
    """For an object-scoped override, give matched objects their own copy of each candidate
    material (when other objects share it). Returns {original: material to edit}."""
    import copy

    if object_pattern is None:
        return {m: m for m in candidates}
    edit: dict[str, str] = {}
    for name in sorted(candidates):
        users = [m for m in stage.meshes if m.material == name]
        hit = [m for m in users if matches(object_pattern, m.name)]
        if not hit:
            continue
        if len(hit) == len(users):
            edit[name] = name
            continue
        clone_name = f"{name}@{tag}"
        if clone_name not in stage.materials:
            c = copy.deepcopy(stage.materials[name])
            c.name = clone_name
            stage.materials[clone_name] = c
        for m in hit:
            m.material = clone_name
        edit[name] = clone_name
    return edit


def _ensure_materials(stage) -> None:
    for mesh in stage.meshes:
        if not mesh.material or mesh.material not in stage.materials:
            stage.material_for(mesh)
            mesh.material = "__default__"


def _texture_slot_hits(mat, pattern: str | None) -> list[str]:
    if pattern is None:
        return []
    return [slot for slot, fld in SLOT_FIELDS.items() if matches(pattern, basename(getattr(mat, fld, None)))] + (
        ["roughness", "metallic"] if matches(pattern, basename(mat.metallic_roughness_tex)) else [])


def match_texture_override(stage, ov: TextureOverride, tag: str) -> list[tuple[str, str]]:
    """→ [(material name to edit, slot)] after splitting materials for object scope. Stage is mutated
    (clones) but no textures are set; call before exporting the stage so clones survive."""
    _ensure_materials(stage)
    m = ov.match
    used = {mesh.material for mesh in stage.meshes if m.object is None or matches(m.object, mesh.name)}
    targets: list[tuple[str, str]] = []
    cands: dict[str, list[str]] = {}
    for name in used:
        mat = stage.materials[name]
        if not matches(m.material, name):
            continue
        if m.texture is not None:
            hits = _texture_slot_hits(mat, m.texture)
            if not hits:
                continue
            slots = [m.slot] if m.slot else list(dict.fromkeys(hits))
        else:
            slots = [m.slot or "baseColor"]
        cands[name] = slots
    edit = _scope_materials(stage, m.object, set(cands), tag)
    for orig, slots in cands.items():
        if orig in edit:
            targets += [(edit[orig], s) for s in slots]
    return targets


def set_texture(mat, slot: str, path: Path | str, tex: ResolvedTexture) -> None:
    setattr(mat, SLOT_FIELDS[slot], str(path))
    mat.tex_uv[slot] = tex.uv()
    mat.tex_colorspace[slot] = tex.colorspace(slot)
    if slot == "baseColor":
        mat.base_color = (1.0, 1.0, 1.0, mat.base_color[3])
    elif slot == "emission":
        mat.emission = (1.0, 1.0, 1.0)
        mat.emission_strength = tex.spec.strength if tex.spec.strength is not None else (mat.emission_strength or 1.0)
    elif slot == "normal" and tex.spec.strength is not None:
        mat.normal_strength = tex.spec.strength
    elif slot == "roughness":
        mat.roughness = 1.0
    elif slot == "metallic":
        mat.metallic = 1.0


def box_uvs(mesh):
    """Box-projected UVs for meshes that have none (STL, scans): each triangle is projected along its
    dominant normal axis, 1 UV unit = the mesh's longest side. Splits vertices per projection axis."""
    import numpy as np

    pts = mesh.points.astype(np.float64)
    tri = pts[mesh.indices]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    axis = np.argmax(np.abs(n), axis=1)
    size = float(np.ptp(pts, axis=0).max()) or 1.0
    lo = pts.min(axis=0)
    new_pts, new_uvs, new_idx = [], [], []
    drop = {0: (1, 2), 1: (0, 2), 2: (0, 1)}
    for a in range(3):
        faces = mesh.indices[axis == a]
        if len(faces) == 0:
            continue
        verts, inv = np.unique(faces.reshape(-1), return_inverse=True)
        u, v = drop[a]
        base = sum(len(p) for p in new_pts)
        new_pts.append(pts[verts])
        new_uvs.append(np.stack([(pts[verts, u] - lo[u]) / size, (pts[verts, v] - lo[v]) / size], axis=1))
        new_idx.append(inv.reshape(-1, 3) + base)
    normals = None
    if mesh.normals is not None and len(mesh.normals) == len(mesh.points):
        normals = np.concatenate([mesh.normals[np.unique(mesh.indices[axis == a].reshape(-1))] for a in range(3)
                                  if (axis == a).any()])
    colors = None
    if mesh.colors is not None and len(mesh.colors) == len(mesh.points):
        colors = np.concatenate([mesh.colors[np.unique(mesh.indices[axis == a].reshape(-1))] for a in range(3)
                                 if (axis == a).any()])
    mesh.points = np.concatenate(new_pts).astype(np.float32)
    mesh.indices = np.concatenate(new_idx).astype(np.int32)
    mesh.uvs = np.concatenate(new_uvs).astype(np.float32)
    mesh.normals = normals.astype(np.float32) if normals is not None else None
    mesh.colors = colors


class TexturePlan:
    """Matched (material, slot) targets per override, computed once per stage."""

    def __init__(self, stage, resolved: list[ResolvedTexture]):
        self.resolved = resolved
        self.targets = [match_texture_override(stage, r.spec, f"tex{r.index}") for r in resolved]
        self.generated_uvs: list[str] = []
        textured = {mat for t in self.targets for mat, _ in t}
        for mesh in stage.meshes:
            if mesh.material in textured and (mesh.uvs is None or len(mesh.uvs) != len(mesh.points)):
                box_uvs(mesh)
                self.generated_uvs.append(mesh.name)

    def warnings(self) -> list[str]:
        out = []
        if self.generated_uvs:
            names = ", ".join(self.generated_uvs[:5]) + ("…" if len(self.generated_uvs) > 5 else "")
            out.append(f"textureOverrides: {names} had no UVs; generated box-projected UVs")
        for r, t in zip(self.resolved, self.targets):
            if not t:
                out.append(f"textureOverrides[{r.index}] matched no material texture slots "
                           f"(match={r.spec.match.model_dump(exclude_none=True) or 'all'})")
        return out

    def report(self) -> list[dict]:
        return [{"index": r.index, "targets": [f"{m}.{s}" for m, s in t]} for r, t in zip(self.resolved, self.targets)]

    @property
    def animated(self) -> bool:
        return any(r.is_video for r in self.resolved)

    def apply(self, stage, frame: int) -> None:
        for r, targets in zip(self.resolved, self.targets):  # in order: later overrides win
            p = r.path(frame)
            for mat_name, slot in targets:
                set_texture(stage.materials[mat_name], slot, p, r)

    def materials_for(self, index: int) -> list[tuple[str, str]]:
        return self.targets[index]
