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
         "freeSecondsLeft": 300, "expiresAt": "2026-10-26T00:00:00Z"}


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
