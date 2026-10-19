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
    # Filmic/AgX view transform gives nicer highlights than Standard
    vs = scene.view_settings
    for name in ("AgX", "Filmic"):
        try:
            vs.view_transform = name
            break
        except TypeError:
            continue


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


def apply_material_overrides(scene, overrides: dict) -> None:
    if not overrides:
        return
    if "*" in overrides:  # objects without materials need one for "*" to reach them
        default = None
        for ob in scene.objects:
            if ob.type == "MESH" and len(ob.material_slots) == 0:
                default = default or bpy.data.materials.new("renda_default")
                ob.data.materials.append(default)
    for mat in bpy.data.materials:
        for key in ("*", mat.name):
            ov = overrides.get(key)
            if not ov:
                continue
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


def frame_auto(scene, cam_spec: dict, aspect: float) -> tuple[Vector, Vector, float]:
    lo, hi = world_bounds(scene)
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1e-4)
    fov_y = math.radians(cam_spec.get("fovDeg", 35))
    fov_x = 2 * math.atan(math.tan(fov_y / 2) * aspect)
    dist = radius * (1 + cam_spec.get("padding", 0.1)) / math.sin(min(fov_x, fov_y) / 2)
    az, el = math.radians(cam_spec.get("azimuthDeg", 35)), math.radians(cam_spec.get("elevationDeg", 20))
    front, side = Vector((0, -1, 0)), Vector((1, 0, 0))
    horiz = math.cos(az) * front + math.sin(az) * side
    direction = math.cos(el) * horiz + math.sin(el) * Vector((0, 0, 1))
    return center + direction * dist, center, cam_spec.get("fovDeg", 35)


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
        pos, tgt, fov = frame_auto(scene, spec, aspect)
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


# -- main ------------------------------------------------------------------------


def main() -> None:
    job = read_job()
    fmt = job["format"]
    open_or_import(job["input"], fmt, job.get("source_up"), WARN)
    scene = bpy.context.scene
    conv = to_blender_space(fmt, job.get("source_up"))
    add_includes(job, conv)
    apply_unit_scale(scene, float(job.get("unit_scale", 1.0)))
    apply_material_overrides(scene, job.get("material_overrides", {}))
    setup_world(scene, job["environment"])
    add_lights(scene, job.get("lights", []), conv)
    configure_engine(scene, job)
    cam, base_pos, target = make_camera(scene, job, conv)

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
    written = []
    for f in job["frames"]:
        scene.frame_set(f)
        if frame_cams:
            fc = frame_cams[str(f)]
            pos, tgt, up = conv(fc["position"]), conv(fc["target"]), conv(fc["up"])
            cam.data.sensor_fit = "VERTICAL"
            cam.data.angle_y = math.radians(fc["fovYDeg"])
            cam.data.clip_start = max(float(fc["near"]), 1e-4)
            cam.data.clip_end = float(fc["far"])
            cam.matrix_world = look_at_matrix(pos, tgt, up)
        elif tt:
            turntable_camera(cam, base_pos, target, tt, f)
        ext = "png" if img.file_format == "PNG" else "exr"
        path = out_dir / f"frame_{f:04d}.{ext}"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        written.append(str(path))
    write_result(job, {"files": written, "warnings": WARN, "gpu": scene.cycles.device if scene.render.engine == "CYCLES" else None})


if __name__ == "__main__":
    try:
        main()
    except Exception:  # make failures visible to the parent with a non-zero exit
        import traceback

        traceback.print_exc()
        sys.stdout.flush()
        os._exit(1)
