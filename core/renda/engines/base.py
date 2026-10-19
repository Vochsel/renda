"""Engine adapter interface + shared types."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..planner import RenderPlan
    from ..spec import RendaPackage


@dataclass(frozen=True)
class Capabilities:
    gpu: bool = False
    cpu: bool = True
    passes: frozenset[str] = frozenset({"beauty", "alpha"})
    transparent: bool = True
    animation: bool = True  # scene animation (vs camera-only turntables, which every engine does)
    scene_lights: bool = True
    textures: bool = True
    max_resolution: int = 16384
    # relative cost per megapixel-sample, used by the planner (cycles GPU == 1.0)
    cost_cpu: float = 1.0
    cost_gpu: float = 1.0
    load_seconds: float = 3.0  # typical engine start + scene load


@dataclass
class RenderResult:
    files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0


@dataclass
class RenderContext:
    plan: "RenderPlan"
    pkg: "RendaPackage"
    asset_paths: dict[str, Path]
    out_dir: Path
    cache_dir: Path | None = None  # for .rir extraction etc.


# Quality presets: samples per engine. `render.samples` overrides.
PRESETS: dict[str, dict[str, int]] = {
    "blender-cycles": {"draft": 32, "standard": 128, "final": 512},
    "blender-eevee": {"draft": 16, "standard": 64, "final": 128},
    "blender-workbench": {"draft": 8, "standard": 16, "final": 32},
    "mitsuba": {"draft": 16, "standard": 64, "final": 256},
    "threejs": {"draft": 1, "standard": 4, "final": 8},  # MSAA-ish supersampling factor (capped)
    "gsplat": {"draft": 1, "standard": 1, "final": 1},  # rasterised once; quality doesn't change the work
}

# Cycles adaptive sampling noise threshold per preset
ADAPTIVE_THRESHOLD = {"draft": 0.1, "standard": 0.03, "final": 0.01}


def samples_for(engine: str, quality: str, override: int | None) -> int:
    if override:
        return int(override)
    return PRESETS.get(engine, PRESETS["blender-cycles"])[quality]


def frame_filename(pass_name: str, frame: int, ext: str) -> str:
    """beauty → frame_0001.png; others → depth_0001.exr."""
    stem = "frame" if pass_name == "beauty" else pass_name
    return f"{stem}_{frame:04d}.{ext}"


class Engine(abc.ABC):
    name: str = ""
    accepts: frozenset[str] = frozenset()  # formats loaded natively; anything else goes through the IR
    capabilities: Capabilities = Capabilities()

    @abc.abstractmethod
    def available(self) -> bool: ...

    def version(self) -> str | None:
        return None

    def unavailable_reason(self) -> str | None:
        return None

    @abc.abstractmethod
    def render(self, ctx: RenderContext, frames: list[int]) -> RenderResult: ...

    def supports_format(self, fmt: str) -> bool:
        """Can render this scene format, natively or via IR conversion."""
        from ..ir.loaders import loader_available

        return fmt in self.accepts or loader_available(fmt)

    def describe(self) -> dict:
        ok = self.available()
        return {
            "name": self.name,
            "available": ok,
            "version": self.version() if ok else None,
            "reason": None if ok else self.unavailable_reason(),
            "accepts": sorted(self.accepts),
            "gpu": self.capabilities.gpu,
            "passes": sorted(self.capabilities.passes),
            "effects": _effects_for(self.name),
        }


def _effects_for(name: str) -> dict:
    from ..effects import MATRIX

    return {**MATRIX.get(name, {}), "post": "shared"}
