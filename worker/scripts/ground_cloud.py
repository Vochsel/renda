"""Cloud check for scene.ground: render the ground grid on the worker's real images (this checkout's core).

    MODAL_IMAGE_BUILDER_VERSION=2025.06 uv run modal run scripts/ground_cloud.py --out ../.ground-cloud

An ephemeral app (nothing deployed, nothing left behind). Engines: Cycles, Eevee, Workbench, Mitsuba (CUDA) on
an L4; three.js on CPU; gsplat on an L4 (splat scene).
"""

import json
import sys
from pathlib import Path

import modal

if modal.is_local():  # in the container this file sits at /root and renda_worker is already importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core" / "scripts"))

from renda_worker.app import blender_image, splat_image, threejs_image  # noqa: E402

app = modal.App("renda-ground-probe")
TESTDATA = Path(__file__).resolve().parents[2] / "testdata" / "scenes" if modal.is_local() else Path("/")


def _render(jobs: list[dict], files: dict[str, bytes]) -> dict[str, bytes]:
    import tempfile

    from renda.render import run_local
    from renda.spec import load_package

    work = Path(tempfile.mkdtemp())
    for name, data in files.items():
        (work / name).write_bytes(data)
    out: dict[str, bytes] = {}
    for job in jobs:
        raw = json.loads(json.dumps(job["package"]))
        raw["scene"]["root"] = str(work / raw["scene"]["root"])
        try:
            run = run_local(load_package(raw), work, work / "out" / job["id"])
            for p in run.outputs:
                out[f"{job['id']}.png"] = Path(p).read_bytes()
            out[f"{job['id']}.warnings"] = json.dumps(run.warnings).encode()
        except Exception as e:  # report and keep going
            out[f"{job['id']}.error"] = repr(e).encode()
    return out


@app.function(image=blender_image, gpu="L4", timeout=3600)
def render_gpu(jobs: list[dict], files: dict[str, bytes]) -> dict[str, bytes]:
    return _render(jobs, files)


@app.function(image=threejs_image, cpu=4, memory=8192, timeout=3600)
def render_threejs(jobs: list[dict], files: dict[str, bytes]) -> dict[str, bytes]:
    return _render(jobs, files)


@app.function(image=splat_image, gpu="L4", timeout=3600)
def render_splat(jobs: list[dict], files: dict[str, bytes]) -> dict[str, bytes]:
    return _render(jobs, files)


@app.local_entrypoint()
def main(out: str = "../.ground-cloud", res: str = "480x360", only: str = ""):
    from ground_grid import CASES, package

    w, h = (int(x) for x in res.split("x"))
    outdir = Path(out)
    outdir.mkdir(parents=True, exist_ok=True)
    mesh_files = {"scene.glb": (TESTDATA / "scene.glb").read_bytes(), "scene.stl": (TESTDATA / "scene.stl").read_bytes()}
    splat_files = {"garden.spz": (TESTDATA / "splats" / "garden.spz").read_bytes()}

    def jobs_for(engine: str, scene: str, quality: str = "draft") -> list[dict]:
        tag = Path(scene).name.replace(".", "_")  # scene.glb and scene.stl must not share an id
        return [{"id": f"{engine}-{tag}-{c}", "package": package(scene, engine, CASES[c], (w, h), quality)} for c in CASES]

    gpu_jobs = [j for e in ("blender-cycles", "blender-eevee", "blender-workbench", "mitsuba") for j in jobs_for(e, "scene.glb")]
    gpu_jobs += [j for e in ("blender-cycles", "mitsuba") for j in jobs_for(e, "scene.stl")]
    calls = []
    if only in ("", "gpu"):
        calls.append(render_gpu.spawn(gpu_jobs, mesh_files))
    if only in ("", "threejs"):
        calls.append(render_threejs.spawn(jobs_for("threejs", "scene.glb") + jobs_for("threejs", "scene.stl"), mesh_files))
    if only in ("", "splat"):
        calls.append(render_splat.spawn(jobs_for("gsplat", "garden.spz"), splat_files))
    for c in calls:
        for name, data in c.get().items():
            (outdir / name).write_bytes(data)
            if name.endswith(".error"):
                print("ERROR", name, data.decode()[:400])
    print("wrote", outdir)
