"""EXPERIMENT: time and score splat bakes on Modal L4s (production's training hardware), in its own app and volume.

    modal run experiments/lit-splat/modal_bench.py              # views (once), lit bakes, every training config
    modal run experiments/lit-splat/modal_bench.py --only lit400k-v32-s2000,baseline

- views: renda's own view path (_splat_bake_frames) renders the standard rig (128 Cycles views) once; scene load and
  per-view seconds are measured, so the view cost of any view count can be priced.
- every config is scored by the trainer's held-out PSNR/SSIM on the same 16 views (every 8th rig camera), which no
  config trains on.
- configs: production `standard` (mesh-surface init, all 112 training views), the lit bake as-is, and hybrids
  (lit bake as the starting point + a few views + a short training run).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import modal

HERE = Path(__file__).resolve().parent
CORE_DIR = HERE.parent.parent / "core"  # (inside the container HERE is /root: no parents[1])
# the showcase package; testdata/showcase-public/fetch.py downloads its assets
PKG_SRC = Path(os.environ.get("LIT_PKG_DIR", HERE.parent.parent / "testdata" / "showcase-public"))

# ---- copied from worker/renda_worker/app.py: identical layer definitions, so Modal reuses the cached layers
BLENDER_VERSION = "4.5.14"
BLENDER_SERIES = ".".join(BLENDER_VERSION.split(".")[:2])
TORCH_VERSION = "2.4.1"
GSPLAT_VERSION = "1.5.3"
CUDA_ARCH = "8.9"
ASSET_ROOT = "/assets"
_IGNORE = ["**/.venv/**", "**/__pycache__/**", "**/tests/**", "**/.pytest_cache/**", "**/*.egg-info/**"]
_WORKER_DEPS = ["requests>=2.32", "boto3>=1.35", "pydantic>=2.7", "fastapi[standard]>=0.115", "pillow>=10"]
_X11_GL_LIBS = [
    "libxi6", "libxxf86vm1", "libxfixes3", "libxrender1", "libgl1", "libegl1", "libglu1-mesa",
    "libxkbcommon0", "libsm6", "libice6", "libx11-6", "libxext6",
]


def _with_core(image: modal.Image, extras: str) -> modal.Image:
    return (
        image.add_local_dir(CORE_DIR, "/opt/renda-core", copy=True, ignore=_IGNORE)
        .run_commands(f"pip install --no-cache-dir '/opt/renda-core[{extras}]'")
    )


def _llvm(image: modal.Image, distro: str = "bookworm") -> modal.Image:
    return image.apt_install("wget", "gnupg", "ca-certificates").run_commands(
        "wget -qO /etc/apt/trusted.gpg.d/apt.llvm.org.asc https://apt.llvm.org/llvm-snapshot.gpg.key",
        f"echo 'deb http://apt.llvm.org/{distro}/ llvm-toolchain-{distro}-19 main' > /etc/apt/sources.list.d/llvm.list",
        "apt-get update && apt-get install -y --no-install-recommends libllvm19 && rm -rf /var/lib/apt/lists/*",
        "ln -sf \"$(ls /usr/lib/x86_64-linux-gnu/libLLVM*19*.so* | head -1)\" /usr/local/lib/libLLVM.so",
    ).env({"DRJIT_LIBLLVM_PATH": "/usr/local/lib/libLLVM.so"})


def _blender(image: modal.Image) -> modal.Image:
    return (
        image.apt_install("wget", "xz-utils", "ffmpeg", *_X11_GL_LIBS)
        .run_commands(
            f"wget -q https://download.blender.org/release/Blender{BLENDER_SERIES}/blender-{BLENDER_VERSION}-linux-x64.tar.xz -O /tmp/blender.tar.xz",
            "mkdir -p /opt/blender && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 && rm /tmp/blender.tar.xz",
            "ln -s /opt/blender/blender /usr/local/bin/blender",
        )
        .pip_install(*_WORKER_DEPS)
        .env({"RENDA_BLENDER": "/opt/blender/blender", "NVIDIA_DRIVER_CAPABILITIES": "all", "RENDA_ASSET_ROOT": ASSET_ROOT})
    )


splat_image = (
    _llvm(modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.11"), distro="jammy")
    .apt_install("git", "build-essential")
    .pip_install(f"torch=={TORCH_VERSION}", index_url="https://download.pytorch.org/whl/cu124")
    .pip_install("ninja", "numpy<2.3", "jaxtyping", "rich", "typing_extensions", "setuptools<75", "wheel")
    .run_commands(
        f"CC=gcc CXX=g++ TORCH_CUDA_ARCH_LIST='{CUDA_ARCH}' MAX_JOBS=8 pip install --no-cache-dir --no-build-isolation "
        f"--no-binary gsplat gsplat=={GSPLAT_VERSION}",
        "python -c \"import importlib.util as u; assert u.find_spec('gsplat.csrc'), 'gsplat built without CUDA'; print('gsplat.csrc ok')\"",
    )
)
splat_image = _with_core(_blender(splat_image), "mitsuba,usd,cad")
# ---- end of copy

image = (
    splat_image.pip_install("scipy")
    .env({"OPENBLAS_NUM_THREADS": "1"})
    .add_local_file(PKG_SRC / "assets" / "porsche.glb", "/data/porsche.glb")
    .add_local_file(PKG_SRC / "porsche-cycles.renda.json", "/data/porsche-cycles.renda.json")
    .add_local_dir(HERE, "/opt/lit", ignore=["__pycache__", "*.md"])
)
app = modal.App("renda-lit-splat-bench")
vol = modal.Volume.from_name("renda-lit-bench", create_if_missing=True)
L4 = dict(image=image, gpu="L4", cpu=4.0, memory=16384, timeout=3600, volumes={"/vol": vol})  # production L4 shape
SPLAT = {"preset": "standard", "views": 128, "shDegree": 3}  # the homepage hero bake


def _setup(quality: str | None = None):
    import sys

    sys.path.insert(0, "/opt/lit")
    from renda import assets as assets_mod
    from renda.engines import available_engines, engine_versions
    from renda.planner import plan as make_plan
    from renda.scene import build_stage
    from renda.spec import load_package
    from renda.splatbake import BakeSettings, adapt_to_scene, bake_rig

    d = json.loads(Path("/data/porsche-cycles.renda.json").read_text())
    d["scene"]["root"] = "/data/porsche.glb"
    d["output"] = {"format": "splat", "splatBake": SPLAT}
    if quality:  # views at another render quality (draft = 32 spp + denoise, renda's default for views)
        d["render"]["quality"] = quality
    pkg_path = Path("/tmp/pkg/porsche.renda.json")
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    pkg_path.write_text(json.dumps(d))
    pkg = load_package(pkg_path)
    asset_paths = assets_mod.resolve(pkg, pkg_path.parent)
    p = make_plan(pkg, asset_paths, "standard", available=set(available_engines()), engine_versions=engine_versions())
    settings = BakeSettings.from_json(p.splat_bake)
    stage, _ = build_stage(pkg, asset_paths)
    settings, _ = adapt_to_scene(settings, stage)
    return dict(pkg=pkg, pkg_path=pkg_path, asset_paths=asset_paths, plan=p, settings=settings, stage=stage,
                rig=bake_rig(stage, settings))


@app.function(**L4)
def render_views(view_set: str = "views", quality: str | None = None) -> dict:
    from renda.render import _splat_bake_frames

    c = _setup(quality)
    frames = sorted(c["rig"])
    vdir = Path(f"/vol/{view_set}")
    vdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    _splat_bake_frames(c["plan"], c["pkg"], c["asset_paths"], vdir, frames[:1], None)
    first = time.time() - t0
    t0 = time.time()
    r = _splat_bake_frames(c["plan"], c["pkg"], c["asset_paths"], vdir, frames[1:], None)
    rest = time.time() - t0
    per_view = rest / (len(frames) - 1)
    info = {"views": len(frames), "first_call_s": round(first, 1), "rest_s": round(rest, 1),
            "per_view_s": round(per_view, 2), "load_s": round(first - per_view, 1),
            "engine": c["settings"].engine, "resolution": c["settings"].resolution, "settings": c["settings"].to_json(),
            "warnings": r.warnings[-5:]}
    (vdir / "timing.json").write_text(json.dumps(info, indent=1))
    vol.commit()
    return info


@app.function(**L4)
def lit_bake(points: int, spp: int = 32) -> dict:
    out = Path(f"/vol/lit/{points}")
    t0 = time.time()
    _setup()  # writes the package
    r = subprocess.run(["python", "-u", "/opt/lit/lit_splat.py", "/tmp/pkg/porsche.renda.json", str(out), "--points",
                        str(points), "--spp", str(spp)], capture_output=True, text=True)
    wall = time.time() - t0
    vol.commit()
    if r.returncode:
        raise RuntimeError(r.stdout[-3000:] + r.stderr[-3000:])
    return {"points": points, "wall_s": round(wall, 1), "log": r.stdout}


@app.function(**L4)
def train_run(cfg: dict) -> dict:
    import sys
    from dataclasses import replace

    import numpy as np

    c = _setup()
    sys.path.insert(0, "/opt/lit")
    from train_patched import train

    from renda.ir.splats import Splats
    from renda.splatbake import HOLDOUT_EVERY, export_splats, init_gaussians, sample_surface
    from renda.splatbake.train import load_views

    vol.reload()
    settings, rig, stage = c["settings"], c["rig"], c["stage"]
    frames = sorted(rig)
    hold = [f for f in frames if f % HOLDOUT_EVERY == 0]
    pool = [f for f in frames if f % HOLDOUT_EVERY != 0]
    n = cfg.get("views", "all")
    train_frames = pool if n == "all" else [pool[i] for i in np.linspace(0, len(pool) - 1, n).round().astype(int)]
    use = sorted(set(train_frames) | set(hold))
    t0 = time.time()
    # training views from the chosen set; held-out views always the final-quality renders (the truth)
    vs = cfg.get("view_set", "views")
    images = {**load_views(Path(f"/vol/{vs}"), [f for f in use if f not in hold]), **load_views(Path("/vol/views"), hold)}
    t_decode = time.time() - t0
    t0 = time.time()
    if cfg["init"] == "mesh":
        init = init_gaussians(sample_surface(stage, settings.init_points, settings.seed), settings.sh_degree)
    else:
        z = np.load(f"/vol/lit/{cfg['init'].removeprefix('lit-')}/lit.npz")
        init = Splats("lit", z["means"], z["scales"], z["rotations"], z["opacities"], z["sh_dc"],
                      z["sh_rest"] if z["sh_rest"].size else None, antialiased=True)
    t_init = time.time() - t0
    lo, hi = stage.bounds()
    s = replace(settings, steps=cfg.get("steps", settings.steps), max_gaussians=cfg.get("max_gaussians", settings.max_gaussians))
    log: list[str] = []
    splats, m = train(init, images, {f: rig[f] for f in use}, s, float(np.linalg.norm(hi - lo)) / 2,
                      freeze=cfg.get("freeze"), full_sh=cfg.get("full_sh", False), eval_only=cfg.get("eval_only", False),
                      log=log.append)
    out = export_splats(splats, Path(f"/vol/out/{cfg['name']}.spz"), stage.up_axis)
    vol.commit()
    return {**cfg, **m, "train_views": len(train_frames), "decode_s": round(t_decode, 1), "init_s": round(t_init, 1),
            "file_mb": round(out.stat().st_size / 1e6, 1), "log": log}


@app.function(**L4)
def core_bake(init: str) -> dict:
    """renda's own pipeline end to end (run_local → planner → train_bake), single container: validates the
    shipped splatBake.init option, not this harness."""
    import shutil

    from renda.render import run_local
    from renda.spec import load_package

    d = json.loads(Path("/data/porsche-cycles.renda.json").read_text())
    d["scene"]["root"] = "/data/porsche.glb"
    d["output"] = {"format": "splat", "splatBake": {**SPLAT, "init": init, "fanOut": False}}
    pkg_path = Path("/tmp/pkg/porsche.renda.json")
    pkg_path.parent.mkdir(parents=True, exist_ok=True)
    pkg_path.write_text(json.dumps(d))
    out = Path(f"/tmp/out-{init}")
    t0 = time.time()
    run = run_local(load_package(pkg_path), pkg_path.parent, out)
    wall = time.time() - t0
    Path("/vol/core").mkdir(parents=True, exist_ok=True)
    for f in run.outputs:
        shutil.copy(f, f"/vol/core/{init}{Path(f).suffix}")
    vol.commit()
    return {"init": init, "wall_s": round(wall, 1), "est_s": run.plan.est_seconds, "warnings": run.warnings}


CONFIGS = [
    {"name": "baseline", "init": "mesh"},  # production standard: all 112 training views, preset steps
    {"name": "lit400k-eval", "init": "lit-400000", "eval_only": True},
    {"name": "lit1m-eval", "init": "lit-1000000", "eval_only": True},
    {"name": "lit400k-v16-s1500", "init": "lit-400000", "views": 16, "steps": 1500, "freeze": 300, "full_sh": True},
    {"name": "lit400k-v32-s2000", "init": "lit-400000", "views": 32, "steps": 2000, "freeze": 300, "full_sh": True},
    {"name": "lit400k-v48-s3000", "init": "lit-400000", "views": 48, "steps": 3000, "freeze": 300, "full_sh": True},
    {"name": "lit1m-v32-s2000", "init": "lit-1000000", "views": 32, "steps": 2000, "freeze": 300, "full_sh": True,
     "max_gaussians": 1_000_000},
    # round 2: does the lit start matter (control), and all views with fewer steps / full steps
    {"name": "mesh-v48-s3000", "init": "mesh", "views": 48, "steps": 3000},
    {"name": "lit400k-v64-s3000", "init": "lit-400000", "views": 64, "steps": 3000, "freeze": 300, "full_sh": True},
    {"name": "lit400k-v80-s4000", "init": "lit-400000", "views": 80, "steps": 4000, "freeze": 300, "full_sh": True},
    {"name": "lit400k-all-s3000", "init": "lit-400000", "steps": 3000, "freeze": 300, "full_sh": True},
    {"name": "lit400k-all-s6000", "init": "lit-400000", "steps": 6000, "freeze": 300, "full_sh": True},
    # round 3: train on draft views (32 spp + denoise), still scored against the final (512 spp) held-out views
    {"name": "draft-baseline", "init": "mesh", "view_set": "views-draft"},
    {"name": "draft-lit400k-all-s3000", "init": "lit-400000", "view_set": "views-draft", "steps": 3000, "freeze": 300, "full_sh": True},
    {"name": "draft-lit400k-v80-s4000", "init": "lit-400000", "view_set": "views-draft", "views": 80, "steps": 4000, "freeze": 300, "full_sh": True},
    {"name": "draft-lit400k-all-s4000", "init": "lit-400000", "view_set": "views-draft", "steps": 4000, "freeze": 300, "full_sh": True},
    {"name": "draft-lit400k-all-s6000", "init": "lit-400000", "view_set": "views-draft", "steps": 6000, "freeze": 300, "full_sh": True},
]


@app.local_entrypoint()
def core(inits: str = "surface,lit"):
    for r in core_bake.map(inits.split(",")):
        print(f"--- init {r['init']}: wall {r['wall_s']}s (planner estimate {r['est_s']})")
        for w in r["warnings"]:
            print("   ", w)


@app.local_entrypoint()
def main(only: str = "", skip_views: bool = False, skip_lit: bool = False, out: str = "", view_set: str = "views",
         quality: str = ""):
    t0 = time.time()
    views = None
    if not skip_views:
        views = render_views.remote(view_set, quality or None)
        print("views:", json.dumps({k: v for k, v in views.items() if k != "settings"}))
    configs = [c for c in CONFIGS if not only or c["name"] in only.split(",")]
    lit = {}
    if not skip_lit:
        need = sorted({int(c["init"].removeprefix("lit-")) for c in configs if c["init"] != "mesh"})
        for r in lit_bake.map(need):
            lit[r["points"]] = r
            print(f"lit {r['points']:,}: {r['wall_s']}s\n" + "\n".join("    " + l for l in r["log"].splitlines()))
    results = list(train_run.map(configs, return_exceptions=True))
    for r in results:
        if isinstance(r, Exception):
            print("FAILED:", r)
            continue
        print(f"{r['name']:22} PSNR {r['psnr'] or 0:6.2f}  SSIM {r['ssim'] or 0:.4f}  train {r['train_seconds']:6.1f}s  "
              f"steps {r['steps']:5}  views {r['train_views']:3}  {r['gaussians']:,} G  {r['file_mb']} MB")
    if out:
        Path(out).write_text(json.dumps({"views": views, "lit": lit, "runs": [r for r in results if not isinstance(r, Exception)]},
                                        indent=1, default=str))
    print(f"done in {time.time() - t0:.0f}s")
