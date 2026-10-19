import json
import shutil
import subprocess

import pytest
from conftest import SPEC
from pydantic import ValidationError

from renda.spec import RendaPackage, format_from_path, load_package, package_for_file, parse_color

EXAMPLES = sorted((SPEC / "examples").glob("*.renda.json"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_parse(path):
    pkg = load_package(path)
    assert isinstance(pkg, RendaPackage)


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_match_json_schema(path):
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((SPEC / "schema" / "renda.v1.json").read_text())
    jsonschema.validate(json.loads(path.read_text()), schema)


def test_defaults_match_typescript():
    """Parse every example with the canonical Zod schema (spec/dist) and compare to pydantic's output."""
    node = shutil.which("node")
    dist = SPEC / "dist" / "index.js"
    if not node or not dist.exists():
        pytest.skip("node or spec/dist missing (run `pnpm -C spec build`)")
    script = (
        "import { parsePackage } from %s; import { readFileSync } from 'node:fs';"
        "const out = {}; for (const f of process.argv.slice(1)) out[f] = parsePackage(JSON.parse(readFileSync(f, 'utf8')));"
        "console.log(JSON.stringify(out));" % json.dumps(dist.as_uri())
    )
    res = subprocess.run([node, "--input-type=module", "-e", script, *map(str, EXAMPLES)], capture_output=True, text=True, check=True)
    ts = json.loads(res.stdout)

    def norm(x):
        if isinstance(x, dict):
            return {k: norm(v) for k, v in x.items() if v is not None}
        if isinstance(x, (list, tuple)):
            return [norm(v) for v in x]
        if isinstance(x, float) and x.is_integer():
            return int(x)
        return x

    for path in EXAMPLES:
        assert norm(load_package(path).dump()) == norm(ts[str(path)]), path.name


def test_video_needs_animation():
    with pytest.raises(ValidationError, match="animation"):
        load_package({"scene": {"root": "a.glb"}, "output": {"format": "mp4"}})


def test_transparent_jpg_rejected():
    with pytest.raises(ValidationError, match="alpha"):
        load_package({"scene": {"root": "a.glb"}, "output": {"format": "jpg", "transparent": True}})


def test_bad_colour_rejected():
    with pytest.raises(ValidationError):
        load_package({"scene": {"root": "a.glb"}, "environment": {"background": "red"}})


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        load_package({"scene": {"root": "a.glb"}, "render": {"resolutoin": [1, 2]}})


def test_package_for_file_defaults():
    p = package_for_file("./robot.fbx")
    assert p.scene.root == "scene" and p.assets["scene"].uri == "./robot.fbx"
    assert p.render.resolution == (1920, 1080) and p.camera.mode == "auto"
    assert p.environment.hdri == "studio"


def test_frames_and_formats():
    p = load_package({"scene": {"root": "a.glb"}, "animation": {"frames": [1, 10], "step": 3}})
    assert p.frames() == [1, 4, 7, 10]
    assert format_from_path("x/Teapot.GLB") == "gltf"
    assert format_from_path("https://h/a.usdz?sig=1") == "usd"
    assert format_from_path("scene.json") == "threejs"
    assert format_from_path("a.txt") is None


def test_parse_color():
    assert parse_color("#ffffff") == (1.0, 1.0, 1.0)
    r, g, b = parse_color("#808080")
    assert 0.2 < r < 0.23  # sRGB 0.5 → linear ~0.216
    assert parse_color([0.1, 0.2, 0.3]) == (0.1, 0.2, 0.3)


def test_explicit_fields_tracked():
    """native_setup relies on pydantic's fields_set distinguishing defaults from explicit values."""
    assert "camera" not in load_package({"scene": {"root": "a.blend"}}).model_fields_set
    assert "camera" in load_package({"scene": {"root": "a.blend"}, "camera": {"mode": "auto"}}).model_fields_set
