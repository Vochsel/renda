"""Package layering: the Python resolver must agree with the canonical TS one on spec/fixtures/layers."""

import json
import socket
from pathlib import Path

import numpy as np
import pytest
from conftest import SPEC

from renda import layers
from renda.layers import LayerError, Loaders, flatten, flatten_file, is_layered, transform_matrix, up_conversion
from renda.netsafe import UnsafeUrlError, is_public_address, safe_fetch_text
from renda.spec import load_package

FIXTURES = SPEC / "fixtures" / "layers"
CASES = sorted(p.name for p in FIXTURES.iterdir() if (p / "input.renda.json").exists())


def fixture_loaders(case_dir: Path) -> Loaders:
    def fetch(url: str) -> str:
        assert url.startswith("https://example.test/")
        return (case_dir / "remote" / url.removeprefix("https://example.test/")).read_text()

    return Loaders(read_file=lambda p: Path(p).read_text(), fetch_https=fetch)


def norm(x):
    if isinstance(x, dict):
        return {k: norm(v) for k, v in x.items()}
    if isinstance(x, list):
        return [norm(v) for v in x]
    if isinstance(x, float) and x.is_integer():
        return int(x)
    if isinstance(x, float):
        return round(x, 9)
    return x


@pytest.mark.parametrize("case", CASES)
def test_fixture_matches_typescript(case):
    case_dir = FIXTURES / case
    doc = json.loads((case_dir / "input.renda.json").read_text())
    assert is_layered(doc)
    r = flatten(doc, base=str(case_dir), loaders=fixture_loaders(case_dir))
    got = json.loads(json.dumps({"package": r.package, "provenance": r.provenance, "layers": r.layers}).replace(str(case_dir), "${DIR}"))
    expected = json.loads((case_dir / "expected.json").read_text())
    assert norm(got) == norm(expected)
    load_package(r.package)  # the flattened result is a valid package


ERRORS = json.loads((FIXTURES / "errors" / "cases.json").read_text())


@pytest.mark.parametrize("c", ERRORS, ids=lambda c: f"{c['entry']}->{c['code']}")
def test_error_cases(c):
    path = (FIXTURES / "errors" / c["entry"]).resolve()
    doc = json.loads(path.read_text())
    L = fixture_loaders(path.parent)
    if c.get("noHttps"):
        L.fetch_https = None
    kw = {}
    if c.get("maxDepth"):
        kw["max_depth"] = c["maxDepth"]
    if c.get("maxLayers"):
        kw["max_layers"] = c["maxLayers"]
    with pytest.raises(LayerError) as e:
        flatten(doc, base=str(path.parent) if c["local"] else None, label=path.as_posix() if c["local"] else "<root>", loaders=L, **kw)
    assert e.value.code == c["code"]
    assert e.value.field == c["field"]


def test_unlayered_unchanged():
    pkg = {"scene": {"root": "./a.glb"}, "render": {"quality": "draft"}}
    assert flatten(pkg, base="/x").package == pkg


def test_load_package_flattens_files_and_refuses_unflattened_dicts(tmp_path):
    (tmp_path / "base.renda.json").write_text(json.dumps({"render": {"quality": "final", "resolution": [640, 480]}}))
    (tmp_path / "shot.renda.json").write_text(json.dumps({"extends": "./base.renda.json", "scene": {"root": "./a.glb"}}))
    pkg = load_package(tmp_path / "shot.renda.json")
    assert pkg.render.quality == "final" and pkg.scene.root == str(tmp_path / "a.glb")
    with pytest.raises(ValueError, match="flatten"):
        load_package({"extends": "./base.renda.json", "scene": {"root": "a.glb"}})


def test_flatten_file_reports_cycle_through_the_root(tmp_path):
    (tmp_path / "a.renda.json").write_text(json.dumps({"extends": "./b.renda.json", "scene": {"root": "x.glb"}}))
    (tmp_path / "b.renda.json").write_text(json.dumps({"extends": "./a.renda.json"}))
    with pytest.raises(LayerError) as e:
        flatten_file(tmp_path / "a.renda.json", loaders=Loaders(read_file=lambda p: Path(p).read_text()))
    assert e.value.code == "layer_cycle"


def test_transform_matrix_matches_trs():
    from renda.ir import trs

    t = {"translate": [1, 2, 3], "rotateDeg": [10, 20, 30], "scale": [1, 2, 3]}
    col = np.asarray(transform_matrix(t)).reshape(4, 4).T
    assert np.allclose(col, trs(t["translate"], t["rotateDeg"], t["scale"]))


def test_up_conversion_matches_core():
    from renda.scene import up_conversion as core_up

    for a in ("Y", "Z", "-Y"):
        for b in ("Y", "Z"):
            assert np.allclose(np.asarray(up_conversion(a, b)).reshape(4, 4).T, core_up(a, b)), (a, b)


def test_is_public_address():
    for ip in ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
               "::1", "::", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "64:ff9b::a00:1", "ff02::1", "2002:a00:1::"]:
        assert not is_public_address(ip), ip
    for ip in ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"]:
        assert is_public_address(ip), ip


def test_safe_fetch_refuses_before_connecting(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("must not connect")

    monkeypatch.setattr(socket, "create_connection", no_network)
    for url, msg in [("http://example.com/a.json", "only https"), ("https://127.0.0.1/a", "not a public"),
                     ("https://[::1]/a", "not a public"), ("https://169.254.169.254/latest", "not a public"),
                     ("https://u:p@example.com/a", "credentials")]:
        with pytest.raises(UnsafeUrlError, match=msg):
            safe_fetch_text(url)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))])
    with pytest.raises(UnsafeUrlError, match="non-public"):
        safe_fetch_text("https://internal.example/a.json")


def test_reference_renders_in_the_right_place(tmp_path):
    """A Z-up referenced package placed with a transform lands upright, scaled and offset."""
    import trimesh

    from renda.scene import build_stage

    trimesh.creation.box(extents=(10, 20, 100)).export(tmp_path / "post.stl")  # 1 m tall in Z (cm units)
    (tmp_path / "post.renda.json").write_text(json.dumps({"scene": {"root": "./post.stl", "unitScale": 0.01}}))
    trimesh.creation.box(extents=(4, 0.1, 4)).export(tmp_path / "floor.glb")  # Y-up
    (tmp_path / "room.renda.json").write_text(json.dumps({
        "scene": {"root": "./floor.glb", "include": [{"package": "./post.renda.json", "as": "post", "transform": {"translate": [1, 0, 0]}}]},
    }))
    from renda.assets import resolve

    pkg = load_package(tmp_path / "room.renda.json")
    assert pkg.scene.include[0].upAxis == "Z"
    from renda.ir.stage import transform_points

    stage, _ = build_stage(pkg, resolve(pkg, tmp_path))
    world = [transform_points(m.transform, m.points) for m in stage.meshes]
    post = [w for w in world if abs(np.ptp(w[:, 1]) - 1.0) < 1e-5]
    assert post, "the post should stand 1 m tall along Y (converted from Z-up, cm → m)"
    assert np.isclose(post[0][:, 0].mean(), 1.0, atol=1e-5)
    assert np.isclose(np.ptp(post[0][:, 2]), 0.2, atol=1e-5)  # its 20 cm Y side became depth


def test_layers_compose_with_texture_overrides_and_bake(tmp_path):
    """A layer's textureOverrides sources resolve against that layer's directory, like assets,
    and bake output set in a layer survives flattening and validates."""
    (tmp_path / "brand").mkdir()
    (tmp_path / "brand" / "brand.renda.json").write_text(json.dumps({
        "scene": {"textureOverrides": [
            {"match": {"material": "/label/i"}, "source": "./logo.png"},
            {"match": {"object": "Screen*", "slot": "emission"}, "source": "https://cdn.example.test/promo.mp4"},
        ]},
        "output": {"format": "glb", "bake": {"mode": "ao", "size": 1024}},
    }))
    (tmp_path / "shot.renda.json").write_text(json.dumps({
        "extends": "./brand/brand.renda.json", "scene": {"root": "./bottle.glb"}, "render": {"engine": "blender-cycles"},
    }))
    flat = flatten_file(tmp_path / "shot.renda.json", loaders=Loaders(read_file=lambda p: Path(p).read_text())).package
    label, promo = flat["scene"]["textureOverrides"]
    assert label["source"] == str(tmp_path / "brand" / "logo.png")
    assert promo["source"] == "https://cdn.example.test/promo.mp4"
    pkg = load_package(flat)
    assert pkg.output.format == "glb" and pkg.output.bake.mode == "ao" and pkg.output.bake.size == 1024
    assert pkg.scene.textureOverrides[0].match.material == "/label/i"
