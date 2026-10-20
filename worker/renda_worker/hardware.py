"""Hardware classes and their Modal resource shapes.

Render functions run preemptible and unpinned, so every one must be safe to retry after
preemption (chunks are idempotent). web/src/lib/pricing.ts sells render-minutes per class.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from enum import Enum


class Hardware(str, Enum):
    CPU = "cpu"
    GPU_L4 = "gpu-l4"
    GPU_L40S = "gpu-l40s"


@dataclass(frozen=True)
class Shape:
    """Resources requested for one render container."""

    gpu: str | None
    cpu_cores: float  # physical cores, as Modal's `cpu=` argument
    memory_mib: int
    timeout_s: int
    max_containers: int
    scaledown_s: int


    min_containers: int = 0
    buffer_containers: int = 0


def _tuned(name: str, shape: Shape) -> Shape:
    """Deploy-time autoscaler overrides, e.g. RENDA_SCALEDOWN_GPU_L4=60, RENDA_BUFFER_GPU_L4=1.

    Idle containers inside the scaledown window are billed to us, and at low traffic the next job
    rarely lands inside it, so defaults are short and nothing is kept warm. Raise them when traffic
    makes warm hits likely (see renda-ops for the break-even maths).
    """
    def num(kind: str, current: int) -> int:
        raw = os.environ.get(f"RENDA_{kind}_{name}")
        return int(raw) if raw not in (None, "") else current

    return replace(shape, scaledown_s=num("SCALEDOWN", shape.scaledown_s), min_containers=num("MIN", shape.min_containers),
                   buffer_containers=num("BUFFER", shape.buffer_containers))


# GPU containers get just enough CPU to feed the GPU and load scenes; CPU containers are
# sized for Cycles/Mitsuba-LLVM scaling (near-linear to ~8 physical cores, worse beyond).
# Cold starts measure ~6–12 s median (snapshot restore), so short scaledowns cost little latency.
SHAPES: dict[Hardware, Shape] = {
    Hardware.CPU: _tuned("CPU", Shape(gpu=None, cpu_cores=8.0, memory_mib=16384, timeout_s=60 * 60, max_containers=200, scaledown_s=20)),
    Hardware.GPU_L4: _tuned("GPU_L4", Shape(gpu="L4", cpu_cores=4.0, memory_mib=16384, timeout_s=60 * 60, max_containers=50, scaledown_s=15)),
    Hardware.GPU_L40S: _tuned("GPU_L40S", Shape(gpu="L40S", cpu_cores=8.0, memory_mib=32768, timeout_s=2 * 60 * 60, max_containers=20, scaledown_s=10)),
}

# Small shapes for the non-render steps.
ORCHESTRATE = _tuned("ORCHESTRATE", Shape(gpu=None, cpu_cores=0.25, memory_mib=1024, timeout_s=6 * 60 * 60, max_containers=500, scaledown_s=60))
ENCODE = _tuned("ENCODE", Shape(gpu=None, cpu_cores=4.0, memory_mib=8192, timeout_s=60 * 60, max_containers=100, scaledown_s=15))
HYPERFRAMES = _tuned("HYPERFRAMES", Shape(gpu=None, cpu_cores=4.0, memory_mib=8192, timeout_s=2 * 60 * 60, max_containers=100, scaledown_s=20))
THREEJS = _tuned("THREEJS", Shape(gpu=None, cpu_cores=2.0, memory_mib=4096, timeout_s=30 * 60, max_containers=100, scaledown_s=20))
FILAMENT = _tuned("FILAMENT", Shape(gpu=None, cpu_cores=4.0, memory_mib=8192, timeout_s=30 * 60, max_containers=100, scaledown_s=20))
PBRT = _tuned("PBRT", Shape(gpu=None, cpu_cores=8.0, memory_mib=16384, timeout_s=60 * 60, max_containers=100, scaledown_s=20))

# GPUs the Blender/Mitsuba L4 runner may fall back to when no L4 is free (cold starts waited 25–166 s for
# an L4 in testing). Cycles ships kernels for every arch and Dr.Jit JITs per device, so an A10G renders
# the same image. Not used for splat runners: gsplat is compiled for sm_89 (L4/L40S) only.
L4_FALLBACK_GPUS = [g for g in os.environ.get("RENDA_L4_FALLBACK_GPUS", "A10G").split(",") if g.strip()]


def gpu_request(shape: Shape, fallback: list[str] | None = None) -> str | list[str] | None:
    """Modal `gpu=` value: the shape's GPU, then any fallbacks in order."""
    if shape.gpu is None or not fallback:
        return shape.gpu
    return [shape.gpu, *[g.strip() for g in fallback if g.strip() and g.strip() != shape.gpu]]


def parse(value: str) -> Hardware:
    try:
        return Hardware(value)
    except ValueError:
        # Planner may say just "gpu"; default to the cheap card.
        if value == "gpu":
            return Hardware.GPU_L4
        raise

