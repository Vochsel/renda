"""glTF textures on a second UV set (texCoord 1), as Renda's own lightmap bakes write them."""

import io
import json
import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from renda.ir.exporters import to_gltf
from renda.ir.loaders import load_stage
from renda.ir.loaders.mesh_formats import _flip_v, _read_accessor, _read_gltf

UV1 = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]], dtype=np.float32)


def _glb(path: Path, unlit: bool = True) -> Path:
    """A quad whose texture samples TEXCOORD_1; TEXCOORD_0 is deliberately degenerate (all zeros)."""
    pos = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float32)
    nrm = np.tile(np.array([0, 0, 1], dtype=np.float32), (4, 1))
    uv0 = np.zeros((4, 2), dtype=np.float32)
    idx = np.array([0, 1, 2, 0, 2, 3], dtype=np.uint16)
    png = io.BytesIO()
    Image.new("RGB", (2, 2), (200, 30, 30)).save(png, format="PNG")
    blobs = [pos.tobytes(), nrm.tobytes(), uv0.tobytes(), UV1.tobytes(), idx.tobytes(), png.getvalue()]
    views, off, body = [], 0, b""
    for b in blobs:
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(b)})
        pad = b + b"\0" * (-len(b) % 4)
        body += pad
        off += len(pad)
    acc = lambda v, n, t, ct=5126, **kw: {"bufferView": v, "count": n, "type": t, "componentType": ct, **kw}  # noqa: E731
    mat = {"name": "baked", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0, "texCoord": 1}}}
    if unlit:
        mat["extensions"] = {"KHR_materials_unlit": {}}
    doc = {
        "asset": {"version": "2.0"},
        "extensionsUsed": ["KHR_materials_unlit"] if unlit else [],
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "quad"}],
        "meshes": [{"name": "quad", "primitives": [{
            "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2, "TEXCOORD_1": 3}, "indices": 4, "material": 0}]}],
        "materials": [mat],
        "textures": [{"source": 0}], "images": [{"bufferView": 5, "mimeType": "image/png"}],
        "accessors": [acc(0, 4, "VEC3", min=[0, 0, 0], max=[1, 1, 0]), acc(1, 4, "VEC3"), acc(2, 4, "VEC2"),
                      acc(3, 4, "VEC2"), acc(4, 6, "SCALAR", 5123)],
        "bufferViews": views, "buffers": [{"byteLength": len(body)}],
    }
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    chunks = struct.pack("<I4s", len(js), b"JSON") + js + struct.pack("<I4s", len(body), b"BIN\0") + body
    path.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks)
    return path


def test_texcoord1_becomes_the_active_uv_set(tmp_path):
    st = load_stage(_glb(tmp_path / "quad.glb"))
    (m,) = st.meshes
    mat = st.materials[m.material]
    assert mat.unlit and mat.tex_coord == {"base_color_tex": 1}
    assert np.allclose(np.sort(m.uvs, axis=0), np.sort(_flip_v(UV1), axis=0))  # textures sample set 1
    assert np.allclose(m.uvs_alt, _flip_v(np.zeros((4, 2), dtype=np.float32)))  # set 0 kept


def test_round_trips_through_the_gltf_exporter(tmp_path):
    st = load_stage(_glb(tmp_path / "quad.glb"))
    out = to_gltf(st, tmp_path / "out.glb")
    doc, _ = _read_gltf(out)
    prim = doc["meshes"][0]["primitives"][0]
    assert "TEXCOORD_1" in prim["attributes"] and "_TEXCOORD_1" not in prim["attributes"]
    assert "KHR_materials_unlit" in doc["materials"][0].get("extensions", {})
    back = load_stage(out)
    (a,), (b,) = st.meshes, back.meshes
    assert np.allclose(np.sort(a.uvs, axis=0), np.sort(b.uvs, axis=0))
    assert back.materials[b.material].unlit


BAKE = Path(__file__).parents[2] / "web" / "public" / "bakes" / "table-cycles.glb"


@pytest.mark.skipif(not BAKE.exists(), reason="bake demo asset not present")
def test_renda_bake_output_uses_its_lightmap_uvs():
    st = load_stage(BAKE)
    doc, binc = _read_gltf(BAKE)
    prims = [p for me in doc["meshes"] for p in me["primitives"]]
    t1 = [_flip_v(_read_accessor(doc, binc, BAKE, p["attributes"]["TEXCOORD_1"])) for p in prims]
    for m in st.meshes:
        assert st.materials[m.material].unlit
        assert any(len(t) == len(m.uvs) and np.allclose(t, m.uvs, atol=1e-4) for t in t1), m.name
