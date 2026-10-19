"""`renda video`: HyperFrames jobs with variables (text, uploads, job:<id>, 3D file → turntable)."""

import json

import httpx

from test_cli import _mock_client, run

KEY = "rnd_live_xxxxxxxxxxxxxxxxxxxxxxxx"
CLIP = {"name": "tt.mp4", "url": "https://r2.test/tt.mp4?sig=1", "contentType": "video/mp4", "bytes": 9}


def _project(tmp_path):
    p = tmp_path / "card"
    p.mkdir()
    (p / "index.html").write_text("<html></html>")
    return p


def _api(posts, uploads):
    def api(req):
        path = req.url.path
        if path == "/api/v1/uploads":
            body = json.loads(req.content)
            uploads.append(body["filename"])
            return httpx.Response(200, json={"assetUri": f"asset://{str(len(uploads)) * 64}", "exists": True})
        if path == "/api/v1/jobs" and req.method == "POST":
            body = json.loads(req.content)
            posts.append(body)
            jid = "job_hf" if body.get("kind") == "hyperframes" else "job_tt"
            return httpx.Response(200, json={"id": jid, "status": "queued", "outputs": []})
        if path == "/api/v1/jobs/job_tt":
            return httpx.Response(200, json={"id": "job_tt", "status": "succeeded", "outputs": [CLIP]})
        if path == "/api/v1/jobs/job_hf":
            return httpx.Response(200, json={"id": "job_hf", "status": "succeeded", "outputs": [], "billedSeconds": 30})
        raise AssertionError(f"unexpected {req.method} {path}")
    return api


def test_video_variables_text_upload_and_job_ref(monkeypatch, tmp_path):
    posts, uploads = [], []
    _mock_client(monkeypatch, _api(posts, uploads), key=KEY)
    project = _project(tmp_path)
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"\x89PNG")
    monkeypatch.chdir(tmp_path)
    code, data = run("video", project, "--var", "title=Hello, world", "--var", "logo=logo.png",
                     "--var", "clipUrl=job:job_tt", "--vars", '{"accent": "#f97316"}', "--quality", "final", "-o", tmp_path / "out")
    assert code == 0, data
    (hf,) = posts
    assert hf["kind"] == "hyperframes" and hf["package"]["quality"] == "delivery"
    assert hf["package"]["project"].startswith("asset://") and uploads[0] == "card.zip"
    assert hf["package"]["variables"] == {"accent": "#f97316", "title": "Hello, world", "logo": "asset://" + "2" * 64,
                                          "clipUrl": CLIP["url"]}
    assert data["inputJobs"][0]["jobId"] == "job_tt"


def test_video_3d_file_variable_renders_a_turntable_first(monkeypatch, tmp_path):
    posts, uploads = [], []
    _mock_client(monkeypatch, _api(posts, uploads), key=KEY)
    project = _project(tmp_path)
    (tmp_path / "robot.glb").write_bytes(b"glTF....")
    monkeypatch.chdir(tmp_path)
    code, data = run("video", project, "--var", "clipUrl=robot.glb", "-o", tmp_path / "out")
    assert code == 0, data
    assert [p.get("kind", "scene") for p in posts] == ["scene", "hyperframes"]
    assert posts[0]["package"]["output"]["format"] == "mp4"
    assert posts[0]["package"]["animation"]["turntable"] == {"revolutions": 1}
    assert posts[1]["package"]["variables"]["clipUrl"] == CLIP["url"]


def test_video_usage_errors(monkeypatch, tmp_path):
    _mock_client(monkeypatch, _api([], []), key=KEY)
    project = _project(tmp_path)
    assert run("video", project, "--var", "novalue")[0] == 2
    assert run("video", project, "--quality", "ultra")[0] == 2
    code, data = run("video", tmp_path, "-o", tmp_path / "out")  # no index.html
    assert code == 1 and data["reason"] == "not_a_project"
