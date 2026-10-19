import io
import subprocess
import zipfile

import pytest

from renda_worker import pipeline
from renda_worker.assets import AssetCache
from renda_worker.models import JobRequest
from renda_worker.storage import MemoryStorage

from .conftest import BLOB, SHA, URI, FakeCore, job_body


def _wire(deps):
    """Run chunks/encode in-process, as Modal would remotely."""
    deps.run_chunks = lambda runner, tasks: [pipeline.run_chunk(t, deps) for t in tasks]
    deps.run_encode = lambda task: pipeline.run_encode(task, deps)
    return deps


def test_still_job_end_to_end(make_deps, recorder):
    core = FakeCore()
    deps = _wire(make_deps(core))
    out = pipeline.run_scene_job(JobRequest.model_validate(job_body()), deps)

    assert out["status"] == "succeeded" and not out["cached"]
    assert [o["key"] for o in out["outputs"]] == ["outputs/job_1/frame_0001.png"]
    assert "outputs/job_1/frame_0001.png" in deps.storage.objects
    assert deps.storage.get_json("cache/v1/ck1.json")["outputs"] == out["outputs"]
    statuses = [e["status"] for e in recorder.of("status")]
    assert statuses[0] == "planning" and statuses[-1] == "succeeded" and "rendering" in statuses
    assert recorder.of("warning")[0]["message"] == "passes [depth] unsupported"
    usage = {e["chunk"]: e for e in recorder.of("usage")}
    assert set(usage) == {0, "orchestrate"} and usage[0]["hardware"] == "cpu"
    assert recorder.of("done")[0]["cached"] is False
    # asset downloaded once and named from the package format hint
    assert len(deps.downloads) == 1
    assert (deps.asset_root / SHA[:2] / SHA / "teapot.glb").read_bytes() == BLOB


def test_cache_hit_short_circuits_without_rendering_or_billing(make_deps, recorder):
    storage = MemoryStorage()
    storage.objects["outputs/old/frame_0001.png"] = (b"png", "image/png")
    storage.put_json("cache/v1/ck1.json", {"outputs": [{"key": "outputs/old/frame_0001.png", "contentType": "image/png", "bytes": 3}]})
    core = FakeCore()
    deps = _wire(make_deps(core, storage=storage))

    out = pipeline.run_scene_job(JobRequest.model_validate(job_body()), deps)

    assert out["cached"] is True
    assert core.rendered == []
    assert storage.objects["outputs/job_1/frame_0001.png"][0] == b"png"
    assert recorder.of("usage") == []
    assert recorder.of("done")[0] == {"jobId": "job_1", "type": "done", "cached": True,
                                     "outputs": [{"key": "outputs/job_1/frame_0001.png", "contentType": "image/png", "bytes": 3}]}


def test_video_job_chunks_then_encodes(make_deps, recorder):
    core = FakeCore(frames=range(1, 7), chunks=[[1, 2, 3], [4, 5, 6]], engine="blender-eevee", hardware="gpu-l4", output="mp4")
    deps = _wire(make_deps(core))
    body = job_body()
    body["package"]["output"] = {"format": "mp4", "keepFrames": True}
    out = pipeline.run_scene_job(JobRequest.model_validate(body), deps)

    assert out["status"] == "succeeded", out
    assert core.rendered == [[1, 2, 3], [4, 5, 6]]
    video = out["outputs"][0]
    assert video["key"] == "outputs/job_1/teapot.mp4" and video["contentType"] == "video/mp4"
    assert deps.storage.objects[video["key"]][0].startswith(b"mp4:frame_0001.png,frame_0002.png")
    assert len(out["outputs"]) == 7 and out["outputs"][1]["key"] == "outputs/job_1/frames/frame_0001.png"
    usage = {e["chunk"]: e["hardware"] for e in recorder.of("usage")}
    assert usage == {0: "gpu-l4", 1: "gpu-l4", "encode": "cpu", "orchestrate": "cpu"}
    assert "encoding" in [e["status"] for e in recorder.of("status")]


def test_render_failure_reports_failed_and_still_bills_spent_time(make_deps, recorder):
    core = FakeCore()

    def boom(*a, **k):
        raise RuntimeError("blender crashed")

    core.render_frames = boom
    deps = _wire(make_deps(core))
    out = pipeline.run_scene_job(JobRequest.model_validate(job_body()), deps)
    assert out["status"] == "failed"
    assert "blender crashed" in recorder.of("failed")[0]["error"]
    assert {e["chunk"] for e in recorder.of("usage")} == {0, "orchestrate"}


def test_cancel_between_steps(make_deps, recorder):
    deps = _wire(make_deps(FakeCore(), is_canceled=lambda: True))
    out = pipeline.run_scene_job(JobRequest.model_validate(job_body()), deps)
    assert out["status"] == "canceled"
    assert recorder.of("status")[-1]["status"] == "canceled"
    assert recorder.of("done") == []


def test_asset_cache_rejects_hash_mismatch(tmp_path):
    cache = AssetCache(tmp_path, {URI: "https://r2/x"}, download=lambda url, dest: dest.write_bytes(b"tampered"))
    with pytest.raises(ValueError, match="hash mismatch"):
        cache.fetch(URI)
    assert not list((tmp_path / SHA[:2] / SHA).iterdir())


def test_asset_cache_hit_links_new_name(tmp_path):
    n = []
    cache = AssetCache(tmp_path, {URI: "https://r2/assets/model.stl?sig"}, download=lambda u, d: (n.append(u), d.write_bytes(BLOB)))
    first = cache.fetch(URI)
    assert first.name == "model.stl"
    second = AssetCache(tmp_path, {URI: "https://r2/x"}, hints={URI: "part.obj"}, download=lambda u, d: n.append(u)).fetch(URI)
    assert second.name == "part.obj" and second.read_bytes() == BLOB and len(n) == 1


def test_asset_cache_requires_presigned_url(tmp_path):
    with pytest.raises(KeyError):
        AssetCache(tmp_path, {}).fetch(URI)
    with pytest.raises(ValueError):
        AssetCache(tmp_path, {}).fetch("file:///etc/passwd")


def _project_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_hyperframes_job(make_deps, recorder, tmp_path):
    import hashlib

    data = _project_zip({"promo/index.html": "<html></html>", "promo/hyperframes.json": "{}"})
    sha = hashlib.sha256(data).hexdigest()
    deps = make_deps(FakeCore())
    deps.download = lambda url, dest: dest.write_bytes(data)
    seen = {}

    def fake_run(cmd, cwd, timeout):
        seen["cmd"], seen["cwd"] = cmd, cwd
        out = cmd[cmd.index("--output") + 1]
        open(out, "wb").write(b"video")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    body = job_body(kind="hyperframes", package={"project": f"asset://{sha}", "format": "webm", "name": "promo"},
                    inputs={f"asset://{sha}": "https://r2/p.zip"})
    out = pipeline.run_hyperframes_job(JobRequest.model_validate(body), deps, run=fake_run)

    assert out["status"] == "succeeded", out
    assert seen["cwd"].name == "promo"  # descended into the zip's top-level folder
    assert out["outputs"][0]["key"] == "outputs/job_1/promo.webm"
    assert out["outputs"][0]["contentType"] == "video/webm"
    assert recorder.of("usage")[0]["chunk"] == "hyperframes" and recorder.of("usage")[0]["hardware"] == "cpu"


def test_hyperframes_failure_and_zip_slip(make_deps, recorder):
    import hashlib

    data = _project_zip({"../escape.html": "x"})
    sha = hashlib.sha256(data).hexdigest()
    deps = make_deps(FakeCore())
    deps.download = lambda url, dest: dest.write_bytes(data)
    body = job_body(kind="hyperframes", package={"project": f"asset://{sha}"}, inputs={f"asset://{sha}": "https://r2/p.zip"})
    out = pipeline.run_hyperframes_job(JobRequest.model_validate(body), deps, run=lambda *a: None)
    assert out["status"] == "failed" and "unsafe path" in recorder.of("failed")[0]["error"]


def test_orchestrator_does_not_bill_time_spent_waiting_on_encode(make_deps, recorder):
    core = FakeCore(frames=range(1, 3), chunks=[[1, 2]], engine="blender-eevee", hardware="gpu-l4", output="mp4")
    deps = _wire(make_deps(core))
    real_encode = deps.run_encode

    def slow_encode(task):
        deps.clock.t += 600  # a 10-minute encode in another (separately billed) container
        return real_encode(task)

    deps.run_encode = slow_encode
    body = job_body()
    body["package"]["output"] = {"format": "mp4"}
    out = pipeline.run_scene_job(JobRequest.model_validate(body), deps)
    assert out["status"] == "succeeded", out
    orchestrate = next(e for e in recorder.of("usage") if e["chunk"] == "orchestrate")
    assert orchestrate["seconds"] < 60


def test_all_usage_is_reported_before_done(make_deps, recorder):
    core = FakeCore(frames=range(1, 4), chunks=[[1], [2], [3]], engine="blender-eevee", hardware="gpu-l4", output="mp4")
    deps = _wire(make_deps(core))
    body = job_body()
    body["package"]["output"] = {"format": "mp4"}
    pipeline.run_scene_job(JobRequest.model_validate(body), deps)
    types = [e["type"] for e in recorder.events]
    assert types.index("done") > max(i for i, t in enumerate(types) if t == "usage")
