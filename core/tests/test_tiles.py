"""Fast mode: rush stills split into tiles, splat rush trains on an L40S."""

import numpy as np

from renda.planner import plan, tile_grid, tile_owned
from renda.spec import load_package

ENGINES = {"blender-cycles", "blender-eevee", "blender-workbench", "mitsuba", "threejs", "gsplat"}
ROOT = "asset://" + "a" * 64


def _pkg(extra=None, **render):
    d = {"version": 1, "scene": {"root": ROOT},
         "render": {"engine": "blender-cycles", "resolution": [3840, 2160], "quality": "final", **render}}
    d.update(extra or {})
    return load_package(d)


def test_rush_4k_still_is_tiled_and_standard_is_not():
    rush = plan(_pkg(), None, "rush", available=ENGINES)
    std = plan(_pkg(), None, "standard", available=ENGINES)
    assert std.tiles is None and len(std.chunks) == 1
    assert rush.tiles and len(rush.chunks) == len(rush.tiles) > 1
    assert rush.est_wall_seconds[1] < std.est_wall_seconds[1]
    assert any("tiles" in w for w in rush.warnings)


def test_small_or_screen_space_or_passes_not_tiled():
    assert plan(_pkg(resolution=[1280, 720]), None, "rush", available=ENGINES).tiles is None
    assert plan(_pkg({"post": {"vignette": 0.3}}), None, "rush", available=ENGINES).tiles is None
    assert plan(_pkg({"output": {"passes": ["beauty", "depth"]}}), None, "rush", available=ENGINES).tiles is None


def test_tile_owned_regions_cover_frame_exactly_once():
    for w, h, n in [(3840, 2160, 8), (4096, 4096, 16), (2560, 1600, 3), (2000, 3000, 5)]:
        cov = np.zeros((h, w), np.int8)
        for b in tile_grid(w, h, n):
            x0, y0, x1, y1 = tile_owned(b, w, h, 32)
            assert b[0] <= x0 < x1 <= b[2] and b[1] <= y0 < y1 <= b[3]
            cov[y0:y1, x0:x1] += 1
        assert (cov == 1).all()


def test_splat_rush_trains_on_l40s():
    pkg = load_package({"version": 1, "scene": {"root": ROOT},
                        "output": {"format": "splat", "splatBake": {"preset": "standard"}}})
    std = plan(pkg, None, "standard", available=ENGINES)
    rush = plan(pkg, None, "rush", available=ENGINES)
    assert std.splat_bake["train_hardware"] == "gpu-l4"
    assert rush.splat_bake["train_hardware"] == "gpu-l40s"
    assert not rush.splat_bake["single_container"] and len(rush.chunks) > 1
    assert rush.est_wall_seconds[1] < std.est_wall_seconds[1]
