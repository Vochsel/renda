"""Extract a scene Blender can open (.blend/.fbx/.abc/…) into a Renda .rir bundle.

blender -b --factory-startup --python extract.py -- job.json
job: {"input", "format", "output", "result"}

Evaluates the depsgraph (modifiers, geometry nodes, instancing, curves → meshes),
splits meshes per material, maps Principled BSDF → IR material, and records
lights, cameras and the world HDRI. Schema matches renda/ir/rir.py.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy  # noqa: E402
import numpy as np  # noqa: E402

from _common import open_or_import, read_job, write_result  # noqa: E402

WARN: list[str] = []


def mat_list(m) -> list:
    return [list(r) for r in m]


def linked_image(sock):
    """Image path feeding a socket directly or via Normal Map / Separate Color nodes."""
    if sock is None or not sock.is_linked:
        return None
    node = sock.links[0].from_node
    hops = 0
    while node.type in ("NORMAL_MAP", "SEPARATE_COLOR", "SEPRGB", "GAMMA", "MIX_RGB", "MIX") and hops < 3:
        nxt = next((i for i in node.inputs if i.is_linked), None)
        if nxt is None:
            return None
        node = nxt.links[0].from_node
        hops += 1
    if node.type == "TEX_IMAGE" and node.image is not None:
        return node.image
    return None


class TextureWriter:
    def __init__(self, root: Path):
        self.root = root
        (root / "textures").mkdir(parents=True, exist_ok=True)
        self.done: dict[str, str] = {}

    def save(self, image) -> str | None:
        if image is None:
            return None
        if image.name in self.done:
            return self.done[image.name]
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in image.name)
        src = bpy.path.abspath(image.filepath) if image.filepath else ""
        rel = None
        try:
            if image.packed_file is None and src and os.path.exists(src):
                ext = Path(src).suffix.lower() or ".png"
                rel = f"textures/{safe}{ext}"
                import shutil

                shutil.copyfile(src, self.root / rel)
            else:
                rel = f"textures/{safe}.png"
                image.filepath_raw = str(self.root / rel)
                image.file_format = "PNG"
                image.save()
        except Exception as e:
            WARN.append(f"extract: texture {image.name!r} not saved: {e}")
            rel = None
        self.done[image.name] = rel
        return rel


def socket_value(node, names, default):
    for n in names:
        s = node.inputs.get(n)
        if s is not None:
            v = s.default_value
            try:
                return tuple(v)
            except TypeError:
                return float(v)
    return default


def convert_material(mat, tex: TextureWriter) -> dict:
    d = {"name": mat.name, "base_color": list(mat.diffuse_color), "metallic": float(mat.metallic),
         "roughness": float(mat.roughness), "emission": [0, 0, 0], "emission_strength": 0.0, "opacity": 1.0,
         "transmission": 0.0, "ior": 1.5, "double_sided": not mat.use_backface_culling, "unlit": False}
    if not mat.use_nodes or mat.node_tree is None:
        return d
    out = next((n for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
    shader = out.inputs["Surface"].links[0].from_node if out and out.inputs["Surface"].is_linked else None
    if shader is None:
        return d
    if shader.type == "EMISSION":
        d["unlit"] = True
        d["base_color"] = list(socket_value(shader, ["Color"], (1, 1, 1, 1)))
        return d
    if shader.type != "BSDF_PRINCIPLED":
        shader = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if shader is None:
            WARN.append(f"extract: material {mat.name!r} has no Principled BSDF; using viewport colour")
            return d
    bc = socket_value(shader, ["Base Color"], (0.8, 0.8, 0.8, 1))
    alpha = float(socket_value(shader, ["Alpha"], 1.0))
    d.update({
        "base_color": [bc[0], bc[1], bc[2], alpha],
        "metallic": float(socket_value(shader, ["Metallic"], 0.0)),
        "roughness": float(socket_value(shader, ["Roughness"], 0.5)),
        "ior": float(socket_value(shader, ["IOR"], 1.5)),
        "transmission": float(socket_value(shader, ["Transmission Weight", "Transmission"], 0.0)),
        "opacity": alpha,
    })
    em = socket_value(shader, ["Emission Color", "Emission"], (0, 0, 0, 1))
    strength = float(socket_value(shader, ["Emission Strength"], 1.0))
    if max(em[:3]) > 0 and strength > 0:
        d["emission"] = list(em[:3])
        d["emission_strength"] = strength
    d["base_color_tex"] = tex.save(linked_image(shader.inputs.get("Base Color")))
    d["roughness_tex"] = tex.save(linked_image(shader.inputs.get("Roughness")))
    d["metallic_tex"] = tex.save(linked_image(shader.inputs.get("Metallic")))
    d["normal_tex"] = tex.save(linked_image(shader.inputs.get("Normal")))
    d["emission_tex"] = tex.save(linked_image(shader.inputs.get("Emission Color") or shader.inputs.get("Emission")))
    d["opacity_tex"] = tex.save(linked_image(shader.inputs.get("Alpha")))
    return d


def mesh_arrays(me):
    """Evaluated mesh → per-material (points, indices, normals, uvs), welded by (vertex, normal, uv)."""
    me.calc_loop_triangles()
    nt = len(me.loop_triangles)
    if nt == 0:
        return {}
    tri_loops = np.empty(nt * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("loops", tri_loops)
    tri_mat = np.empty(nt, dtype=np.int64)
    me.loop_triangles.foreach_get("material_index", tri_mat)
    nl = len(me.loops)
    loop_vert = np.empty(nl, dtype=np.int64)
    me.loops.foreach_get("vertex_index", loop_vert)
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    normals = np.empty(nl * 3, dtype=np.float32)
    if hasattr(me, "corner_normals") and len(me.corner_normals) == nl:  # 4.1+
        me.corner_normals.foreach_get("vector", normals)
    else:  # 4.0 and older: split normals must be computed explicitly
        if hasattr(me, "calc_normals_split"):
            me.calc_normals_split()
        me.loops.foreach_get("normal", normals)
    normals = normals.reshape(-1, 3)
    uvs = None
    if me.uv_layers.active is not None:
        uvs = np.empty(nl * 2, dtype=np.float32)
        me.uv_layers.active.data.foreach_get("uv", uvs)
        uvs = uvs.reshape(-1, 2)
    out = {}
    for mi in np.unique(tri_mat):
        loops = tri_loops.reshape(-1, 3)[tri_mat == mi].reshape(-1)
        key = np.concatenate([loop_vert[loops, None].astype(np.float32), np.round(normals[loops], 4)]
                             + ([np.round(uvs[loops], 5)] if uvs is not None else []), axis=1)
        uniq, first, inverse = np.unique(key, axis=0, return_index=True, return_inverse=True)
        sel = loops[first]
        out[int(mi)] = (co[loop_vert[sel]], inverse.reshape(-1, 3).astype(np.int32), normals[sel],
                        uvs[sel] if uvs is not None else None)
    return out


def camera_fov_y(cam, scene) -> tuple[float, float | None]:
    data = cam.data
    aspect = scene.render.resolution_x * scene.render.pixel_aspect_x / max(scene.render.resolution_y * scene.render.pixel_aspect_y, 1)
    if data.type == "ORTHO":
        return 40.0, data.ortho_scale / aspect if aspect >= 1 else data.ortho_scale
    fit = data.sensor_fit
    if fit == "VERTICAL" or (fit == "AUTO" and aspect < 1):
        sensor = data.sensor_height if fit == "VERTICAL" else data.sensor_width
        return math.degrees(2 * math.atan(sensor / (2 * data.lens))), None
    fov_x = 2 * math.atan(data.sensor_width / (2 * data.lens))
    return math.degrees(2 * math.atan(math.tan(fov_x / 2) / aspect)), None


def main() -> None:
    job = read_job()
    open_or_import(job["input"], job["format"], None, WARN)
    scene = bpy.context.scene
    root = Path(job["output"])
    (root / "meshes").mkdir(parents=True, exist_ok=True)
    tex = TextureWriter(root)
    deps = bpy.context.evaluated_depsgraph_get()

    materials: dict[str, dict] = {}
    meshes, lights, cameras = [], [], []
    count = 0
    for inst in deps.object_instances:
        ob = inst.object
        if ob.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
            if ob.original.hide_render:
                continue
            try:
                me = ob.to_mesh()
            except RuntimeError:
                continue
            if me is None:
                continue
            slots = [s.material for s in ob.material_slots]
            for mi, (pts, idx, nrm, uv) in mesh_arrays(me).items():
                mat = slots[mi] if mi < len(slots) else None
                mname = None
                if mat is not None:
                    if mat.name not in materials:
                        materials[mat.name] = convert_material(mat, tex)
                    mname = mat.name
                rel = f"meshes/{count:05d}.npz"
                arrays = {"points": pts, "indices": idx, "normals": nrm}
                if uv is not None:
                    arrays["uvs"] = uv
                np.savez_compressed(root / rel, **arrays)
                meshes.append({"name": f"{ob.name}.{mi}" if mi else ob.name, "file": rel,
                               "transform": mat_list(inst.matrix_world), "material": mname})
                count += 1
            ob.to_mesh_clear()
        elif ob.type == "LIGHT" and not inst.is_instance:
            L = ob.data
            t = {"SUN": "sun", "POINT": "point", "SPOT": "spot", "AREA": "area"}[L.type]
            d = {"name": ob.name, "type": t, "color": list(L.color), "intensity": float(L.energy),
                 "transform": mat_list(ob.matrix_world), "angle_deg": 45.0, "size": [1.0, 1.0],
                 "radius": float(getattr(L, "shadow_soft_size", 0.05)), "texture": None}
            if t == "spot":
                d["angle_deg"] = math.degrees(L.spot_size)
            if t == "area":
                d["size"] = [float(L.size), float(L.size_y if L.shape in ("RECTANGLE", "ELLIPSE") else L.size)]
            lights.append(d)
        elif ob.type == "CAMERA" and not inst.is_instance:
            fov, ortho = camera_fov_y(ob, scene)
            cameras.append({"name": ob.name, "transform": mat_list(ob.matrix_world), "fov_y_deg": fov,
                            "ortho_height": ortho, "near": ob.data.clip_start, "far": ob.data.clip_end,
                            "aspect": scene.render.resolution_x / max(scene.render.resolution_y, 1)})
    # active camera first so "scene" mode without a name picks it
    if scene.camera is not None:
        cameras.sort(key=lambda c: c["name"] != scene.camera.name)

    world = scene.world
    if world is not None and world.use_nodes and world.node_tree:
        env = next((n for n in world.node_tree.nodes if n.type == "TEX_ENVIRONMENT" and n.image), None)
        bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
        if env is not None:
            strength = float(bg.inputs["Strength"].default_value) if bg else 1.0
            lights.append({"name": "world", "type": "dome", "color": [1, 1, 1], "intensity": strength,
                           "transform": mat_list(np.eye(4)), "angle_deg": 45.0, "size": [1, 1], "radius": 0,
                           "texture": tex.save(env.image)})

    doc = {
        "rir": 1, "upAxis": "Z", "unitScale": scene.unit_settings.scale_length or 1.0,
        "sourceFormat": job["format"], "frameRange": [scene.frame_start, scene.frame_end],
        "fps": scene.render.fps / scene.render.fps_base, "meta": {"warnings": WARN, "blender": bpy.app.version_string},
        "materials": materials, "meshes": meshes, "lights": lights, "cameras": cameras,
    }
    (root / "stage.json").write_text(json.dumps(doc))
    write_result(job, {"output": str(root), "warnings": WARN, "meshes": len(meshes)})


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
