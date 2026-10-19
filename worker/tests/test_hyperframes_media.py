import hashlib
import io
import json
import subprocess
import zipfile

from renda_worker import pipeline
from renda_worker.hyperframes import _sniff_ext, _url_ext
from renda_worker.models import HyperframesPackage, JobRequest

from .conftest import URI, FakeCore, job_body


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_media_variables_are_localized(make_deps, recorder):
    """asset:// and https media variables are downloaded into the project; text variables pass through."""
    project = _zip({"index.html": "<html></html>"})
    clip = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64  # an mp4 header, no extension anywhere
    psha, csha = hashlib.sha256(project).hexdigest(), hashlib.sha256(clip).hexdigest()
    blobs = {"https://r2/p.zip": project, "https://r2/clip": clip,
             "https://cdn.test/logo.png": b"\x89PNG\r\n\x1a\n" + b"0" * 32}
    deps = make_deps(FakeCore())
    deps.download = lambda url, dest: dest.write_bytes(blobs[url])
    seen = {}

    def fake_run(cmd, cwd, timeout):
        seen["vars"] = json.loads(cmd[cmd.index("--variables") + 1])
        seen["files"] = {p.relative_to(cwd).as_posix(): p.read_bytes() for p in cwd.rglob("*") if p.is_file()}
        open(cmd[cmd.index("--output") + 1], "wb").write(b"video")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    pkg = {"project": f"asset://{psha}", "durationSec": 6, "fps": 25,
           "variables": {"clipUrl": f"asset://{csha}", "logo": "https://cdn.test/logo.png",
                         "title": "Hello", "link": "https://renda.dev", "n": 3}}
    body = job_body(kind="hyperframes", package=pkg,
                    inputs={f"asset://{psha}": "https://r2/p.zip", f"asset://{csha}": "https://r2/clip"})
    out = pipeline.run_hyperframes_job(JobRequest.model_validate(body), deps, run=fake_run)

    assert out["status"] == "succeeded", out
    assert seen["vars"] == {"clipUrl": "renda-inputs/clipUrl.mp4", "logo": "renda-inputs/logo.png",
                            "title": "Hello", "link": "https://renda.dev", "n": 3}
    assert seen["files"]["renda-inputs/clipUrl.mp4"] == clip
    assert seen["files"]["renda-inputs/logo.png"].startswith(b"\x89PNG")


def test_sniff_and_url_ext(tmp_path):
    cases = {b"\x00\x00\x00\x18ftypisom": ".mp4", b"\x00\x00\x00\x14ftypqt  ": ".mov", b"\x1aE\xdf\xa3": ".webm",
             b"GIF89a": ".gif", b"\x89PNG\r\n": ".png", b"\xff\xd8\xff\xe0": ".jpg", b"RIFF\0\0\0\0WEBPVP8": ".webp",
             b"<svg xmlns=": ".svg", b"plain text": ""}
    for i, (head, ext) in enumerate(cases.items()):
        p = tmp_path / f"b{i}"
        p.write_bytes(head + b"\0" * 16)
        assert _sniff_ext(p) == ext, head
    assert _url_ext("https://x/a/clip.MP4?sig=1") == ".mp4"
    assert _url_ext("https://renda.dev/") == ""


def test_package_accepts_api_fields():
    pkg = HyperframesPackage(project=URI, fps=25, durationSec=12.5)
    assert pkg.fps == 25 and pkg.durationSec == 12.5
