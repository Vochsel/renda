"""Measure container cold start for each worker image (time from .remote() to function entry).

Uses the worker's real images and snapshot settings in a separate throwaway app, so production is untouched:

    cd worker && uv run modal deploy scripts/coldstart_probe.py   # MODAL_IMAGE_BUILDER_VERSION=2025.06
    uv run python scripts/coldstart_probe.py --rounds 3           # cold, cold, warm per function
    uv run modal app stop renda-coldprobe -y
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import modal  # noqa: E402

from renda_worker import app as w  # noqa: E402

APP = "renda-coldprobe"
app = modal.App(APP)


def _entry(t_submit: float) -> dict:
    return {"entry": time.time(), "lat": round(time.time() - t_submit, 2)}


def _cuda_init() -> None:
    try:
        import torch

        torch.zeros(1, device="cuda")
    except Exception:
        pass


@app.function(image=w.blender_image, gpu="L4", enable_memory_snapshot=True, scaledown_window=2)
def blender_l4(t: float) -> dict:
    return _entry(t)


@app.function(image=w.blender_image, gpu="L4", enable_memory_snapshot=True, scaledown_window=2,
              experimental_options={"enable_gpu_snapshot": True})
def blender_l4_gpusnap(t: float) -> dict:
    return _entry(t)


@app.function(image=w.splat_image, gpu="L4", enable_memory_snapshot=True, scaledown_window=2)
def splat_l4(t: float) -> dict:
    r = _entry(t)
    t0 = time.time()
    _cuda_init()
    r["cuda_init_s"] = round(time.time() - t0, 2)
    return r


@app.cls(image=w.splat_image, gpu="L4", enable_memory_snapshot=True, scaledown_window=2,
         experimental_options={"enable_gpu_snapshot": True})
class SplatGpuSnap:
    @modal.enter(snap=True)
    def warm(self) -> None:
        _cuda_init()

    @modal.method()
    def run(self, t: float) -> dict:
        r = _entry(t)
        t0 = time.time()
        _cuda_init()
        r["cuda_init_s"] = round(time.time() - t0, 2)
        return r


@app.function(image=w.cpu_image, cpu=8, memory=16384, enable_memory_snapshot=True, scaledown_window=2)
def cpu(t: float) -> dict:
    return _entry(t)


@app.function(image=w.threejs_image, cpu=2, memory=4096, enable_memory_snapshot=True, scaledown_window=2)
def threejs(t: float) -> dict:
    return _entry(t)


@app.function(image=w.blender_image, gpu="A10G", scaledown_window=2, timeout=600)
def a10g_check() -> dict:
    """Fallback-GPU sanity check: Cycles sees a CUDA/OptiX device and Eevee renders on the GPU."""
    expr = (
        "import bpy,time\n"
        "p=bpy.context.preferences.addons['cycles'].preferences\n"
        "devs=[]\n"
        "for b in ('OPTIX','CUDA'):\n"
        "    try:\n"
        "        p.compute_device_type=b; p.get_devices(); devs+=[(b,d.name) for d in p.devices if d.type==b]\n"
        "    except Exception as e: devs.append((b,str(e)))\n"
        "print('DEVICES',devs)\n"
        "s=bpy.context.scene; s.render.engine='BLENDER_EEVEE_NEXT'; s.eevee.taa_render_samples=16\n"
        "s.render.resolution_x=s.render.resolution_y=512; s.render.filepath='/tmp/e.png'; t=time.time()\n"
        "bpy.ops.render.render(write_still=True); print('EEVEE_S',round(time.time()-t,2))\n"
        "s.render.engine='CYCLES'; s.cycles.device='GPU'; s.cycles.samples=32; t=time.time()\n"
        "bpy.ops.render.render(write_still=True); print('CYCLES_S',round(time.time()-t,2))\n"
    )
    r = subprocess.run(["blender", "-b", "--factory-startup", "--python-expr", expr], capture_output=True, text=True)
    out = [ln for ln in (r.stdout + r.stderr).splitlines() if any(k in ln for k in ("DEVICES", "EEVEE_S", "CYCLES_S", "Error"))]
    import renda.engines.gsplat  # noqa: F401  (import check only; gsplat itself lives in the splat image)
    return {"rc": r.returncode, "out": out[-6:]}


@app.function(image=w.splat_image, gpu="A10G", scaledown_window=2, timeout=600)
def a10g_splat_check() -> dict:
    import torch
    import gsplat  # noqa: F401

    return {"cuda": torch.cuda.is_available(), "name": torch.cuda.get_device_name(0), "cap": torch.cuda.get_device_capability(0)}


@app.function(image=w.blender_image, scaledown_window=2)
def sizes() -> dict:
    sh = lambda c: subprocess.run(c, shell=True, capture_output=True, text=True).stdout  # noqa: E731
    return {
        "blender": sh("du -sm /opt/blender; du -sm /opt/blender/* | sort -n | tail -6; du -sm /opt/blender/*/* | sort -n | tail -10"),
        "site": sh("du -sm /usr/local/lib/python3.11/site-packages; du -sm /usr/local/lib/python3.11/site-packages/* | sort -n | tail -15"),
        "system": sh("du -sm /usr/lib /usr/share /usr/local 2>/dev/null"),
    }


def _stop_containers() -> None:
    rows = json.loads(subprocess.run(["modal", "container", "list", "--json"], capture_output=True, text=True).stdout or "[]")
    for r in rows:
        if APP in json.dumps(r):
            cid = r.get("Container ID") or r.get("container_id")
            subprocess.run(["modal", "container", "stop", cid, "--yes"], capture_output=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--sizes", action="store_true")
    args = ap.parse_args()
    if args.sizes:
        f = modal.Function.from_name(APP, "sizes")
        print(json.dumps(f.remote(), indent=1))
        return
    names = ["blender_l4", "blender_l4_gpusnap", "splat_l4", "SplatGpuSnap", "cpu", "threejs"]
    names = [n for n in names if not args.only or n in args.only]
    results: dict[str, list] = {n: [] for n in names}
    # Each round: stop everything, then per function one cold call (round 0 also builds the snapshot)
    # followed immediately by a warm call into the same container.
    for rnd in range(args.rounds):
        _stop_containers()
        time.sleep(8)
        for n in names:
            fn = modal.Cls.from_name(APP, n)().run if n[0].isupper() else modal.Function.from_name(APP, n)
            for kind in ("cold-first" if rnd == 0 else "cold", "warm"):
                t = time.time()
                r = fn.remote(t)
                r["wall"] = round(time.time() - t, 2)
                r["kind"] = kind
                results[n].append(r)
                print(n, r, flush=True)
    print(json.dumps(results))


if __name__ == "__main__":
    main()
