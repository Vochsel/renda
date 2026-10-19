import json

import pytest
from fixtures import write_mesh_files, write_threejs

from renda.formats import UnknownFormat, detect


def test_mesh_formats(tmp_path):
    files = write_mesh_files(tmp_path)
    assert detect(files["glb"]) == "gltf"
    assert detect(files["stl"]) == "stl"
    assert detect(files["obj"]) == "obj"
    assert detect(files["ply"]) == "ply"


def test_magic_beats_extension(tmp_path):
    files = write_mesh_files(tmp_path)
    disguised = tmp_path / "model.bin"
    disguised.write_bytes(files["glb"].read_bytes())
    assert detect(disguised) == "gltf"
    blend = tmp_path / "x.dat"
    blend.write_bytes(b"BLENDER-v402" + b"\0" * 100)
    assert detect(blend) == "blend"
    usda = tmp_path / "x.txt"
    usda.write_text("#usda 1.0\n")
    assert detect(usda) == "usd"


def test_extensionless_stl(tmp_path):
    files = write_mesh_files(tmp_path)
    p = tmp_path / "part"
    p.write_bytes(files["stl"].read_bytes())
    assert detect(p) == "stl"


def test_threejs_json_sniffing(tmp_path):
    assert detect(write_threejs(tmp_path)) == "threejs"
    other = tmp_path / "data.json"
    other.write_text(json.dumps({"hello": "world"}))
    with pytest.raises(UnknownFormat, match="three.js"):
        detect(other)


def test_rir_dir(tmp_path):
    (tmp_path / "s.rir").mkdir()
    (tmp_path / "s.rir" / "stage.json").write_text("{}")
    assert detect(tmp_path / "s.rir") == "rir"


def test_sidecars_rejected(tmp_path):
    import pytest
    from renda.formats import UnknownFormat, detect

    mtl = tmp_path / "scene.mtl"
    mtl.write_text("# Blender MTL File\nnewmtl red\nKd 1 0 0\n")
    with pytest.raises(UnknownFormat, match="material library"):
        detect(mtl)
    commented = tmp_path / "notes"
    commented.write_text("# just a comment\n")
    with pytest.raises(UnknownFormat):
        detect(commented)


def test_detect_extensionless_blobs(tmp_path):
    """The worker's asset cache names files by sha256 with no extension."""
    import shutil
    from pathlib import Path

    scenes = Path(__file__).parents[2] / "testdata" / "scenes"
    expected = {"scene.blend": "blend", "scene.usdz": "usd", "threejs/scene.json": "threejs", "scene.3mf": "3mf",
                "scene.glb": "gltf", "scene.fbx": "fbx", "scene.abc": "abc", "scene.stl": "stl", "scene.ply": "ply"}
    for name, fmt in expected.items():
        blob = tmp_path / name.replace("/", "_").split(".")[0]
        shutil.copy(scenes / name, blob)
        assert detect(blob) == fmt, name


def test_with_extensions_names_blobs(tmp_path):
    import shutil
    from pathlib import Path
    from renda.render import with_extensions

    scenes = Path(__file__).parents[2] / "testdata" / "scenes"
    paths = {}
    for name in ("scene.usdz", "scene.blend", "scene.glb", "threejs/scene.json", "scene.stl"):
        d = tmp_path / "db" / name.replace("/", "_")
        d.mkdir(parents=True)
        shutil.copy(scenes / name, d / "blob")  # the worker's asset-cache layout
        paths[name] = d / "blob"
    named = with_extensions(paths, tmp_path / "named")
    assert {k: v.suffix for k, v in named.items()} == {
        "scene.usdz": ".usdz", "scene.blend": ".blend", "scene.glb": ".glb", "threejs/scene.json": ".json", "scene.stl": ".stl"}
