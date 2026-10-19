"""Cloud client against a mock transport: upload dedupe, uri rewriting, error hints."""

import json

import httpx
import pytest
from fixtures import write_mesh_files

from renda.cloud import CloudError, RendaClient


class FakeAPI:
    def __init__(self):
        self.blobs: set[str] = set()
        self.puts: list[str] = []
        self.jobs: dict[str, dict] = {}

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if req.method == "PUT" and path.startswith("/r2/"):
            self.blobs.add(path.split("/")[-1])
            self.puts.append(path)
            return httpx.Response(200)
        assert req.headers.get("authorization") == "Bearer rnd_test"
        body = json.loads(req.content or b"{}")
        if path == "/api/v1/uploads":
            sha = body["sha256"]
            exists = sha in self.blobs
            return httpx.Response(200, json={"assetUri": f"asset://{sha}", "exists": exists,
                                             "uploadUrl": None if exists else f"https://r2.test/r2/{sha}"})
        if path == "/api/v1/jobs" and req.method == "POST":
            if body["package"]["scene"]["root"] == "broke":
                return httpx.Response(402, json={"error": {"code": "payment_required", "message": "free minutes used",
                                                           "hint": "POST /api/v1/billing/checkout"}})
            job = {"id": "job_1", "status": "succeeded", "package": body["package"], "outputs": []}
            self.jobs["job_1"] = job
            return httpx.Response(200, json=job)
        if path == "/api/v1/jobs/job_1":
            return httpx.Response(200, json=self.jobs["job_1"])
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})


@pytest.fixture
def client():
    api = FakeAPI()
    c = RendaClient("https://api.test", "rnd_test")
    c.http = httpx.Client(transport=httpx.MockTransport(api))
    c.api = api
    return c


def test_prepare_package_uploads_once_and_rewrites(client, tmp_path):
    files = write_mesh_files(tmp_path)
    raw = {"assets": {"m": {"uri": "./box.glb"}}, "scene": {"root": "m", "include": [{"asset": "./box.stl"}]}}
    out = client.prepare_package(raw, tmp_path)
    assert out["assets"]["m"]["uri"].startswith("asset://")
    assert out["scene"]["include"][0]["asset"].startswith("asset://")
    assert raw["assets"]["m"]["uri"] == "./box.glb"  # input untouched
    assert "camera" not in out  # stays as-authored: server sees which fields were explicit
    assert len(client.api.puts) == 2
    client.prepare_package(raw, tmp_path)
    assert len(client.api.puts) == 2  # dedup by sha256
    assert files


def test_errors_carry_hints(client):
    with pytest.raises(CloudError) as e:
        client.create_job({"scene": {"root": "broke"}})
    assert e.value.code == "payment_required" and "checkout" in e.value.hint and e.value.status == 402


def test_wait(client):
    client.create_job({"scene": {"root": "asset://" + "a" * 64}})
    assert client.wait("job_1", interval=0)["status"] == "succeeded"
