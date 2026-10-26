""".blend / .fbx / .abc → Stage by running Blender headless with extract.py.

There's no good pure-Python reader for these, so Blender does the import (and
for .blend, evaluates modifiers, geometry nodes and instancing) and writes a .rir.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ...procenv import untrusted_env
from ..rir import read_rir
from ..stage import Stage

SCRIPTS = Path(__file__).resolve().parents[2] / "engines" / "blender_scripts"


def blender_binary() -> str | None:
    """`$RENDA_BLENDER`, then `blender` on PATH, then the macOS app bundle."""
    env = os.environ.get("RENDA_BLENDER")
    if env and Path(env).exists():
        return env
    found = shutil.which("blender")
    if found:
        return found
    mac = Path("/Applications/Blender.app/Contents/MacOS/Blender")
    return str(mac) if mac.exists() else None


def bpy_available() -> bool:
    try:
        import bpy  # noqa: F401
    except ImportError:
        return False
    return True


def available() -> bool:
    return blender_binary() is not None or bpy_available()


def run_blender_script(script: str, job: dict, timeout: float | None = None) -> dict:
    """Run a blender_scripts/*.py with a JSON job; returns the JSON the script writes to job['result']."""
    work = Path(tempfile.mkdtemp(prefix="renda-blender-"))
    job = dict(job)
    job.setdefault("result", str(work / "result.json"))
    job_path = work / "job.json"
    job_path.write_text(json.dumps(job))
    binary = blender_binary()
    if binary:
        cmd = [binary, "--background", "--factory-startup", "--python-exit-code", "1",
               "--python", str(SCRIPTS / script), "--", str(job_path)]
        # Blender runs the user's .blend (drivers, geometry nodes, importers): no secrets in its environment.
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=untrusted_env())
        if proc.returncode != 0:
            tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-30:])
            raise RuntimeError(f"blender {script} failed (exit {proc.returncode}):\n{tail}")
    elif bpy_available():
        # bpy wheel: run the script in a subprocess python so bpy state can't leak between jobs
        import sys

        cmd = [sys.executable, str(SCRIPTS / script), "--", str(job_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              env=untrusted_env(keep_prefixes=("PYTHON", "VIRTUAL_ENV")))
        if proc.returncode != 0:
            raise RuntimeError(f"bpy {script} failed:\n{proc.stderr[-4000:]}")
    else:
        raise RuntimeError("Blender not found: install Blender (or set RENDA_BLENDER) or `uv tool install 'renda[blender]'`")
    result_path = Path(job["result"])
    return json.loads(result_path.read_text()) if result_path.exists() else {}


def load(path: Path, fmt: str, cache_dir: Path | None = None) -> Stage:
    out = Path(cache_dir) if cache_dir else Path(tempfile.mkdtemp(prefix="renda-rir-")) / "scene.rir"
    if not (out / "stage.json").exists():
        run_blender_script("extract.py", {"input": str(path), "format": fmt, "output": str(out)})
    stage = read_rir(out)
    stage.source_format = fmt
    return stage
