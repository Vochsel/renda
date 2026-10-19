import math

import numpy as np
import pytest

from renda.ir.camera import frame_bounds


def _ndc(cam, pts, aspect):
    fwd = cam.target - cam.position
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, cam.up)
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    rel = pts - cam.position
    depth = rel @ fwd
    t = math.tan(math.radians(cam.fov_y_deg) / 2)
    return (rel @ right) / depth / (t * aspect), (rel @ up) / depth / t


def _corners(lo, hi):
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])], float)


# a car (long, low), a statue (tall, thin), a flat diorama
BOXES = [([-1.5, 0, -3], [1.5, 1.9, 3.3]), ([-0.35, 0, -0.3], [0.35, 2.1, 0.3]), ([-30, 0, -20], [30, 8, 20])]


@pytest.mark.parametrize("lo,hi", BOXES)
@pytest.mark.parametrize("aspect", [16 / 10, 4 / 3, 1.0])
def test_auto_framing_fills_frame_without_clipping(lo, hi, aspect):
    lo, hi = np.array(lo, float), np.array(hi, float)
    cam = frame_bounds(lo, hi, np.array([0, 1.0, 0]), 30, aspect, elevation_deg=12, azimuth_deg=35, padding=0.06)
    x, y = _ndc(cam, _corners(lo, hi), aspect)
    extent = max(np.abs(x).max(), np.abs(y).max())
    assert extent <= 1.0 + 1e-6  # nothing clipped
    assert extent >= 1 / 1.06 - 0.02  # and the tighter axis is filled up to the padding
    # centred: the box's projection is balanced in the frame
    assert abs(x.max() + x.min()) < 0.05 and abs(y.max() + y.min()) < 0.05


@pytest.mark.parametrize("lo,hi", BOXES)
def test_orbit_framing_never_clips_during_turntable(lo, hi):
    lo, hi = np.array(lo, float), np.array(hi, float)
    up = np.array([0, 1.0, 0])
    base = frame_bounds(lo, hi, up, 30, 4 / 3, elevation_deg=10, azimuth_deg=0, padding=0.08, orbit=True)
    rel = base.position - base.target
    for deg in range(0, 360, 10):
        a = math.radians(deg)
        rot = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
        cam = type(base)(base.target + rot @ rel, base.target, up, base.fov_y_deg)
        x, y = _ndc(cam, _corners(lo, hi), 4 / 3)
        assert max(np.abs(x).max(), np.abs(y).max()) <= 1.0 + 1e-6, deg


def test_blender_auto_framing_matches_core(tmp_path):
    """Blender's in-process auto camera (render.py frame_auto) must frame like core's frame_bounds, so a
    package looks the same in Cycles/Eevee as in Mitsuba/three.js/gsplat."""
    import json
    import subprocess
    from pathlib import Path

    import numpy as np
    import pytest

    from renda.ir.loaders.blender import blender_binary
    from renda.ir.camera import frame_bounds

    exe = blender_binary()
    if not exe:
        pytest.skip("blender not installed")
    script = Path(__file__).parents[1] / "renda" / "engines" / "blender_scripts" / "render.py"
    lo, hi = (-2.0, -0.5, 0.0), (2.0, 0.5, 0.3)  # long and flat: where sphere framing is far too loose
    out = tmp_path / "cam.json"
    expr = f"""
import bpy, json, importlib.util, sys
from mathutils import Vector
sys.argv = ["x"]
spec = importlib.util.spec_from_file_location("rr", {str(script)!r}); rr = importlib.util.module_from_spec(spec)
sys.path.insert(0, {str(script.parent)!r}); spec.loader.exec_module(rr)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add()
ob = bpy.context.active_object
ob.scale = ({(hi[0]-lo[0])/2}, {(hi[1]-lo[1])/2}, {(hi[2]-lo[2])/2}); ob.location = ({(hi[0]+lo[0])/2}, {(hi[1]+lo[1])/2}, {(hi[2]+lo[2])/2})
bpy.context.view_layer.update()
res = {{}}
for orbit in (False, True):
    pos, tgt, fov = rr.frame_auto(bpy.context.scene, {{"fovDeg": 35, "elevationDeg": 20, "azimuthDeg": 35, "padding": 0.1}}, 16/9, orbit=orbit)
    res[str(orbit)] = {{"pos": list(pos), "tgt": list(tgt)}}
json.dump(res, open({str(out)!r}, "w"))
"""
    subprocess.run([exe, "-b", "--factory-startup", "--python-expr", expr], check=True, capture_output=True, timeout=120)
    got = json.loads(out.read_text())
    for orbit in (False, True):
        ref = frame_bounds(np.array(lo), np.array(hi), np.array([0.0, 0, 1]), 35, 16 / 9, 20, 35, 0.1, orbit=orbit)
        assert np.allclose(got[str(orbit)]["pos"], ref.position, atol=1e-3), orbit
        assert np.allclose(got[str(orbit)]["tgt"], ref.target, atol=1e-3), orbit
