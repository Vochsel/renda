"""OBJ, STL, PLY, COLLADA, 3MF and glTF via trimesh, plus glTF cameras/lights."""

from __future__ import annotations

import json
import math
import struct
import tempfile
from pathlib import Path

import numpy as np
import trimesh

from ..stage import Camera, Light, Material, Mesh, Stage, compose, identity, auto_smooth

# Source conventions when the file doesn't say.
DEFAULT_UP = {"stl": "Z", "3mf": "Z", "ply": "Y", "obj": "Y", "dae": "Y", "gltf": "Y"}


def _is_glb(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(4) == b"glTF"


def load(path: Path, fmt: str) -> Stage:
    tex_dir = Path(tempfile.mkdtemp(prefix="renda-tex-"))
    # Content-addressed assets have no extension, so tell .glb from .gltf by magic bytes.
    file_type = {"gltf": "glb" if _is_glb(path) else "gltf"}.get(fmt, fmt)
    scene = trimesh.load(str(path), file_type=file_type, force="scene", process=False)
    stage = Stage(up_axis=DEFAULT_UP.get(fmt, "Y"), source_format=fmt, base_dir=str(path.parent))

    if fmt == "dae":
        up = _collada_up(path)
        if up:
            stage.up_axis = up

    mat_ids: dict[int, str] = {}
    for node in scene.graph.nodes_geometry:
        matrix, geom_name = scene.graph[node]
        geom = scene.geometry[geom_name]
        if not isinstance(geom, trimesh.Trimesh) or len(geom.faces) == 0:
            continue
        uvs = None
        colors = None
        mat_name = None
        visual = geom.visual
        if isinstance(visual, trimesh.visual.TextureVisuals):
            if visual.uv is not None and len(visual.uv) == len(geom.vertices):
                uvs = np.asarray(visual.uv, dtype=np.float32)
            if visual.material is not None:
                key = id(visual.material)
                if key not in mat_ids:
                    m = _convert_material(visual.material, tex_dir, len(mat_ids))
                    m.name = _unique(m.name, stage.materials)
                    stage.materials[m.name] = m
                    mat_ids[key] = m.name
                mat_name = mat_ids[key]
        elif isinstance(visual, trimesh.visual.ColorVisuals):
            if visual.kind == "vertex":
                colors = np.asarray(visual.vertex_colors, dtype=np.float32)[:, :4] / 255.0
            elif visual.kind == "face":
                fc = np.asarray(visual.face_colors[0], dtype=np.float32) / 255.0
                name = _unique(f"color_{len(stage.materials)}", stage.materials)
                stage.materials[name] = Material(name=name, base_color=tuple(_srgb_lin(fc[:3])) + (float(fc[3]),))
                mat_name = name
        # normals stored in the file land in trimesh's cache; otherwise compute our own
        # (trimesh's computation needs scipy, which we don't depend on)
        if fmt == "stl":  # STL stores every triangle separately; weld so smoothing can see neighbours
            geom.merge_vertices()
        points, faces = geom.vertices, geom.faces
        cached = geom._cache.cache.get("vertex_normals")
        if cached is not None and len(cached) == len(points) and fmt != "stl":
            normals = np.asarray(cached, dtype=np.float32)
        else:
            points, faces, normals, uvs = auto_smooth(points, faces, 30.0, uvs)
            if colors is not None and len(colors) != len(points):
                colors = colors[np.asarray(geom.faces).reshape(-1)]
        stage.meshes.append(Mesh(
            name=str(node), points=points, indices=faces, normals=normals, uvs=uvs,
            colors=colors, transform=np.asarray(matrix, dtype=np.float64), material=mat_name,
        ))

    if fmt == "gltf":
        _gltf_cameras_lights(path, stage)
    elif fmt == "obj":
        _obj_object_names(path, stage)
    elif fmt == "dae":
        _collada_names(path, stage)
    return stage


def _obj_object_names(path: Path, stage: Stage) -> None:
    """trimesh splits OBJs per material and names the parts after the material; put the `o`/`g`
    object names back (when a material belongs to one object) so overrides can target objects."""
    users: dict[str, set[str]] = {}
    current = None
    try:
        with open(path, errors="replace") as f:
            for line in f:
                if line.startswith(("o ", "g ")):
                    current = line[2:].strip() or current
                elif line.startswith("usemtl "):
                    users.setdefault(line[7:].strip(), set()).add(current or "")
    except OSError:
        return
    for mesh in stage.meshes:
        objs = users.get(mesh.material or "") or users.get(mesh.name) or set()
        objs.discard("")
        if len(objs) == 1:
            mesh.name = next(iter(objs))


def _collada_names(path: Path, stage: Stage) -> None:
    """trimesh's COLLADA reader drops material names; recover them (and node names) with pycollada."""
    try:
        import collada
    except ImportError:
        return
    try:
        dae = collada.Collada(str(path), ignore=[collada.common.DaeUnsupportedError, collada.common.DaeBrokenRefError])
    except Exception:
        return
    geom_mat: dict[str, str] = {}
    geom_node: dict[str, str] = {}

    def walk(node, owner):
        for ch in getattr(node, "children", []):
            if isinstance(ch, collada.scene.GeometryNode):
                gid = ch.geometry.id
                geom_node.setdefault(gid, owner)
                for mn in ch.materials:
                    if mn.target is not None:
                        geom_mat.setdefault(gid, mn.target.name or mn.target.id)
            elif isinstance(ch, collada.scene.Node):
                walk(ch, ch.id or owner)

    if dae.scene is not None:
        for n in dae.scene.nodes:
            walk(n, n.id)
    renames: dict[str, str] = {}
    for mesh in stage.meshes:
        gid = mesh.name
        if gid in geom_node and geom_node[gid]:
            mesh.name = geom_node[gid]
        target = geom_mat.get(gid)
        if target and mesh.material and mesh.material.startswith("material_") and mesh.material in stage.materials:
            renames.setdefault(mesh.material, target)
    for old, new in renames.items():
        if new in stage.materials and new != old:
            continue
        mat = stage.materials.pop(old)
        mat.name = new
        stage.materials[new] = mat
        for mesh in stage.meshes:
            if mesh.material == old:
                mesh.material = new


def _unique(name: str, existing: dict) -> str:
    name = name or "material"
    if name not in existing:
        return name
    i = 1
    while f"{name}.{i:03d}" in existing:
        i += 1
    return f"{name}.{i:03d}"


def _srgb_lin(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _save_image(img, tex_dir: Path, name: str) -> str | None:
    if img is None:
        return None
    try:
        p = tex_dir / f"{name}.png"
        img.save(p)
        return str(p)
    except Exception:
        return None


def _convert_material(mat, tex_dir: Path, idx: int) -> Material:
    name = getattr(mat, "name", None) or f"material_{idx}"
    safe = "".join(ch if ch.isalnum() else "_" for ch in name)[:40] + f"_{idx}"
    if isinstance(mat, trimesh.visual.material.PBRMaterial):
        bc = np.asarray(mat.baseColorFactor if mat.baseColorFactor is not None else [255, 255, 255, 255], dtype=np.float64)
        if bc.max() > 1.0:  # trimesh stores factors as uint8; glTF factors are already linear
            bc = bc / 255.0
        if len(bc) < 4:
            bc = np.concatenate([bc, [1.0]])
        em = np.asarray(mat.emissiveFactor if mat.emissiveFactor is not None else [0, 0, 0], dtype=np.float64)
        if em.max() > 1.0:
            em = em / 255.0
        return Material(
            name=name,
            base_color=tuple(float(x) for x in bc),
            metallic=float(mat.metallicFactor if mat.metallicFactor is not None else 1.0),
            roughness=float(mat.roughnessFactor if mat.roughnessFactor is not None else 1.0),
            emission=tuple(float(x) for x in em[:3]),
            emission_strength=1.0 if em.max() > 0 else 0.0,
            opacity=float(bc[3]),
            base_color_tex=_save_image(mat.baseColorTexture, tex_dir, f"{safe}_basecolor"),
            metallic_roughness_tex=_save_image(mat.metallicRoughnessTexture, tex_dir, f"{safe}_mr"),
            normal_tex=_save_image(mat.normalTexture, tex_dir, f"{safe}_normal"),
            emission_tex=_save_image(mat.emissiveTexture, tex_dir, f"{safe}_emissive"),
        )
    # SimpleMaterial (OBJ/MTL): diffuse in 0..255 sRGB, glossiness → roughness
    diffuse = np.asarray(getattr(mat, "diffuse", [204, 204, 204, 255]), dtype=np.float64) / 255.0
    gloss = float(getattr(mat, "glossiness", None) or 0.0)
    rough = float(np.clip(1.0 - math.sqrt(min(gloss, 1000) / 1000.0), 0.05, 1.0)) if gloss else 0.5
    return Material(
        name=name,
        base_color=tuple(float(x) for x in _srgb_lin(diffuse[:3])) + (float(diffuse[3]) if len(diffuse) > 3 else 1.0,),
        roughness=rough,
        base_color_tex=_save_image(getattr(mat, "image", None), tex_dir, f"{safe}_diffuse"),
    )


def _collada_up(path: Path) -> str | None:
    try:
        head = path.read_text(errors="ignore")[:4000]
    except OSError:
        return None
    if "<up_axis>Z_UP</up_axis>" in head:
        return "Z"
    if "<up_axis>Y_UP</up_axis>" in head:
        return "Y"
    return None


# -- glTF cameras + KHR_lights_punctual (trimesh drops them) ----------------------


def read_gltf_json(path: Path) -> dict:
    data = path.read_bytes()
    if data[:4] == b"glTF":
        length = struct.unpack_from("<I", data, 12)[0]
        return json.loads(data[20 : 20 + length])
    return json.loads(data)


def _node_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T  # glTF is column-major
    return compose(node.get("translation", (0, 0, 0)), node.get("rotation", (0, 0, 0, 1)), node.get("scale", (1, 1, 1)))


def _gltf_cameras_lights(path: Path, stage: Stage) -> None:
    try:
        doc = read_gltf_json(path)
    except Exception:
        return
    nodes = doc.get("nodes", [])
    cams = doc.get("cameras", [])
    lights = doc.get("extensions", {}).get("KHR_lights_punctual", {}).get("lights", [])
    scene_idx = doc.get("scene", 0)
    scenes = doc.get("scenes", [{"nodes": list(range(len(nodes)))}])
    roots = scenes[scene_idx]["nodes"] if scenes else []

    def walk(i: int, parent: np.ndarray) -> None:
        node = nodes[i]
        world = parent @ _node_matrix(node)
        if "camera" in node and node["camera"] < len(cams):
            c = cams[node["camera"]]
            name = node.get("name") or c.get("name") or f"camera_{node['camera']}"
            if c.get("type") == "orthographic":
                o = c.get("orthographic", {})
                stage.cameras.append(Camera(name, world, 40.0, ortho_height=2 * o.get("ymag", 1.0), near=o.get("znear", 0.01), far=o.get("zfar", 1000)))
            else:
                p = c.get("perspective", {})
                stage.cameras.append(Camera(name, world, math.degrees(p.get("yfov", 0.7)), near=p.get("znear", 0.01),
                                            far=p.get("zfar", 10000), aspect=p.get("aspectRatio")))
        ext = node.get("extensions", {}).get("KHR_lights_punctual")
        if ext is not None and ext.get("light", -1) < len(lights):
            ld = lights[ext["light"]]
            t = {"directional": "sun", "point": "point", "spot": "spot"}.get(ld.get("type"), "point")
            inten = float(ld.get("intensity", 1.0))
            # glTF: lux for sun, candela for point/spot → Blender-style watts (683 lm/W, 4π sr),
            # matching Blender's importer. Exporters that follow the spec (Blender writes a 3 W/m²
            # sun as 2049 lx) produce large sun values; three.js-style files use ~1–10 "artist"
            # units, which we keep as-is rather than turning them into near-black.
            if t != "sun":
                inten = inten * 4 * math.pi / 683.0
            elif inten > 50:
                inten = inten / 683.0
            angle = math.degrees(2 * ld.get("spot", {}).get("outerConeAngle", math.pi / 4))
            stage.lights.append(Light(node.get("name") or ld.get("name") or t, t, tuple(ld.get("color", (1, 1, 1))),
                                      inten, world, angle_deg=angle))
        for ch in node.get("children", []):
            walk(ch, world)

    for r in roots:
        walk(r, identity())
