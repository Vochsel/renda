"""Helpers shared by the scripts that run *inside* Blender (render.py, extract.py).

Only bpy/mathutils/stdlib/numpy here — this file is executed by Blender's Python,
not by the renda package. Targets Blender 4.2 LTS → 5.x.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

NATIVE_IMPORT = {"blend", "usd", "gltf", "fbx", "obj", "stl", "ply", "abc"}
# Formats whose Blender importer converts Y-up sources into Blender's Z-up
CONVERTS_Y_UP = {"gltf", "fbx", "obj", "usd", "abc", "ply"}


def read_job() -> dict:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else sys.argv[1:]
    return json.loads(Path(argv[0]).read_text())


def write_result(job: dict, data: dict) -> None:
    data.setdefault("blender", bpy.app.version_string)
    Path(job["result"]).write_text(json.dumps(data))


def clear_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_file(path: str, fmt: str, up_axis: str | None = None, warnings: list | None = None) -> list:
    """Import into the current scene; returns the new top-level objects."""
    before = set(bpy.data.objects)
    up = (up_axis or "").upper()
    if fmt == "usd":
        bpy.ops.wm.usd_import(filepath=path)
    elif fmt == "gltf":
        bpy.ops.import_scene.gltf(filepath=path)
    elif fmt == "fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif fmt == "obj":
        kw = {"up_axis": "Z", "forward_axis": "Y"} if up == "Z" else {}
        bpy.ops.wm.obj_import(filepath=path, **kw)
    elif fmt == "stl":
        kw = {"up_axis": "Y", "forward_axis": "NEGATIVE_Z"} if up == "Y" else {}
        if hasattr(bpy.ops.wm, "stl_import"):
            bpy.ops.wm.stl_import(filepath=path, **kw)
        else:  # pragma: no cover - Blender < 4.1
            bpy.ops.import_mesh.stl(filepath=path)
    elif fmt == "ply":
        # Blender's PLY importer defaults to Z-up; Renda treats PLY as Y-up (like the IR loader)
        # unless the package says otherwise.
        kw = {"up_axis": "Z", "forward_axis": "Y"} if up == "Z" else {"up_axis": "Y", "forward_axis": "NEGATIVE_Z"}
        bpy.ops.wm.ply_import(filepath=path, **kw)
    elif fmt == "abc":
        bpy.ops.wm.alembic_import(filepath=path, as_background_job=False)
    else:
        raise ValueError(f"Blender can't import {fmt} directly (renda converts it to glTF first)")
    new = [o for o in bpy.data.objects if o not in before]
    return [o for o in new if o.parent is None or o.parent not in new]


def open_or_import(path: str, fmt: str, up_axis: str | None, warnings: list) -> None:
    if fmt == "blend":
        bpy.ops.wm.open_mainfile(filepath=path)
    else:
        clear_scene()
        import_file(path, fmt, up_axis, warnings)
    scene = bpy.context.scene
    if scene.world is None:
        scene.world = bpy.data.worlds.new("World")


def to_blender_space(fmt: str, source_up: str | None):
    """Map package coordinates (in the source file's convention) into Blender's Z-up world."""
    y_up_source = (source_up or ("Z" if fmt in ("blend", "stl") else "Y")) == "Y"
    if y_up_source and (fmt in CONVERTS_Y_UP or fmt == "stl"):
        return lambda v: Vector((v[0], -v[2], v[1]))
    return lambda v: Vector(v)


def mesh_objects(scene) -> list:
    return [o for o in scene.objects if o.type in {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME"}
            and o.visible_get() and not o.hide_render]


def world_bounds(scene) -> tuple[Vector, Vector]:
    deps = bpy.context.evaluated_depsgraph_get()
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    found = False
    for inst in deps.object_instances:
        ob = inst.object
        if ob.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT", "CURVES", "POINTCLOUD", "VOLUME"}:
            continue
        if ob.original.hide_render:
            continue
        mw = inst.matrix_world
        for c in ob.bound_box:
            w = mw @ Vector(c)
            lo = Vector(map(min, lo, w))
            hi = Vector(map(max, hi, w))
            found = True
    if not found:
        return Vector((-0.5,) * 3), Vector((0.5,) * 3)
    return lo, hi


def look_at_matrix(pos: Vector, target: Vector, up: Vector = Vector((0, 0, 1))) -> Matrix:
    fwd = (target - pos).normalized()
    if abs(fwd.dot(up)) > 0.999:
        up = Vector((0, 1, 0))
    m = _track(fwd, up).to_matrix().to_4x4()
    m.translation = pos
    return m


def _track(fwd: Vector, up: Vector):
    right = fwd.cross(up).normalized()
    true_up = right.cross(fwd).normalized()
    m = Matrix((right, true_up, -fwd)).transposed()
    return m.to_quaternion()
