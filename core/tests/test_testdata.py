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
]
NEEDS_BLENDER = {".blend", ".fbx", ".abc"}


@pytest.mark.parametrize("path", SCENES, ids=lambda p: str(p.relative_to(TESTDATA / "scenes")))
def test_scene_loads(path, tmp_path):
    if path.suffix in NEEDS_BLENDER and not blender_available():
        pytest.skip("needs Blender")
    if path.suffix in (".usda", ".usdz"):
        pytest.importorskip("pxr")
    stage = load_stage(path, cache_dir=tmp_path) if path.suffix in NEEDS_BLENDER else load_stage(path)
    assert stage.meshes
    assert sum(len(m.indices) for m in stage.meshes) > 1000
    if path.suffix not in (".stl", ".ply", ".3mf", ".abc"):  # formats/exports without materials
        assert stage.materials


@pytest.mark.parametrize("path", sorted((TESTDATA / "packages").glob("*.renda.json")), ids=lambda p: p.name)
def test_package_validates(path):
    pkg = load_package(path)
    assert pkg.scene.root


def test_testdata_is_small():
    total = sum(p.stat().st_size for p in TESTDATA.rglob("*") if p.is_file() and "out" not in p.parts
                and "node_modules" not in p.parts)
    assert total < 20 * 1024 * 1024
