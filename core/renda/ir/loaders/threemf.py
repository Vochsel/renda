"""3MF loader: meshes, components, build-item transforms and colours.

trimesh's 3MF reader drops materials, so this parses the package XML directly: core-spec meshes and
components, `basematerials` (name + displaycolor) and the materials extension's `colorgroup`s. Each
object's triangles are split by resolved colour so every part keeps its own material. Coordinates stay
in the file's units (like the other mesh loaders); scale with `scene.unitScale`.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

from ..stage import Material, Mesh, Stage, auto_smooth
from .mesh_formats import _srgb_lin, _unique

_CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_MAT = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"
_MODEL_REL = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _model_path(z: zipfile.ZipFile) -> str:
    try:
        rels = ET.fromstring(z.read("_rels/.rels"))
        for r in rels:
            if r.get("Type") == _MODEL_REL and r.get("Target"):
                return r.get("Target").lstrip("/")
    except (KeyError, ET.ParseError):
        pass
    for name in z.namelist():
        if name.lower().endswith(".model"):
            return name
    raise ValueError("3MF package has no 3D model part")


def parse_color(value: str | None) -> tuple[float, float, float, float] | None:
    """'#RRGGBB' or '#RRGGBBAA' (sRGB) → linear RGBA."""
    if not value or not re.fullmatch(r"#?[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", value.strip()):
        return None
    h = value.strip().lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    a = int(h[6:8], 16) / 255.0 if len(h) == 8 else 1.0
    lin = _srgb_lin(rgb)
    return (float(lin[0]), float(lin[1]), float(lin[2]), a)


def _matrix(value: str | None) -> np.ndarray:
    """3MF's 12-number row-major 4x3 transform (applied to row vectors) → 4x4 column-vector matrix."""
    m = np.eye(4)
    if value:
        v = [float(x) for x in value.split()]
        if len(v) == 12:
            m[:3, :3] = np.array(v[:9]).reshape(3, 3).T
            m[:3, 3] = v[9:12]
    return m


def load(path: Path) -> Stage:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read(_model_path(z)))
    stage = Stage(up_axis="Z", source_format="3mf", base_dir=str(path.parent))

    resources = next((el for el in root if _tag(el) == "resources"), None)
    build = next((el for el in root if _tag(el) == "build"), None)
    if resources is None:
        return stage

    # property groups: id → list of (material name, linear rgba)
    groups: dict[str, list[tuple[str, tuple]]] = {}
    objects: dict[str, ET.Element] = {}
    for el in resources:
        t = _tag(el)
        if t == "basematerials":
            groups[el.get("id")] = [(b.get("name") or f"material_{i}", parse_color(b.get("displaycolor")) or (0.8, 0.8, 0.8, 1.0))
                                    for i, b in enumerate(el) if _tag(b) == "base"]
        elif t == "colorgroup":
            groups[el.get("id")] = [(f"color_{c.get('color', '').lstrip('#').lower()}", parse_color(c.get("color")) or (0.8, 0.8, 0.8, 1.0))
                                    for c in el if _tag(c) == "color"]
        elif t == "object":
            objects[el.get("id")] = el

    materials: dict[tuple, str] = {}

    def material_for(pid: str | None, index: str | None) -> str | None:
        if pid is None or pid not in groups:
            return None
        entries = groups[pid]
        i = int(index or 0)
        if not 0 <= i < len(entries):
            return None
        name, rgba = entries[i]
        key = (name, rgba)
        if key not in materials:
            uname = _unique(name, stage.materials)
            stage.materials[uname] = Material(name=uname, base_color=rgba, opacity=rgba[3])
            materials[key] = uname
        return materials[key]

    def emit(obj_id: str, xform: np.ndarray, depth: int = 0) -> None:
        obj = objects.get(obj_id)
        if obj is None or depth > 16:
            return
        name = obj.get("name") or f"object_{obj_id}"
        mesh_el = next((el for el in obj if _tag(el) == "mesh"), None)
        if mesh_el is not None:
            _emit_mesh(stage, mesh_el, name, xform, obj.get("pid"), obj.get("pindex"), material_for)
        comps = next((el for el in obj if _tag(el) == "components"), None)
        if comps is not None:
            for c in comps:
                if _tag(c) == "component" and c.get("objectid"):
                    emit(c.get("objectid"), xform @ _matrix(c.get("transform")), depth + 1)

    items = [el for el in build if _tag(el) == "item"] if build is not None else []
    if items:
        for it in items:
            emit(it.get("objectid"), _matrix(it.get("transform")))
    else:  # no build section: show every model object once
        for oid, obj in objects.items():
            if obj.get("type", "model") == "model":
                emit(oid, np.eye(4))
    return stage


def _emit_mesh(stage: Stage, mesh_el, name: str, xform: np.ndarray, obj_pid, obj_pindex, material_for) -> None:
    verts_el = next((el for el in mesh_el if _tag(el) == "vertices"), None)
    tris_el = next((el for el in mesh_el if _tag(el) == "triangles"), None)
    if verts_el is None or tris_el is None:
        return
    points = np.array([[float(v.get("x")), float(v.get("y")), float(v.get("z"))] for v in verts_el], dtype=np.float32)
    tris, mats = [], []
    for t in tris_el:
        tris.append((int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))))
        # a triangle may override the object's property group/index; p1 is its (first-vertex) index
        mats.append(material_for(t.get("pid", obj_pid), t.get("p1", obj_pindex if t.get("pid") is None else None)))
    if not tris:
        return
    tris = np.array(tris, dtype=np.int32)
    order = {}
    for i, m in enumerate(mats):
        order.setdefault(m, []).append(i)
    for part, (mat, idx) in enumerate(order.items()):
        faces = tris[idx]
        used, remap = np.unique(faces, return_inverse=True)
        pts, fcs = points[used], remap.reshape(-1, 3).astype(np.int32)
        pts, fcs, normals, _ = auto_smooth(pts, fcs, 30.0, None)
        suffix = "" if len(order) == 1 else f"_{part}"
        stage.meshes.append(Mesh(name=name + suffix, points=pts, indices=fcs, normals=normals,
                                 transform=xform.copy(), material=mat))
