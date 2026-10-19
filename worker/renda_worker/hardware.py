"""Hardware classes and their Modal resource shapes.

Render functions run preemptible and unpinned, so every one must be safe to retry after
preemption (chunks are idempotent). web/src/lib/pricing.ts sells render-minutes per class.
"""

from __future__ import annotations

from dataclasses import dataclass
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


# GPU containers get just enough CPU to feed the GPU and load scenes; CPU containers are
# sized for Cycles/Mitsuba-LLVM scaling (near-linear to ~8 physical cores, worse beyond).
# Short scaledown on GPUs: idle GPU time is the most expensive thing we can waste.
SHAPES: dict[Hardware, Shape] = {
    Hardware.CPU: Shape(gpu=None, cpu_cores=8.0, memory_mib=16384, timeout_s=60 * 60, max_containers=200, scaledown_s=60),
    Hardware.GPU_L4: Shape(gpu="L4", cpu_cores=4.0, memory_mib=16384, timeout_s=60 * 60, max_containers=50, scaledown_s=30),
    Hardware.GPU_L40S: Shape(gpu="L40S", cpu_cores=8.0, memory_mib=32768, timeout_s=2 * 60 * 60, max_containers=20, scaledown_s=20),
}

# Small shapes for the non-render steps.
ORCHESTRATE = Shape(gpu=None, cpu_cores=0.25, memory_mib=1024, timeout_s=6 * 60 * 60, max_containers=500, scaledown_s=120)
ENCODE = Shape(gpu=None, cpu_cores=4.0, memory_mib=8192, timeout_s=60 * 60, max_containers=100, scaledown_s=60)
HYPERFRAMES = Shape(gpu=None, cpu_cores=4.0, memory_mib=8192, timeout_s=2 * 60 * 60, max_containers=100, scaledown_s=60)
THREEJS = Shape(gpu=None, cpu_cores=2.0, memory_mib=4096, timeout_s=30 * 60, max_containers=100, scaledown_s=60)


def parse(value: str) -> Hardware:
    try:
        return Hardware(value)
    except ValueError:
        # Planner may say just "gpu"; default to the cheap card.
        if value == "gpu":
            return Hardware.GPU_L4
        raise

