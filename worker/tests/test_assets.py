"""Asset cache: parallel ranged downloads, local staging, one commit per job, fetch time not billed."""

import hashlib
import http.server
import os
import threading

import pytest

from renda_worker import assets
from renda_worker.assets import AssetCache
from renda_worker.models import JobRequest

from .conftest import FakeCore, job_body
from .test_pipeline import _wire


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    data = b""
    ranges = True
    requests_seen: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range")
        type(self).requests_seen.append(rng)
        body, status = self.data, 200
        if rng and self.ranges:
            a, b = rng.removeprefix("bytes=").split("-")
            a, b = int(a), min(int(b), len(self.data) - 1)
            body, status = self.data[a:b + 1], 206
        self.send_response(status)
        if status == 206:
            self.send_header("Content-Range", f"bytes {a}-{b}/{len(self.data)}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


@pytest.mark.parametrize("ranges", [True, False])
def test_http_download_parallel_ranges_and_fallback(server, tmp_path, monkeypatch, ranges):
    monkeypatch.setattr(assets, "PART_SIZE", 64 << 10)
    data = os.urandom((64 << 10) * 5 + 123)
    _RangeHandler.data, _RangeHandler.ranges, _RangeHandler.requests_seen = data, ranges, []
    dest = tmp_path / "out.bin"
    assets._http_download(f"http://127.0.0.1:{server.server_port}/blob", dest)
    assert dest.read_bytes() == data
    parts = [r for r in _RangeHandler.requests_seen if r and r != "bytes=0-0"]
    assert (len(parts) > 1) == ranges


def test_downloads_stage_locally_then_land_once_in_the_volume(tmp_path):
    vol, scratch = tmp_path / "vol", tmp_path / "scratch"
    data = b"glTF" + b"x" * 1000
    sha = hashlib.sha256(data).hexdigest()
    writes = []

    def download(url, dest):
        writes.append(dest.parent)
        dest.write_bytes(data)

    cache = AssetCache(vol, {f"asset://{sha}": "https://r2/x"}, download=download, scratch=scratch)
    p = cache.fetch(f"asset://{sha}")
    assert writes == [scratch] and p.read_bytes() == data and p.parent.parent.parent == vol
    assert not [f for f in p.parent.iterdir() if f.name.startswith(".")]


def test_one_commit_per_job_for_many_new_assets(tmp_path):
    blobs = [b"a" * 10, b"b" * 10, b"c" * 10]
    shas = [hashlib.sha256(b).hexdigest() for b in blobs]
    by_url = {f"https://r2/{i}": b for i, b in enumerate(blobs)}
    commits = []
    cache = AssetCache(tmp_path / "vol", {f"asset://{s}": f"https://r2/{i}" for i, s in enumerate(shas)},
                       download=lambda url, dest: dest.write_bytes(by_url[url]), on_new=lambda: commits.append(1),
                       scratch=tmp_path / "scratch", defer_commit=True)
    for s in shas:
        cache.fetch(f"asset://{s}")
    assert commits == []
    cache.commit()
    cache.commit()  # nothing new
    assert commits == [1]


def test_asset_cache_fill_time_is_not_billed(make_deps, recorder):
    """A slow first download (10 minutes) must not show up in the orchestrator's or a chunk's usage."""
    deps = _wire(make_deps(FakeCore()))
    real = deps.download

    def slow(url, dest):
        deps.clock.t += 600
        real(url, dest)

    deps.download = slow
    out = __import__("renda_worker.pipeline", fromlist=["x"]).run_scene_job(JobRequest.model_validate(job_body()), deps)
    assert out["status"] == "succeeded", out
    usage = {e["chunk"]: e["seconds"] for e in recorder.of("usage")}
    assert usage["orchestrate"] < 60 and all(v < 60 for v in usage.values()), usage
