"""Lens, antialias, motion blur and post effects: spec, maths, camera resolution, engine matrix, post stage."""

import math

import numpy as np
import pytest
from pydantic import ValidationError

from renda import effects, lens, post
from renda.ir.camera import resolve_cameras, resolve_subframes
from renda.ir.stage import Mesh, Stage
from renda.spec import RendaPackage, load_package


def _stage() -> Stage:
    pts = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)
    return Stage(meshes=[Mesh("box", pts, np.array([[0, 1, 2]]))], up_axis="Y")


def _pkg(**over) -> RendaPackage:
    data = {"scene": {"root": "a.glb"}, "render": {"resolution": [160, 90]}}
    for k, v in over.items():
        data[k] = {**data.get(k, {}), **v} if isinstance(v, dict) and isinstance(data.get(k), dict) else v
    return load_package(data)


# -- spec ---------------------------------------------------------------------------------------


def test_spec_accepts_effects_and_rejects_static_motion_blur():
    p = _pkg(camera={"mode": "auto", "lens": {"focalLengthMm": 50, "fStop": 2}},
             render={"resolution": [160, 90], "motionBlur": {}, "antialias": {"filter": "box"}},
             animation={"frames": [1, 10], "turntable": {}}, post={"vignette": 0.3, "bloom": {}})
    assert p.camera.lens.dof and p.render.motionBlur.shutter == 0.5 and p.post.bloom.threshold == 0.8
    with pytest.raises(ValidationError, match="motion blur needs movement"):
        _pkg(render={"motionBlur": {}})
    with pytest.raises(ValidationError):
        _pkg(post={"tonemap": "reinhard"})


# -- lens maths ---------------------------------------------------------------------------------


@pytest.mark.parametrize("aspect", [16 / 9, 1.0, 9 / 16])
def test_focal_fov_roundtrip(aspect):
    fov = lens.fov_y_from_focal(50, 36, aspect)
    assert lens.focal_from_fov_y(fov, 36, aspect) == pytest.approx(50)
    if aspect >= 1:  # the 36 mm side is horizontal: 50 mm → 39.6° horizontal
        assert math.degrees(2 * math.atan(math.tan(math.radians(fov) / 2) * aspect)) == pytest.approx(39.598, abs=1e-2)


def test_coc_is_zero_at_focus_and_grows_away():
    d = np.array([1.0, 2.0, 4.0, np.inf])
    c = lens.coc_pixels(d, 50, 2.0, 2.0, 36, 1920, 1080)
    assert c[1] == pytest.approx(0, abs=1e-6)
    assert c[0] > 0 and c[2] > 0 and c[3] > c[2]


# -- camera resolution --------------------------------------------------------------------------


def test_focal_length_overrides_fov_and_sets_focus():
    p = _pkg(camera={"mode": "lookat", "position": [0, 0, 5], "target": [0, 0, 0], "lens": {"focalLengthMm": 85, "fStop": 1.4}})
    cam = resolve_cameras(p, _stage(), [1])[1]
    assert cam.fov_y_deg == pytest.approx(lens.fov_y_from_focal(85, 36, 160 / 90))
    assert cam.focus_distance == pytest.approx(5) and cam.focal_mm == 85
    assert cam.aperture_radius == pytest.approx(85 / 1.4 / 2 / 1000)


def test_focus_target_point_and_object():
    p = _pkg(camera={"mode": "lookat", "position": [0, 0, 5], "lens": {"fStop": 2, "focusTarget": [0, 0, 1]}})
    assert resolve_cameras(p, _stage(), [1])[1].focus_distance == pytest.approx(4)
    warnings = []
    p = _pkg(camera={"mode": "lookat", "position": [0, 0, 5], "lens": {"fStop": 2, "focusTarget": "nope"}})
    assert resolve_cameras(p, _stage(), [1], warnings)[1].focus_distance == pytest.approx(5)
    assert any("nope" in w for w in warnings)


def test_turntable_single_frame_chunk_is_rotated():
    """Regression: a one-frame chunk used to get the un-rotated base camera."""
    p = _pkg(animation={"frames": [1, 4], "turntable": {}})
    full = resolve_cameras(p, _stage(), [1, 2, 3, 4])
    assert np.allclose(resolve_cameras(p, _stage(), [3])[3].position, full[3].position)
    assert not np.allclose(full[1].position, full[3].position)


def test_subframes_straddle_the_frame():
    p = _pkg(render={"resolution": [160, 90], "motionBlur": {"shutter": 0.5, "subframes": 4}},
             animation={"frames": [1, 24], "turntable": {}})
    offs = effects.subframe_offsets(p)
    assert len(offs) == 4 and sum(offs) == pytest.approx(0) and max(offs) - min(offs) == pytest.approx(0.375)
    subs = resolve_subframes(p, _stage(), [5])
    assert len(subs[5]) == 4 and not np.allclose(subs[5][0].position, subs[5][-1].position)
    assert resolve_subframes(p, _stage(), [5], emulate_motion_blur=False)[5][0].position.shape == (3,)


# -- engine matrix ------------------------------------------------------------------------------


def test_effect_warnings_follow_the_matrix():
    p = _pkg(camera={"mode": "auto", "lens": {"fStop": 2, "apertureBlades": 6}},
             render={"resolution": [160, 90], "motionBlur": {}}, animation={"frames": [1, 4], "turntable": {}})
    assert effects.warnings_for(p, "blender-cycles") == []
    mi = " ".join(effects.warnings_for(p, "mitsuba"))
    assert "blades" in mi and "subframes" in mi
    assert any("depth buffer" in w for w in effects.warnings_for(p, "threejs"))
    # tonemap only warns when asked for explicitly
    assert effects.warnings_for(_pkg(post={"vignette": 0.2}), "gsplat") == []
    assert any("display-referred" in w for w in effects.warnings_for(_pkg(post={"tonemap": "aces"}), "gsplat"))
    assert any("Filmic" in w for w in effects.warnings_for(_pkg(post={"tonemap": "aces"}), "blender-cycles"))


def test_emulated_motion_blur_costs_subframes():
    p = _pkg(render={"resolution": [160, 90], "motionBlur": {"subframes": 8}}, animation={"frames": [1, 4], "turntable": {}})
    assert effects.frame_cost_factor(p, "gsplat") == pytest.approx(8)
    assert effects.frame_cost_factor(p, "mitsuba") < 1.5
    assert effects.frame_cost_factor(_pkg(), "gsplat") == 1.0


# -- post stage ---------------------------------------------------------------------------------


def _grey(h=60, w=80, v=0.5):
    return np.full((h, w, 3), v)


def test_vignette_darkens_corners_only():
    out, _ = post.apply(_grey(), None, load_package({"scene": {"root": "a"}, "post": {"vignette": 0.8}}).post)
    assert out[30, 40, 0] == pytest.approx(0.5, abs=0.02) and out[0, 0, 0] < 0.35


def test_chromatic_aberration_splits_channels_at_edges():
    img = np.zeros((61, 81, 3))
    img[:, 70:72] = 1.0  # a white line near the right edge
    out, _ = post.apply(img, None, load_package({"scene": {"root": "a"}, "post": {"chromaticAberration": 1}}).post)
    assert np.argmax(out[30, :, 0]) != np.argmax(out[30, :, 2])
    assert np.argmax(out[30, :, 1]) == 70 or np.argmax(out[30, :, 1]) == 71


def test_grain_is_deterministic_per_seed():
    p = load_package({"scene": {"root": "a"}, "post": {"filmGrain": 0.5}}).post
    a, _ = post.apply(_grey(), None, p, seed=3)
    b, _ = post.apply(_grey(), None, p, seed=3)
    c, _ = post.apply(_grey(), None, p, seed=4)
    assert np.array_equal(a, b) and not np.array_equal(a, c)


def test_bloom_spreads_light_from_highlights():
    img = np.zeros((64, 64, 3))
    img[30:34, 30:34] = 1.0
    out, _ = post.apply(img, None, load_package({"scene": {"root": "a"}, "post": {"bloom": {"intensity": 1}}}).post)
    assert out[32, 40, 0] > 0.02 and out[32, 32, 0] >= 0.99


def test_bokeh_kernel_shapes():
    k = post.bokeh_kernel(6)
    assert k.sum() == pytest.approx(1)
    hexa = post.bokeh_kernel(6, blades=6)
    assert (hexa > 0).sum() < (k > 0).sum()  # a hexagon inscribed in the disc
    tall = post.bokeh_kernel(6, ratio=2)
    assert tall.shape[0] > tall.shape[1]


def test_depth_of_field_keeps_focus_sharp_and_blurs_background():
    rng = np.random.default_rng(0)
    h, w = 64, 96
    img = rng.random((h, w, 3))
    depth = np.full((h, w), 10.0)
    depth[:, : w // 2] = 2.0  # left half in focus
    p = load_package({"scene": {"root": "a"}, "camera": {"mode": "auto", "lens": {"fStop": 1.4}}})
    rgb, a = post.dof_display(img, np.ones((h, w)), depth, p.camera.lens, 50, 2.0)
    left_err = np.abs(rgb[:, 5:40] - img[:, 5:40]).mean()
    right_var = rgb[:, 70:].std()
    assert left_err < 0.02 and right_var < img[:, 70:].std() * 0.6
    assert np.allclose(a, 1)


def test_accumulate_identical_frames_is_identity():
    img = np.random.default_rng(1).random((8, 8, 3))
    rgb, a = post.accumulate([(img, np.ones((8, 8)))] * 3)
    assert np.allclose(rgb, img, atol=1e-6) and np.allclose(a, 1)


def test_post_inactive_by_default():
    assert not post.is_active(None)
    assert not post.is_active(load_package({"scene": {"root": "a"}, "post": {}}).post)
    assert post.is_active(load_package({"scene": {"root": "a"}, "post": {"exposure": 1}}).post, emulate_exposure=True)


def test_tonemap_modes_differ_and_stay_in_range():
    from renda.images import tonemap

    x = np.array([[0.0, 0.18, 1.0, 8.0]] * 3).T
    outs = {m: tonemap(x, m) for m in ("agx", "aces", "neutral", "filmic", "standard")}
    for m, o in outs.items():
        assert o.min() >= 0 and o.max() <= 1, m
    assert not np.allclose(outs["agx"], outs["aces"])
    assert tonemap(x, "standard", exposure=1)[1, 0] == pytest.approx(0.36)
