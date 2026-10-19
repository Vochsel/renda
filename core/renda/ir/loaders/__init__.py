"""Format → Stage dispatch."""

from __future__ import annotations

from pathlib import Path

from ...formats import detect
from ..stage import Stage

TRIMESH_FORMATS = {"obj", "stl", "ply", "dae", "3mf", "gltf"}
BLENDER_FORMATS = {"blend", "fbx", "abc"}
CAD_FORMATS = {"step", "iges"}


def load_stage(path: str | Path, fmt: str | None = None, cache_dir: Path | None = None, tessellation=None) -> Stage:
    """Load any supported scene file into the IR. `cache_dir` holds derived files (Blender .rir
    extractions, CAD tessellations); `tessellation` is the package's scene.tessellation for CAD."""
    p = Path(path)
    fmt = fmt or detect(p)
    if fmt in CAD_FORMATS:
        from . import cad

        return cad.load(p, fmt, tessellation, cache_dir)
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
    if fmt in CAD_FORMATS:
        from . import cad

        return cad.available()
    if fmt == "usd":
        from . import usd

        return usd.available()
    if fmt in BLENDER_FORMATS:
        from . import blender

        return blender.available()
    return False
