"""Mesh → Gaussian splat bake: settings, surface sampling, camera rig, COLMAP export, file axes, planning."""

import math
import struct
from pathlib import Path

import numpy as np
import pytest

from renda import splatbake
from renda.ir.camera import ResolvedCamera, override_cameras, resolve_cameras
from renda.ir.loaders.splat import load as load_splat_stage
from renda.planner import plan
from renda.scene import build_stage, normalize_up
from renda.spec import load_package

ROOT = Path(__file__).resolve().parents[2]
GLB = ROOT / "testdata" / "scenes" / "scene.glb"


def _pkg(**output):
    return load_package({"scene": {"root": str(GLB)}, "output": {"format": "splat", **output}})


@pytest.fixture(scope="module")
def stage():
    s, _ = build_stage(_pkg(), {str(GLB): GLB})
    return s


def test_spec_validation():
    with pytest.raises(ValueError, match="static scene"):
        load_package({"scene": {"root": "a.glb"}, "animation": {"frames": [1, 2]}, "output": {"format": "splat"}})
    with pytest.raises(ValueError, match='need output.format "splat"'):
        load_package({"scene": {"root": "a.glb"}, "output": {"format": "png", "splatBake": {"preset": "fast"}}})
    with pytest.raises(ValueError, match="mesh renderer"):
        load_package({"scene": {"root": "a.glb"}, "output": {"format": "splat", "splatBake": {"engine": "gsplat"}}})


def test_presets_and_overrides():
    fast = splatbake.resolve_settings(_pkg(splatBake={"preset": "fast"}))
    assert fast.direct and fast.train_hardware == "cpu"
    std = splatbake.resolve_settings(_pkg())
    assert (std.views, std.steps, std.sh_degree, std.train_hardware, std.splat_format) == (96, 6000, splatbake.SH_AUTO, "gpu-l4", "spz")
    high = splatbake.resolve_settings(_pkg(splatFormat="spz", splatBake={"preset": "high", "views": 120, "steps": 9000}))
    assert (high.views, high.steps, high.train_hardware, high.splat_format) == (120, 9000, "gpu-l40s", "spz")


def test_surface_samples_cover_meshes_with_unit_normals(stage):
    s = splatbake.sample_surface(stage, 20_000, seed=1)
    assert abs(len(s.points) - 20_000) < 200
    lo, hi = stage.bounds()
    assert (s.points >= lo - 1e-4).all() and (s.points <= hi + 1e-4).all()
    assert np.allclose(np.linalg.norm(s.normals, axis=1), 1, atol=1e-4)
    assert (s.colors >= 0).all() and (s.colors <= 1).all() and s.colors.std() > 0.05  # textures/materials vary
    g = splatbake.init_gaussians(s, sh_degree=2)
    assert len(g) == len(s.points) and g.sh_degree == 2 and g.antialiased
    # flat discs: the smallest scale is along the normal
    assert (np.exp(g.scales[:, 2]) < np.exp(g.scales[:, 0])).all()


def test_rig_is_deterministic_and_frames_the_scene(stage):
    st = splatbake.resolve_settings(_pkg(splatBake={"views": 40, "coverage": "hemisphere"}))
    a, b = splatbake.bake_rig(stage, st), splatbake.bake_rig(stage, st)
    assert sorted(a) == list(range(1, 41))
    assert all(np.allclose(a[f].position, b[f].position) for f in a)
    up = stage.up_vector() / np.linalg.norm(stage.up_vector())
    for cam in a.values():
        d = cam.position - cam.target
        elev = math.degrees(math.asin(np.clip(d @ up / np.linalg.norm(d), -1, 1)))
        assert 4.0 <= elev <= 86.0  # hemisphere coverage band


def test_override_cameras_replace_package_camera(stage):
    cam = ResolvedCamera(np.array([1.0, 2, 3]), np.zeros(3), np.array([0, 1.0, 0]), 30)
    pkg = load_package({"scene": {"root": str(GLB)}})
    with override_cameras({7: cam}):
        assert resolve_cameras(pkg, stage, [7])[7] is cam
    assert resolve_cameras(pkg, stage, [1])[1] is not cam


def test_colmap_poses_project_like_the_renderer(tmp_path, stage):
    from renda.engines.splat_raster import camera_matrices
    from renda.ir.splats import quat_wxyz_to_mat

    st = splatbake.resolve_settings(_pkg(splatBake={"views": 8}))
    rig = splatbake.bake_rig(stage, st)
    cams = [rig[f] for f in sorted(rig)]
    pts = splatbake.sample_surface(stage, 500)
    names = [splatbake.view_image_name(f) for f in sorted(rig)]
    sparse = splatbake.write_colmap(tmp_path, cams, 640, 640, names, pts.points, pts.colors)
    images = splatbake.read_colmap_images_txt(sparse / "images.txt")
    assert [i[3] for i in images] == names
    p = pts.points[0].astype(float)
    for (q, t, _, _), cam in zip(images, cams):
        view, k = camera_matrices(cam, 640, 640)
        r = quat_wxyz_to_mat(q[None])[0]
        assert np.allclose(r @ p + t, view[:3, :3] @ p + view[:3, 3], atol=1e-6)
    # binary model: counts in headers match
    assert struct.unpack("<Q", (sparse / "images.bin").read_bytes()[:8])[0] == 8
    assert struct.unpack("<Q", (sparse / "points3D.bin").read_bytes()[:8])[0] == len(pts.points)
    cam_line = [l for l in (sparse / "cameras.txt").read_text().splitlines() if not l.startswith("#")][0].split()
    assert cam_line[1] == "PINHOLE" and float(cam_line[4]) == pytest.approx(320 / math.tan(math.radians(20)))


@pytest.mark.parametrize("ext", ["ply", "spz", "splat"])
def test_exported_splats_reopen_upright(tmp_path, stage, ext):
    s = splatbake.init_gaussians(splatbake.sample_surface(stage, 3000), 0)
    out = splatbake.export_splats(s, tmp_path / f"bake.{ext}", stage.up_axis)
    reopened = normalize_up(load_splat_stage(out))
    back = reopened.splats[0].baked().means
    tol = 0.01 if ext != "ply" else 1e-4  # spz/splat quantise positions
    assert np.allclose(np.sort(back[:, 1]), np.sort(s.means[:, 1]), atol=tol)


def test_planner_fast_and_standard():
    fast = plan(_pkg(splatBake={"preset": "fast"}), {str(GLB): GLB})
    assert (fast.engine, fast.hardware, fast.chunks) == (splatbake.FAST_ENGINE, "cpu", [[1]])
    std = plan(_pkg(), {str(GLB): GLB})
    assert std.frames == list(range(1, 97)) and std.splat_bake["views"] == 96
    assert std.engine != "gsplat" and std.hardware.startswith("gpu")
    # ~96 cheap views: rendering them in the training container beats 5 extra container start-ups
    assert std.splat_bake["single_container"] and std.chunks == [std.frames] and not std.splat_bake["stream"]
    # rush fans the views out and streams them into the trainer; so does an explicit fanOut
    rush = plan(_pkg(), {str(GLB): GLB}, "rush")
    assert not rush.splat_bake["single_container"] and rush.splat_bake["stream"] and len(rush.chunks) > 1
    forced = plan(_pkg(splatBake={"fanOut": True}), {str(GLB): GLB})
    assert not forced.splat_bake["single_container"] and len(forced.chunks) > 1
    assert plan(_pkg(splatBake={"preset": "high"}), {str(GLB): GLB}).splat_bake["single_container"] is False
    # three.js views can't render in the trainer image
    tj = plan(_pkg(splatBake={"engine": "threejs", "fanOut": False}), {str(GLB): GLB})
    assert not tj.splat_bake["single_container"] and any("fanning out" in w for w in tj.warnings)
    assert std.est_wall_seconds[0] > 0 and rush.est_wall_seconds[0] < std.est_wall_seconds[0]


def test_direct_bake_end_to_end(tmp_path):
    from renda.render import render_frames

    pkg = _pkg(splatFormat="spz", splatBake={"preset": "fast"})
    p = plan(pkg, {str(GLB): GLB})
    r = render_frames(p, pkg, {str(GLB): GLB}, tmp_path, [1])
    assert [f.name for f in r.files] == ["scene.spz"] and r.files[0].stat().st_size > 10_000
    assert any("albedo" in w for w in r.warnings)


def test_splat_input_is_rejected():
    splat = ROOT / "testdata" / "scenes" / "splats" / "garden.ply"
    pkg = load_package({"scene": {"root": str(splat)}, "output": {"format": "splat"}})
    with pytest.raises(ValueError, match="already a Gaussian splat"):
        plan(pkg, {str(splat): splat})


def test_view_package_drops_lens_blur_and_screen_space_post():
    from renda import splatbake
    from renda.spec import load_package

    pkg = load_package({
        "scene": {"root": "model.glb"},
        "camera": {"mode": "auto", "lens": {"focalLengthMm": 85, "fStop": 1.4}},
        "post": {"exposure": 0.5, "tonemap": "filmic", "vignette": 0.4, "chromaticAberration": 0.3, "filmGrain": 0.2},
        "output": {"format": "splat", "splatBake": {"preset": "standard"}},
    })
    settings = splatbake.resolve_settings(pkg)
    v = splatbake.view_package(pkg, settings)
    assert v.camera.mode == "auto" and getattr(v.camera, "lens", None) is None
    assert v.post.exposure == 0.5 and v.post.tonemap == "filmic"
    assert v.post.vignette == 0 and v.post.chromaticAberration == 0 and v.post.filmGrain == 0
    assert set(splatbake.ignored_view_settings(pkg)) == {"camera.lens", "post.vignette", "post.chromaticAberration", "post.filmGrain"}


def test_override_cameras_win_over_package_lens():
    import numpy as np

    from renda.ir.camera import ResolvedCamera, override_cameras, resolve_cameras
    from renda.ir.stage import Stage
    from renda.spec import load_package

    pkg = load_package({"scene": {"root": "m.glb"}, "camera": {"mode": "auto", "lens": {"focalLengthMm": 200, "fStop": 1.2}}})
    rig = {1: ResolvedCamera(position=np.array([0.0, 0, 5]), target=np.zeros(3), up=np.array([0.0, 1, 0]), fov_y_deg=40.0)}
    with override_cameras(rig):
        cams = resolve_cameras(pkg, Stage(), [1])
    assert cams[1] is rig[1] and cams[1].fov_y_deg == 40.0


def test_explicit_and_orbit_cameras():
    from renda.scene import build_stage

    cams = [{"position": [3 * np.cos(a), 1.5, 3 * np.sin(a)], "target": [0, 0.5, 0], "fovDeg": 35}
            for a in np.linspace(0, 2 * np.pi, 12, endpoint=False)]
    pkg = _pkg(splatBake={"cameras": cams})
    st = splatbake.resolve_settings(pkg)
    assert st.views == 12 and st.cameras
    stage, _ = build_stage(pkg, {str(GLB): GLB})
    rig = splatbake.bake_rig(stage, st)
    assert len(rig) == 12 and np.allclose(rig[1].position, cams[0]["position"]) and rig[1].fov_y_deg == 35
    orbit = splatbake.resolve_settings(_pkg(splatBake={"coverage": "orbit", "views": 24}))
    ring = splatbake.camera_rig(stage, orbit)
    up = stage.up_vector() / np.linalg.norm(stage.up_vector())
    lo, hi = stage.bounds()
    c = (lo + hi) / 2
    elev = [np.degrees(np.arcsin(((cam.position - c) / np.linalg.norm(cam.position - c)) @ up)) for cam in ring]
    assert len(ring) == 24 and np.allclose(elev, 20, atol=0.5)


def test_coverage_rig_spreads_closeups_and_respects_band():
    from renda.scene import build_stage

    pkg = _pkg(splatBake={"coverage": "hemisphere"})
    stage, _ = build_stage(pkg, {str(GLB): GLB})
    st = splatbake.resolve_settings(pkg)
    rig = splatbake.bake_rig(stage, st)
    assert len(rig) == 96
    lo, hi = stage.bounds()
    up = stage.up_vector() / np.linalg.norm(stage.up_vector())
    radius = np.linalg.norm(hi - lo) / 2
    close = [c for c in rig.values() if np.linalg.norm(c.position - c.target) < radius * 2]
    assert len(close) >= 30  # ~45% close-ups
    targets = np.array([c.target for c in close])
    # farthest-point sampled: no two close-up targets nearly coincide
    d = np.linalg.norm(targets[:, None] - targets[None], axis=-1) + np.eye(len(targets)) * 1e9
    assert d.min() > radius * 0.01
    # hemisphere: no view from below the targets' horizon
    for c in rig.values():
        v = (c.position - c.target) / np.linalg.norm(c.position - c.target)
        assert v @ up > np.sin(np.radians(5)) - 1e-3
    # deterministic across containers
    assert all(np.allclose(a.position, b.position) for a, b in zip(rig.values(), splatbake.bake_rig(stage, st).values()))


def test_adapt_to_scene_picks_engine_and_sh():
    from renda.scene import build_stage

    pkg = _pkg()
    stage, _ = build_stage(pkg, {str(GLB): GLB})
    st = splatbake.resolve_settings(pkg)
    st.engine = "blender-cycles"
    glossy_st, notes = splatbake.adapt_to_scene(st, stage)
    assert splatbake.glossy_fraction(stage) > splatbake.GLOSSY_SCENE  # the gold knot
    assert glossy_st.engine == "blender-cycles" and glossy_st.sh_degree == 2 and notes
    hi = splatbake.resolve_settings(_pkg(splatBake={"preset": "high"}))
    hi.engine = "blender-cycles"
    assert splatbake.adapt_to_scene(hi, stage)[0].sh_degree == 3
    for m in stage.materials.values():
        m.metallic, m.roughness, m.transmission, m.emission_strength = 0.0, 0.8, 0.0, 0.0
    diffuse_st, _ = splatbake.adapt_to_scene(st, stage)
    assert diffuse_st.engine == "blender-eevee" and diffuse_st.sh_degree == 1
    pinned = splatbake.resolve_settings(_pkg(splatBake={"engine": "mitsuba", "shDegree": 2}))
    pinned.engine = "mitsuba"
    same, _ = splatbake.adapt_to_scene(pinned, stage)
    assert same.engine == "mitsuba" and same.sh_degree == 2


def test_view_package_uses_cheap_denoised_views():
    st = splatbake.resolve_settings(_pkg())
    v = splatbake.view_package(_pkg(), st)
    assert v.render.quality == "draft" and v.render.denoise
    final = load_package({"scene": {"root": str(GLB)}, "render": {"quality": "final"}, "output": {"format": "splat"}})
    keep = splatbake.view_package(final, st)
    assert keep.render.quality == "final"
