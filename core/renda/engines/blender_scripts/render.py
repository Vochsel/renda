"""Render a Renda job inside Blender.  blender -b --factory-startup --python render.py -- job.json

The job JSON is built by renda/engines/blender.py. Writes frame_NNNN.png|exr (+ pass
files) into job["out_dir"] and a result JSON with warnings to job["result"].
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy  # noqa: E402
from mathutils import Matrix, Vector  # noqa: E402

from _common import (  # noqa: E402
    import_file, look_at_matrix, open_or_import, read_job, to_blender_space, world_bounds, write_result,
)
from _match import basename, matches  # noqa: E402

WARN: list[str] = []


# -- engine settings -------------------------------------------------------------


def eevee_id() -> str:
    items = {i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    return "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in items else "BLENDER_EEVEE"


def setup_gpu(scene) -> str:
    """Enable OptiX (else CUDA/HIP/Metal) devices for Cycles. Returns the backend used or 'CPU'."""
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except KeyError:
        return "CPU"
    for backend in ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"):
        try:
            prefs.compute_device_type = backend
        except TypeError:
            continue
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type == backend]
        if gpus:
            for d in prefs.devices:
                d.use = d.type == backend
            scene.cycles.device = "GPU"
            return backend
    return "CPU"


def configure_engine(scene, job: dict) -> None:
    eng = job["engine"]
    r = scene.render
    r.resolution_x, r.resolution_y = job["resolution"]
    r.resolution_percentage = 100
    r.film_transparent = bool(job["transparent_film"])
    r.use_persistent_data = True  # keep BVH/textures between frames: big win for animations
    if eng == "cycles":
        r.engine = "CYCLES"
        c = scene.cycles
        backend = setup_gpu(scene) if job.get("gpu") else "CPU"
        if job.get("gpu") and backend == "CPU":
            WARN.append("blender: no GPU device found, Cycles rendered on CPU")
        c.samples = int(job["samples"])
        c.use_adaptive_sampling = True
        c.adaptive_threshold = float(job["adaptive_threshold"])
        c.seed = int(job.get("seed", 0))
        c.use_denoising = bool(job["denoise"])
        if job["denoise"]:
            # OIDN everywhere: the OptiX denoiser needs driver pieces some cloud GPUs (e.g. Modal L4)
            # don't expose, and OIDN 2 runs on CUDA GPUs anyway with near-identical quality.
            c.denoiser = "OPENIMAGEDENOISE"
            if hasattr(c, "denoising_use_gpu"):
                c.denoising_use_gpu = backend != "CPU"
        # sensible light-path limits; caustics off unless final quality
        c.max_bounces = 12 if job["samples"] >= 256 else 8
        c.caustics_reflective = c.caustics_refractive = job["samples"] >= 256
        c.use_auto_tile = True
        c.tile_size = 2048
    elif eng == "eevee":
        r.engine = eevee_id()
        scene.eevee.taa_render_samples = int(job["samples"])
    elif eng == "workbench":
        r.engine = "BLENDER_WORKBENCH"
        aa = min(("5", "8", "11", "16", "32"), key=lambda v: abs(int(v) - int(job["samples"])))
        scene.display.render_aa = aa
        scene.display.shading.light = "STUDIO"
        scene.display.shading.color_type = "MATERIAL"
        scene.display.shading.show_shadows = True
        scene.display.shading.show_cavity = True
    else:
        raise ValueError(eng)
    if eng == "workbench" and (job.get("lens") or {}).get("fstop"):
        scene.display.shading.use_dof = True

    aa = job.get("antialias")
    if aa:
        if eng == "cycles":
            c.pixel_filter_type = {"gaussian": "GAUSSIAN", "box": "BOX", "blackman-harris": "BLACKMAN_HARRIS",
                                   "mitchell": "GAUSSIAN"}[aa["filter"]]
            c.filter_width = float(aa["width"])
        else:
            r.filter_size = float(aa["width"])

    mb = job.get("motion_blur")
    if mb and not mb.get("emulate"):
        pos = mb["position"].upper()
        for owner in (r, scene.eevee, getattr(scene, "cycles", None)):
            if owner is None:
                continue
            for attr, val in (("use_motion_blur", True), ("motion_blur_shutter", float(mb["shutter"])),
                              ("motion_blur_position", pos)):
                if hasattr(owner, attr):
                    try:
                        setattr(owner, attr, val)
                    except (TypeError, ValueError):
                        pass
        if eng == "eevee" and hasattr(scene.eevee, "motion_blur_steps"):
            scene.eevee.motion_blur_steps = 2  # smoother arcs for camera turntables

    # Tone mapping: AgX by default (nicer highlights than Standard). ACES isn't a built-in view transform.
    vs = scene.view_settings
    wanted = {"agx": ("AgX", "Filmic"), "filmic": ("Filmic", "AgX"), "standard": ("Standard",),
              "neutral": ("Khronos PBR Neutral", "Standard"), "aces": ("Filmic", "AgX")}[job.get("tonemap", "agx")]
    for name in wanted:
        try:
            vs.view_transform = name
            break
        except TypeError:
            continue
    vs.exposure = float(job.get("exposure", 0.0))


# -- scene edits -----------------------------------------------------------------


def apply_unit_scale(scene, s: float) -> None:
    if abs(s - 1.0) < 1e-12:
        return
    for ob in scene.objects:
        if ob.parent is None:
            ob.matrix_world = Matrix.Scale(s, 4) @ ob.matrix_world


def add_includes(job: dict, conv) -> None:
    for inc in job.get("includes", []):
        roots = import_file(inc["path"], inc["format"], job.get("source_up"), WARN)
        m = Matrix(inc["matrix"])
        # transform authored in package (source) coordinates
        t = Vector(conv(m.translation[:]))
        rot_scale = m.to_3x3()
        basis = Matrix(((1, 0, 0), (0, 0, -1), (0, 1, 0))) if conv((0, 1, 0)) == Vector((0, 0, 1)) else Matrix.Identity(3)
        rs = basis @ rot_scale @ basis.inverted()
        mw = Matrix.Translation(t) @ rs.to_4x4()
        for ob in roots:
            ob.matrix_world = mw @ ob.matrix_world


def principled(mat):
    if not mat.use_nodes or mat.node_tree is None:
        mat.use_nodes = True
    for n in mat.node_tree.nodes:
        if n.type == "BSDF_PRINCIPLED":
            return n
    return None


def _set_input(node, names, value, links) -> None:
    for name in names:
        sock = node.inputs.get(name)
        if sock is None:
            continue
        for link in list(sock.links):
            links.remove(link)
        sock.default_value = value
        return


def _mesh_objects(scene):
    return [ob for ob in scene.objects if ob.type == "MESH"]


# Same neutral "clay" as the IR's default Material, so material-less files (STL, Alembic, PLY scans
# without colours) look alike in every engine.
CLAY = (0.8, 0.8, 0.8, 1.0)


def _clay_material(name: str):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
        bsdf = principled(mat)
        bsdf.inputs["Base Color"].default_value = CLAY
        bsdf.inputs["Roughness"].default_value = 0.5
    return mat


def _color_attribute_name(ob) -> str | None:
    attrs = getattr(ob.data, "color_attributes", None)
    if not attrs or len(attrs) == 0:
        return None
    active = getattr(attrs, "active_color", None)
    return (active or attrs[0]).name


def _ensure_materials(scene) -> None:
    """Objects without materials get one (clay) so "*" and object-scoped overrides reach them."""
    for ob in _mesh_objects(scene):
        if len(ob.material_slots) == 0:
            layer = _color_attribute_name(ob)
            ob.data.materials.append(_clay_material(f"renda_default:{layer}" if layer else "renda_default"))


def _socket(sockets, identifier):
    return next(s for s in sockets if s.identifier == identifier)


def wire_vertex_colors(scene) -> None:
    """Show vertex colours on meshes that arrived without materials (PLY scans, Alembic Cd): multiply the
    colour attribute into the clay Base Color. Only Renda's own default materials are touched — authored
    materials (.blend, FBX, glTF) decide for themselves whether colour attributes mean colour."""
    users: dict = {}
    for ob in _mesh_objects(scene):
        for slot in ob.material_slots:
            if slot.material is not None:
                users.setdefault(slot.material.name, []).append(_color_attribute_name(ob))
    for name, attr_names in users.items():
        if not name.startswith("renda_default:"):
            continue
        layer = attr_names[0]
        if layer is None or any(a != layer for a in attr_names):
            continue
        mat = bpy.data.materials[name]
        bsdf = principled(mat)
        base = bsdf.inputs.get("Base Color") if bsdf else None
        if base is None or base.is_linked:
            continue
        nt = mat.node_tree
        vc = nt.nodes.new("ShaderNodeVertexColor")
        vc.layer_name = layer
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.inputs["Factor"].default_value = 1.0
        _socket(mix.inputs, "B_Color").default_value = tuple(base.default_value)
        nt.links.new(vc.outputs["Color"], _socket(mix.inputs, "A_Color"))
        nt.links.new(_socket(mix.outputs, "Result_Color"), base)


def scoped_materials(scene, object_pattern, material_ok, tag: str) -> dict:
    """{original material name: material to edit}. With an object pattern, materials shared with
    non-matching objects are copied for the matching ones (mirrors renda.textures._scope_materials)."""
    users: dict = {}
    for ob in _mesh_objects(scene):
        for slot in ob.material_slots:
            if slot.material is not None and material_ok(slot.material):
                users.setdefault(slot.material.name, []).append((ob, slot))
    edit = {}
    for name, uses in users.items():
        hit = [(ob, s) for ob, s in uses if matches(object_pattern, ob.name)]
        if not hit:
            continue
        if object_pattern is None or len(hit) == len(uses):
            edit[name] = name
            continue
        clone = bpy.data.materials.get(f"{name}@{tag}") or bpy.data.materials[name].copy()
        clone.name = f"{name}@{tag}"
        for ob, s in hit:
            if s.link == "OBJECT":
                s.material = clone
            else:  # mesh data may be shared with non-matching objects: give this object its own slot link
                s.link = "OBJECT"
                s.material = clone
        edit[name] = clone.name
    return edit


def apply_material_overrides(scene, overrides) -> None:
    """`overrides`: list of {"key", "object", ...values} in application order ("*" first). A dict
    (older jobs) means exact names + "*"."""
    if not overrides:
        return
    if isinstance(overrides, dict):
        overrides = [{"key": k, **v} for k, v in sorted(overrides.items(), key=lambda kv: kv[0] != "*")]
    _ensure_materials(scene)
    for i, ov in enumerate(overrides):
        key = ov["key"]
        edit = scoped_materials(scene, ov.get("object"), lambda m, k=key: k == m.name or matches(k, m.name), f"mo{i}")
        if not edit and key != "*":
            WARN.append(f"blender: materialOverrides[{key!r}] matched no materials")
        for name in edit.values():
            mat = bpy.data.materials[name]
            node = principled(mat)
            if node is None:
                WARN.append(f"blender: material {mat.name!r} has no Principled BSDF; override skipped")
                continue
            links = mat.node_tree.links
            if "baseColor" in ov:
                c = ov["baseColor"]
                _set_input(node, ["Base Color"], (c[0], c[1], c[2], 1.0), links)
                mat.diffuse_color = (c[0], c[1], c[2], 1.0)
            if "metallic" in ov:
                _set_input(node, ["Metallic"], ov["metallic"], links)
            if "roughness" in ov:
                _set_input(node, ["Roughness"], ov["roughness"], links)
            if "emission" in ov:
                c = ov["emission"]
                _set_input(node, ["Emission Color", "Emission"], (c[0], c[1], c[2], 1.0), links)
                _set_input(node, ["Emission Strength"], ov.get("emissionStrength", 1.0), links)
            elif "emissionStrength" in ov:
                _set_input(node, ["Emission Strength"], ov["emissionStrength"], links)
            if "opacity" in ov:
                _set_input(node, ["Alpha"], ov["opacity"], links)
            if "transmission" in ov:
                _set_input(node, ["Transmission Weight", "Transmission"], ov["transmission"], links)
            if "ior" in ov:
                _set_input(node, ["IOR"], ov["ior"], links)


# -- texture overrides -------------------------------------------------------------

_SLOT_SOCKETS = {
    "baseColor": ["Base Color"],
    "normal": ["Normal"],
    "roughness": ["Roughness"],
    "metallic": ["Metallic"],
    "emission": ["Emission Color", "Emission"],
    "opacity": ["Alpha"],
}
COLOR_SLOTS = {"baseColor", "emission"}


def _upstream_images(sock, depth: int = 4) -> list:
    """Image Texture nodes feeding a socket (through normal-map/mix/separate nodes)."""
    out, frontier = [], [sock]
    for _ in range(depth):
        nxt = []
        for s in frontier:
            for link in s.links:
                n = link.from_node
                if n.type == "TEX_IMAGE":
                    out.append(n)
                else:
                    nxt += list(n.inputs)
        frontier = nxt
    return out


def slot_texture_names(mat) -> dict:
    """{slot: [image file names]} for a Principled-based material."""
    node = principled(mat)
    if node is None:
        return {}
    names = {}
    for slot, socks in _SLOT_SOCKETS.items():
        for sname in socks:
            sock = node.inputs.get(sname)
            if sock is not None:
                for img_node in _upstream_images(sock):
                    if img_node.image is not None:
                        names.setdefault(slot, []).append(basename(img_node.image.filepath) or img_node.image.name)
    return names


def texture_targets(scene, ov: dict) -> list:
    """[(material name, slot)] for one override; uses the precomputed IR match when given."""
    if ov.get("targets") is not None:
        return [tuple(t) for t in ov["targets"] if t[0] in bpy.data.materials]
    m = ov.get("match") or {}
    cands = {}

    def ok(mat) -> bool:
        if not matches(m.get("material"), mat.name):
            return False
        if m.get("texture") is None:
            cands[mat.name] = [ov.get("slot") or "baseColor"]
            return True
        hits = [slot for slot, names in slot_texture_names(mat).items() if any(matches(m["texture"], n) for n in names)]
        if not hits:
            return False
        cands[mat.name] = [ov["slot"]] if ov.get("slot") else hits
        return True

    edit = scoped_materials(scene, m.get("object"), ok, f"tex{ov['index']}")
    return [(edit[orig], s) for orig, slots in cands.items() if orig in edit for s in slots]


def _image(path: str, colorspace: str):
    img = bpy.data.images.load(path, check_existing=True)
    try:
        img.colorspace_settings.name = "sRGB" if colorspace == "srgb" else "Non-Color"
    except TypeError:
        pass
    return img


def setup_texture_overrides(scene, overrides: list, first_frame: int) -> list:
    """Wire an Image Texture node (with a Mapping node for the UV transform) into each target slot.
    Returns [(override, [image nodes])] so the frame loop can swap images for video textures."""
    _ensure_materials(scene)
    wired = []
    for ov in overrides:
        targets = texture_targets(scene, ov)
        if not targets:
            WARN.append(f"blender: textureOverrides[{ov['index']}] matched no material texture slots")
            continue
        nodes_for = []
        for mat_name, slot in targets:
            mat = bpy.data.materials[mat_name]
            bsdf = principled(mat)
            if bsdf is None:
                WARN.append(f"blender: material {mat_name!r} has no Principled BSDF; texture override skipped")
                continue
            nt = mat.node_tree
            cs = ov.get("colorSpace") or "auto"
            cs = ("srgb" if slot in COLOR_SLOTS else "linear") if cs == "auto" else cs
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = _image(ov["frames"][str(first_frame)], cs)
            tex.interpolation = "Linear"
            tex.extension = "REPEAT"
            sx, sy, ox, oy, rot = ov.get("uv") or (1, 1, 0, 0, 0)
            no_uvs = _material_lacks_uvs(scene, mat_name)
            if no_uvs:  # STL/scans: box-project from generated coordinates instead of UVs
                tex.projection = "BOX"
                tex.projection_blend = 0.2
            if no_uvs or (sx, sy, ox, oy, rot) != (1, 1, 0, 0, 0):
                coord = nt.nodes.new("ShaderNodeTexCoord")
                mapping = nt.nodes.new("ShaderNodeMapping")
                mapping.vector_type = "POINT"  # scale → rotate → translate, like KHR_texture_transform
                mapping.inputs["Location"].default_value = (ox, oy, 0)
                mapping.inputs["Rotation"].default_value = (0, 0, math.radians(rot))
                mapping.inputs["Scale"].default_value = (sx, sy, 1 if not no_uvs else sx)
                nt.links.new(coord.outputs["Generated" if no_uvs else "UV"], mapping.inputs["Vector"])
                nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
            strength = ov.get("strength")
            if slot == "normal":
                nmap = nt.nodes.new("ShaderNodeNormalMap")
                if strength is not None:
                    nmap.inputs["Strength"].default_value = strength
                nt.links.new(tex.outputs["Color"], nmap.inputs["Color"])
                _link(nt, nmap.outputs["Normal"], bsdf, _SLOT_SOCKETS[slot])
            else:
                _link(nt, tex.outputs["Color"], bsdf, _SLOT_SOCKETS[slot])
            if slot == "baseColor":
                mat.diffuse_color = (1, 1, 1, 1)
            if slot == "emission":
                sock = bsdf.inputs.get("Emission Strength")
                if sock is not None:
                    sock.default_value = strength if strength is not None else max(sock.default_value, 1.0)
            nodes_for.append((tex, cs))
        wired.append((ov, nodes_for))
    return wired


def _material_lacks_uvs(scene, mat_name: str) -> bool:
    for ob in _mesh_objects(scene):
        if any(s.material is not None and s.material.name == mat_name for s in ob.material_slots):
            if len(ob.data.uv_layers) == 0:
                return True
    return False


def _link(nt, out_sock, bsdf, names) -> None:
    for name in names:
        sock = bsdf.inputs.get(name)
        if sock is not None:
            for link in list(sock.links):
                nt.links.remove(link)
            nt.links.new(out_sock, sock)
            return


def _frame_keys(wired: list) -> set[str]:
    return {k for ov, _ in wired for k in ov["frames"]}


def update_texture_frame(wired: list, frame: int) -> None:
    for ov, nodes in wired:
        path = ov["frames"].get(str(frame))
        if path is None:
            continue
        for tex, cs in nodes:
            if tex.image is None or bpy.path.abspath(tex.image.filepath) != path:
                tex.image = _image(path, cs)


def setup_world(scene, env: dict) -> None:
    if env.get("keep_scene_world"):
        return
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = float(env.get("intensity", 1.0))
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    if env.get("hdri"):
        tex = nt.nodes.new("ShaderNodeTexEnvironment")
        tex.image = bpy.data.images.load(env["hdri"], check_existing=True)
        mapping = nt.nodes.new("ShaderNodeMapping")
        coord = nt.nodes.new("ShaderNodeTexCoord")
        mapping.inputs["Rotation"].default_value[2] = math.radians(float(env.get("rotationDeg", 0)))
        nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
        nt.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Color"], bg.inputs["Color"])
    else:
        bg.inputs["Color"].default_value = (0.05, 0.05, 0.05, 1.0)


def add_lights(scene, lights: list, conv) -> None:
    for i, l in enumerate(lights):
        t = l["type"]
        data = bpy.data.lights.new(f"renda_{t}_{i}", {"sun": "SUN", "point": "POINT", "spot": "SPOT", "area": "AREA"}[t])
        data.color = l.get("color", (1, 1, 1))
        data.energy = float(l["intensity"])
        ob = bpy.data.objects.new(data.name, data)
        scene.collection.objects.link(ob)
        if t == "sun":
            d = conv(l["direction"]).normalized()
            ob.matrix_world = look_at_matrix(Vector((0, 0, 0)), d)
        elif t == "point":
            data.shadow_soft_size = float(l.get("radius", 0.05))
            ob.location = conv(l["position"])
        else:
            pos, tgt = conv(l["position"]), conv(l["target"])
            ob.matrix_world = look_at_matrix(pos, tgt)
            if t == "spot":
                data.spot_size = math.radians(float(l.get("angleDeg", 45)))
                data.spot_blend = 0.2
            else:
                data.shape = "RECTANGLE"
                data.size, data.size_y = l.get("size", (1, 1))


# -- camera ----------------------------------------------------------------------


def frame_auto(scene, cam_spec: dict, aspect: float, orbit: bool = False) -> tuple[Vector, Vector, float]:
    """Same fit as core renda.ir.camera.frame_bounds (kept in sync, see test_framing): the 8 box corners fill
    the frame from the view direction, re-centred on their projection; orbit=True takes the worst case over a
    revolution and keeps the box centre as target so turntables never clip."""
    lo, hi = world_bounds(scene)
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1e-4)
    fov_deg = cam_spec.get("fovDeg", 35)
    padding = cam_spec.get("padding", 0.1)
    # fitFovDeg: core narrows the fit when post lens distortion / CA push content outwards (resolve_cameras)
    tan_y = math.tan(math.radians(cam_spec.get("fitFovDeg", fov_deg)) / 2) / (1 + padding)
    tan_x = tan_y * aspect
    corners = [Vector((x, y, z)) - center for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)]
    up, front, side = Vector((0, 0, 1)), Vector((0, -1, 0)), Vector((1, 0, 0))
    el = math.radians(cam_spec.get("elevationDeg", 20))

    def view(az_deg: float) -> Vector:
        az = math.radians(az_deg)
        horiz = math.cos(az) * front + math.sin(az) * side
        return math.cos(el) * horiz + math.sin(el) * up

    def fit(direction: Vector, offset=(0.0, 0.0)):
        fwd = -direction
        right = fwd.cross(up)
        right = right.normalized() if right.length > 1e-6 else side
        cam_up = right.cross(fwd)
        dist = max(max(c.dot(direction) + abs(c.dot(right) - offset[0]) / tan_x,
                       c.dot(direction) + abs(c.dot(cam_up) - offset[1]) / tan_y) for c in corners)
        return dist, right, cam_up

    az0 = cam_spec.get("azimuthDeg", 35)
    direction = view(az0)
    target = center.copy()
    if orbit:
        dist = max(fit(view(az0 + a))[0] for a in range(0, 360, 15))
    else:
        offset = (0.0, 0.0)
        for _ in range(4):
            dist, right, cam_up = fit(direction, offset)
            target = center + right * offset[0] + cam_up * offset[1]
            px, py = [], []
            for c in corners:
                rel = c - (target - center)
                depth = dist - rel.dot(direction)
                px.append(rel.dot(right) / depth)
                py.append(rel.dot(cam_up) / depth)
            offset = (offset[0] + (max(px) + min(px)) / 2 * dist, offset[1] + (max(py) + min(py)) / 2 * dist)
    dist = max(dist, radius * 1e-3)
    return target + direction * dist, target, fov_deg


def make_camera(scene, job: dict, conv):
    """Returns (camera object, base position, target, turntable-able)."""
    spec = job["camera"]
    w, h = job["resolution"]
    aspect = w / h
    mode = spec["mode"]
    if mode == "scene" or (job.get("use_scene_camera_default") and scene.camera is not None):
        name = spec.get("name")
        cam = bpy.data.objects.get(name) if name else scene.camera
        if cam is None and not name:  # importers (Alembic, USD…) add cameras without making one active
            cam = next((o for o in scene.objects if o.type == "CAMERA"), None)
        if cam is not None and cam.type == "CAMERA":
            scene.camera = cam
            return cam, None, None
        WARN.append(f"camera: no camera {name!r} in scene; using auto framing" if name else "camera: scene has no camera; using auto framing")
        spec = {"mode": "auto"}
        mode = "auto"
    data = bpy.data.cameras.new("renda_camera")
    cam = bpy.data.objects.new("renda_camera", data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    data.sensor_fit = "VERTICAL"
    if mode == "lookat":
        pos, tgt, fov = conv(spec["position"]), conv(spec.get("target", (0, 0, 0))), spec.get("fovDeg", 40)
    else:
        pos, tgt, fov = frame_auto(scene, spec, aspect, orbit=bool(job.get("turntable")))
    data.angle_y = math.radians(fov)
    radius = (pos - tgt).length
    data.clip_start = max(radius * 0.001, 0.001)
    data.clip_end = max(radius * 100, 1000)
    cam.matrix_world = look_at_matrix(pos, tgt)
    return cam, pos, tgt


def turntable_camera(cam, base_pos, target, tt: dict, frame: int) -> None:
    if base_pos is None:  # scene camera: orbit it around the scene centre
        lo, hi = world_bounds(bpy.context.scene)
        target = (lo + hi) / 2
        base_pos = cam.matrix_world.translation.copy() if "renda_tt_base" not in cam else Vector(cam["renda_tt_base"])
        cam["renda_tt_base"] = list(base_pos)
    span = max(tt["frame_end"] - tt["frame_start"] + 1, 1)
    ang = 2 * math.pi * tt.get("revolutions", 1) * (frame - tt["frame_start"]) / span
    axis = {"X": Vector((1, 0, 0)), "Y": Vector((0, 1, 0)), "Z": Vector((0, 0, 1))}.get(tt.get("axis", "up"), Vector((0, 0, 1)))
    rel = Matrix.Rotation(ang, 3, axis) @ (base_pos - target)
    cam.matrix_world = look_at_matrix(target + rel, target)


def _fcurves(ob):
    act = ob.animation_data.action if ob.animation_data else None
    if act is None:
        return []
    try:
        if act.fcurves is not None:
            return list(act.fcurves)
    except AttributeError:
        pass
    out = []  # Blender 5 slotted actions
    for layer in getattr(act, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                out += list(bag.fcurves)
    return out


def rig_turntable(scene, cam, base_pos, target, tt: dict) -> bool:
    """Parent the camera to a pivot at the target with a linear rotation keyframed over the animation, so
    native motion blur sees the true arc between frames. Returns True when rigged."""
    if base_pos is None:
        lo, hi = world_bounds(scene)
        target = (lo + hi) / 2
    pivot = bpy.data.objects.new("renda_tt_pivot", None)
    scene.collection.objects.link(pivot)
    pivot.location = target
    pivot.rotation_mode = "XYZ"
    bpy.context.view_layer.update()
    mw = cam.matrix_world.copy()
    cam.parent = pivot
    cam.matrix_parent_inverse = pivot.matrix_world.inverted()
    cam.matrix_world = mw
    ax = {"X": 0, "Y": 1, "Z": 2}.get(tt.get("axis", "up"), 2)
    span = max(tt["frame_end"] - tt["frame_start"] + 1, 1)
    total = 2 * math.pi * tt.get("revolutions", 1)
    pivot.rotation_euler[ax] = 0.0
    pivot.keyframe_insert("rotation_euler", index=ax, frame=tt["frame_start"])
    pivot.rotation_euler[ax] = total
    pivot.keyframe_insert("rotation_euler", index=ax, frame=tt["frame_start"] + span)
    curves = _fcurves(pivot)
    if not curves:
        WARN.append("blender: couldn't make the turntable keyframes linear; motion blur arcs may ease")
    for fc in curves:
        fc.extrapolation = "LINEAR"
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    return True


def apply_lens(scene, cam, job: dict, conv, pos, tgt) -> None:
    """Physical lens on the render camera: focal length + sensor, and depth of field."""
    lens = job.get("lens")
    if not lens or cam is None or cam.type != "CAMERA":
        return
    d = cam.data
    w, h = job["resolution"]
    aspect = w / h
    if lens.get("focal_mm"):
        d.sensor_fit = "AUTO"
        d.sensor_width = float(lens["sensor_mm"])
        d.lens = float(lens["focal_mm"])
    elif cam.name == "renda_camera":
        # keep the framing FOV but give it a physical focal length/sensor so DoF matches the other engines
        half_y = math.tan(d.angle_y / 2)
        half_wide = half_y * aspect if aspect >= 1 else half_y
        d.sensor_fit = "AUTO"
        d.sensor_width = float(lens["sensor_mm"])
        d.lens = float(lens["sensor_mm"]) / (2 * half_wide)
    if not lens.get("fstop"):
        return
    dof = d.dof
    dof.use_dof = True
    dof.aperture_fstop = float(lens["fstop"])
    dof.aperture_blades = int(lens.get("blades", 0))
    dof.aperture_rotation = math.radians(float(lens.get("rotation_deg", 0)))
    if hasattr(dof, "aperture_ratio"):
        dof.aperture_ratio = float(lens.get("ratio", 1.0))
    obj_name = lens.get("focus_object")
    if obj_name:
        ob = bpy.data.objects.get(obj_name)
        if ob is not None:
            dof.focus_object = ob
            return
        WARN.append(f"lens: no object named {obj_name!r} to focus on; focusing on the camera target")
    if lens.get("focus_distance"):
        dof.focus_distance = float(lens["focus_distance"])
    elif pos is not None and tgt is not None and not lens.get("focus_point"):
        dof.focus_distance = max((pos - tgt).length, 1e-3)
    update_focus(scene, cam, lens, conv)


def update_focus(scene, cam, lens: dict | None, conv) -> None:
    """Re-aim the plane of focus each frame for a fixed focus point / auto focus on a scene camera."""
    if not lens or not lens.get("fstop") or lens.get("focus_distance") or cam.data.dof.focus_object:
        return
    bpy.context.view_layer.update()
    mw = cam.matrix_world
    fwd = (mw.to_3x3() @ Vector((0, 0, -1))).normalized()
    if lens.get("focus_point"):
        p = conv(lens["focus_point"])
    elif cam.name != "renda_camera":  # scene camera with auto focus: the scene centre
        lo, hi = world_bounds(scene)
        p = (lo + hi) / 2
    else:
        return
    cam.data.dof.focus_distance = max((p - mw.translation).dot(fwd), 1e-3)


# -- passes ----------------------------------------------------------------------


def setup_passes(scene, passes: list, out_dir: Path) -> None:
    wanted = [p for p in passes if p in ("depth", "normal", "albedo")]
    if not wanted:
        return
    vl = scene.view_layers[0]
    if "depth" in wanted:
        vl.use_pass_z = True
    if "normal" in wanted:
        vl.use_pass_normal = True
    if "albedo" in wanted:
        vl.use_pass_diffuse_color = True
    try:
        if hasattr(scene, "compositing_node_group"):  # Blender 5.x
            tree = bpy.data.node_groups.new("renda_passes", "CompositorNodeTree")
            scene.compositing_node_group = tree
        else:
            scene.use_nodes = True
            tree = scene.node_tree
        rl = tree.nodes.new("CompositorNodeRLayers")
        fo = tree.nodes.new("CompositorNodeOutputFile")
        if hasattr(fo, "base_path"):
            fo.base_path = str(out_dir)
        else:  # 5.x
            fo.directory = str(out_dir)
        fo.format.file_format = "OPEN_EXR"
        fo.format.color_depth = "32"
        slots = fo.file_slots if hasattr(fo, "file_slots") else None
        if slots is not None:
            slots.clear()
        sock_names = {"depth": "Depth", "normal": "Normal", "albedo": "DiffCol"}
        for p in wanted:
            src = rl.outputs.get(sock_names[p]) or rl.outputs.get({"albedo": "Diffuse Color"}.get(p, ""))
            if src is None:
                WARN.append(f"blender: pass {p} unavailable with this engine")
                continue
            if slots is not None:
                slots.new(f"{p}_")
                tree.links.new(src, fo.inputs[-1])
            else:
                item = fo.file_output_items.new("FLOAT", f"{p}_")
                tree.links.new(src, fo.inputs[item.name])
    except Exception as e:  # compositor API differs between versions
        WARN.append(f"blender: couldn't set up pass outputs ({e}); passes skipped")


# -- ground plane / shadow catcher (scene.ground) ----------------------------------

GROUND = "renda_ground"


def add_ground(scene, g: dict, job: dict, conv):
    """Plane under the scene (Blender space is Z-up). Cycles catches shadows natively; other engines get a white
    matte plane plus the extra layers rendered in render_ground_layers for core's differential composite."""
    meshes = [ob for ob in _mesh_objects(scene) if ob.name != GROUND]
    if not meshes:
        return None
    corners = [ob.matrix_world @ Vector(c) for ob in meshes for c in ob.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    diag = (hi - lo).length or 1.0
    unit = float(job.get("unit_scale", 1.0))
    if g.get("offset") is not None:
        up_src = {"Z": (0, 0, 1), "-Y": (0, -1, 0)}.get(job.get("source_up") or "", (0, 1, 0))
        z = Vector(conv([c * float(g["offset"]) for c in up_src])).z * unit
    else:
        z = lo.z - float(g["epsilon"]) * diag
    size = float(g["size"]) * unit if g.get("size") else float(g["auto_size"]) * diag
    bpy.ops.mesh.primitive_plane_add(size=size, location=((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, z))
    plane = bpy.context.active_object
    plane.name = GROUND
    mat = bpy.data.materials.new("renda_ground_mat")
    bsdf = principled(mat)
    links = mat.node_tree.links
    _set_input(bsdf, ["Base Color"], (*g["color"], 1.0), links)
    _set_input(bsdf, ["Roughness"], float(g["roughness"]), links)
    _set_input(bsdf, ["Specular IOR Level", "Specular"], float(g["specular"]), links)
    plane.data.materials.append(mat)
    if g.get("catcher_native"):
        plane.is_shadow_catcher = True
    if scene.render.engine != "CYCLES" and scene.render.engine != "BLENDER_WORKBENCH":
        ev = scene.eevee
        if g["mode"] == "reflective":
            for attr in ("use_raytracing", "use_ssr"):  # Eevee Next / legacy screen-space reflections
                if hasattr(ev, attr):
                    setattr(ev, attr, True)
        if g.get("contact") and hasattr(ev, "use_gtao"):  # legacy Eevee casts no world-light shadows: add AO
            ev.use_gtao = True
            if hasattr(ev, "gtao_distance"):
                ev.gtao_distance = 0.25 * diag
    if g["mode"] == "reflective" and scene.render.engine == "BLENDER_WORKBENCH":
        WARN.append("blender-workbench: no reflections; the reflective ground renders as a plain floor")
    return {"plane": plane, "meshes": meshes, "size": size}


def render_ground_layers(scene, ground: dict, g: dict, out_dir: Path, f: int) -> None:
    """Extra layers for core's catcher composite: the plane alone (same lighting), an object-coverage mask
    (fast Workbench) and, over a visible HDRI, the backdrop with neither."""
    r = scene.render
    plane, meshes = ground["plane"], ground["meshes"]

    def shot(name: str) -> None:
        scene.render.filepath = str(out_dir / f"frame_{f:04d}.{name}.png")
        bpy.ops.render.render(write_still=True)

    if g.get("emulate"):
        for ob in meshes:
            ob.hide_render = True
        shot("gplane")
        for ob in meshes:
            ob.hide_render = False
    if g.get("object_mask"):
        plane.hide_render = True
        engine, film = r.engine, r.film_transparent
        r.engine, r.film_transparent = "BLENDER_WORKBENCH", True
        shot("gobj")
        r.engine, r.film_transparent = engine, film
        plane.hide_render = False
    if g.get("backdrop"):
        for ob in meshes + [plane]:
            ob.hide_render = True
        film = r.film_transparent
        r.film_transparent = False
        shot("gback")
        r.film_transparent = film
        for ob in meshes + [plane]:
            ob.hide_render = False


# -- main ------------------------------------------------------------------------


def bake(scene, job: dict) -> dict:
    """Bake lighting onto the "RendaLightmap" UVs (TEXCOORD_1 of the core-written GLB) of every mesh and
    dump the float atlas as .npy; renda core dilates, encodes and writes the final GLB."""
    import numpy as np

    b = job["bake"]
    size = int(b["size"])
    objs = [o for o in scene.objects if o.type == "MESH" and not o.hide_render]
    if not objs:
        raise RuntimeError("bake: no mesh objects in the scene")
    img = bpy.data.images.new("renda_bake", size, size, alpha=True, float_buffer=True)
    img.generated_color = (0.0, 0.0, 0.0, 0.0)
    # float images are scene-linear by default, so the pixels read back are linear radiance
    for o in objs:
        uvl = o.data.uv_layers
        if len(uvl) == 0:
            raise RuntimeError(f"bake: mesh {o.name} has no UVs")
        layer = uvl[1] if len(uvl) > 1 else uvl[0]
        layer.name = "RendaLightmap"
        if not o.data.materials:
            m = bpy.data.materials.new("renda_default")
            m.use_nodes = True
            o.data.materials.append(m)
        for slot in o.material_slots:
            m = slot.material
            if m is None:
                continue
            m.use_nodes = True
            nt = m.node_tree
            node = nt.nodes.get("RendaBakeTarget") or nt.nodes.new("ShaderNodeTexImage")
            node.name = "RendaBakeTarget"
            node.image = img
            for n in nt.nodes:
                n.select = False
            node.select = True
            nt.nodes.active = node
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    mode = b["mode"]
    if mode == "ao" and scene.world is not None:
        scene.world.light_settings.distance = float(b.get("ao_distance", 1.0))
    kw = dict(margin=int(b.get("margin", 4)), use_clear=True, uv_layer="RendaLightmap", target="IMAGE_TEXTURES")
    if mode == "unlit":
        kw.update(type="COMBINED", pass_filter={"EMIT", "DIRECT", "INDIRECT", "DIFFUSE", "GLOSSY", "TRANSMISSION"})
    elif mode == "ao":
        kw.update(type="AO")
    else:  # lightmap: incoming light only, no albedo
        kw.update(type="DIFFUSE", pass_filter={"DIRECT", "INDIRECT"})
    try:
        scene.render.bake.margin_type = "EXTEND"
    except Exception:
        pass
    bpy.ops.object.bake(**kw)
    px = np.empty(size * size * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    out = Path(job["out_dir"]) / "bake.npy"
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, px.reshape(size, size, 4))
    return {"files": [str(out)], "warnings": WARN, "gpu": scene.cycles.device}


def main() -> None:
    job = read_job()
    fmt = job["format"]
    open_or_import(job["input"], fmt, job.get("source_up"), WARN)
    scene = bpy.context.scene
    conv = to_blender_space(fmt, job.get("source_up"))
    add_includes(job, conv)
    apply_unit_scale(scene, float(job.get("unit_scale", 1.0)))
    _ensure_materials(scene)
    wire_vertex_colors(scene)
    apply_material_overrides(scene, job.get("material_overrides", {}))
    wired_textures = setup_texture_overrides(scene, job.get("texture_overrides") or [], job["frames"][0])
    setup_world(scene, job["environment"])
    add_lights(scene, job.get("lights", []), conv)
    configure_engine(scene, job)
    if job.get("bake"):
        write_result(job, bake(scene, job))
        return
    cam, base_pos, target = make_camera(scene, job, conv)
    ground_job = job.get("ground")
    ground = add_ground(scene, ground_job, job, conv) if ground_job else None  # after framing: never affects it

    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_passes(scene, job.get("passes", []), out_dir)
    img = scene.render.image_settings
    img.file_format = job.get("image_format", "PNG")
    img.color_mode = "RGBA"
    if img.file_format == "PNG":
        img.color_depth = "8"
    else:
        img.color_depth = "32"

    tt = job.get("turntable")
    frame_cams = job.get("frame_cameras")  # explicit per-frame cameras (hybrid splat renders must match exactly)
    lens = job.get("lens")
    if not frame_cams:
        apply_lens(scene, cam, job, conv, base_pos, target)
    mb = job.get("motion_blur")
    offsets = mb["offsets"] if mb and mb.get("emulate") else [0.0]
    rigged = bool(tt and mb and not mb.get("emulate") and rig_turntable(scene, cam, base_pos, target, tt))
    if frame_cams and mb and not mb.get("emulate"):
        # hybrid: keyframe the explicit cameras so native motion blur has motion to sample
        for f in job["frames"]:
            fc = frame_cams[str(f)]
            cam.matrix_world = look_at_matrix(conv(fc["position"]), conv(fc["target"]), conv(fc["up"]))
            cam.keyframe_insert("location", frame=f)
            cam.keyframe_insert("rotation_euler", frame=f)
        for fc_ in _fcurves(cam):
            fc_.extrapolation = "LINEAR"
    ext = "png" if img.file_format == "PNG" else "exr"
    written = []
    for f in job["frames"]:
        for i, off in enumerate(offsets):
            t = f + off
            fi = math.floor(t)
            scene.frame_set(fi, subframe=t - fi)
            # video textures step per render frame (subframes share their frame's image)
            update_texture_frame(wired_textures, fi if str(fi) in _frame_keys(wired_textures) else f)
            if frame_cams:
                fc = frame_cams[str(f)]
                pos, tgt, up = conv(fc["position"]), conv(fc["target"]), conv(fc["up"])
                cam.data.sensor_fit = "VERTICAL"
                cam.data.angle_y = math.radians(fc["fovYDeg"])
                cam.data.clip_start = max(float(fc["near"]), 1e-4)
                cam.data.clip_end = float(fc["far"])
                if not (mb and not mb.get("emulate")):
                    cam.matrix_world = look_at_matrix(pos, tgt, up)
                if lens and lens.get("fstop") and fc.get("focusDistance"):
                    cam.data.dof.use_dof = True
                    cam.data.dof.aperture_fstop = float(lens["fstop"])
                    cam.data.dof.aperture_blades = int(lens.get("blades", 0))
                    cam.data.dof.focus_distance = float(fc["focusDistance"])
                    if fc.get("focalMm"):
                        cam.data.sensor_fit = "AUTO"
                        cam.data.sensor_width = float(lens["sensor_mm"])
                        cam.data.lens = float(fc["focalMm"])
            elif tt and not rigged:
                turntable_camera(cam, base_pos, target, tt, t)
            update_focus(scene, cam, lens, conv)
            if ground is not None:
                cam.data.clip_end = max(cam.data.clip_end, ground["size"] * 1.5)  # don't clip the floor
            name = f"frame_{f:04d}.{ext}" if len(offsets) == 1 else f"frame_{f:04d}.sub{i:02d}.{ext}"
            path = out_dir / name
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            if ground is not None and i == len(offsets) // 2:
                render_ground_layers(scene, ground, ground_job, out_dir, f)
        written.append(str(out_dir / f"frame_{f:04d}.{ext}"))
    write_result(job, {"files": written, "warnings": WARN, "gpu": scene.cycles.device if scene.render.engine == "CYCLES" else None})


if __name__ == "__main__":
    try:
        main()
    except Exception:  # make failures visible to the parent with a non-zero exit
        import traceback

        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
