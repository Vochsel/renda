"""Scene format detection by extension + magic bytes."""

from __future__ import annotations

import json
from pathlib import Path

from .spec import EXTENSION_FORMATS


class UnknownFormat(ValueError):
    pass


def _head(path: Path, n: int = 512) -> bytes:
    with open(path, "rb") as f:
        return f.read(n)


def is_threejs_json(path: Path) -> bool:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    meta = data.get("metadata", {}) if isinstance(data, dict) else {}
    return isinstance(meta, dict) and meta.get("type") in ("Object", "Scene", "BufferGeometry", "Geometry")


def _zip_format(path: Path) -> str | None:
    import zipfile

    try:
        names = zipfile.ZipFile(path).namelist()
    except (OSError, zipfile.BadZipFile):
        return None
    if any(n.lower().endswith((".usdc", ".usda", ".usd")) for n in names):
        return "usd"
    if any(n.lower().endswith(".model") for n in names) or "[Content_Types].xml" in names and any(n.startswith("3D/") for n in names):
        return "3mf"
    return None


def _is_compressed_blend(path: Path, head: bytes) -> bool:
    """Blender 3.0+ saves zstd-compressed .blend files when compression is on; older versions used gzip."""
    try:
        if head.startswith(b"\x28\xb5\x2f\xfd"):
            import zstandard

            with open(path, "rb") as f, zstandard.ZstdDecompressor().stream_reader(f) as r:
                return r.read(7) == b"BLENDER"
        if head.startswith(b"\x1f\x8b"):
            import gzip

            with gzip.open(path, "rb") as f:
                return f.read(7) == b"BLENDER"
    except Exception:
        return False
    return False


def _is_gltf_json(path: Path) -> bool:
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(data, dict) and isinstance(data.get("asset"), dict) and "version" in data["asset"]


def is_binary_stl(path: Path) -> bool:
    """Binary STL: 80-byte header + uint32 count, size must match. ASCII starts with 'solid' (but some binaries do too)."""
    size = path.stat().st_size
    if size < 84:
        return False
    with open(path, "rb") as f:
        f.seek(80)
        count = int.from_bytes(f.read(4), "little")
    return 84 + count * 50 == size


# Files that travel with a scene but aren't one.
SIDECARS = {
    ".mtl": "an OBJ material library (it's loaded with its .obj)",
    ".bin": "a glTF buffer (it's loaded with its .gltf)",
}


def detect(path: str | Path) -> str:
    """Return a scene format id (see spec.SCENE_FORMATS). Raises UnknownFormat."""
    p = Path(path)
    if p.is_dir():
        if (p / "stage.json").exists():
            return "rir"
        raise UnknownFormat(f"{p} is a directory but not a .rir bundle")
    head = _head(p)
    # magic bytes first: they beat misleading extensions
    if head.startswith(b"BLENDER"):
        return "blend"
    if head.startswith(b"glTF"):
        return "gltf"
    if head.startswith(b"PXR-USDC"):
        return "usd"
    if head.startswith(b"#usda"):
        return "usd"
    if head.startswith(b"Kaydara FBX Binary"):
        return "fbx"
    if head.startswith(b"Ogawa"):
        return "abc"
    if head.startswith(b"ply\n") or head.startswith(b"ply\r\n"):
        from .ir.loaders.splat import is_splat_ply

        return "splat" if is_splat_ply(p) else "ply"
    if head.startswith(b"PK\x03\x04"):
        ext = p.suffix.lower()
        if ext in (".usdz", ".3mf"):
            return EXTENSION_FORMATS[ext]
        zipped = _zip_format(p)
        if zipped:
            return zipped
    if head.startswith(b"\x1f\x8b"):
        from .ir.loaders.splat import is_spz

        if is_spz(p):
            return "splat"
    if _is_compressed_blend(p, head):
        return "blend"
    ext = p.suffix.lower()
    if ext not in EXTENSION_FORMATS and ext not in SIDECARS and head.lstrip()[:1] == b"{":  # content-addressed blobs have no extension
        if is_threejs_json(p):
            return "threejs"
        if _is_gltf_json(p):
            return "gltf"
    if ext in SIDECARS:
        raise UnknownFormat(f"{p.name} is {SIDECARS[ext]}, not a scene; render the main scene file instead")
    if ext == ".json":
        if is_threejs_json(p):
            return "threejs"
        raise UnknownFormat(f"{p.name}: JSON file is not a three.js scene (expected metadata.type == 'Object')")
    if ext == ".gltf":
        return "gltf"
    if ext in EXTENSION_FORMATS:
        return EXTENSION_FORMATS[ext]
    text = head.lstrip()
    if text.startswith(b"solid") or is_binary_stl(p):
        return "stl"
    if b"<COLLADA" in head:
        return "dae"
    if text.startswith(b"v ") or b"\nv " in head:  # needs vertex lines, not just comments
        return "obj"
    from .ir.loaders.splat import looks_like_splat

    if looks_like_splat(p):  # antimatter15 .splat has no magic; sniff the records
        return "splat"
    raise UnknownFormat(f"can't detect the 3D format of {p.name}; supported: {', '.join(sorted(set(EXTENSION_FORMATS)))}")
