"""Texture overrides: name matching, universal image ingestion, video sync, IR application."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from renda.engines.blender_scripts._match import compile_pattern, matches
from renda.ir import Material, Mesh, Stage, load_stage
from renda.spec import load_package
from renda.textures import (
    TexturePlan, _sniff_ext, ingest_image, is_video_source, resolve_texture_overrides, source_times,
)

TEX = Path(__file__).parents[2] / "testdata" / "textures"
SCENES = Path(__file__).parents[2] / "testdata" / "scenes"


# -- matching ------------------------------------------------------------------------------------

@pytest.mark.parametrize("pattern,name,ok", [
    ("Wood.001", "Wood.001", True),
    ("wood*", "Wood.001", True),          # globs are case-insensitive
    ("*_screen", "Phone_Screen", True),
    ("Mat[12]", "Mat2", True),
    ("Mat[12]", "Mat3", False),
    ("/^wood(_\\d+)?$/i", "WOOD_12", True),
    ("/^wood$/", "Wood", False),           # regex flags are explicit
    ("*", "anything", True),
    ("Screen", "ScreenGlass", False),      # plain names are exact
])
def test_matches(pattern, name, ok):
    assert matches(pattern, name) is ok


def test_bad_regex_is_a_clear_error():
    with pytest.raises(ValueError, match="bad regex"):
        compile_pattern("/(unclosed/")


# -- ingestion -----------------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "oak.jpg", "oak.webp", "oak.avif", "oak.heic", "oak_roughness16.tif", "oak_layer.psd", "logo.svg",
    "bumps_normal.png", "marble.hdr", "marble.exr",
])
def test_every_image_format_becomes_engine_readable(name, tmp_path):
    src = TEX / name
    if not src.exists():
        pytest.skip(f"{name} not generated (optional writer missing)")
    out = ingest_image(src, tmp_path)
    assert out.suffix in (".png", ".jpg", ".exr", ".hdr")
    if out.suffix in (".png", ".jpg"):
        img = Image.open(out)
        assert img.size[0] >= 128 and img.mode in ("RGB", "RGBA", "L", "I;16", "I")


def test_extensionless_blobs_are_sniffed(tmp_path):
    for name, ext in [("oak.webp", ".webp"), ("oak.avif", ".avif"), ("logo.svg", ".svg"), ("screen.mp4", ".mp4"),
                      ("oak_layer.psd", ".psd"), ("marble.hdr", ".hdr")]:
        if not (TEX / name).exists():
            continue
        blob = tmp_path / ("x" * 8 + name.replace(".", "_"))
        shutil.copy(TEX / name, blob)
        assert _sniff_ext(blob) == ext, name


def test_svg_rasterises_at_a_useful_size(tmp_path):
    out = ingest_image(TEX / "logo.svg", tmp_path)
    assert max(Image.open(out).size) == 2048


def test_video_detection():
    assert is_video_source(TEX / "screen.mp4")
    assert is_video_source(TEX / "screen.gif")      # animated gif = video
    assert not is_video_source(TEX / "oak.jpg")


# -- video timing --------------------------------------------------------------------------------

def _pkg(**scene_extra):
    return load_package({
        "scene": {"root": str(TEX / "phone.glb"), **scene_extra},
        "animation": {"frames": [1, 48], "fps": 24},
        "output": {"format": "mp4"},
    })


def test_source_times_follow_fps_speed_and_start():
    pkg = _pkg(textureOverrides=[{"source": str(TEX / "screen.mp4"), "video": {"startSec": 0.5, "speed": 2}}])
    t = source_times(pkg.scene.textureOverrides[0], pkg, [1, 13, 25])
    assert t == pytest.approx({1: 0.5, 13: 1.5, 25: 2.5})


def _frame_number(png: Path) -> float:
    """The generated video has a white bar sweeping left→right; its x position encodes the frame."""
    a = np.asarray(Image.open(png).convert("L"), dtype=np.float32)
    col = (a > 245).mean(axis=0)
    return float(np.argmax(col)) / a.shape[1]


def test_video_frames_sync_and_loop(tmp_path):
    pkg = _pkg(textureOverrides=[{"source": str(TEX / "screen.mp4")}])
    frames = [1, 13, 25, 49 + 0]  # 49 → wraps to the start (2 s clip, loop)
    (r,) = resolve_texture_overrides(pkg, {str(TEX / "phone.glb"): TEX / "phone.glb",
                                           str(TEX / "screen.mp4"): TEX / "screen.mp4"}, frames, tmp_path)
    assert r.is_video and set(r.frames) == set(frames)
    pos = {f: _frame_number(r.path(f)) for f in frames}
    assert pos[1] < pos[13] < pos[25]                     # the bar moves forward with the render
    assert pos[13] == pytest.approx(0.25, abs=0.06)       # 12 frames of 48 at the same fps → a quarter in
    assert pos[49] == pytest.approx(pos[1], abs=0.03)     # looped


def test_video_hold_without_loop(tmp_path):
    pkg = _pkg(textureOverrides=[{"source": str(TEX / "screen.mp4"), "video": {"loop": False}}])
    (r,) = resolve_texture_overrides(pkg, {str(TEX / "phone.glb"): TEX / "phone.glb",
                                           str(TEX / "screen.mp4"): TEX / "screen.mp4"}, [47, 90], tmp_path)
    assert _frame_number(r.path(90)) == pytest.approx(_frame_number(r.path(47)), abs=0.06)  # held on the last frame


def test_still_render_uses_video_frame(tmp_path):
    pkg = load_package({"scene": {"root": "x.glb", "textureOverrides": [
        {"source": str(TEX / "screen.mp4"), "video": {"frame": 24}}]}})
    (r,) = resolve_texture_overrides(pkg, {str(TEX / "screen.mp4"): TEX / "screen.mp4"}, [1], tmp_path)
    assert not r.is_video
    assert _frame_number(r.still) == pytest.approx(0.5, abs=0.06)


def test_animated_gif_frames(tmp_path):
    pkg = _pkg(textureOverrides=[{"source": str(TEX / "screen.gif")}])
    (r,) = resolve_texture_overrides(pkg, {str(TEX / "screen.gif"): TEX / "screen.gif"}, [1, 13], tmp_path)
    assert r.is_video and r.path(1) != r.path(13)


# -- applying to the IR --------------------------------------------------------------------------

def _two_tables():
    st = Stage()
    st.materials["Wood"] = Material("Wood")
    st.materials["Metal"] = Material("Metal", base_color_tex="/tmp/brushed_steel.png")
    quad = dict(points=[[0, 0, 0], [1, 0, 0], [1, 1, 0]], indices=[[0, 1, 2]], uvs=[[0, 0], [1, 0], [1, 1]])
    st.meshes += [Mesh("TableA", material="Wood", **quad), Mesh("TableB", material="Wood", **quad),
                  Mesh("Legs", material="Metal", **quad)]
    return st


def _resolved(tmp_path, overrides, frames=(1,)):
    pkg = load_package({"scene": {"root": "x.glb", "textureOverrides": overrides}})
    paths = {o["source"]: Path(o["source"]) for o in overrides}
    return resolve_texture_overrides(pkg, paths, list(frames), tmp_path)


def test_plan_matches_by_material_and_texture_name(tmp_path):
    st = _two_tables()
    res = _resolved(tmp_path, [{"match": {"material": "wood*"}, "source": str(TEX / "oak.jpg"), "uv": {"scale": 4}},
                               {"match": {"texture": "*steel*"}, "source": str(TEX / "bumps_normal.png")}])
    plan = TexturePlan(st, res)
    assert plan.targets[0] == [("Wood", "baseColor")]
    assert plan.targets[1] == [("Metal", "baseColor")]  # slot = where the matched texture was
    plan.apply(st, 1)
    assert st.materials["Wood"].base_color_tex.endswith(".jpg")
    assert st.materials["Wood"].tex_uv["baseColor"] == (4.0, 4.0, 0.0, 0.0, 0.0)
    assert st.materials["Wood"].tex_colorspace["baseColor"] == "srgb"


def test_object_scope_splits_shared_materials(tmp_path):
    st = _two_tables()
    res = _resolved(tmp_path, [{"match": {"object": "TableB", "slot": "roughness"}, "source": str(TEX / "oak.jpg")}])
    plan = TexturePlan(st, res)
    plan.apply(st, 1)
    a, b = st.meshes[0], st.meshes[1]
    assert a.material == "Wood" and b.material != "Wood"
    assert st.materials[b.material].roughness_tex and not st.materials["Wood"].roughness_tex
    assert st.materials[b.material].tex_colorspace["roughness"] == "linear"


def test_later_overrides_win_and_unmatched_warn(tmp_path):
    st = _two_tables()
    res = _resolved(tmp_path, [{"match": {"material": "Wood"}, "source": str(TEX / "oak.jpg")},
                               {"match": {"material": "Wood"}, "source": str(TEX / "oak.webp")},
                               {"match": {"material": "/^glass/i"}, "source": str(TEX / "oak.jpg")}])
    plan = TexturePlan(st, res)
    plan.apply(st, 1)
    assert st.materials["Wood"].base_color_tex.endswith(".png")  # webp → normalised png, applied last
    assert any("textureOverrides[2] matched no" in w for w in plan.warnings())


def test_meshes_without_uvs_get_box_uvs(tmp_path):
    st = load_stage(SCENES / "scene.stl")
    res = _resolved(tmp_path, [{"source": str(TEX / "oak.jpg")}])
    plan = TexturePlan(st, res)
    assert all(m.uvs is not None and len(m.uvs) == len(m.points) for m in st.meshes)
    assert any("box-projected" in w for w in plan.warnings())


def test_pattern_material_overrides_with_object_scope():
    from renda.scene import apply_material_overrides

    st = _two_tables()
    pkg = load_package({"scene": {"root": "x.glb", "materialOverrides": {
        "*": {"roughness": 0.9}, "/^wood/i": {"object": "TableA", "baseColor": "#ff0000"}}}})
    apply_material_overrides(st, pkg)
    a, b = st.meshes[0], st.meshes[1]
    assert st.materials[a.material].base_color[0] == pytest.approx(1.0)
    assert st.materials[b.material].base_color[0] == pytest.approx(0.8)  # untouched copy
    assert st.materials[b.material].roughness == pytest.approx(0.9)


def test_override_sources_are_referenced_assets():
    from renda.assets import referenced

    pkg = load_package({"scene": {"root": "x.glb", "textureOverrides": [{"source": "./oak.jpg"}]}})
    assert "./oak.jpg" in referenced(pkg)


def test_spec_parity_with_zod():
    """Defaults for texture overrides match the canonical Zod schema."""
    node = shutil.which("node")
    dist = Path(__file__).parents[2] / "spec" / "dist" / "index.js"
    if not node or not dist.exists():
        pytest.skip("node or spec/dist missing")
    pkg = {"scene": {"root": "a.glb", "textureOverrides": [{"source": "a.png", "match": {"material": "/x/i"}}],
                     "materialOverrides": {"wood*": {"object": "Table", "roughness": 0.2}}}}
    js = subprocess.run([node, "--input-type=module", "-e",
                         f"import {{ parsePackage }} from {json.dumps(dist.as_uri())};"
                         f"console.log(JSON.stringify(parsePackage({json.dumps(pkg)}).scene))"],
                        capture_output=True, text=True, check=True)
    zod = json.loads(js.stdout)
    py = load_package(pkg).model_dump(mode="json", exclude_none=True)["scene"]
    assert zod["textureOverrides"] == py["textureOverrides"]
    assert zod["materialOverrides"] == py["materialOverrides"]


def test_mitsuba_scene_dict_with_overrides_loads(tmp_path):
    """Exporter path (UV transforms, colour space, normal strength, emission texture) yields a scene
    Mitsuba accepts. Loads the dict only — no render."""
    mi = pytest.importorskip("mitsuba")
    from renda.ir.exporters.mitsuba import TextureCache, stage_to_dict

    mi.set_variant("scalar_rgb")
    st = load_stage(SCENES / "scene.glb")
    res = _resolved(tmp_path, [
        {"match": {"material": "/wood/i"}, "source": str(TEX / "oak.heic"), "uv": {"scale": 3, "rotationDeg": 90}},
        {"match": {"material": "/wood/i", "slot": "roughness"}, "source": str(TEX / "oak_roughness16.tif")},
        {"match": {"material": "gold*", "slot": "normal"}, "source": str(TEX / "bumps_normal.png"), "strength": 0.5},
        {"match": {"object": "Sphere", "slot": "emission"}, "source": str(TEX / "logo.svg"), "strength": 2},
    ])
    plan = TexturePlan(st, res)
    plan.apply(st, 1)
    d = stage_to_dict(st, mi, TextureCache(st.base_dir), tmp_path / "ply")
    d["integrator"] = {"type": "path"}
    mi.load_dict(d)
    text = repr(d)
    assert "to_uv" in text and "normalmap" in text and ".exr" in text


# -- CLI flag ------------------------------------------------------------------------------------

def test_cli_texture_flag_parsing(monkeypatch):
    from renda.cli import parse_texture_flag

    monkeypatch.chdir(TEX)
    e = parse_texture_flag("material=/Wood.*,x:y/i,slot=baseColor,scale=4,rotation=90:./oak.jpg")
    assert e["match"] == {"material": "/Wood.*,x:y/i", "slot": "baseColor"}
    assert e["uv"] == {"scale": 4.0, "rotationDeg": 90.0}
    assert e["source"] == str((TEX / "oak.jpg").resolve())  # made absolute: relative to the shell
    e = parse_texture_flag("object=Display,slot=emission,strength=3,loop=false,start=1.5:https://x.test/a.mp4")
    assert e["source"] == "https://x.test/a.mp4" and e["strength"] == 3.0
    assert e["video"] == {"loop": False, "startSec": 1.5}
    assert parse_texture_flag("./oak.jpg")["source"].endswith("oak.jpg")  # bare source → every base colour
    assert parse_texture_flag("scale=2x3:asset://" + "a" * 64)["uv"] == {"scale": [2.0, 3.0]}
