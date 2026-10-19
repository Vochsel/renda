import pytest
import base64
import io
from pathlib import Path

from PIL import Image

from renda_worker import media, pipeline
from renda_worker.models import JobRequest
from renda_worker.storage import MemoryStorage

from .conftest import FakeCore, job_body


def _png(path: Path, size=(640, 360), mode="RGB") -> Path:
    img = Image.new(mode, size)
    if mode == "RGB":
        img.putdata([(x % 256, y % 256, 128) for y in range(size[1]) for x in range(size[0])])
    img.save(path)
    return path


def _wire(deps):
    deps.run_chunks = lambda runner, tasks: [pipeline.run_chunk(t, deps) for t in tasks]
    deps.run_encode = lambda task: pipeline.run_encode(task, deps)
    return deps


class PngCore(FakeCore):
    """FakeCore that writes real PNGs so media metadata can be computed."""

    def render_frames(self, plan, pkg, asset_paths, out_dir, frames):
        self.rendered.append(list(frames))
        files = [_png(Path(out_dir) / f"frame_{f:04d}.png", size=(800, 450)) for f in frames]
        return {"files": files, "warnings": []}


def test_image_media_dimensions_thumb_and_placeholder(tmp_path):
    m = media.image_media(_png(tmp_path / "a.png", size=(1600, 1000)))
    assert (m.width, m.height) == (1600, 1000)
    thumb = Image.open(io.BytesIO(m.thumb))
    assert thumb.format == "WEBP" and max(thumb.size) == media.THUMB_MAX
    assert m.placeholder.startswith("data:image/webp;base64,")
    tiny = Image.open(io.BytesIO(base64.b64decode(m.placeholder.split(",", 1)[1])))
    assert max(tiny.size) <= media.PLACEHOLDER_MAX and len(m.placeholder) < 1000


def test_sixteen_bit_and_unreadable_files(tmp_path):
    depth = Image.new("I;16", (64, 32))
    depth.save(tmp_path / "frame_0001.depth.png")
    assert media.image_media(tmp_path / "frame_0001.depth.png").width == 64
    (tmp_path / "frame.exr").write_bytes(b"not an image")
    assert media.image_media(tmp_path / "frame.exr") is None


def test_thumb_key_layout():
    assert media.thumb_key_for("outputs/job_1/frame_0001.png") == "outputs/job_1/thumbs/frame_0001.webp"
    assert media.thumb_key_for("outputs/job_1/teapot.mp4") == "outputs/job_1/thumbs/teapot.webp"


def test_attach_uploads_thumb_and_sets_fields(tmp_path):
    storage = MemoryStorage()
    obj = media.attach({"key": "outputs/j/frame_0001.png"}, _png(tmp_path / "frame_0001.png"), storage)
    assert obj["width"] == 640 and obj["height"] == 360 and obj["thumbKey"] == "outputs/j/thumbs/frame_0001.webp"
    assert storage.objects["outputs/j/thumbs/frame_0001.webp"][1] == "image/webp"


def test_video_uses_poster_frame(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not decoded when a poster frame is given")
    m = media.video_media(video, poster_frame=_png(tmp_path / "f.png", size=(720, 720)))
    assert (m.width, m.height) == (720, 720)


def test_video_poster_extracted_with_ffmpeg(tmp_path):
    import subprocess

    try:
        ffmpeg_binary = media._ffmpeg  # core's bundled ffmpeg when installed, else the system one
        ffmpeg_binary()
    except RuntimeError:
        pytest.skip("no ffmpeg available")

    for i in range(3):
        _png(tmp_path / f"f{i}.png", size=(320, 180))
    clip = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg_binary(), "-loglevel", "error", "-y", "-framerate", "10", "-i", str(tmp_path / "f%d.png"),
                    "-pix_fmt", "yuv420p", str(clip)], check=True)
    m = media.video_media(clip)
    assert (m.width, m.height) == (320, 180)
    assert not (tmp_path / "clip.poster.png").exists()


def test_still_job_outputs_carry_gallery_metadata(make_deps, recorder):
    deps = _wire(make_deps(PngCore()))
    out = pipeline.run_scene_job(JobRequest.model_validate(job_body()), deps)
    assert out["status"] == "succeeded", out
    (o,) = recorder.of("done")[0]["outputs"]
    assert (o["width"], o["height"]) == (800, 450) and o["placeholder"].startswith("data:image/webp")
    assert o["thumbKey"] in deps.storage.objects


def test_video_job_output_gets_poster_but_intermediate_frames_do_not(make_deps, recorder):
    core = PngCore(frames=range(1, 4), chunks=[[1, 2, 3]], engine="blender-eevee", hardware="gpu-l4", output="mp4")
    deps = _wire(make_deps(core))
    body = job_body()
    body["package"]["output"] = {"format": "mp4"}
    pipeline.run_scene_job(JobRequest.model_validate(body), deps)
    (video,) = recorder.of("done")[0]["outputs"]
    assert video["contentType"] == "video/mp4" and video["width"] == 800
    thumbs = [k for k in deps.storage.objects if "/thumbs/" in k]
    assert thumbs == [video["thumbKey"]]  # no thumbnails for work/…/frames


def test_cache_restore_copies_thumbnails(make_deps, recorder):
    storage = MemoryStorage()
    deps = _wire(make_deps(PngCore(), storage=storage))
    pipeline.run_scene_job(JobRequest.model_validate(job_body()), deps)
    deps2 = _wire(make_deps(PngCore(), storage=storage))
    body = job_body()
    body["jobId"] = "job_2"
    body["outputPrefix"] = "outputs/job_2/"
    out = pipeline.run_scene_job(JobRequest.model_validate(body), deps2)
    assert out["cached"] is True
    restored = out["outputs"][0]
    assert restored["thumbKey"].startswith("outputs/job_2/thumbs/") and restored["thumbKey"] in storage.objects
