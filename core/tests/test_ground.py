"""scene.ground: placement, compositing maths, splat approximation, spec + CLI."""

import numpy as np
import pytest

from renda import ground as G
from renda.spec import Ground, load_package


def test_spec_defaults_and_validation():
    pkg = load_package({"version": 1, "scene": {"root": "a.glb", "ground": {}}})
    g = pkg.scene.ground
    assert (g.enabled, g.mode, g.offset, g.size, g.shadowOpacity, g.contactShadows) == (True, "shadowCatcher", None, None, 1, True)
    assert load_package({"version": 1, "scene": {"root": "a.glb"}}).scene.ground is None  # off unless asked
    with pytest.raises(Exception):
        load_package({"version": 1, "scene": {"root": "a.glb", "ground": {"mode": "glass"}}})
    with pytest.raises(Exception):
        load_package({"version": 1, "scene": {"root": "a.glb", "ground": {"color": "red"}}})


@pytest.mark.parametrize("up", [(0, 1.0, 0), (0, 0, 1.0)])
def test_placement_sits_under_the_lowest_point(up):
    lo, hi = np.array([-1.0, -2.0, -0.5]), np.array([1.0, 2.0, 0.5])
    gp = G.placement(lo, hi, np.array(up), Ground())
    lowest = min(np.array([x, y, z]) @ np.array(up) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2]))
    assert lowest - 1e-3 < gp.height < lowest  # just below, no z-fighting
    assert abs(gp.center @ np.array(up) - gp.height) < 1e-9  # center lies on the plane
    assert gp.size == pytest.approx(G.AUTO_SIZE["shadowCatcher"] * np.linalg.norm(hi - lo))


def test_explicit_offset_and_size():
    gp = G.placement(np.zeros(3), np.ones(3), np.array([0, 1.0, 0]), Ground(offset=-0.25, size=3, mode="solid"))
    assert gp.height == -0.25 and gp.size == 3


def test_plane_matrix_maps_quad_onto_plane():
    gp = G.placement(np.array([-1.0, 0.2, -1]), np.array([1.0, 1.0, 1]), np.array([0, 0, 1.0]), Ground(size=4))
    m = gp.matrix()
    for x, y in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        p = m @ np.array([x, y, 0, 1.0])
        assert abs(p[:3] @ gp.up - gp.height) < 1e-9
    assert np.allclose(m[:3, 2], gp.up)  # quad normal (+Z) → up
    assert np.allclose(np.linalg.norm(m[:3, 0]), 2.0)  # half the 4 m side


def test_composite_catcher_maths():
    h, w = 4, 4
    plane = np.zeros((h, w, 4), np.float32)
    plane[..., :3], plane[..., 3] = 0.8, 1.0  # plane everywhere
    main = plane.copy()
    main[0, 0, :3] = 0.2  # a shadowed pixel: 75% darker → shadow 0.75 (after the noise floor)
    main[1, 1, :3] = (0.9, 0.1, 0.1)  # object pixel
    obj = np.zeros((h, w), np.float32)
    obj[1, 1] = 1.0
    out = G.composite_catcher(main, plane, obj, opacity=1.0)
    expect = (0.75 - G.NOISE_FLOOR) / (1 - G.NOISE_FLOOR)
    assert out[0, 0, 3] == pytest.approx(expect, abs=1e-5) and np.allclose(out[0, 0, :3], 0)
    assert out[1, 1, 3] == pytest.approx(1.0) and np.allclose(out[1, 1, :3], (0.9, 0.1, 0.1))
    assert out[2, 2, 3] == pytest.approx(0.0)  # lit plane: fully transparent
    half = G.composite_catcher(main, plane, obj, opacity=0.5)
    assert half[0, 0, 3] == pytest.approx(expect / 2, abs=1e-5)
    back = np.full((h, w, 3), 0.6, np.float32)
    opaque = G.composite_catcher(main, plane, obj, 1.0, back)
    assert np.allclose(opaque[..., 3], 1)
    assert opaque[0, 0, 0] == pytest.approx(0.6 * (1 - expect), abs=1e-5)
    assert opaque[2, 2, 0] == pytest.approx(0.6)


def test_scale_shadow_alpha_keeps_objects():
    rgba = np.zeros((1, 3, 4), np.float32)
    rgba[0, :, 3] = (1.0, 0.6, 0.0)  # object, shadow, empty
    obj = np.array([[1.0, 0.0, 0.0]], np.float32)
    out = G.scale_shadow_alpha(rgba, obj, 0.5)
    assert np.allclose(out[0, :, 3], (1.0, 0.3, 0.0))


def test_mirror_camera_reflects_across_the_plane():
    from renda.ir.camera import ResolvedCamera

    gp = G.placement(np.array([-1.0, 0, -1]), np.array([1.0, 2, 1]), np.array([0, 1.0, 0]), Ground(offset=0.0))
    cam = ResolvedCamera(position=np.array([0, 3.0, 5]), target=np.array([0, 1.0, 0]), up=np.array([0, 1.0, 0]), fov_y_deg=40)
    m = G.mirror_camera(cam, gp)
    assert np.allclose(m.position, (0, -3, 5)) and np.allclose(m.target, (0, -1, 0)) and np.allclose(m.up, (0, -1, 0))


def test_ground_hits_and_coverage_shadow():
    from renda.ir.camera import ResolvedCamera

    gp = G.placement(np.array([-1.0, 0, -1]), np.array([1.0, 1, 1]), np.array([0, 1.0, 0]), Ground(offset=0.0))
    cam = ResolvedCamera(position=np.array([0, 2.0, 6]), target=np.zeros(3), up=np.array([0, 1.0, 0]), fov_y_deg=60)
    on, uv = G.ground_hits(cam, 40, 30, gp, ext=2.0)
    assert on[-1].all()  # bottom row looks at the floor
    assert not on[0].any()  # top row looks above the horizon
    centre = uv[15, 20]
    assert np.allclose(centre, (0.5, 0.5), atol=0.12)  # camera aims at the plane centre (±half a pixel, perspective)
    cov = np.zeros((64, 64))
    cov[24:40, 24:40] = 1.0  # something sitting at the centre
    s = G.coverage_shadow(cov, np.clip(uv, 0, 1), on, blur_px=1)
    assert s[15, 20] > 0.5 and s[-1, 0] < 0.2


def test_cost_factor():
    pkg = load_package({"version": 1, "scene": {"root": "a.glb", "ground": {}}})
    assert G.cost_factor(pkg, "blender-cycles") < G.cost_factor(pkg, "mitsuba")  # native vs emulated catcher
    assert G.cost_factor(load_package({"version": 1, "scene": {"root": "a.glb"}}), "mitsuba") == 1.0


def test_cli_ground_flag():
    from renda.cli import CliError, apply_overrides

    raw = apply_overrides({"scene": {"root": "a.glb"}}, ground="shadow")
    assert raw["scene"]["ground"] == {"mode": "shadowCatcher"}
    assert apply_overrides({"scene": {"root": "a.glb", "ground": {"mode": "solid"}}}, ground="none")["scene"] == {"root": "a.glb"}
    with pytest.raises(CliError):
        apply_overrides({"scene": {"root": "a.glb"}}, ground="glass")
