"""Tiny scene fixtures generated in code (no binaries in the repo)."""

from __future__ import annotations

import json
from pathlib import Path

import trimesh


def threejs_cube_scene() -> dict:
    """A hand-written three.js Object/Scene JSON (format 4.6): indexed cube + plane, camera, lights."""
    pos = [-0.5, -0.5, 0.5, 0.5, -0.5, 0.5, 0.5, 0.5, 0.5, -0.5, 0.5, 0.5,
           -0.5, -0.5, -0.5, 0.5, -0.5, -0.5, 0.5, 0.5, -0.5, -0.5, 0.5, -0.5]
    idx = [0, 1, 2, 0, 2, 3, 1, 5, 6, 1, 6, 2, 5, 4, 7, 5, 7, 6, 4, 0, 3, 4, 3, 7, 3, 2, 6, 3, 6, 7, 4, 5, 1, 4, 1, 0]
    ident = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    cam = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0.5, 4, 1]  # column-major: translate (0, 0.5, 4)
    plane_m = [1, 0, 0, 0, 0, 0, -1, 0, 0, 1, 0, 0, 0, -0.5, 0, 1]  # rotate -90° about X, move down
    return {
        "metadata": {"version": 4.6, "type": "Object", "generator": "Object3D.toJSON"},
        "geometries": [
            {"uuid": "geo-cube", "type": "BufferGeometry", "data": {
                "attributes": {"position": {"itemSize": 3, "type": "Float32Array", "array": pos, "normalized": False}},
                "index": {"type": "Uint16Array", "array": idx},
                "groups": [{"start": 0, "count": 18, "materialIndex": 0}, {"start": 18, "count": 18, "materialIndex": 1}],
            }},
            {"uuid": "geo-plane", "type": "PlaneGeometry", "width": 6, "height": 6},
        ],
        "materials": [
            {"uuid": "mat-red", "type": "MeshStandardMaterial", "name": "Red", "color": 0xCC2222, "roughness": 0.4, "metalness": 0.0},
            {"uuid": "mat-blue", "type": "MeshPhysicalMaterial", "name": "Blue", "color": 0x2244CC, "roughness": 0.2,
             "metalness": 0.5, "transmission": 0.0, "ior": 1.5},
            {"uuid": "mat-floor", "type": "MeshLambertMaterial", "name": "Floor", "color": 0xDDDDDD},
        ],
        "object": {
            "uuid": "scene", "type": "Scene", "name": "Scene", "matrix": ident, "background": 0x101010,
            "children": [
                {"uuid": "cube", "type": "Mesh", "name": "Cube", "matrix": ident, "geometry": "geo-cube",
                 "material": ["mat-red", "mat-blue"]},
                {"uuid": "floor", "type": "Mesh", "name": "Floor", "matrix": plane_m, "geometry": "geo-plane", "material": "mat-floor"},
                {"uuid": "cam", "type": "PerspectiveCamera", "name": "MainCam", "matrix": cam, "fov": 45, "aspect": 1.333,
                 "near": 0.1, "far": 100, "zoom": 1},
                {"uuid": "sun", "type": "DirectionalLight", "name": "Sun", "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 3, 5, 2, 1],
                 "color": 0xFFFFFF, "intensity": 2.5, "target": "cube"},
                {"uuid": "amb", "type": "AmbientLight", "name": "Amb", "matrix": ident, "color": 0xFFFFFF, "intensity": 0.3},
            ],
        },
    }


def write_threejs(dir: Path) -> Path:
    p = dir / "scene.json"
    p.write_text(json.dumps(threejs_cube_scene()))
    return p


def write_mesh_files(dir: Path) -> dict[str, Path]:
    box = trimesh.creation.box(extents=(1.0, 2.0, 3.0))
    out = {}
    for ext in ("glb", "stl", "obj", "ply"):
        p = dir / f"box.{ext}"
        box.export(p)
        out[ext] = p
    return out
