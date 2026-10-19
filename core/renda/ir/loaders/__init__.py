"""Format → Stage dispatch."""

from __future__ import annotations

from pathlib import Path

from ...formats import detect
from ..stage import Stage

TRIMESH_FORMATS = {"obj", "stl", "ply", "dae", "3mf", "gltf"}
BLENDER_FORMATS = {"blend", "fbx", "abc"}


def load_stage(path: str | Path, fmt: str | None = None, cache_dir: Path | None = None) -> Stage:
    """Load any supported scene file into the IR. `cache_dir` is used for Blender extraction (.rir)."""
    p = Path(path)
    fmt = fmt or detect(p)
    if fmt == "rir":
        from ..rir import read_rir

        return read_rir(p)
    if fmt in TRIMESH_FORMATS:
        from . import mesh_formats

        return mesh_formats.load(p, fmt)
    if fmt == "threejs":
        from . import threejs

        return threejs.load(p)
    if fmt == "usd":
        from . import usd

        return usd.load(p)
    if fmt in BLENDER_FORMATS:
        from . import blender

        return blender.load(p, fmt, cache_dir)
    if fmt == "splat":
        from . import splat

        return splat.load(p)
    raise ValueError(f"no loader for format {fmt!r}")


def loader_available(fmt: str) -> bool:
    if fmt in TRIMESH_FORMATS or fmt in ("threejs", "rir", "splat"):
        return True
    if fmt == "usd":
        from . import usd

        return usd.available()
    if fmt in BLENDER_FORMATS:
        from . import blender

        return blender.available()
    return False
