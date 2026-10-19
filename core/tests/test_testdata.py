"""Every file in testdata/scenes loads and every testdata/packages package validates.

Renders live in `testdata/smoke.py --render --matrix` (slow, needs the engines installed).
"""

from pathlib import Path

import pytest

from renda.ir.loaders import load_stage
from renda.ir.loaders.blender import available as blender_available
from renda.spec import load_package

TESTDATA = Path(__file__).resolve().parents[2] / "testdata"
SCENES = sorted(p for p in (TESTDATA / "scenes").glob("*.*") if p.suffix not in (".mtl", ".bin", ".png")) + [
    TESTDATA / "scenes" / "threejs" / "scene.json"
] + sorted((TESTDATA / "scenes" / "cad").glob("*.*"))
NEEDS_BLENDER = {".blend", ".fbx", ".abc"}
CAD = {".step", ".stp", ".iges", ".igs"}


@pytest.mark.parametrize("path", SCENES, ids=lambda p: str(p.relative_to(TESTDATA / "scenes")))
def test_scene_loads(path, tmp_path):
    if path.suffix in NEEDS_BLENDER and not blender_available():
        pytest.skip("needs Blender")
    if path.suffix in (".usda", ".usdz"):
        pytest.importorskip("pxr")
    if path.suffix in CAD:
        pytest.importorskip("cascadio")
    stage = load_stage(path, cache_dir=tmp_path) if path.suffix in NEEDS_BLENDER | CAD else load_stage(path)
    assert stage.meshes
    # CAD samples are mostly planar: IGES is 12 flat patches, the STEP bracket ~870 triangles at default density
    min_tris = {".iges": 20, ".igs": 20, ".step": 500, ".stp": 500}.get(path.suffix, 1000)
    assert sum(len(m.indices) for m in stage.meshes) > min_tris
    if path.suffix not in (".stl", ".ply", ".3mf", ".abc", ".iges", ".igs"):  # formats/exports without materials
        assert stage.materials


@pytest.mark.parametrize("path", sorted((TESTDATA / "packages").glob("*.renda.json")), ids=lambda p: p.name)
def test_package_validates(path):
    pkg = load_package(path)
    assert pkg.scene.root


def test_testdata_is_small():
    # committed files only: rendered outputs and fetched third-party assets are gitignored
    skip = {"out", "node_modules", "assets", "probe"}
    total = sum(p.stat().st_size for p in TESTDATA.rglob("*") if p.is_file() and not skip & set(p.parts))
    assert total < 20 * 1024 * 1024
