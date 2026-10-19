"""Renda render worker on Modal.

    modal serve worker/renda_worker/app.py     # dev: hot-reloading endpoints
    modal deploy worker/renda_worker/app.py    # prod

Flow: submit (HTTP) → orchestrate (0.25 core) → render_chunk_* .map() → encode_video → R2 + events.
All job logic lives in pipeline.py; this file is resource shapes and wiring only.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import modal
from fastapi import Header

from renda_worker import hardware as hw

# Override to deploy a throwaway copy for testing (e.g. RENDA_APP_NAME=renda-worker-d ./deploy.sh) without
# touching production. Volumes, dicts and secrets are shared (all content-addressed or keyed by job id).
APP_NAME = os.environ.get("RENDA_APP_NAME", "renda-worker")
# production URLs stay …--renda-submit / …--renda-cancel; test apps get …--renda-worker-d-submit etc.
_LABEL_PREFIX = "renda-" if APP_NAME == "renda-worker" else f"{APP_NAME}-"
WORKER_DIR = Path(__file__).resolve().parents[1]
CORE_DIR = WORKER_DIR.parent / "core"

BLENDER_VERSION = "4.5.14"  # 4.5 LTS
BLENDER_SERIES = ".".join(BLENDER_VERSION.split(".")[:2])
NODE_MAJOR = 22
HYPERFRAMES_VERSION = "0.8.173"
# Gaussian splats: gsplat's CUDA kernels are compiled into the image (no prebuilt cp311 wheels exist),
# for Ada GPUs (L4/L40S = sm_89).
TORCH_VERSION = "2.4.1"
GSPLAT_VERSION = "1.5.3"
CUDA_ARCH = "8.9"

ASSET_ROOT = "/assets"
CACHE_ROOT = "/cache"

_IGNORE = ["**/.venv/**", "**/__pycache__/**", "**/tests/**", "**/.pytest_cache/**", "**/*.egg-info/**"]
# pillow: gallery thumbnails (media.py) in every image, incl. hyperframes which has no core.
_WORKER_DEPS = ["requests>=2.32", "boto3>=1.35", "pydantic>=2.7", "fastapi[standard]>=0.115", "pillow>=10"]
_X11_GL_LIBS = [
    "libxi6", "libxxf86vm1", "libxfixes3", "libxrender1", "libgl1", "libegl1", "libglu1-mesa",
    "libxkbcommon0", "libsm6", "libice6", "libx11-6", "libxext6",
]


def _with_core(image: modal.Image, extras: str) -> modal.Image:
    # copy=True bakes core into a layer (needed so pip can build it); the layer rebuilds when core changes.
    return (
        image.add_local_dir(CORE_DIR, "/opt/renda-core", copy=True, ignore=_IGNORE)
        .run_commands(f"pip install --no-cache-dir '/opt/renda-core[{extras}]'")
    )


def _llvm(image: modal.Image, distro: str = "bookworm") -> modal.Image:
    # Mitsuba 3.9's LLVM backend emits intrinsics (e.g. fminimum) that bookworm's LLVM 14/15 can't
    # select ("LLVM ERROR: Cannot select"), aborting the container. Use LLVM 19 from apt.llvm.org.
    return image.apt_install("wget", "gnupg", "ca-certificates").run_commands(
        "wget -qO /etc/apt/trusted.gpg.d/apt.llvm.org.asc https://apt.llvm.org/llvm-snapshot.gpg.key",
        f"echo 'deb http://apt.llvm.org/{distro}/ llvm-toolchain-{distro}-19 main' > /etc/apt/sources.list.d/llvm.list",
        "apt-get update && apt-get install -y --no-install-recommends libllvm19 && rm -rf /var/lib/apt/lists/*",
        "ln -sf \"$(ls /usr/lib/x86_64-linux-gnu/libLLVM*19*.so* | head -1)\" /usr/local/lib/libLLVM.so",
    ).env({"DRJIT_LIBLLVM_PATH": "/usr/local/lib/libLLVM.so"})


def _node(image: modal.Image) -> modal.Image:
    return image.apt_install("curl", "ca-certificates", "gnupg").run_commands(
        f"curl -fsSL https://deb.nodesource.com/setup_{NODE_MAJOR}.x | bash -",
        "apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*",
    )


# Everything: Blender (Cycles/Eevee/Workbench, FBX/Alembic import, .blend extraction) + Mitsuba + USD.
# Cycles ships precompiled CUDA kernels; OptiX needs the driver's libnvoptix, which Modal mounts when
# NVIDIA_DRIVER_CAPABILITIES includes "graphics"/"all". Eevee headless uses EGL from the same driver.
def _blender(image: modal.Image) -> modal.Image:
    return (
        image.apt_install("wget", "xz-utils", "ffmpeg", *_X11_GL_LIBS)
        .run_commands(
            f"wget -q https://download.blender.org/release/Blender{BLENDER_SERIES}/blender-{BLENDER_VERSION}-linux-x64.tar.xz -O /tmp/blender.tar.xz",
            "mkdir -p /opt/blender && tar -xJf /tmp/blender.tar.xz -C /opt/blender --strip-components=1 && rm /tmp/blender.tar.xz",
            "ln -s /opt/blender/blender /usr/local/bin/blender",
        )
        .pip_install(*_WORKER_DEPS)
        .env({
            "RENDA_BLENDER": "/opt/blender/blender",
            "NVIDIA_DRIVER_CAPABILITIES": "all",
            "RENDA_ASSET_ROOT": ASSET_ROOT,
        })
    )


blender_image = _blender(_llvm(modal.Image.debian_slim(python_version="3.11")))
blender_image = _with_core(blender_image, "mitsuba,usd,cad").add_local_python_source("renda_worker")

# Gaussian splats on GPU: the Blender image's contents plus torch + gsplat (CUDA rasteriser). Only jobs
# with splats use it (engine=gsplat, or Blender/Mitsuba scenes with splats composited in), so the much
# bigger CUDA-devel base doesn't slow every cold start. gsplat is built from source at image build time
# (needs nvcc, not a GPU) so containers never JIT-compile.
splat_image = (
    _llvm(modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.11"), distro="jammy")
    .apt_install("git", "build-essential")
    .pip_install(f"torch=={TORCH_VERSION}", index_url="https://download.pytorch.org/whl/cu124")
    .pip_install("ninja", "numpy<2.3", "jaxtyping", "rich", "typing_extensions", "setuptools<75", "wheel")
    .run_commands(
        f"CC=gcc CXX=g++ TORCH_CUDA_ARCH_LIST='{CUDA_ARCH}' MAX_JOBS=8 pip install --no-cache-dir --no-build-isolation "
        f"--no-binary gsplat gsplat=={GSPLAT_VERSION}",
        # the compiled extension must be in the wheel (importing it needs a GPU driver, so just locate it)
        "python -c \"import importlib.util as u; assert u.find_spec('gsplat.csrc'), 'gsplat built without CUDA'; print('gsplat.csrc ok')\"",
    )
)
splat_image = _with_core(_blender(splat_image), "mitsuba,usd").add_local_python_source("renda_worker")

# Slim CPU image: Mitsuba (LLVM variant), trimesh, USD, ffmpeg. ~5x smaller than the Blender image →
# faster cold starts for Mitsuba stills of non-Blender formats and for video encoding.
cpu_image = (
    _llvm(modal.Image.debian_slim(python_version="3.11"))
    .apt_install("ffmpeg", "libgl1")
    .pip_install(*_WORKER_DEPS)
    .env({"RENDA_ASSET_ROOT": ASSET_ROOT})
)
cpu_image = _with_core(cpu_image, "mitsuba,usd,cad").add_local_python_source("renda_worker")

# three.js engine: headless Chromium via Playwright, software WebGL (SwiftShader).
threejs_image = _node(modal.Image.debian_slim(python_version="3.11")).apt_install("ffmpeg").pip_install(*_WORKER_DEPS)
threejs_image = _with_core(threejs_image, "threejs,cad").env({"RENDA_CACHE_DIR": "/opt/renda-cache"}).run_commands(
    "playwright install --with-deps chromium",
    # vendor three.js + Spark (splats) at build time instead of on every cold start
    "python -c 'from renda.engines.threejs import three_dir, spark_dir; three_dir(); spark_dir()'",
).env({"RENDA_ASSET_ROOT": ASSET_ROOT}).add_local_python_source("renda_worker")

# HyperFrames HTML compositions → video. `browser ensure` bakes the pinned Chrome into the image.
hyperframes_image = (
    _node(modal.Image.debian_slim(python_version="3.11"))
    .apt_install("ffmpeg", "unzip", "fonts-noto", "fonts-noto-color-emoji", "fonts-liberation")
    .run_commands(
        f"npm install -g hyperframes@{HYPERFRAMES_VERSION}",
        "npx -y playwright@1.56.1 install-deps chromium",  # system libs for headless Chrome
        "hyperframes browser ensure",
        "HYPERFRAMES_TELEMETRY_DISABLED=1 hyperframes doctor --json || true",
    )
    .pip_install(*_WORKER_DEPS)
    .env({"HYPERFRAMES_TELEMETRY_DISABLED": "1", "RENDA_ASSET_ROOT": ASSET_ROOT})
    .add_local_python_source("renda_worker")
)

# Tiny image for the HTTP endpoints (no core, no Blender) so submit/cancel cold-start in ~1s.
api_image = modal.Image.debian_slim(python_version="3.11").pip_install(*_WORKER_DEPS).add_local_python_source("renda_worker")

app = modal.App(APP_NAME)

assets_volume = modal.Volume.from_name("renda-assets", create_if_missing=True)
kernel_cache_volume = modal.Volume.from_name("renda-kernel-cache", create_if_missing=True)
cancel_flags = modal.Dict.from_name("renda-cancel", create_if_missing=True)
job_calls = modal.Dict.from_name("renda-calls", create_if_missing=True)

r2_secret = modal.Secret.from_name("renda-r2")
worker_secret = modal.Secret.from_name("renda-worker")
SECRETS = [r2_secret, worker_secret]

# Heavy imports at module scope in containers so memory snapshots capture them (core + numpy +
# trimesh + mitsuba init is several seconds per cold start otherwise). Best effort: a module
# missing from a given image must not crash the container.
def _preload(*modules: str) -> None:
    import importlib

    for m in modules:
        try:
            importlib.import_module(m)
        except Exception:
            pass


if not modal.is_local():
    # Not mitsuba: snapshots are taken before the GPU is attached, and Dr.Jit decides at import time that
    # CUDA is unavailable, which then silently forced the LLVM variant on L4s.
    _preload("numpy", "trimesh", "renda", "renda.spec", "renda.planner", "renda.render", "renda.video", "boto3", "requests")


# ---------------------------------------------------------------- wiring helpers

def _deps(job_id: str, callback_url: str, **kw):
    from renda_worker.core_api import RendaCore
    from renda_worker.events import EventSink
    from renda_worker.pipeline import Deps
    from renda_worker.storage import R2Storage

    events = EventSink(job_id, callback_url, os.environ["RENDA_WORKER_SECRET"])
    return Deps(
        core=RendaCore(),
        storage=R2Storage(),
        events=events,
        asset_root=Path(ASSET_ROOT),
        is_canceled=lambda: bool(cancel_flags.get(job_id, False)),
        **kw,
    )


def _reload_assets() -> None:
    # Warm containers only see assets committed after they started once they reload.
    try:
        assets_volume.reload()
    except Exception:
        pass


def _chunk(task, started: float):
    from renda_worker.pipeline import run_chunk

    _reload_assets()
    return run_chunk(task, _deps(task.job_id, task.callback_url, on_new_asset=assets_volume.commit), started=started)


def _retries() -> modal.Retries:
    # Chunks are idempotent (same frames → same keys), so preemption/transient failures just retry.
    return modal.Retries(max_retries=2, initial_delay=2.0, backoff_coefficient=2.0, max_delay=30.0)


def _shape_kwargs(shape: hw.Shape) -> dict:
    return dict(
        cpu=shape.cpu_cores,
        memory=shape.memory_mib,
        timeout=shape.timeout_s,
        max_containers=shape.max_containers,
        scaledown_window=shape.scaledown_s,
    )


_CPU = hw.SHAPES[hw.Hardware.CPU]
_L4 = hw.SHAPES[hw.Hardware.GPU_L4]
_L40S = hw.SHAPES[hw.Hardware.GPU_L40S]


# ---------------------------------------------------------------- render chunks

@app.function(image=cpu_image, secrets=SECRETS, volumes={ASSET_ROOT: assets_volume}, retries=_retries(),
              enable_memory_snapshot=True, **_shape_kwargs(_CPU))
def render_chunk_cpu(task):
    return _chunk(task, time.monotonic())


@app.function(image=blender_image, secrets=SECRETS, volumes={ASSET_ROOT: assets_volume}, retries=_retries(),
              enable_memory_snapshot=True, **_shape_kwargs(_CPU))
def render_chunk_blender_cpu(task):
    return _chunk(task, time.monotonic())


@app.function(image=blender_image, gpu=_L4.gpu, secrets=SECRETS,
              volumes={ASSET_ROOT: assets_volume, CACHE_ROOT: kernel_cache_volume}, retries=_retries(),
              enable_memory_snapshot=True, **_shape_kwargs(_L4))
def render_chunk_l4(task):
    _use_kernel_cache()
    try:
        return _chunk(task, time.monotonic())
    finally:
        kernel_cache_volume.commit()  # persist newly compiled shaders/kernels for the next container


@app.function(image=splat_image, gpu=_L4.gpu, secrets=SECRETS,
              volumes={ASSET_ROOT: assets_volume, CACHE_ROOT: kernel_cache_volume}, retries=_retries(),
              enable_memory_snapshot=True, **_shape_kwargs(_L4))
def render_chunk_splat_l4(task):
    _use_kernel_cache()
    try:
        return _chunk(task, time.monotonic())
    finally:
        kernel_cache_volume.commit()


@app.function(image=splat_image, gpu=_L40S.gpu, secrets=SECRETS,
              volumes={ASSET_ROOT: assets_volume, CACHE_ROOT: kernel_cache_volume}, retries=_retries(),
              enable_memory_snapshot=True, **_shape_kwargs(_L40S))
def render_chunk_splat_l40s(task):
    _use_kernel_cache()
    try:
        return _chunk(task, time.monotonic())
    finally:
        kernel_cache_volume.commit()


def _use_kernel_cache() -> None:
    # Cycles caches compiled OptiX kernels under $XDG_CACHE_HOME/cycles and NVIDIA's GL driver caches
    # Eevee's shaders under $XDG_CACHE_HOME/nvidia (measured on L4: first Eevee frame 17s cold → 2s with a
    # warm cache in a fresh container). Set at runtime, not in the image env: build steps would otherwise
    # write into CACHE_ROOT and Modal refuses to mount a Volume over a non-empty path.
    os.environ["XDG_CACHE_HOME"] = f"{CACHE_ROOT}/xdg"
    os.environ["__GL_SHADER_DISK_CACHE_SKIP_CLEANUP"] = "1"
    os.environ["__GL_SHADER_DISK_CACHE_SIZE"] = str(2 << 30)
    # gsplat is precompiled; if torch ever falls back to JIT-building an extension, keep it too.
    os.environ["TORCH_EXTENSIONS_DIR"] = f"{CACHE_ROOT}/torch_extensions"


@app.function(image=blender_image, gpu=_L40S.gpu, secrets=SECRETS,
              volumes={ASSET_ROOT: assets_volume, CACHE_ROOT: kernel_cache_volume}, retries=_retries(),
              enable_memory_snapshot=True, **_shape_kwargs(_L40S))
def render_chunk_l40s(task):
    _use_kernel_cache()
    try:
        return _chunk(task, time.monotonic())
    finally:
        kernel_cache_volume.commit()  # persist newly compiled shaders/kernels for the next container


@app.function(image=threejs_image, secrets=SECRETS, volumes={ASSET_ROOT: assets_volume}, retries=_retries(),
              **_shape_kwargs(hw.THREEJS))
def render_chunk_threejs(task):
    return _chunk(task, time.monotonic())


RUNNER_FUNCTIONS = {
    "cpu": render_chunk_cpu,
    "blender-cpu": render_chunk_blender_cpu,
    "l4": render_chunk_l4,
    "l40s": render_chunk_l40s,
    "threejs": render_chunk_threejs,
    "splat-l4": render_chunk_splat_l4,
    "splat-l40s": render_chunk_splat_l40s,
}


# ---------------------------------------------------------------- encode / orchestrate / hyperframes

@app.function(image=cpu_image, secrets=SECRETS, retries=_retries(), enable_memory_snapshot=True, **_shape_kwargs(hw.ENCODE))
def encode_video(task):
    from renda_worker.pipeline import run_encode

    started = time.monotonic()
    return run_encode(task, _deps(task.job_id, task.callback_url), started=started)


@app.function(image=blender_image, secrets=SECRETS, volumes={ASSET_ROOT: assets_volume},
              enable_memory_snapshot=True, **_shape_kwargs(hw.ORCHESTRATE))
def orchestrate(job_body: dict):
    """Cheap coordinator: fetch + plan on 0.25 core, fan out renders, finalize. No retries: it
    reports its own failure, and a blind retry would re-render everything."""
    from renda_worker.models import JobRequest
    from renda_worker.pipeline import run_scene_job

    job = JobRequest.model_validate(job_body)

    def run_chunks(runner: str, tasks):
        # Unordered: progress events arrive as soon as any chunk finishes.
        return RUNNER_FUNCTIONS[runner].map(tasks, order_outputs=False)

    deps = _deps(
        job.jobId,
        str(job.callbackUrl),
        run_chunks=run_chunks,
        run_encode=encode_video.remote,
        on_new_asset=assets_volume.commit,
    )
    try:
        return run_scene_job(job, deps)
    finally:
        job_calls.pop(job.jobId, None)
        cancel_flags.pop(job.jobId, None)


@app.function(image=hyperframes_image, secrets=SECRETS, volumes={ASSET_ROOT: assets_volume}, **_shape_kwargs(hw.HYPERFRAMES))
def render_hyperframes(job_body: dict):
    from renda_worker.models import JobRequest
    from renda_worker.pipeline import run_hyperframes_job

    started = time.monotonic()
    job = JobRequest.model_validate(job_body)
    try:
        return run_hyperframes_job(job, _deps(job.jobId, str(job.callbackUrl), on_new_asset=assets_volume.commit),
                                   timeout=hw.HYPERFRAMES.timeout_s - 120, started=started)
    finally:
        job_calls.pop(job.jobId, None)
        cancel_flags.pop(job.jobId, None)


# ---------------------------------------------------------------- HTTP endpoints

def _authorize(authorization: str | None) -> None:
    import hmac

    from fastapi import HTTPException

    expected = f"Bearer {os.environ['RENDA_WORKER_TOKEN']}"
    if not authorization or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="invalid worker token")


@app.function(image=api_image, secrets=[worker_secret], scaledown_window=300, cpu=0.25, memory=256)
@modal.fastapi_endpoint(method="POST", label=_LABEL_PREFIX + "submit")
def submit(body: dict, authorization: str | None = Header(default=None)):
    """POST {jobId, kind, package, priority, inputs, outputPrefix, callbackUrl} → {accepted, callId}."""
    from fastapi import HTTPException
    from pydantic import ValidationError

    from renda_worker.models import JobRequest

    _authorize(authorization)
    try:
        job = JobRequest.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False, include_context=False))
    fn = render_hyperframes if job.kind == "hyperframes" else orchestrate
    call = fn.spawn(job.model_dump(mode="json"))
    job_calls[job.jobId] = call.object_id
    return {"accepted": True, "callId": call.object_id}


@app.function(image=api_image, secrets=[worker_secret], scaledown_window=300, cpu=0.25, memory=256)
@modal.fastapi_endpoint(method="POST", label=_LABEL_PREFIX + "cancel")
def cancel(body: dict, authorization: str | None = Header(default=None)):
    """POST {jobId} → {canceled}. Sets the cooperative flag, then cancels the call tree.
    Chunk functions still report usage for time already spent (their `finally` runs on cancel)."""
    from fastapi import HTTPException

    _authorize(authorization)
    job_id = body.get("jobId")
    if not isinstance(job_id, str):
        raise HTTPException(status_code=422, detail="jobId required")
    cancel_flags[job_id] = True
    call_id = job_calls.get(job_id)
    if call_id:
        modal.FunctionCall.from_id(call_id).cancel()
    return {"canceled": bool(call_id)}
