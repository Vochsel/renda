"""Gaussian splats: formats, detection, IR, rasteriser, compositing, planner, engines."""

import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from renda.formats import detect
from renda.ir import load_stage, read_rir, write_rir
from renda.ir.camera import ResolvedCamera
from renda.ir.loaders.splat import read_any, read_ply, read_splat, read_spz, write_ply, write_splat, write_spz
from renda.ir.splats import SH_C0, Splats, logit, sh_basis
from renda.ir.stage import trs

SPLATS = Path(__file__).resolve().parents[2] / "testdata" / "scenes" / "splats"
SCENES = SPLATS.parent


def blob(n=2000, seed=0, sh=True) -> Splats:
    rng = np.random.default_rng(seed)
    means = rng.normal(size=(n, 3)).astype(np.float32) * 0.5
    q = rng.normal(size=(n, 4))
    rest = rng.normal(size=(n, 3, 3)).astype(np.float32) * 0.2 if sh else None
    return Splats("blob", means, np.log(rng.uniform(0.01, 0.05, (n, 3))), q / np.linalg.norm(q, axis=1, keepdims=True),
                  logit(rng.uniform(0.3, 0.95, n)), (rng.uniform(0.1, 0.9, (n, 3)) - 0.5) / SH_C0, rest)


def _same(a: Splats, b: Splats, pos=1e-5, col=1e-5):
    assert len(a) == len(b)
    np.testing.assert_allclose(a.means, b.means, atol=pos)
    np.testing.assert_allclose(a.colors(), b.colors(), atol=col)
    np.testing.assert_allclose(a.alphas(), b.alphas(), atol=max(col, 1e-5))


def test_ply_roundtrip_exact(tmp_path):
    s = blob()
    t = read_ply(write_ply(s, tmp_path / "a.ply"))
    _same(s, t)
    np.testing.assert_allclose(t.sh_rest, s.sh_rest, atol=1e-6)
    np.testing.assert_allclose(t.scales, s.scales, atol=1e-6)


def test_splat_roundtrip_quantised(tmp_path):
    s = blob(sh=False)
    t = read_splat(write_splat(s, tmp_path / "a.splat"))
    order = np.lexsort(t.means.T)
    ref = s.subset(np.lexsort(s.means.T))
    t = t.subset(order)
    _same(ref, t, pos=1e-6, col=3 / 255)
    assert t.sh_rest is None  # .splat stores band 0 only


def test_spz_roundtrip_quantised(tmp_path):
    s = blob()
    t = read_spz(write_spz(s, tmp_path / "a.spz"))
    _same(s, t, pos=1 / 4096, col=3 / 255)
    assert t.sh_degree == 1
    np.testing.assert_allclose(t.sh_rest, s.sh_rest, atol=1 / 128 + 1e-6)
    # rotations survive 8-bit quantisation up to sign
    qa = s.rotations / np.linalg.norm(s.rotations, axis=1, keepdims=True)
    qb = t.rotations / np.linalg.norm(t.rotations, axis=1, keepdims=True)
    assert np.abs(np.abs((qa * qb).sum(1)) - 1).max() < 0.02


@pytest.mark.parametrize("name,fmt", [("garden.ply", "splat"), ("garden.splat", "splat"), ("garden.spz", "splat"),
                                      ("environment.ply", "splat")])
def test_detect_splat_files_and_blobs(name, fmt, tmp_path):
    assert detect(SPLATS / name) == fmt
    blob_path = tmp_path / "ab" / "blob"  # the worker's content-addressed asset cache has no extensions
    blob_path.parent.mkdir()
    shutil.copy(SPLATS / name, blob_path)
    assert detect(blob_path) == "splat"
    s, _ = read_any(blob_path)
    assert len(s) > 1000


def test_mesh_ply_is_not_a_splat():
    assert detect(SCENES / "scene.ply") == "ply"


def test_three_formats_are_the_same_scene():
    """.ply/.splat are Y-down, .spz is Y-up: after build-time normalisation they must coincide."""
    from renda.scene import normalize_up

    bounds = {}
    for name in ("garden.ply", "garden.splat", "garden.spz"):
        st = normalize_up(load_stage(SPLATS / name))
        assert st.up_axis == "Y"
        bounds[name] = np.array(st.bounds())
    for name in ("garden.splat", "garden.spz"):
        np.testing.assert_allclose(bounds[name], bounds["garden.ply"], atol=0.02)
    lo, hi = bounds["garden.ply"]
    assert lo[1] > -0.2 and hi[1] > 1.0  # ground near y=0, sculpture above it: right way up


def test_robust_bounds_ignore_floaters():
    s = read_ply(SPLATS / "garden.ply")
    lo, hi = s.robust_bounds()
    assert np.abs(lo).max() < 3 and np.abs(hi).max() < 3  # floaters are ~12 m out
    raw = s.world_means()
    assert np.abs(raw).max() > 10


def test_bake_rotates_positions_and_view_dependent_colour():
    s = blob(200)
    m = trs((1, 2, 3), (30, 50, -20), 2.0)
    s.transform = m
    b = s.baked()
    np.testing.assert_allclose(b.means, s.world_means(), atol=1e-4)
    np.testing.assert_allclose(b.scales, s.scales + np.log(2.0), atol=1e-5)
    # SH colour seen from world direction d after baking == colour seen from local direction R^T d before
    rot = m[:3, :3] / 2.0
    d = np.random.default_rng(1).normal(size=(200, 3))
    d /= np.linalg.norm(d, axis=1, keepdims=True)
    before = np.einsum("nk,nkc->nc", sh_basis(d @ rot, 1)[:, 1:], s.sh_rest)
    after = np.einsum("nk,nkc->nc", sh_basis(d, 1)[:, 1:], b.sh_rest)
    np.testing.assert_allclose(after, before, atol=1e-4)


def test_rir_roundtrip_with_splats(tmp_path):
    st = load_stage(SPLATS / "garden.spz")
    back = read_rir(write_rir(st, tmp_path / "g.rir"))
    assert len(back.splats) == 1
    _same(st.splats[0], back.splats[0])
    assert back.splats[0].sh_degree == 1


def test_numpy_rasteriser_single_splat():
    from renda.engines.splat_raster import rasterize

    s = Splats("one", [[0, 0, 0]], np.log([[0.2, 0.2, 0.2]]), [[1, 0, 0, 0]], logit(np.array([0.95])),
               (np.array([[0.9, 0.2, 0.1]]) - 0.5) / SH_C0)
    cam = ResolvedCamera(np.array([0, 0, 3.0]), np.zeros(3), np.array([0, 1.0, 0]), 40)
    layer = rasterize(s, cam, 65, 65, backend="numpy")
    c = layer.alpha[32, 32]
    assert 0.9 < c <= 0.99
    np.testing.assert_allclose(layer.straight()[32, 32], [0.9, 0.2, 0.1], atol=0.01)
    assert abs(layer.depth[32, 32] - 3.0) < 1e-3
    assert layer.alpha[0, 0] < 0.01 and not np.isfinite(layer.depth[0, 0])


def test_composite_depth_order():
    from renda.engines.splat_raster import SplatLayer, composite_over

    h, w = 1, 2
    layer = SplatLayer(np.full((h, w, 3), 1.0), np.ones((h, w)), np.array([[1.0, 5.0]]))  # white, opaque
    mesh = np.zeros((h, w, 3))  # black mesh at depth 3 covering both pixels
    rgb, a = composite_over(layer, mesh, np.ones((h, w)), np.full((h, w), 3.0), np.zeros((h, w, 3)), np.ones((h, w)))
    assert rgb[0, 0, 0] == pytest.approx(1.0)  # splat in front
    assert rgb[0, 1, 0] == pytest.approx(0.0)  # splat behind the mesh
    assert np.all(a == 1)


def test_planner_picks_engines_for_splats():
    from renda.planner import plan
    from renda.spec import load_package

    assets = {"g": SPLATS / "garden.ply", "t": SCENES / "scene.glb"}
    solo = load_package({"assets": {"g": {"uri": "g.ply"}}, "scene": {"root": "g"}})
    p = plan(solo, {"g": assets["g"]})
    assert p.engine == "gsplat" and p.hardware.startswith("gpu") and p.splats
    mixed = load_package({"assets": {"g": {"uri": "g.ply"}, "t": {"uri": "t.glb"}},
                          "scene": {"root": "g", "include": [{"asset": "t"}]}})
    p = plan(mixed, assets)
    assert p.engine == "blender-cycles" and p.splats
    mesh_root = load_package({"assets": {"g": {"uri": "g.ply"}, "t": {"uri": "t.glb"}},
                              "scene": {"root": "t", "include": [{"asset": "g"}]}, "render": {"quality": "draft"}})
    p = plan(mesh_root, assets)
    assert p.splats
    local = plan(solo, {"g": assets["g"]}, available={"threejs", "mitsuba"})
    assert local.engine == "threejs"


def test_spec_accepts_splat_fields():
    from renda.spec import load_package

    pkg = load_package({"scene": {"root": "x.spz", "upAxis": "-Y"}, "render": {"engine": "gsplat"}})
    assert pkg.scene.upAxis == "-Y" and pkg.render.engine == "gsplat"


def test_gsplat_engine_cli_numpy(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from renda.cli import app

    monkeypatch.setenv("RENDA_SPLAT_BACKEND", "numpy")
    from renda.engines import splat_raster

    splat_raster.gsplat_available.cache_clear()
    pkg = tmp_path / "p.renda.json"
    pkg.write_text(json.dumps({"version": 1, "scene": {"root": str(SPLATS / "garden.spz")},
                               "render": {"engine": "gsplat", "resolution": [96, 64]},
                               "output": {"format": "png", "passes": ["beauty", "alpha", "depth"]}}))
    r = CliRunner().invoke(app, ["render", str(pkg), "-o", str(tmp_path / "out"), "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output.strip().splitlines()[-1])
    names = sorted(Path(p).name for p in out["outputs"])
    assert names == ["alpha_0001.png", "depth_0001.png", "frame_0001.png"]
    from PIL import Image

    img = np.asarray(Image.open(tmp_path / "out" / "frame_0001.png").convert("RGB"), dtype=float)
    assert img.std() > 10  # something rendered over the background


@pytest.mark.parametrize("ext", [".spz", ".splat", ".ply"])
def test_convert_keeps_scene_axes(ext, tmp_path):
    from typer.testing import CliRunner

    from renda.cli import app
    from renda.scene import normalize_up

    out = tmp_path / f"g{ext}"
    r = CliRunner().invoke(app, ["convert", str(SPLATS / "garden.ply"), str(out), "--json"])
    assert r.exit_code == 0, r.output
    a = np.array(normalize_up(load_stage(SPLATS / "garden.ply")).bounds())
    b = np.array(normalize_up(load_stage(out)).bounds())
    np.testing.assert_allclose(a, b, atol=0.01)


def test_info_reports_splats():
    from typer.testing import CliRunner

    from renda.cli import app

    r = CliRunner().invoke(app, ["info", str(SPLATS / "garden.spz"), "--json"])
    d = json.loads(r.output.strip().splitlines()[-1])
    assert d["splats"] > 40000 and d["shDegree"] == 1 and d["meshes"] == 0
