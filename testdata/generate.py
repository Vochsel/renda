"""Generate the Renda test set: one small studio scene in every supported format.

    uv run --project core python testdata/generate.py

Runs in two modes:
- outside Blender (default): orchestrates everything — runs Blender headless with this same
  file, runs the three.js node script, then writes the formats Blender doesn't (3MF, PLY with
  vertex colours, the HDRI, the three.js texture data URI).
- inside Blender (`blender --background --python generate.py -- <out_dir>`): builds the scene
  and exports .blend/.usda/.usdz/.glb/.gltf/.fbx/.obj/.stl/.dae/.abc and anim.blend.

Everything is generated from code (no downloaded assets), so the set is license-clean (CC0).
Generation is deterministic apart from the timestamps some exporters embed.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCENES = HERE / "scenes"
BLENDER = os.environ.get("RENDA_BLENDER", "/Applications/Blender.app/Contents/MacOS/Blender")

# name → (base colour RGBA, metallic, roughness)
MATERIALS = {
    "red_plastic": ((0.80, 0.08, 0.06, 1.0), 0.0, 0.35),
    "gold_metal": ((1.00, 0.72, 0.30, 1.0), 1.0, 0.25),
    "blue_rough": ((0.10, 0.25, 0.80, 1.0), 0.0, 0.80),
    "table_wood": ((0.45, 0.30, 0.18, 1.0), 0.0, 0.60),
}
CHECKER = "checker.png"


def checker_pixels(size: int = 64, cells: int = 8):
    """RGBA rows for an orange/white checker (pure python so Blender can use it too)."""
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            on = ((x * cells // size) + (y * cells // size)) % 2 == 0
            row.append((1.0, 0.55, 0.1, 1.0) if on else (0.95, 0.95, 0.95, 1.0))
        rows.append(row)
    return rows


def torus_knot(p: int = 2, q: int = 3, segments: int = 160, sides: int = 16, r: float = 0.35, tube: float = 0.1):
    """Vertices/faces of a (p,q) torus knot tube, like three.js TorusKnotGeometry."""

    def curve(t):
        cu, su = math.cos(t), math.sin(t)
        qr = q / p * t
        cs = math.cos(qr)
        return (r * (2 + cs) * 0.5 * cu, r * (2 + cs) * 0.5 * su, r * math.sin(qr) * 0.5)

    verts, faces = [], []
    for i in range(segments):
        t = i / segments * p * math.pi * 2
        p1, p2 = curve(t), curve(t + 0.01)
        tng = [b - a for a, b in zip(p1, p2)]
        nrm = [b + a for a, b in zip(p1, p2)]
        bin_ = [tng[1] * nrm[2] - tng[2] * nrm[1], tng[2] * nrm[0] - tng[0] * nrm[2], tng[0] * nrm[1] - tng[1] * nrm[0]]
        nrm = [bin_[1] * tng[2] - bin_[2] * tng[1], bin_[2] * tng[0] - bin_[0] * tng[2], bin_[0] * tng[1] - bin_[1] * tng[0]]
        bl = math.sqrt(sum(c * c for c in bin_)) or 1
        nl = math.sqrt(sum(c * c for c in nrm)) or 1
        bin_ = [c / bl for c in bin_]
        nrm = [c / nl for c in nrm]
        for j in range(sides):
            v = j / sides * math.pi * 2
            cx, cy = -tube * math.cos(v), tube * math.sin(v)
            verts.append(tuple(p1[k] + cx * nrm[k] + cy * bin_[k] for k in range(3)))
    for i in range(segments):
        for j in range(sides):
            a = i * sides + j
            b = ((i + 1) % segments) * sides + j
            c = ((i + 1) % segments) * sides + (j + 1) % sides
            d = i * sides + (j + 1) % sides
            faces.append((a, b, c, d))
    return verts, faces


# ───────────────────────────── inside Blender ─────────────────────────────


def blender_main(out: Path) -> None:
    import bpy

    out.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.frame_start, scene.frame_end = 1, 24
    scene.render.fps = 24

    # Texture image, packed into the .blend and written next to the exports.
    size = 64
    img = bpy.data.images.new(CHECKER, size, size, alpha=True)
    img.pixels = [c for row in reversed(checker_pixels(size)) for px in row for c in px]
    img.filepath_raw = str(out / CHECKER)
    img.file_format = "PNG"
    img.save()
    img.filepath = str(out / CHECKER)
    img.pack()

    mats = {}
    for name, (rgba, metallic, rough) in MATERIALS.items():
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        bsdf = m.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = rgba
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = rough
        m.diffuse_color = rgba  # viewport/workbench colour
        mats[name] = m
    chk = bpy.data.materials.new("checker")
    chk.use_nodes = True
    nt = chk.node_tree
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    nt.links.new(tex.outputs["Color"], nt.nodes["Principled BSDF"].inputs["Base Color"])
    nt.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.5
    mats["checker"] = chk

    def add(obj, mat, loc=(0, 0, 0)):
        obj.location = loc
        obj.data.materials.append(mats[mat])
        return obj

    # Table: top + four legs, joined into one object.
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, -0.05))
    top = bpy.context.active_object
    top.scale = (2.4, 1.6, 0.1)
    legs = [top]
    for sx in (-1, 1):
        for sy in (-1, 1):
            bpy.ops.mesh.primitive_cube_add(size=1, location=(sx * 1.05, sy * 0.65, -0.55))
            leg = bpy.context.active_object
            leg.scale = (0.1, 0.1, 0.9)
            legs.append(leg)
    for o in legs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = top
    bpy.ops.object.transform_apply(scale=True)
    bpy.ops.object.join()
    table = bpy.context.active_object
    table.name = table.data.name = "Table"
    add(table, "table_wood")

    verts, faces = torus_knot()
    me = bpy.data.meshes.new("TorusKnot")
    me.from_pydata(verts, [], faces)
    me.update()
    knot = bpy.data.objects.new("TorusKnot", me)
    scene.collection.objects.link(knot)
    add(knot, "gold_metal", (0, 0, 0.45))
    for poly in me.polygons:
        poly.use_smooth = True

    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.28, segments=48, ring_count=24, location=(0.75, 0.25, 0.28))
    sphere = bpy.context.active_object
    sphere.name = sphere.data.name = "Sphere"
    bpy.ops.object.shade_smooth()
    add(sphere, "red_plastic", sphere.location)

    bpy.ops.mesh.primitive_cube_add(size=0.4, location=(-0.75, -0.2, 0.2))
    cube = bpy.context.active_object
    cube.name = cube.data.name = "CheckerCube"
    cube.rotation_euler = (0, 0, math.radians(25))
    add(cube, "checker", cube.location)

    bpy.ops.mesh.primitive_cylinder_add(radius=0.15, depth=0.3, location=(-0.3, 0.5, 0.15))
    cyl = bpy.context.active_object
    cyl.name = cyl.data.name = "Cylinder"
    add(cyl, "blue_rough", cyl.location)

    cam_data = bpy.data.cameras.new("Camera")
    cam_data.lens = 40
    cam = bpy.data.objects.new("Camera", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (3.2, -3.0, 2.0)
    direction = [0 - 3.2, 0 - -3.0, 0.3 - 2.0]
    from mathutils import Vector

    cam.rotation_euler = Vector(direction).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    sun_data = bpy.data.lights.new("Sun", "SUN")
    sun_data.energy = 3.0
    sun_data.angle = math.radians(5)
    sun = bpy.data.objects.new("Sun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(40), math.radians(10), math.radians(35))

    area_data = bpy.data.lights.new("Fill", "AREA")
    area_data.energy = 150
    area_data.size = 2
    area = bpy.data.objects.new("Fill", area_data)
    scene.collection.objects.link(area)
    area.location = (-2.5, -1.5, 2.5)
    area.rotation_euler = Vector((2.5, 1.5, -2.5)).to_track_quat("-Z", "Y").to_euler()

    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.05, 0.06, 0.08, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6
    scene.world = world

    def select_all():
        bpy.ops.object.select_all(action="SELECT")

    def select_only(obj):
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

    def save_blend(path: Path):
        bpy.context.preferences.filepaths.save_version = 0  # no .blend1 backups on regenerate
        bpy.ops.wm.save_as_mainfile(filepath=str(path), compress=True, copy=True)

    # Static exports (no animation yet).
    save_blend(out / "scene.blend")
    select_all()
    bpy.ops.wm.usd_export(filepath=str(out / "scene.usda"), selected_objects_only=False, export_textures=True,
                          relative_paths=True, export_materials=True)
    bpy.ops.wm.usd_export(filepath=str(out / "scene.usdz"), selected_objects_only=False, export_materials=True)
    bpy.ops.export_scene.gltf(filepath=str(out / "scene.glb"), export_format="GLB", export_cameras=True,
                              export_lights=True, export_apply=True)
    bpy.ops.export_scene.gltf(filepath=str(out / "scene.gltf"), export_format="GLTF_SEPARATE", export_cameras=True,
                              export_lights=True, export_apply=True, export_texture_dir="")
    bpy.ops.export_scene.fbx(filepath=str(out / "scene.fbx"), path_mode="COPY", embed_textures=True,
                             object_types={"MESH", "CAMERA", "LIGHT"})
    bpy.ops.wm.obj_export(filepath=str(out / "scene.obj"), export_materials=True, path_mode="RELATIVE")
    bpy.ops.wm.collada_export(filepath=str(out / "scene.dae"), include_children=True)

    select_only(knot)
    bpy.ops.export_mesh.stl(filepath=str(out / "scene.stl"), use_selection=True, ascii=False)
    bpy.ops.export_mesh.stl(filepath=str(out / "scene_ascii.stl"), use_selection=True, ascii=True)

    # Animated exports: knot spins, sphere bounces.
    knot.rotation_euler = (0, 0, 0)
    knot.keyframe_insert("rotation_euler", frame=1)
    knot.rotation_euler = (0, 0, math.radians(360))
    knot.keyframe_insert("rotation_euler", frame=25)
    for fc in knot.animation_data.action.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
    for f, z in ((1, 0.28), (7, 0.7), (13, 0.28), (19, 0.7), (25, 0.28)):
        sphere.location.z = z
        sphere.keyframe_insert("location", index=2, frame=f)
    select_all()
    bpy.ops.wm.alembic_export(filepath=str(out / "scene.abc"), start=1, end=24, selected=False, uvs=True, normals=True)

    scene.frame_end = 48
    knot.animation_data.action.fcurves[2].keyframe_points[-1].co = (49, math.radians(360))
    save_blend(out / "anim.blend")


# ───────────────────────────── outside Blender ─────────────────────────────


def write_hdr(path: Path, w: int = 256, h: int = 128) -> None:
    """Tiny Radiance .hdr sky: warm sun blob, blue sky gradient, grey ground. Uncompressed RGBE."""
    import numpy as np

    v = (np.arange(h) + 0.5) / h  # 0 = top
    u = (np.arange(w) + 0.5) / w
    uu, vv = np.meshgrid(u, v)
    elev = (0.5 - vv) * np.pi
    sky = np.stack([0.35 + 0.3 * np.cos(elev), 0.5 + 0.3 * np.cos(elev), 0.9 * np.ones_like(elev)], -1)
    ground = np.full_like(sky, 0.18)
    img = np.where((elev > 0)[..., None], sky, ground)
    sun_az, sun_el = 0.3 * 2 * np.pi, np.radians(35)
    az = uu * 2 * np.pi
    d = np.arccos(np.clip(np.sin(elev) * np.sin(sun_el) + np.cos(elev) * np.cos(sun_el) * np.cos(az - sun_az), -1, 1))
    img = img + (np.exp(-(d / 0.05) ** 2) * 60)[..., None] * np.array([1.0, 0.85, 0.6])
    m = img.max(-1)
    e = np.ceil(np.log2(np.maximum(m, 1e-32)))
    scale = np.where(m > 1e-32, 256.0 / np.exp2(e), 0)
    rgbe = np.concatenate([np.clip(img * scale[..., None], 0, 255), np.where(m > 1e-32, e + 128, 0)[..., None]], -1)
    with open(path, "wb") as f:
        f.write(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n" + f"-Y {h} +X {w}\n".encode())
        f.write(rgbe.astype(np.uint8).tobytes())


def write_ply_and_3mf() -> None:
    import numpy as np
    import trimesh

    scene = trimesh.load(SCENES / "scene.glb", force="scene")
    mesh = scene.to_geometry() if hasattr(scene, "to_geometry") else scene.dump(concatenate=True)
    mesh = trimesh.Trimesh(mesh.vertices, mesh.faces, process=False)
    # Height-based vertex colours so PLY colour support is visible.
    z = mesh.vertices[:, 1]  # glTF is Y-up
    t = (z - z.min()) / max(np.ptp(z), 1e-9)
    cols = np.stack([t, 0.3 + 0.4 * (1 - t), 1 - t, np.ones_like(t)], -1)
    mesh.visual.vertex_colors = (cols * 255).astype(np.uint8)
    mesh.export(SCENES / "scene.ply")  # PLY has no axis convention; Renda reads it as Y-up
    # 3MF is Z-up by spec; the glTF source is Y-up, so rotate (x, y, z) → (x, -z, y).
    scene.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, (1, 0, 0)))
    scene.export(SCENES / "scene.3mf")


def write_threejs() -> None:
    import base64
    import io
    import json
    import subprocess

    from PIL import Image

    tools = HERE / "tools"
    if not (tools / "node_modules" / "three").exists():
        subprocess.run(["npm", "install", "--silent", "--no-audit", "--no-fund"], cwd=tools, check=True)
    out = SCENES / "threejs" / "scene.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["node", str(tools / "threejs_scene.mjs"), str(out)], check=True)

    # three's toJSON serialises a DataTexture as raw pixel arrays; swap in a PNG data URI —
    # the form browsers produce and the one ObjectLoader expects most often.
    rows = checker_pixels(64)
    im = Image.new("RGBA", (64, 64))
    im.putdata([tuple(int(c * 255) for c in px) for row in rows for px in row])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    doc = json.loads(out.read_text())
    for image in doc.get("images", []):
        image["url"] = uri
    out.write_text(json.dumps(doc, separators=(",", ":")))


def write_placeholder_clip() -> None:
    """The HyperFrames test composition's video slot: a 6 s test pattern (swap in a Renda turntable)."""
    import subprocess

    import imageio_ffmpeg

    out = HERE / "hyperframes" / "title-card" / "assets" / "placeholder.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=480x480:rate=30:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-crf", "32", str(out)], check=True)


def main() -> None:
    import subprocess

    SCENES.mkdir(parents=True, exist_ok=True)
    subprocess.run([BLENDER, "--background", "--factory-startup", "--python", str(Path(__file__).resolve()),
                    "--", str(SCENES)], check=True)
    write_ply_and_3mf()
    write_threejs()
    (HERE / "hdri").mkdir(exist_ok=True)
    write_hdr(HERE / "hdri" / "sky.hdr")
    write_placeholder_clip()
    for p in sorted(SCENES.rglob("*")):
        if p.is_file():
            print(f"{p.relative_to(HERE)}  {p.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    try:
        import bpy  # noqa: F401

        blender_main(Path(sys.argv[sys.argv.index("--") + 1]))
    except ImportError:
        main()
