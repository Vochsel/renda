"""STEP / IGES → Stage, via OpenCASCADE (cascadio) tessellation to glTF.

Measured cascadio 0.1.1 behaviour this relies on (see tests/test_cad.py):
- STEP comes out in metres (the file's declared units are honoured); IGES comes out in OpenCASCADE's
  internal millimetres whatever the file declares, so it's scaled by 1e-3 here.
- Coordinates are kept in CAD axes (Z up) — no glTF Y-up conversion — so the stage is marked Z-up.
- `tol_linear` is in millimetres (OpenCASCADE's internal unit) regardless of file units.
- STEP part/assembly colours arrive as glTF base colours; IGES colours are not carried over.

Tessellation defaults to a chord error of 0.05% of the part's bounding-box diagonal (found with a
fast coarse pass) and 0.3 rad between facets, so tiny parts and building-sized assemblies both mesh
sensibly. The result is cached by content hash + tolerances.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import numpy as np

from ..stage import Stage

DEFAULT_RELATIVE_DEFLECTION = 0.0005  # × bbox diagonal
DEFAULT_ANGULAR_DEFLECTION = 0.3  # radians
MIN_DEFLECTION_M = 1e-6


def available() -> bool:
    try:
        import cascadio  # noqa: F401
    except ImportError:
        return False
    return True


def _glb_bounds(glb: bytes) -> np.ndarray | None:
    import trimesh

    scene = trimesh.load(trimesh.util.wrap_as_stream(glb), file_type="glb", force="scene")
    return scene.bounds


def tessellate(data: bytes, fmt: str, tessellation=None, cache_dir: Path | None = None) -> tuple[Path, float, dict]:
    """→ (glb path, factor to metres, info). Raises ValueError if OpenCASCADE yields nothing."""
    try:
        import cascadio
    except ImportError as e:
        raise RuntimeError("STEP/IGES need the CAD extra locally: `uvx --with 'renda[cad]' renda …` "
                           "(or render with --cloud, where it's always installed)") from e

    file_type = "iges" if fmt == "iges" else "step"
    to_m = 1e-3 if file_type == "iges" else 1.0
    lin = getattr(tessellation, "linearDeflection", None) if tessellation is not None else None
    ang = (getattr(tessellation, "angularDeflection", None) if tessellation is not None else None) or DEFAULT_ANGULAR_DEFLECTION
    info: dict = {"angularDeflection": ang}
    if lin is None:
        coarse = cascadio.load(data, file_type=file_type, tol_linear=0.1, tol_relative=True, tol_angular=1.0)
        bounds = _glb_bounds(coarse) if coarse else None
        if bounds is None:
            raise ValueError(f"OpenCASCADE found no surfaces to tessellate in this {file_type.upper()} file")
        diag = float(np.linalg.norm(bounds[1] - bounds[0])) * to_m
        lin = max(diag * DEFAULT_RELATIVE_DEFLECTION, MIN_DEFLECTION_M)
        info["bboxDiagonal"] = diag
    info["linearDeflection"] = lin
    key = hashlib.sha256(data).hexdigest()[:24] + f"_{lin:.3e}_{ang:.3f}"
    out_dir = Path(cache_dir) / "cad" if cache_dir else Path(tempfile.gettempdir()) / "renda-cad"
    out_dir.mkdir(parents=True, exist_ok=True)
    glb_path = out_dir / f"{key}.glb"
    if not glb_path.exists():
        glb = cascadio.load(data, file_type=file_type, tol_linear=lin * 1000.0, tol_angular=ang)
        if not glb or _glb_bounds(glb) is None:
            raise ValueError(f"OpenCASCADE produced no geometry for this {file_type.upper()} file (unsupported or empty B-rep?)")
        tmp = glb_path.with_suffix(".part")
        tmp.write_bytes(glb)
        tmp.replace(glb_path)
    return glb_path, to_m, info


def load(path: str | Path, fmt: str, tessellation=None, cache_dir: Path | None = None) -> Stage:
    from . import mesh_formats

    p = Path(path)
    glb_path, to_m, info = tessellate(p.read_bytes(), fmt, tessellation, cache_dir)
    stage = mesh_formats.load(glb_path, "gltf")
    if to_m != 1.0:
        stage.apply_transform(np.diag([to_m, to_m, to_m, 1.0]))
    stage.up_axis = "Z"  # CAD axes; cascadio doesn't convert to glTF's Y-up
    stage.source_format = fmt
    stage.base_dir = str(p.parent)
    stage.meta["tessellation"] = info
    if fmt == "iges":
        stage.meta.setdefault("warnings", []).append("iges: colours aren't imported from IGES; use scene.materialOverrides")
    return stage
