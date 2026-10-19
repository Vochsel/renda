import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from renda_worker.events import EventSink
from renda_worker.pipeline import Deps
from renda_worker.storage import MemoryStorage

BLOB = b"solid teapot"
SHA = hashlib.sha256(BLOB).hexdigest()
URI = f"asset://{SHA}"


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, url, body, headers):
        self.calls.append((url, body, headers))
        return 200

    @property
    def events(self):
        import json

        return [json.loads(b) for _, b, _ in self.calls]

    def of(self, type_):
        return [e for e in self.events if e["type"] == type_]


class FakeCore:
    def __init__(self, frames=(1,), chunks=None, engine="mitsuba", hardware="cpu", cache_key="ck1", output="png"):
        self.frames, self.chunks, self.engine, self.hardware, self.cache_key, self.output = list(frames), chunks, engine, hardware, cache_key, output
        self.rendered = []

    def load_package(self, data):
        out = {"format": self.output, "keepFrames": data.get("output", {}).get("keepFrames", False)}
        return SimpleNamespace(raw=data, output=out, animation={"fps": 24} if self.output == "mp4" else None)

    def resolve(self, pkg, base_dir, fetch):
        return {k: fetch(a["uri"]) for k, a in pkg.raw.get("assets", {}).items()}

    def plan(self, pkg, asset_paths, priority):
        return SimpleNamespace(engine=self.engine, hardware=self.hardware, frames=self.frames, chunks=self.chunks,
                               est_seconds=10, warnings=["passes [depth] unsupported"], cache_key=self.cache_key)

    def render_frames(self, plan, pkg, asset_paths, out_dir, frames):
        self.rendered.append(list(frames))
        files = []
        for f in frames:
            p = Path(out_dir) / f"frame_{f:04d}.png"
            p.write_bytes(b"png%d" % f)
            files.append(p)
        return SimpleNamespace(files=files, warnings=[], seconds=1.0)

    def encode(self, frame_files, fps, output_cfg, out_path):
        Path(out_path).write_bytes(b"mp4:" + b",".join(Path(f).name.encode() for f in frame_files))
        return out_path


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        self.t += 1.5
        return self.t


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def make_deps(tmp_path, recorder):
    def _make(core, **kw):
        sink = EventSink("job_1", "https://api.test/internal/worker/events", "s3cret", poster=recorder, sleep=lambda s: None)
        downloads = []

        def download(url, dest):
            downloads.append(url)
            dest.write_bytes(BLOB)

        deps = Deps(core=core, storage=kw.pop("storage", MemoryStorage()), events=sink, asset_root=tmp_path / "assets",
                    download=download, clock=Clock(), work_root=tmp_path / "work", **kw)
        deps.downloads = downloads
        return deps

    return _make


def job_body(**over):
    body = {
        "jobId": "job_1",
        "kind": "scene",
        "package": {"name": "teapot", "assets": {"teapot": {"uri": URI, "format": "glb" if False else "gltf"}}, "scene": {"root": "teapot"}},
        "priority": "standard",
        "inputs": {URI: "https://r2.test/assets/" + SHA + "?sig=1"},
        "callbackUrl": "https://api.test/internal/worker/events",
    }
    body.update(over)
    return body
