"""Engine registry. Adding an engine = one adapter module + one line here."""

from __future__ import annotations

from functools import lru_cache

from .base import Capabilities, Engine, RenderContext, RenderResult, frame_filename, samples_for


@lru_cache(maxsize=1)
def registry() -> dict[str, Engine]:
    from .blender import BlenderEngine
    from .filament import FilamentEngine
    from .gsplat import GsplatEngine
    from .mitsuba import MitsubaEngine
    from .pbrt import PbrtEngine
    from .threejs import ThreeEngine

    engines: list[Engine] = [
        BlenderEngine("cycles"),
        BlenderEngine("eevee"),
        BlenderEngine("workbench"),
        MitsubaEngine(),
        ThreeEngine(),
        GsplatEngine(),
        FilamentEngine(),
        PbrtEngine(),
    ]
    return {e.name: e for e in engines}


def get_engine(name: str) -> Engine:
    try:
        return registry()[name]
    except KeyError:
        raise ValueError(f"unknown engine {name!r}; known: {', '.join(registry())}") from None


def available_engines() -> list[str]:
    return [n for n, e in registry().items() if e.available()]


def engine_versions() -> dict[str, str]:
    return {n: (e.version() or "unknown") for n, e in registry().items() if e.available()}


__all__ = [
    "Capabilities", "Engine", "RenderContext", "RenderResult", "frame_filename", "samples_for",
    "registry", "get_engine", "available_engines", "engine_versions",
]
