"""Splat bake: views render in parallel chunks onto the Volume, then one training task produces the splat."""

from pathlib import Path
from types import SimpleNamespace

from renda_worker import dispatch, pipeline
from renda_worker.models import JobRequest

from .conftest import FakeCore, job_body


class BakeCore(FakeCore):
    def __init__(self, views=6, chunks=None, train_hardware="gpu-l4"):
        super().__init__(frames=range(1, views + 1), chunks=chunks or [[1, 2, 3], [4, 5, 6]],
                         engine="blender-cycles", hardware="gpu-l4", output="splat")
        self.train_hardware = train_hardware
        self.trained_on = None

    def plan(self, pkg, asset_paths, priority):
        p = super().plan(pkg, asset_paths, priority)
        p.splat_bake = {"views": len(self.frames), "train_hardware": self.train_hardware, "preset": "standard"}
        return p

    def train_splat(self, plan, pkg, asset_paths, views_dir, out_dir):
        self.trained_on = sorted(p.name for p in Path(views_dir).iterdir())
        out = Path(out_dir) / "teapot.ply"
        out.write_bytes(b"ply")
        return SimpleNamespace(files=[out], warnings=["splat bake (standard): 9 Gaussians"], seconds=1.0)


def _wire(deps):
    deps.run_chunks = lambda runner, tasks: [pipeline.run_chunk(t, deps) for t in tasks]
    deps.run_train = lambda task: pipeline.run_train(task, deps)
    return deps


def test_splat_bake_renders_views_to_volume_then_trains(make_deps, recorder):
    core = BakeCore()
    deps = _wire(make_deps(core))
    out = pipeline.run_scene_job(JobRequest.model_validate(job_body()), deps)

    assert out["status"] == "succeeded", out
    assert core.trained_on == [f"frame_{i:04d}.png" for i in range(1, 7)]
    assert [o["key"] for o in out["outputs"]] == ["outputs/job_1/teapot.ply"]
    # views never touched R2; only the splat (and the result-cache manifest) did
    assert {k for k in deps.storage.objects if not k.startswith("cache/")} == {"outputs/job_1/teapot.ply"}
    usage = {e["chunk"]: e["hardware"] for e in recorder.of("usage")}
    assert usage == {0: "gpu-l4", 1: "gpu-l4", "train": "gpu-l4", "orchestrate": "cpu"}
    assert any("Gaussians" in w["message"] for w in recorder.of("warning"))
    assert not (deps.asset_root / "bakes" / "job_1").exists()  # views cleaned up after training


def test_train_task_targets_preset_hardware():
    job = JobRequest.model_validate(job_body())
    plan = SimpleNamespace(splat_bake={"views": 200, "train_hardware": "gpu-l40s"})
    task = dispatch.build_train_task(job, plan, "/assets")
    assert task.hardware == "gpu-l40s" and dispatch.TRAIN_RUNNERS[task.hardware] == "train_splat_l40s"
    assert task.views_dir == "/assets/bakes/job_1/views"


def test_fast_preset_is_a_plain_single_chunk():
    job = JobRequest.model_validate(job_body())
    plan = SimpleNamespace(engine="splatbake", hardware="cpu", frames=[1], chunks=[[1]], splat_bake={"views": 0})
    tasks = dispatch.build_tasks(job, plan, ["gltf"], "splat", "/assets")
    assert len(tasks) == 1 and tasks[0].runner == "cpu" and tasks[0].final and tasks[0].local_dir is None
    assert not dispatch.is_splat_training(plan)
