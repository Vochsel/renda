"""Renda cloud API client (ARCHITECTURE.md §3). Sync httpx, no magic."""

from __future__ import annotations

import copy
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from ..assets import local_files
from ..cache import sha256_file
from ..spec import load_package
from ..video import CONTENT_TYPES

DEFAULT_API_URL = "https://renda.vochsel.com"  # may move to renda.online; override with RENDA_API_URL
TERMINAL = {"succeeded", "failed", "canceled"}


class CloudError(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None, status: int | None = None):
        super().__init__(message)
        self.code, self.message, self.hint, self.status = code, message, hint, status

    def to_json(self) -> dict:
        d = {"code": self.code, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d


# -- credentials ------------------------------------------------------------------


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "renda"


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


def load_credentials() -> dict:
    try:
        return json.loads(credentials_path().read_text())
    except (OSError, ValueError):
        return {}


def save_credentials(data: dict) -> None:
    p = credentials_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def api_url() -> str:
    return (os.environ.get("RENDA_API_URL") or load_credentials().get("apiUrl") or DEFAULT_API_URL).rstrip("/")


def api_key() -> str | None:
    return os.environ.get("RENDA_API_KEY") or load_credentials().get("apiKey")


# -- client -----------------------------------------------------------------------


class RendaClient:
    def __init__(self, base_url: str | None = None, key: str | None = None, timeout: float = 60):
        self.base_url = (base_url or api_url()).rstrip("/")
        self.key = key if key is not None else api_key()
        self.http = httpx.Client(timeout=timeout, follow_redirects=True,
                                 headers={"user-agent": f"renda-cli/{_version()}"})

    # low level
    def request(self, method: str, path: str, body: Any = None, auth: bool = True, params: dict | None = None) -> Any:
        headers = {}
        if auth:
            if not self.key:
                raise CloudError("unauthenticated", "not logged in",
                                 "run `renda trial` (no signup), `renda signup <email>` then `renda verify <code>`, or `renda login`", 401)
            headers["authorization"] = f"Bearer {self.key}"
        try:
            r = self.http.request(method, f"{self.base_url}/api/v1{path}", json=body, headers=headers, params=params)
        except httpx.HTTPError as e:
            raise CloudError("network_error", f"can't reach {self.base_url}: {e}", "check RENDA_API_URL / your connection") from e
        if r.status_code == 428:
            return {"pending": True, **_safe_json(r)}
        if r.status_code >= 400:
            err = _safe_json(r).get("error") or {}
            raise CloudError(err.get("code", f"http_{r.status_code}"), err.get("message", r.text[:500] or r.reason_phrase),
                             err.get("hint"), r.status_code)
        return _safe_json(r)

    # auth
    def magic_start(self, email: str) -> dict:
        return self.request("POST", "/auth/magic/start", {"email": email}, auth=False)

    def magic_verify(self, email: str, code: str, key_name: str | None = None) -> dict:
        return self.request("POST", "/auth/magic/verify", {"email": email, "code": code, "keyName": key_name or _key_name()}, auth=False)

    def trial(self) -> dict:
        """Anonymous claimable trial: {apiKey, claimUrl, freeSecondsLeft, expiresAt}."""
        return self.request("POST", "/auth/trial", {}, auth=False)

    def device_start(self) -> dict:
        return self.request("POST", "/auth/device", {}, auth=False)

    def device_token(self, device_code: str) -> dict:
        return self.request("POST", "/auth/device/token", {"deviceCode": device_code}, auth=False)

    def me(self) -> dict:
        return self.request("GET", "/me")

    # assets
    def upload(self, path: Path, on_progress: Callable[[str], None] | None = None) -> str:
        path = Path(path)
        sha = sha256_file(path)
        ext = path.suffix.lower().lstrip(".")
        ctype = CONTENT_TYPES.get(ext, "application/octet-stream")
        res = self.request("POST", "/uploads", {"filename": path.name, "size": path.stat().st_size, "sha256": sha, "contentType": ctype})
        if not res.get("exists") and res.get("uploadUrl"):
            if on_progress:
                on_progress(f"uploading {path.name} ({path.stat().st_size / 1e6:.1f} MB)")
            with open(path, "rb") as f:
                r = self.http.put(res["uploadUrl"], content=f, headers={"content-type": ctype}, timeout=None)
            if r.status_code >= 300:
                raise CloudError("upload_failed", f"upload of {path.name} failed: HTTP {r.status_code}", "retry the command")
        return res.get("assetUri") or f"asset://{sha}"

    def prepare_package(self, raw: dict, base_dir: Path, on_progress: Callable[[str], None] | None = None) -> dict:
        """Upload local files referenced by a raw package dict; return a copy with asset:// uris.

        Works on the raw (as-written) dict so the server sees which fields the author set.
        """
        pkg = load_package(raw)
        out = copy.deepcopy(raw)
        uploaded: dict[str, str] = {}
        for key, p in local_files(pkg, base_dir).items():
            if not p.exists():
                raise CloudError("asset_missing", f"{p} does not exist", "fix the path in your package")
            uploaded[key] = self.upload(p, on_progress)
        for aid, asset in out.get("assets", {}).items():
            if aid in uploaded:
                asset["uri"] = uploaded[aid]
        scene = out.get("scene", {})
        if scene.get("root") in uploaded and scene["root"] not in out.get("assets", {}):
            scene["root"] = uploaded[scene["root"]]
        for inc in scene.get("include", []):
            if inc.get("asset") in uploaded and inc["asset"] not in out.get("assets", {}):
                inc["asset"] = uploaded[inc["asset"]]
        env = out.get("environment", {})
        if env.get("hdri") in uploaded and env["hdri"] not in out.get("assets", {}):
            env["hdri"] = uploaded[env["hdri"]]
        return out

    # jobs
    def estimate(self, package: dict) -> dict:
        return self.request("POST", "/estimate", {"package": package})

    def create_job(self, package: dict, kind: str = "scene", priority: str = "standard", idempotency_key: str | None = None) -> dict:
        body = {"package": package, "kind": kind, "priority": priority}
        if idempotency_key:
            body["idempotencyKey"] = idempotency_key
        return self.request("POST", "/jobs", body)

    def get_job(self, job_id: str) -> dict:
        return self.request("GET", f"/jobs/{job_id}")

    def list_jobs(self, limit: int = 20) -> list:
        res = self.request("GET", "/jobs", params={"limit": limit})
        return res.get("jobs", res) if isinstance(res, dict) else res

    def cancel_job(self, job_id: str) -> dict:
        return self.request("POST", f"/jobs/{job_id}/cancel", {})

    def wait(self, job_id: str, on_update: Callable[[dict], None] | None = None, interval: float = 2.0, timeout: float | None = None) -> dict:
        t0 = time.time()
        while True:
            job = self.get_job(job_id)
            if on_update:
                on_update(job)
            if job.get("status") in TERMINAL:
                return job
            if timeout and time.time() - t0 > timeout:
                raise CloudError("timeout", f"job {job_id} still {job.get('status')} after {timeout:.0f}s",
                                 f"check later with `renda jobs {job_id}`")
            time.sleep(interval)

    def download_outputs(self, job: dict, out_dir: Path) -> list[Path]:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for o in job.get("outputs", []):
            dst = out_dir / Path(o["name"]).name
            with self.http.stream("GET", o["url"], timeout=None) as r:
                r.raise_for_status()
                with open(dst, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
            files.append(dst)
        return files

    # account
    def usage(self, since: str | None = None, until: str | None = None) -> dict:
        params = {k: v for k, v in (("from", since), ("to", until)) if v}
        return self.request("GET", "/usage", params=params or None)

    def billing_checkout(self) -> dict:
        return self.request("POST", "/billing/checkout", {})

    def billing_portal(self) -> dict:
        return self.request("GET", "/billing/portal")


def _safe_json(r: httpx.Response) -> dict:
    try:
        data = r.json()
        return data if isinstance(data, dict) else {"data": data}
    except ValueError:
        return {}


def _key_name() -> str:
    import socket

    return f"renda-cli@{socket.gethostname()}"


def _version() -> str:
    from .. import __version__

    return __version__
