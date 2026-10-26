import json

from fixtures import write_mesh_files, write_threejs
from typer.testing import CliRunner

from renda.cli import app

runner = CliRunner()


def run(*args):
    res = runner.invoke(app, [*map(str, args), "--json"])
    return res.exit_code, json.loads(res.stdout.strip().splitlines()[-1])


def test_info(tmp_path):
    code, data = run("info", write_threejs(tmp_path))
    assert code == 0
    assert data["format"] == "threejs" and data["triangles"] == 14 and data["cameras"] == ["MainCam"]


def test_validate_ok_and_error(tmp_path):
    good = tmp_path / "a.renda.json"
    good.write_text(json.dumps({"scene": {"root": "./missing.glb"}}))
    code, data = run("validate", good)
    assert code == 0 and data["valid"] and "not found" in data["warnings"][0]
    bad = tmp_path / "b.renda.json"
    bad.write_text(json.dumps({"scene": {"root": "x.glb"}, "render": {"quality": "ultra"}}))
    code, data = run("validate", bad)
    assert code == 1 and data["error"]["code"] == "invalid_package"
    assert data["error"]["issues"][0]["path"] == "render.quality" and data["error"]["hint"]


def test_init_then_validate(tmp_path):
    files = write_mesh_files(tmp_path)
    code, data = run("init", files["glb"])
    assert code == 0
    code, data = run("validate", data["package"])
    assert code == 0 and data["package"]["assets"]["scene"]["uri"] == "./box.glb"
    code, data = run("init", files["glb"])
    assert code == 1 and data["error"]["code"] == "exists"


def test_convert(tmp_path):
    code, data = run("convert", write_threejs(tmp_path), tmp_path / "out.glb")
    assert code == 0 and (tmp_path / "out.glb").exists() and data["triangles"] == 14


def test_engines():
    code, data = run("engines")
    assert code == 0 and {e["name"] for e in data["engines"]} >= {"blender-cycles", "mitsuba", "threejs"}


def test_estimate_local(tmp_path):
    files = write_mesh_files(tmp_path)
    code, data = run("estimate", files["glb"], "--local", "--turntable", "--format", "mp4")
    assert code == 0 and data["frames"] == 120 and data["estCostUsd"][0] > 0


def test_cloud_commands_need_login(tmp_path, monkeypatch):
    monkeypatch.delenv("RENDA_API_KEY", raising=False)
    code, data = run("whoami")
    assert code == 3 and data["error"]["code"] == "unauthenticated" and "signup" in data["error"]["hint"]
    assert data["status"] == "error" and data["reason"] == "unauthenticated"
    assert any("renda trial" in n for n in data["next"])


def test_bad_option(tmp_path):
    files = write_mesh_files(tmp_path)
    code, data = run("render", files["glb"], "--res", "big")
    assert code == 1 and data["error"]["code"] == "bad_option"


def _mock_client(monkeypatch, handler, key=None):
    import httpx

    import renda.cli as cli
    from renda.cloud import RendaClient

    def make():
        c = RendaClient("https://api.test", key)
        c.http = httpx.Client(transport=httpx.MockTransport(handler))
        return c

    monkeypatch.setattr(cli, "_client", make)


TRIAL = {"apiKey": "rnd_live_trialtrialtrialtrialtrial", "claimUrl": "https://api.test/claim?token=t",
         "creditUsd": 0, "freeSecondsLeft": 0, "expiresAt": "2026-10-26T00:00:00Z"}


def test_trial_saves_key_and_never_prints_it(monkeypatch):
    import httpx

    from renda.cloud import load_credentials

    monkeypatch.delenv("RENDA_API_KEY", raising=False)
    _mock_client(monkeypatch, lambda req: httpx.Response(201, json=TRIAL))
    res = runner.invoke(app, ["trial", "--force", "--json"])
    assert res.exit_code == 0, res.stdout
    data = json.loads(res.stdout.strip().splitlines()[-1])
    assert data["status"] == "ok" and data["claimUrl"] == TRIAL["claimUrl"]
    assert TRIAL["apiKey"] not in res.stdout
    assert load_credentials()["apiKey"] == TRIAL["apiKey"]
    code, claim = run("claim")
    assert code == 0 and claim["claimUrl"] == TRIAL["claimUrl"]
    assert any("renda billing" in n for n in data["next"])


def test_balance_reads_credit_and_billing_sends_amount(monkeypatch):
    import httpx

    seen = []

    def handler(req):
        if req.url.path.endswith("/me"):
            return httpx.Response(200, json={"user": None, "org": {"id": "o"}, "balance": {"creditUsd": 0, "freeSecondsLeft": 0}, "billing": {}})
        if req.url.path.endswith("/billing/checkout"):
            seen.append(json.loads(req.content or b"{}"))
            return httpx.Response(200, json={"url": "https://checkout.stripe.test/c"})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": req.url.path}})

    _mock_client(monkeypatch, handler, key="rnd_live_xxxxxxxxxxxxxxxxxxxxxxxx")
    code, data = run("balance")
    assert code == 0 and data["creditUsd"] == 0
    assert any("renda billing" in n for n in data["next"])
    code, data = run("billing", "--amount", "50")
    assert code == 0 and data["url"] == "https://checkout.stripe.test/c" and seen == [{"amountUsd": 50.0}]


def test_cloud_render_max_minutes_refuses(monkeypatch, tmp_path):
    import httpx

    files = write_mesh_files(tmp_path)

    def api(req):
        p = req.url.path
        if p == "/api/v1/uploads":
            return httpx.Response(200, json={"assetUri": "asset://" + "a" * 64, "exists": True})
        if p == "/api/v1/estimate":
            return httpx.Response(200, json={"estSeconds": [600, 1200]})
        raise AssertionError(f"unexpected {p}")

    _mock_client(monkeypatch, api, key="rnd_live_xxxxxxxxxxxxxxxxxxxxxxxx")
    code, data = run("render", files["glb"], "--cloud", "--max-minutes", "5")
    assert code == 1 and data["reason"] == "over_budget"


def test_cloud_render_failed_job_is_an_error(monkeypatch, tmp_path):
    import httpx

    files = write_mesh_files(tmp_path)
    job = {"id": "job_x", "status": "failed", "error": "UnknownFormat: blob", "outputs": []}

    def api(req):
        p = req.url.path
        if p == "/api/v1/uploads":
            return httpx.Response(200, json={"assetUri": "asset://" + "a" * 64, "exists": True})
        if p == "/api/v1/jobs" and req.method == "POST":
            return httpx.Response(200, json={**job, "status": "queued"})
        if p == "/api/v1/jobs/job_x":
            return httpx.Response(200, json=job)
        raise AssertionError(f"unexpected {req.method} {p}")

    _mock_client(monkeypatch, api, key="rnd_live_xxxxxxxxxxxxxxxxxxxxxxxx")
    code, data = run("render", files["glb"], "--cloud")
    assert code == 1
    assert data["status"] == "error" and data["reason"] == "job_failed" and "UnknownFormat" in data["message"]


def test_bake_glb_converts_locally(tmp_path):
    import json as _json

    from typer.testing import CliRunner

    from renda.cli import app

    from pathlib import Path

    scene = Path(__file__).parents[2] / "testdata" / "scenes" / "scene.obj"
    r = CliRunner().invoke(app, ["bake", str(scene), "--glb", "-o", str(tmp_path), "--json"])
    assert r.exit_code == 0, r.output
    out = _json.loads(r.output.strip().splitlines()[-1])
    assert out["engine"] == "export" and out["outputs"][0].endswith(".glb")


def test_bake_rejects_unknown_mode(tmp_path):
    from typer.testing import CliRunner

    from renda.cli import app

    from pathlib import Path

    scene = Path(__file__).parents[2] / "testdata" / "scenes" / "scene.obj"
    r = CliRunner().invoke(app, ["bake", str(scene), "--mode", "shiny", "--json"])
    assert r.exit_code != 0 and "unlit" in r.output


def test_flatten_and_layered_validate(tmp_path):
    (tmp_path / "base.renda.json").write_text(json.dumps({"render": {"quality": "final"}, "output": {"format": "webp"}}))
    shot = tmp_path / "shot.renda.json"
    shot.write_text(json.dumps({"extends": "./base.renda.json", "scene": {"root": "./a.stl"}, "render": {"resolution": [640, 480]}}))
    code, data = run("flatten", shot, "--provenance")
    assert code == 0
    assert data["package"]["render"] == {"quality": "final", "resolution": [640, 480]}
    assert data["provenance"]["render.quality"].endswith("base.renda.json")
    assert data["provenance"]["render.resolution"].endswith("shot.renda.json")
    code, data = run("validate", shot)
    assert code == 0 and data["package"]["output"]["format"] == "webp"


def test_layer_errors_name_the_layer_and_field(tmp_path):
    shot = tmp_path / "shot.renda.json"
    shot.write_text(json.dumps({"extends": ["./missing.renda.json"], "scene": {"root": "./a.stl"}}))
    code, data = run("flatten", shot)
    assert code == 1
    assert data["reason"] == "layer_fetch" and data["error"]["field"] == "extends[0]"
    assert data["error"]["layer"].endswith("shot.renda.json")


def test_estimate_fast_prints_fast_vs_standard(tmp_path):
    import trimesh

    from renda.cli import app

    f = tmp_path / "m.glb"
    trimesh.creation.box().export(f)
    r = runner.invoke(app, ["estimate", str(f), "--local", "--res", "3840x2160", "--quality", "final", "--engine", "mitsuba", "--fast"])
    assert r.exit_code == 0, r.output
    assert "fast (--fast)" in r.output and "standard:" in r.output


def _cloud_api(job_id="job_p"):
    """Fake API: upload → create (queued) → polls (running, with live cost) → succeeded."""
    import httpx

    seq = [
        {"status": "rendering", "progress": 0.3, "live": {"costUsd": 0.002}},
        {"status": "rendering", "progress": 0.7, "live": {"costUsd": 0.006}},
        {"status": "succeeded", "progress": 1, "billedSeconds": 12.0, "costUsd": 0.007},
    ]

    def api(req):
        p = req.url.path
        if p == "/api/v1/uploads":
            return httpx.Response(200, json={"assetUri": "asset://" + "a" * 64, "exists": True})
        if p == "/api/v1/jobs" and req.method == "POST":
            return httpx.Response(200, json={"id": job_id, "status": "queued", "outputs": [],
                                             "estimate": {"hardware": "gpu-l4", "estWallSeconds": [20, 60], "estCostUsd": [0.003, 0.01]}})
        if p == f"/api/v1/jobs/{job_id}":
            j = seq.pop(0) if len(seq) > 1 else seq[0]
            return httpx.Response(200, json={"id": job_id, "outputs": [], **j})
        raise AssertionError(f"unexpected {req.method} {p}")

    return api


def test_cloud_render_announces_job_and_streams_progress_on_stderr(monkeypatch, tmp_path):
    from renda.cloud import client as cloud_client

    monkeypatch.setattr(cloud_client.time, "sleep", lambda s: None)
    files = write_mesh_files(tmp_path)
    _mock_client(monkeypatch, _cloud_api(), key="rnd_live_xxxxxxxxxxxxxxxxxxxxxxxx")
    res = runner.invoke(app, ["render", str(files["glb"]), "--cloud", "-o", str(tmp_path / "out"), "--json"])
    assert res.exit_code == 0, res.output
    # stdout stays exactly one JSON document (the final result), so existing parsers keep working
    lines = [ln for ln in res.stdout.strip().splitlines() if ln.strip()]
    assert len(lines) == 1 and json.loads(lines[0])["status"] == "succeeded"
    # stderr: an immediate "submitted" line with the id + resume hint, then progress lines with live cost
    events = [json.loads(ln) for ln in res.stderr.strip().splitlines() if ln.startswith("{")]
    assert events[0]["status"] == "submitted" and events[0]["id"] == "job_p"
    assert events[0]["next"][0].startswith("renda jobs job_p --wait")
    assert events[0]["estimate"]["estWallSeconds"] == [20, 60]
    progress = [e for e in events if e["status"] == "progress"]
    assert progress and progress[0]["liveCostUsd"] == 0.002 and progress[-1]["jobStatus"] == "succeeded"


def test_jobs_wait_resumes_and_downloads(monkeypatch, tmp_path):
    from renda.cloud import client as cloud_client

    monkeypatch.setattr(cloud_client.time, "sleep", lambda s: None)
    _mock_client(monkeypatch, _cloud_api(), key="rnd_live_xxxxxxxxxxxxxxxxxxxxxxxx")
    code, data = run("jobs", "job_p", "--wait", "-o", tmp_path / "out")
    assert code == 0 and data["status"] == "succeeded" and data["id"] == "job_p"


def test_no_wait_tells_agents_how_to_resume(monkeypatch, tmp_path):
    files = write_mesh_files(tmp_path)
    _mock_client(monkeypatch, _cloud_api(), key="rnd_live_xxxxxxxxxxxxxxxxxxxxxxxx")
    code, data = run("render", files["glb"], "--cloud", "--no-wait")
    assert code == 0 and data["id"] == "job_p" and data["next"][0].startswith("renda jobs job_p --wait")
