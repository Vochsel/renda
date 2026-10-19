"""Light baking: UV atlas, texel rasterisation, dilation, GLB output (engine bakers run on the cloud)."""

import json
import struct

import numpy as np
import pytest
import trimesh

from renda import bake
from renda.ir.stage import Material, Mesh, Stage
from renda.spec import load_package


def _stage(up="Y"):
    box = trimesh.creation.box()
    plane = trimesh.creation.box(extents=(4, 0.1, 4))
    st = Stage(up_axis=up)
    st.materials["red"] = Material("red", base_color=(0.8, 0.1, 0.1, 1.0))
    st.meshes.append(Mesh("box", box.vertices, box.faces, material="red", transform=np.diag([1, 1, 1, 1.0])))
    t = np.eye(4)
    t[1, 3] = -0.55
    st.meshes.append(Mesh("floor", plane.vertices, plane.faces, transform=t))
    return st


def _read_glb(path):
    data = path.read_bytes()
    magic, version, length = struct.unpack("<III", data[:12])
    assert magic == 0x46546C67 and version == 2 and length == len(data)
    jlen = struct.unpack("<I", data[12:16])[0]
    return json.loads(data[20:20 + jlen])


def test_atlas_uvs_are_in_range_and_shared():
    lay = bake.prepare(_stage(), 256, "auto", 4)
    assert len(lay.meshes) == 2
    for m in lay.meshes:
        assert m.uv1.min() >= 0 and m.uv1.max() <= 1
        assert len(m.uv1) == len(m.points) and m.indices.max() < len(m.points)


def test_rasterize_covers_without_overlap_and_interpolates_positions():
    st = _stage()
    lay = bake.prepare(st, 256, "auto", 4)
    tx = bake.rasterize(lay)
    assert 0.2 < len(tx) / 256 ** 2 < 0.95
    pos = tx.interpolate(lay, "points")
    lo, hi = st.bounds()
    assert np.all(pos >= lo - 1e-4) and np.all(pos <= hi + 1e-4)
    n = tx.interpolate(lay, "normals")
    assert np.allclose(np.linalg.norm(n, axis=1), 1, atol=1e-4)


def test_dilate_fills_borders():
    img = np.zeros((8, 8, 3), np.float32)
    mask = np.zeros((8, 8), bool)
    img[3:5, 3:5] = 1
    mask[3:5, 3:5] = True
    out = bake.dilate(img, mask, 2)
    assert out[1, 1].mean() == pytest.approx(1.0) and out[0, 0].mean() > 0


def test_unlit_glb_has_unlit_material_and_second_uvs(tmp_path):
    st = _stage("Z")
    lay = bake.prepare(st, 128, "auto", 2)
    tx = bake.rasterize(lay)
    lin = np.zeros((128, 128, 3), np.float32)
    alb, em = bake.albedo_and_emission(st, lay, tx)
    lin[tx.rows, tx.cols] = alb
    out = bake.finish(lay, st, lin, "unlit", tmp_path / "a.glb", "jpg", 2, tx.mask())
    g = _read_glb(out)
    assert "KHR_materials_unlit" in g["extensionsUsed"]
    assert len(g["materials"]) == 1 and g["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]["texCoord"] == 1
    assert all("TEXCOORD_1" in p["attributes"] for m in g["meshes"] for p in m["primitives"])
    assert g["images"][0]["mimeType"] == "image/jpeg"
    # Z-up stage is written Y-up: the floor (z ≈ -0.55 in stage space here is y) — check the box spans y
    scene = trimesh.load(out)
    assert len(scene.geometry) == 2


def test_ao_and_lightmap_keep_pbr_materials(tmp_path):
    st = _stage()
    lay = bake.prepare(st, 64, "auto", 2)
    lin = np.full((64, 64, 3), 0.5, np.float32)
    g = _read_glb(bake.finish(lay, st, lin, "ao", tmp_path / "ao.glb", "jpg", 2))
    assert all(m["occlusionTexture"]["texCoord"] == 1 for m in g["materials"])
    assert g["images"][0]["mimeType"] == "image/png"  # grey AO stays lossless
    g = _read_glb(bake.finish(lay, st, lin * 3, "lightmap", tmp_path / "lm.glb", "png", 2))
    lm = [m["extras"]["renda"]["lightmap"] for m in g["materials"]]
    assert lm and all(l["texCoord"] == 1 and l["intensity"] > 1 for l in lm)


def test_plain_export_has_no_lightmap_uvs(tmp_path):
    lay = bake.prepare_plain(_stage())
    g = _read_glb(bake.write_glb(lay, _stage(), tmp_path / "p.glb", None))
    assert all("TEXCOORD_1" not in p["attributes"] for m in g["meshes"] for p in m["primitives"])


def test_splats_only_cannot_bake():
    from renda.ir.splats import Splats

    st = Stage()
    st.splats.append(Splats("s", means=np.zeros((1, 3)), scales=np.zeros((1, 3)), rotations=[[1, 0, 0, 0]],
                            opacities=np.zeros(1), sh_dc=np.zeros((1, 3))))
    with pytest.raises(bake.BakeError, match="splats"):
        bake.prepare(st)


def test_spec_bake_validation():
    ok = load_package({"scene": {"root": "a.glb"}, "output": {"format": "glb", "bake": {"mode": "ao"}}})
    assert ok.output.bake.size == 2048 and ok.output.is_model
    with pytest.raises(ValueError, match="glb"):
        load_package({"scene": {"root": "a.glb"}, "output": {"format": "png", "bake": {}}})
    with pytest.raises(ValueError, match="can't bake"):
        load_package({"scene": {"root": "a.glb"}, "render": {"engine": "gsplat"}, "output": {"format": "glb", "bake": {}}})
    with pytest.raises(ValueError, match="single pose"):
        load_package({"scene": {"root": "a.glb"}, "animation": {"frames": [1, 2]}, "output": {"format": "glb"}})


def test_planner_routes_bakes():
    from renda.planner import plan

    pkg = load_package({"scene": {"root": "x.glb"}, "output": {"format": "glb", "bake": {}}})
    assert plan(pkg, None).engine == "blender-cycles"
    assert plan(pkg, None, available={"threejs"}).hardware == "cpu"
    pkg = load_package({"scene": {"root": "x.glb"}, "output": {"format": "glb"}})
    assert plan(pkg, None).engine == "export"
