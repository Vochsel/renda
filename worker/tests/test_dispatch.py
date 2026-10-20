from types import SimpleNamespace

import pytest

from renda_worker import dispatch
from renda_worker.hardware import SHAPES, Hardware, parse
from renda_worker.models import JobRequest

from .conftest import job_body


@pytest.mark.parametrize(
    "engine,hardware,formats,runner",
    [
        ("mitsuba", Hardware.CPU, ["gltf"], "cpu"),
        ("mitsuba", Hardware.CPU, ["blend"], "blender-cpu"),  # .blend needs Blender to extract IR
        ("mitsuba", Hardware.CPU, ["fbx"], "blender-cpu"),
        ("blender-workbench", Hardware.CPU, ["stl"], "blender-cpu"),
        ("blender-cycles", Hardware.GPU_L4, ["blend"], "l4"),
        ("mitsuba", Hardware.GPU_L40S, ["usd"], "l40s"),
        ("threejs", Hardware.GPU_L4, ["threejs"], "threejs"),
    ],
)
def test_runner_for(engine, hardware, formats, runner):
    assert dispatch.runner_for(engine, hardware, formats) == runner
    assert runner in dispatch.RUNNERS


def test_build_tasks_still_uploads_to_outputs():
    job = JobRequest.model_validate(job_body())
    plan = SimpleNamespace(engine="blender-cycles", hardware="gpu-l4", frames=[1], chunks=None)
    [t] = dispatch.build_tasks(job, plan, ["blend"], "png")
    assert t.runner == "l4" and t.final and t.upload_prefix == "outputs/job_1/" and t.frames == [1]


def test_build_tasks_video_chunks_go_to_work_prefix():
    job = JobRequest.model_validate(job_body())
    plan = SimpleNamespace(engine="blender-eevee", hardware="gpu", frames=list(range(1, 121)), chunks=[list(range(1, 61)), list(range(61, 121))])
    tasks = dispatch.build_tasks(job, plan, ["usd"], "mp4")
    assert [t.chunk for t in tasks] == [0, 1]
    assert all(t.upload_prefix == "work/job_1/frames/" and not t.final and t.runner == "l4" for t in tasks)
    assert dispatch.group_by_runner(tasks) == {"l4": tasks}


def test_progress_budget():
    assert dispatch.render_progress(0, 10) == pytest.approx(0.05)
    assert dispatch.render_progress(10, 10) == pytest.approx(0.9)
    assert dispatch.render_progress(20, 10) == pytest.approx(0.9)


def test_output_name_sanitised():
    assert dispatch.output_name({"name": "My Shoe/../x"}, "mp4") == "My-Shoe-..-x.mp4"
    assert dispatch.output_name({}, ".png") == "render.png"


def test_scene_formats_from_paths(tmp_path):
    assert dispatch.scene_formats({"a": tmp_path / "x.blend", "b": tmp_path / "s.usda", "h": tmp_path / "e.hdr"}) == ["blend", "hdr", "usd"]


def test_hardware_parse_and_shapes():
    assert parse("gpu") is Hardware.GPU_L4
    assert SHAPES[Hardware.GPU_L4].gpu == "L4" and SHAPES[Hardware.GPU_L40S].gpu == "L40S"
    assert SHAPES[Hardware.CPU].gpu is None


@pytest.mark.parametrize(
    "engine,hardware,splats,runner",
    [
        ("gsplat", Hardware.GPU_L4, True, "splat-l4"),
        ("gsplat", Hardware.GPU_L40S, True, "splat-l40s"),
        ("blender-cycles", Hardware.GPU_L4, True, "splat-l4"),  # mesh render + gsplat layer
        ("mitsuba", Hardware.GPU_L4, True, "splat-l4"),
        ("mitsuba", Hardware.CPU, True, "cpu"),  # CPU splat layer: core's numpy rasteriser
        ("threejs", Hardware.CPU, True, "threejs"),  # Spark in Chromium
        ("blender-cycles", Hardware.GPU_L4, False, "l4"),
    ],
)
def test_runner_for_splats(engine, hardware, splats, runner):
    assert dispatch.runner_for(engine, hardware, ["ply"], splats=splats) == runner
    assert runner in dispatch.RUNNERS
    assert dispatch.runner_hardware(runner) is hardware


def test_build_tasks_routes_splat_plans():
    job = JobRequest.model_validate(job_body())
    plan = SimpleNamespace(engine="gsplat", hardware="gpu-l4", frames=[1], chunks=[[1]], splats=True)
    tasks = dispatch.build_tasks(job, plan, ["ply"], "png")
    assert {t.runner for t in tasks} == {"splat-l4"}


def test_glb_outputs_and_export_runner():
    from renda_worker.storage import content_type

    assert content_type("outputs/job_1/baked.glb") == "model/gltf-binary"
    # plain GLB conversion ("export") runs on the slim CPU image, or Blender's for .blend/.fbx/.abc
    assert dispatch.runner_for("export", Hardware.CPU, ["gltf"]) == "cpu"
    assert dispatch.runner_for("export", Hardware.CPU, ["blend"]) == "blender-cpu"
    assert dispatch.runner_for("blender-cycles", Hardware.GPU_L4, ["gltf"]) == "l4"


def test_autoscaler_knobs_and_gpu_fallback(monkeypatch):
    import importlib

    from renda_worker import hardware

    monkeypatch.setenv("RENDA_SCALEDOWN_GPU_L4", "90")
    monkeypatch.setenv("RENDA_BUFFER_GPU_L4", "1")
    monkeypatch.setenv("RENDA_L4_FALLBACK_GPUS", "A10G,L4")
    h = importlib.reload(hardware)
    try:
        l4 = h.SHAPES[h.Hardware.GPU_L4]
        assert (l4.scaledown_s, l4.buffer_containers, l4.min_containers) == (90, 1, 0)
        assert h.gpu_request(l4, h.L4_FALLBACK_GPUS) == ["L4", "A10G"]  # primary not repeated
        assert h.gpu_request(h.SHAPES[h.Hardware.CPU], h.L4_FALLBACK_GPUS) is None
        assert h.gpu_request(l4, []) == "L4"
    finally:
        monkeypatch.undo()
        importlib.reload(hardware)
