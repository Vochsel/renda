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


def _stage_for_box(lo, hi):
    """A Stage whose bounds are the box (one mesh with the box corners)."""
    from renda.ir.stage import Mesh, Stage

    pts = _corners(np.array(lo, float), np.array(hi, float)).astype(np.float32)
    faces = np.array([[0, 1, 2], [4, 5, 6]], dtype=np.uint32)
    return Stage(meshes=[Mesh(name="box", points=pts, indices=faces)], up_axis="Y")


def _resolved(lo, hi, res, camera, post=None, turntable=False):
    from renda.ir.camera import resolve_cameras
    from renda.spec import load_package

    d = {"scene": {"root": "x.glb"}, "camera": camera, "render": {"resolution": list(res)}}
    if post:
        d["post"] = post
    if turntable:
        d["animation"] = {"frames": [1, 24], "turntable": {"revolutions": 1}}
    pkg = load_package(d)
    frames = list(range(1, 25)) if turntable else [1]
    return pkg, resolve_cameras(pkg, _stage_for_box(lo, hi), frames)


@pytest.mark.parametrize("lo,hi", BOXES)
@pytest.mark.parametrize("res", [(1920, 1080), (960, 600), (1000, 1000), (1080, 1920), (2390, 1000)])
@pytest.mark.parametrize("focal", [24, 85, 200])
@pytest.mark.parametrize("turntable", [False, True])
def test_long_lens_auto_framing_keeps_subject_in_frame(lo, hi, res, focal, turntable):
    camera = {"mode": "auto", "elevationDeg": 10, "azimuthDeg": 30, "padding": 0.1, "lens": {"focalLengthMm": focal}}
    _, cams = _resolved(lo, hi, res, camera, turntable=turntable)
    aspect = res[0] / res[1]
    for cam in cams.values():
        assert cam.focal_mm == focal
        x, y = _ndc(cam, _corners(np.array(lo, float), np.array(hi, float)), aspect)
        assert max(np.abs(x).max(), np.abs(y).max()) <= 1.0 - 0.04  # inside, with margin


def _displayed(x, y, res, distortion, ca):
    """Where post._lens_remap shows content that the camera put at NDC (x, y) — worst channel."""
    W, H = res
    cx, cy = (W - 1) / 2, (H - 1) / 2
    norm = math.hypot(cx, cy)
    dx, dy = x * cx / norm, y * cy / norm
    s = math.hypot(dx, dy)
    if s == 0:
        return 0.0, 0.0
    k = -0.25 * distortion
    worst = 0.0
    for shift in (1 + 0.01 * ca, 1.0, 1 - 0.01 * ca):
        def src(r):
            sc = 1 + k * r * r
            if k > 0:
                sc /= 1 + k
            return r * sc * shift
        # solve src(R) = s where src is increasing (pincushion turns back at R = 1/sqrt(3|k|))
        lo_r, hi_r = 0.0, min(4.0, 1 / math.sqrt(-3 * k)) if k < 0 else 4.0
        if src(hi_r) < s:
            return math.inf, math.inf  # pushed out of the picture entirely
        for _ in range(60):
            mid = (lo_r + hi_r) / 2
            lo_r, hi_r = (mid, hi_r) if src(mid) < s else (lo_r, mid)
        worst = max(worst, lo_r / s)
    return x * worst, y * worst


@pytest.mark.parametrize("distortion,ca", [(1.0, 0.0), (-1.0, 0.0), (0.5, 1.0), (-0.3, 0.6), (0.0, 1.0)])
@pytest.mark.parametrize("res", [(960, 600), (1080, 1920)])
def test_auto_framing_compensates_post_lens_distortion(distortion, ca, res):
    lo, hi = BOXES[0]
    camera = {"mode": "auto", "elevationDeg": 10, "azimuthDeg": 30, "padding": 0.02, "lens": {"focalLengthMm": 85}}
    _, cams = _resolved(lo, hi, res, camera, post={"lensDistortion": distortion, "chromaticAberration": ca})
    cam = cams[1]
    x, y = _ndc(cam, _corners(np.array(lo, float), np.array(hi, float)), res[0] / res[1])
    shown = [_displayed(a, b, res, distortion, ca) for a, b in zip(x, y)]
    assert max(max(abs(a), abs(b)) for a, b in shown) <= 1.0 + 1e-6
    # the camera still renders with the lens's real field of view
    from renda.lens import effective_fov_y
    from renda.spec import Lens

    assert cam.fov_y_deg == pytest.approx(effective_fov_y(35, Lens(focalLengthMm=85), res[0] / res[1]))


ASPECT_RES = [(1920, 1080), (1600, 1200), (1000, 1000), (1080, 1920)]


def test_blender_camera_projects_like_core_for_every_aspect(tmp_path):
    """End to end through render.py make_camera (sensor fit, angle_y, render resolution): Blender must put the box
    corners where core's resolved camera does, for 16:9, 4:3, 1:1 and 9:16, with and without post lens distortion
    (which core compensates by fitting a narrower view — engines/blender.py with_fit_fov)."""
    import json
    import subprocess
    from pathlib import Path

    from renda.engines.blender import with_fit_fov
    from renda.ir.loaders.blender import blender_binary

    exe = blender_binary()
    if not exe:
        pytest.skip("blender not installed")
    lo, hi = [-1.5, 0, -3], [1.5, 1.9, 3.3]  # core Y-up box; Blender sees it Z-up as (x, -z, y)
    camera = {"mode": "auto", "elevationDeg": 20, "azimuthDeg": 35, "padding": 0.1}
    posts = {"plain": None, "distorted": {"lensDistortion": 0.5, "chromaticAberration": 0.6}}
    cases, expected = [], {}
    for name, post in posts.items():
        for res in ASPECT_RES:
            pkg, cams = _resolved(lo, hi, res, camera, post=post)
            key = f"{name}-{res[0]}x{res[1]}"
            x, y = _ndc(cams[1], _corners(np.array(lo, float), np.array(hi, float)), res[0] / res[1])
            expected[key] = np.stack([x, y], axis=1)
            cases.append({"key": key, "res": list(res), "camera": with_fit_fov(pkg.camera.model_dump(), pkg.post)})
    script = Path(__file__).parents[1] / "renda" / "engines" / "blender_scripts" / "render.py"
    out = tmp_path / "proj.json"
    zlo, zhi = (lo[0], -hi[2], lo[1]), (hi[0], -lo[2], hi[1])
    expr = f"""
import bpy, json, importlib.util, sys
from mathutils import Vector
from bpy_extras.object_utils import world_to_camera_view
sys.argv = ["x"]
spec = importlib.util.spec_from_file_location("rr", {str(script)!r}); rr = importlib.util.module_from_spec(spec)
sys.path.insert(0, {str(script.parent)!r}); spec.loader.exec_module(rr)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add()
ob = bpy.context.active_object
lo, hi = {list(zlo)!r}, {list(zhi)!r}
ob.scale = [(b - a) / 2 for a, b in zip(lo, hi)]; ob.location = [(a + b) / 2 for a, b in zip(lo, hi)]
bpy.context.view_layer.update()
corners = [Vector((x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
scene = bpy.context.scene
res = {{}}
for c in json.loads({json.dumps(json.dumps(cases))}):
    scene.render.resolution_x, scene.render.resolution_y = c["res"]
    scene.render.resolution_percentage = 100
    cam, _, _ = rr.make_camera(scene, {{"camera": c["camera"], "resolution": c["res"]}}, Vector)
    bpy.context.view_layer.update()
    res[c["key"]] = [list(world_to_camera_view(scene, cam, p))[:2] for p in corners]
    bpy.data.objects.remove(cam)
json.dump(res, open({str(out)!r}, "w"))
"""
    r = subprocess.run([exe, "-b", "--factory-startup", "--python-expr", expr], capture_output=True, text=True, timeout=180)
    assert out.exists(), r.stdout[-2000:] + r.stderr[-2000:]  # Blender exits 0 even when the script raises
    got = json.loads(out.read_text())
    # Corner i is (x, y, z) ∈ {lo, hi}³ in that axis order. Blender's box is core's rotated to Z-up:
    # Blender (x, y, z) = core (x, -z, y), so Blender corner (ix, iy, iz) is core corner (ix, iz, 1 - iy).
    order = [(ix, iy, iz) for ix in (0, 1) for iy in (0, 1) for iz in (0, 1)]
    for key, ref in expected.items():
        blender = np.array(got[key]) * 2 - 1  # [0, 1] camera view → NDC
        for bi, (ix, iy, iz) in enumerate(order):
            ci = order.index((ix, iz, 1 - iy))
            assert np.allclose(blender[bi], ref[ci], atol=2e-3), (key, bi, blender[bi], ref[ci])
