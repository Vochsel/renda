"""Render-quality fixes: vertex colours, 3MF colours, OIDN denoising, default clay material."""

import json
import struct
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pytest

from renda.ir.exporters import to_gltf
from renda.ir.loaders import load_stage
from renda.ir.loaders import threemf
from renda.ir.stage import Mesh, Stage

TESTDATA = Path(__file__).resolve().parents[2] / "testdata" / "scenes"


def _glb_json(path: Path) -> dict:
    b = path.read_bytes()
    n = struct.unpack_from("<I", b, 12)[0]
    assert struct.unpack_from("<I", b, 8)[0] == len(b)  # header length still right after patching
    return json.loads(b[20:20 + n])


# ---- vertex colours ---------------------------------------------------------------------------------


def test_ply_vertex_colours_are_linear():
    st = load_stage(TESTDATA / "scene.ply")
    c = st.meshes[0].colors
    assert c is not None and c.shape[1] >= 3
    # the generator writes 8-bit sRGB; linear values sit below their sRGB encoding for mid tones
    assert 0.0 <= c.min() and c.max() <= 1.0


def test_gltf_export_writes_color_0_next_to_the_material(tmp_path):
    tri = Mesh(name="tri", points=[[0, 0, 0], [1, 0, 0], [0, 1, 0]], indices=[[0, 1, 2]],
               colors=np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], np.float32), material="paint")
    from renda.ir.stage import Material

    st = Stage(meshes=[tri], materials={"paint": Material(name="paint", roughness=0.7)})
    j = _glb_json(to_gltf(st, tmp_path / "x.glb"))
    attrs = j["meshes"][0]["primitives"][0]["attributes"]
    assert "COLOR_0" in attrs and "_COLOR_0" not in attrs
    assert j["materials"][0]["name"] == "paint"  # PBR material kept, not replaced by a colour visual
    # .gltf (JSON) path is patched too
    g = json.loads(to_gltf(st, tmp_path / "x.gltf").read_text())
    assert "COLOR_0" in g["meshes"][0]["primitives"][0]["attributes"]


def test_meshes_without_colours_have_no_color_attribute(tmp_path):
    st = load_stage(TESTDATA / "scene.glb")
    j = _glb_json(to_gltf(st, tmp_path / "y.glb"))
    for m in j["meshes"]:
        for p in m["primitives"]:
            assert "COLOR_0" not in p["attributes"]


# ---- 3MF ----------------------------------------------------------------------------------------------


def test_3mf_basematerials_become_materials():
    st = load_stage(TESTDATA / "scene.3mf")
    by_mesh = {m.name: st.materials[m.material] for m in st.meshes}
    assert set(by_mesh) == {"Cylinder", "CheckerCube", "Sphere", "TorusKnot", "Table"}
    red = by_mesh["Sphere"].base_color
    assert red[0] > 0.5 and red[1] < 0.2 and red[2] < 0.2
    assert by_mesh["Cylinder"].base_color[2] > by_mesh["Cylinder"].base_color[0]  # blue


def _write_3mf(path: Path, model: str) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", model)
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    return path


def test_3mf_triangle_colours_components_and_transforms(tmp_path):
    model = """<?xml version="1.0"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
       xmlns:m="http://schemas.microsoft.com/3dmanufacturing/material/2015/02">
 <resources>
  <basematerials id="1"><base name="steel" displaycolor="#808080"/><base name="paint" displaycolor="#FF0000"/></basematerials>
  <m:colorgroup id="2"><m:color color="#00FF00FF"/></m:colorgroup>
  <object id="3" name="part" pid="1" pindex="0"><mesh>
   <vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/><vertex x="1" y="1" z="0"/></vertices>
   <triangles><triangle v1="0" v2="1" v3="2"/><triangle v1="1" v2="3" v3="2" pid="1" p1="1"/></triangles>
  </mesh></object>
  <object id="4" name="leaf" pid="2" pindex="0"><mesh>
   <vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="0" z="1"/></vertices>
   <triangles><triangle v1="0" v2="1" v3="2"/></triangles>
  </mesh></object>
  <object id="5" name="asm"><components><component objectid="4" transform="1 0 0 0 1 0 0 0 1 5 0 0"/></components></object>
 </resources>
 <build><item objectid="3"/><item objectid="5" transform="1 0 0 0 1 0 0 0 1 0 10 0"/></build>
</model>"""
    st = threemf.load(_write_3mf(tmp_path / "t.3mf", model))
    mats = {m.name: st.materials[m.material] for m in st.meshes}
    assert set(mats) == {"part_0", "part_1", "leaf"}  # part split by per-triangle override
    colours = sorted(tuple(round(c, 2) for c in m.base_color[:3]) for m in mats.values())
    assert (1.0, 0.0, 0.0) in colours and (0.0, 1.0, 0.0) in colours
    leaf = next(m for m in st.meshes if m.name == "leaf")
    # component transform (+5 x) composed with the build item (+10 y)
    assert np.allclose(leaf.transform[:3, 3], [5, 10, 0])


def test_3mf_colour_parsing():
    assert threemf.parse_color("#FFFFFF") == (1.0, 1.0, 1.0, 1.0)
    r, g, b, a = threemf.parse_color("#80808080")
    assert abs(r - 0.2158) < 1e-3 and abs(a - 128 / 255) < 1e-6  # sRGB 0.5 → linear ~0.216
    assert threemf.parse_color("nope") is None


# ---- denoising -----------------------------------------------------------------------------------------


def test_suppress_fireflies_clamps_isolated_hot_pixels_only():
    from renda.denoise import suppress_fireflies

    img = np.full((40, 50, 3), 0.3, np.float32)
    img[10, 10] = 50.0  # firefly
    img[20:30, 20:30] = 5.0  # a genuinely bright region
    out = suppress_fireflies(img)
    assert out[10, 10, 0] < 2.0
    assert np.allclose(out[25, 25], 5.0) and np.allclose(out[0, 0], 0.3)


def test_denoise_falls_back_without_oidn(monkeypatch):
    from renda import denoise

    monkeypatch.setitem(denoise._STATE, "ok", False)
    img = np.random.default_rng(0).random((8, 8, 3)).astype(np.float32)
    assert denoise.denoise_rgb(img) is img


def test_oidn_reduces_noise():
    from renda import denoise

    if not denoise.available():
        pytest.skip("pyoidn not installed")
    rng = np.random.default_rng(0)
    clean = np.tile(np.linspace(0.2, 0.8, 96, dtype=np.float32)[None, :, None], (64, 1, 3))
    noisy = np.clip(clean + rng.normal(0, 0.05, clean.shape).astype(np.float32), 0, None)
    out = denoise.denoise_rgb(noisy, np.full_like(clean, 0.5), np.dstack([np.zeros((64, 96, 2), np.float32), np.ones((64, 96), np.float32)]))
    assert np.abs(out - clean).mean() < 0.4 * np.abs(noisy - clean).mean()


def test_mitsuba_uses_box_filter_for_oidn_unless_antialias_is_set():
    from renda.engines.mitsuba import _rfilter
    from renda.spec import load_package

    plain = load_package({"version": 1, "scene": {"root": "a.glb"}})
    assert _rfilter(plain) == {"type": "gaussian"}
    assert _rfilter(plain, for_oidn=True) == {"type": "box"}
    explicit = load_package({"version": 1, "scene": {"root": "a.glb"}, "render": {"antialias": {"filter": "mitchell"}}})
    assert _rfilter(explicit, for_oidn=True) == {"type": "mitchell"}


# ---- Blender: clay defaults + vertex colours (real Blender, no render) ---------------------------------


def test_blender_wires_ply_vertex_colours_into_clay_material(tmp_path):
    from renda.ir.loaders.blender import blender_binary

    exe = blender_binary()
    if not exe:
        pytest.skip("blender not installed")
    script = Path(__file__).parents[1] / "renda" / "engines" / "blender_scripts" / "render.py"
    out = tmp_path / "mats.json"
    expr = f"""
import bpy, json, importlib.util, sys
sys.argv = ["x"]
spec = importlib.util.spec_from_file_location("rr", {str(script)!r}); rr = importlib.util.module_from_spec(spec)
sys.path.insert(0, {str(script.parent)!r}); spec.loader.exec_module(rr)
bpy.ops.wm.read_factory_settings(use_empty=True)
from _common import import_file
import_file({str(TESTDATA / "scene.ply")!r}, "ply", "Y")
bpy.ops.mesh.primitive_cube_add()  # a second, colourless object: plain clay
scene = bpy.context.scene
rr._ensure_materials(scene); rr.wire_vertex_colors(scene)
res = {{}}
for ob in rr._mesh_objects(scene):
    m = ob.material_slots[0].material
    base = rr.principled(m).inputs["Base Color"]
    res[ob.name] = {{"material": m.name, "linked": base.is_linked,
                    "from": base.links[0].from_node.type if base.is_linked else None,
                    "color": list(base.default_value)}}
json.dump(res, open({str(out)!r}, "w"))
"""
    subprocess.run([exe, "-b", "--factory-startup", "--python-expr", expr], check=True, capture_output=True, timeout=180)
    res = json.loads(out.read_text())
    ply = next(v for k, v in res.items() if k != "Cube")
    assert ply["material"].startswith("renda_default:") and ply["linked"] and ply["from"] == "MIX"
    cube = res["Cube"]
    assert cube["material"] == "renda_default" and not cube["linked"]
    assert [round(c, 2) for c in cube["color"]] == [0.8, 0.8, 0.8, 1.0]
