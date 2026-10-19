"""STEP / IGES input via OpenCASCADE (cascadio)."""

import shutil
from pathlib import Path

import numpy as np
import pytest

from renda.formats import detect
from renda.ir import load_stage
from renda.ir.stage import transform_points
from renda.planner import choose_engine
from renda.spec import Tessellation, format_from_path, load_package

pytest.importorskip("cascadio")

CAD = Path(__file__).resolve().parents[2] / "testdata" / "scenes" / "cad"
STEP, ASM, IGES = CAD / "bracket.step", CAD / "bracket_assembly.stp", CAD / "plate_block.igs"


def world_bounds(stage):
    pts = np.concatenate([transform_points(m.transform, m.points) for m in stage.meshes])
    return pts.min(axis=0), pts.max(axis=0)


def test_extensions_and_sniffing(tmp_path):
    assert format_from_path("part.STEP") == "step" and format_from_path("a.stp") == "step"
    assert format_from_path("a.igs") == "iges" and format_from_path("https://x/a.iges?sig=1") == "iges"
    assert detect(STEP) == "step" and detect(IGES) == "iges"
    for src, fmt in [(STEP, "step"), (IGES, "iges")]:
        blob = tmp_path / ("ab" * 32)  # the worker's asset cache has no extensions
        shutil.copy(src, blob)
        assert detect(blob) == fmt
        blob.unlink()


@pytest.mark.parametrize("path", [STEP, ASM], ids=["part", "assembly"])
def test_step_metres_z_up_with_colours(path, tmp_path):
    stage = load_stage(path, cache_dir=tmp_path)
    assert stage.up_axis == "Z" and stage.source_format == "step"
    lo, hi = world_bounds(stage)
    assert np.allclose(lo, [0, 0, 0], atol=1e-4) and np.allclose(hi, [0.12, 0.08, 0.044], atol=1e-4)  # mm → m
    colours = {tuple(round(c, 1) for c in m.base_color[:3]) for m in stage.materials.values()}
    assert len(colours) >= 3, colours  # grey plate, orange bosses, blue knob
    assert stage.meta["tessellation"]["linearDeflection"] == pytest.approx(0.0005 * np.linalg.norm([0.12, 0.08, 0.044]), rel=1e-3)


def test_iges_is_converted_from_millimetres(tmp_path):
    stage = load_stage(IGES, cache_dir=tmp_path)
    lo, hi = world_bounds(stage)
    assert np.allclose(hi, [0.12, 0.08, 0.04], atol=1e-4)
    assert any("colours" in w for w in stage.meta.get("warnings", []))


def test_tessellation_density_follows_deflection(tmp_path):
    coarse = load_stage(STEP, cache_dir=tmp_path, tessellation=Tessellation(linearDeflection=0.002, angularDeflection=1.0))
    fine = load_stage(STEP, cache_dir=tmp_path, tessellation=Tessellation(linearDeflection=0.00002, angularDeflection=0.1))
    # the plate and the polygonal knob are planar (fixed triangle counts); the true cylinders densify
    assert fine.triangle_count() > 2 * coarse.triangle_count()
    assert len(list((tmp_path / "cad").glob("*.glb"))) == 2  # cached per tolerance


def test_package_with_step_root_and_unit_scale(tmp_path):
    from renda.assets import resolve
    from renda.scene import build_stage

    shutil.copy(STEP, tmp_path / "bracket.step")
    pkg = load_package({"scene": {"root": "./bracket.step", "unitScale": 10, "tessellation": {"angularDeflection": 0.5}}})
    stage, _ = build_stage(pkg, resolve(pkg, tmp_path), tmp_path / "cache")
    lo, hi = world_bounds(stage)
    assert np.allclose(hi, [1.2, 0.8, 0.44], atol=1e-3)


def test_planner_treats_cad_like_product_shots():
    assert choose_engine("step", "standard", None)[0] == "mitsuba"
    assert choose_engine("iges", "final", None)[0] == "blender-cycles"
