"""Real renders. Each engine test skips when the engine isn't installed."""

import numpy as np
import pytest
from fixtures import write_mesh_files, write_threejs
from PIL import Image

from renda.engines import get_engine
from renda.hdri import builtin_path, read_hdr
from renda.render import run_local
from renda.spec import load_package


def need(engine):
    if not get_engine(engine).available():
        pytest.skip(f"{engine} not installed")


def tiny(root, engine, **kw):
    pkg = {"scene": {"root": root}, "render": {"engine": engine, "resolution": [64, 48], "samples": 4, "quality": "draft"}}
    for k, v in kw.items():
        pkg.setdefault(k, {}).update(v) if isinstance(v, dict) else pkg.__setitem__(k, v)
    return load_package(pkg)


def assert_image(path, w=64, h=48):
    img = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
    assert img.shape == (h, w, 3)
    assert img.std() > 2, "image is flat — nothing rendered?"


@pytest.mark.parametrize("engine", ["mitsuba", "blender-cycles", "blender-eevee", "threejs"])
def test_render_threejs_scene(tmp_path, engine):
    need(engine)
    write_threejs(tmp_path)
    run = run_local(tiny("./scene.json", engine), tmp_path, tmp_path / "out")
    assert len(run.outputs) == 1
    assert_image(run.outputs[0])


def test_mitsuba_passes_transparent_and_overrides(tmp_path):
    need("mitsuba")
    write_mesh_files(tmp_path)
    pkg = tiny("./box.glb", "mitsuba", output={"format": "png", "transparent": True, "passes": ["beauty", "depth", "normal", "alpha"]},
               scene={"materialOverrides": {"*": {"baseColor": "#00ff00"}}})
    run = run_local(pkg, tmp_path, tmp_path / "out")
    names = sorted(p.name for p in run.outputs)
    assert names == ["alpha_0001.png", "depth_0001.png", "frame_0001.png", "normal_0001.png"]
    rgba = np.asarray(Image.open(tmp_path / "out" / "frame_0001.png"))
    assert rgba.shape[2] == 4 and rgba[0, 0, 3] == 0 and rgba[24, 32, 3] == 255  # transparent bg, solid object
    center = rgba[24, 32, :3].astype(int)
    assert center[1] > center[0] and center[1] > center[2]  # green override applied


def test_mitsuba_solid_background(tmp_path):
    need("mitsuba")
    write_mesh_files(tmp_path)
    run = run_local(tiny("./box.stl", "mitsuba", environment={"background": "#ff0000"}), tmp_path, tmp_path / "out")
    corner = np.asarray(Image.open(run.outputs[0]).convert("RGB"))[0, 0]
    assert corner[0] > 200 and corner[1] < 40


def test_turntable_video(tmp_path):
    need("mitsuba")
    write_mesh_files(tmp_path)
    pkg = tiny("./box.glb", "mitsuba", animation={"frames": [1, 6], "fps": 12, "turntable": {"revolutions": 1}},
               output={"format": "mp4"})
    run = run_local(pkg, tmp_path, tmp_path / "out")
    assert run.outputs[0].suffix == ".mp4" and run.outputs[0].stat().st_size > 1000
    assert len(run.frames) == 6
    a, b = (np.asarray(Image.open(f), dtype=np.float32) for f in run.frames[:2])
    assert np.abs(a - b).mean() > 1  # camera moved


def test_blender_blend_roundtrip(tmp_path):
    """Make a .blend with Blender, render it natively, and via extract → IR → Mitsuba."""
    need("blender-cycles")
    from renda.ir.loaders.blender import run_blender_script  # noqa: F401
    import subprocess

    from renda.ir.loaders.blender import blender_binary

    script = tmp_path / "mk.py"
    script.write_text(
        "import bpy, sys\n"
        "bpy.ops.wm.read_factory_settings(use_empty=False)\n"
        "bpy.ops.mesh.primitive_plane_add(size=10, location=(0,0,-1))\n"
        "bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-1])\n"
    )
    subprocess.run([blender_binary(), "-b", "--factory-startup", "--python", str(script), "--", str(tmp_path / "s.blend")],
                   check=True, capture_output=True)
    run = run_local(tiny("./s.blend", "blender-cycles"), tmp_path, tmp_path / "cy")
    assert_image(run.outputs[0])
    if get_engine("mitsuba").available():
        run = run_local(tiny("./s.blend", "mitsuba"), tmp_path, tmp_path / "mi")
        assert_image(run.outputs[0])


def test_builtin_hdri_roundtrip():
    p = builtin_path("studio", 128)
    img = read_hdr(p)
    assert img.shape == (64, 128, 3) and img.max() > 2  # has a bright softbox
    assert img[:32].mean() > img[32:].mean()  # brighter sky than floor
