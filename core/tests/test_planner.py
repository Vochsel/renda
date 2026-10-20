import pytest

from renda.cache import cache_key
from renda.planner import DEFAULT_PRICES, chunk_frames, estimate_cost, plan
from renda.spec import load_package


def P(**kw):
    base = {"scene": {"root": kw.pop("root", "./model.glb")}}
    base.update(kw)
    return load_package(base)


@pytest.mark.parametrize("root,quality,engine", [
    ("a.blend", "standard", "blender-cycles"),
    ("a.blend", "draft", "blender-eevee"),
    ("a.fbx", "final", "blender-cycles"),
    ("a.json", "standard", "threejs"),
    ("a.stl", "draft", "threejs"),
    ("a.step", "draft", "threejs"),
    ("a.obj", "standard", "mitsuba"),
    ("a.glb", "standard", "blender-cycles"),
    ("a.glb", "draft", "threejs"),
    ("a.usdz", "draft", "threejs"),
])
def test_auto_engine(root, quality, engine):
    assert plan(P(root=root, render={"quality": quality}), None).engine == engine


def test_draft_preview_runs_on_cpu_and_falls_back_to_gpu_engines():
    p = plan(P(root="a.stl", render={"quality": "draft"}), None)
    assert (p.engine, p.hardware) == ("threejs", "cpu")
    # no browser engine on this machine/image → previous GPU choice
    assert plan(P(root="a.stl", render={"quality": "draft"}), None, available={"blender-workbench", "mitsuba"}).engine == "blender-workbench"
    assert plan(P(root="a.glb", render={"quality": "draft"}), None, available={"blender-eevee", "mitsuba"}).engine == "blender-eevee"


def test_auto_engine_matches_shared_fixture():
    """Same table as web/test/estimate.test.ts, so the web estimate picks the engine the worker will."""
    import json
    from pathlib import Path

    from renda.planner import choose_engine

    cases = json.loads((Path(__file__).parents[2] / "spec/fixtures/estimator/auto-engine.json").read_text())["cases"]
    for c in cases:
        assert choose_engine(c["format"], c["quality"], None, mixed=c.get("mixed", False))[0] == c["engine"], c


def test_auto_engine_respects_availability():
    p = plan(P(root="a.blend"), None, available={"mitsuba"})
    assert p.engine == "mitsuba"
    p = plan(P(root="a.json"), None, available={"mitsuba", "blender-eevee"})
    assert p.engine == "blender-eevee"
    with pytest.raises(RuntimeError, match="not available|isn't available"):
        plan(P(render={"engine": "threejs"}), None, available={"mitsuba"})


def test_explicit_engine_kept():
    assert plan(P(render={"engine": "mitsuba"}), None).engine == "mitsuba"


def test_hardware_choices():
    assert plan(P(root="a.blend"), None).hardware == "gpu-l4"
    assert plan(P(root="a.json"), None).hardware == "cpu"  # threejs raster → cheap CPU
    eevee = plan(P(root="a.blend", render={"engine": "blender-eevee", "hardware": "cpu"}), None)
    assert eevee.hardware == "gpu-l4" and any("GPU" in w for w in eevee.warnings)
    small = plan(P(root="a.obj", render={"engine": "mitsuba", "resolution": [256, 256], "quality": "draft"}), None)
    assert small.hardware == "cpu"
    assert plan(P(root="a.blend", render={"hardware": "cpu"}), None).hardware == "cpu"


def test_rush_can_use_l40s_economy_never():
    big = dict(root="a.blend", render={"quality": "final", "resolution": [3840, 2160]}, animation={"frames": [1, 600]})
    assert plan(P(**big), None, "rush").hardware == "gpu-l40s"
    assert plan(P(**big), None, "economy").hardware != "gpu-l40s"


def test_chunking_amortises_load():
    frames = list(range(1, 241))
    chunks = chunk_frames(frames, load_s=8, frame_s=2, priority="standard")
    per = len(chunks[0])
    assert 8 / (8 + per * 2) <= 0.10 + 1e-9
    assert sum(len(c) for c in chunks) == 240
    assert [f for c in chunks for f in c] == frames
    assert len(chunk_frames(list(range(5000)), 1, 10, "standard")) <= 50
    assert len(chunk_frames(list(range(5000)), 1, 10, "economy")) <= 10


def test_still_is_one_chunk_and_cost_scales():
    still = plan(P(root="a.blend"), None)
    assert still.frames == [1] and still.chunks == [[1]]
    anim = plan(P(root="a.blend", animation={"frames": [1, 100]}), None)
    lo1, hi1 = estimate_cost(still)
    lo2, hi2 = estimate_cost(anim)
    assert 0 < lo1 <= hi1 and lo2 > lo1 * 20
    assert estimate_cost(still, {"gpu-l4": DEFAULT_PRICES["gpu-l4"] * 2})[0] == pytest.approx(lo1 * 2, rel=0.02)


def test_cache_key_stability():
    a = load_package({"name": "one", "assets": {"m": {"uri": "./a/model.glb"}}, "scene": {"root": "m"}})
    b = load_package({"name": "two", "assets": {"m": {"uri": "/elsewhere/renamed.glb"}}, "scene": {"root": "m"}})
    k1 = cache_key(a, {"m": "aa" * 32}, {"mitsuba": "3.9"})
    assert k1 == cache_key(b, {"m": "aa" * 32}, {"mitsuba": "3.9"})  # paths/names don't matter
    assert k1 != cache_key(a, {"m": "bb" * 32}, {"mitsuba": "3.9"})  # content does
    assert k1 != cache_key(a, {"m": "aa" * 32}, {"mitsuba": "3.10"})  # engine version does
    c = load_package({"assets": {"m": {"uri": "./a/model.glb"}}, "scene": {"root": "m"}, "render": {"samples": 9}})
    assert k1 != cache_key(c, {"m": "aa" * 32}, {"mitsuba": "3.9"})
    assert len(k1) == 64


def test_plan_json_roundtrip():
    from renda.planner import RenderPlan

    p = plan(P(root="a.blend", animation={"frames": [1, 30]}), None)
    assert RenderPlan.from_json(p.to_json()) == p
