"""The one place the worker touches the `renda` core package.

Keeps the worker tolerant of small signature drift in core and gives tests a single seam to fake.
Contract (core/): spec.load_package, assets.resolve, planner.plan, render.render_frames, video.encode.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence


class Core(Protocol):
    def load_package(self, data: Mapping[str, Any]) -> Any: ...
    def resolve(self, pkg: Any, base_dir: Path, fetch: Callable[[str], Path]) -> Mapping[str, Path]: ...
    def plan(self, pkg: Any, asset_paths: Mapping[str, Path], priority: str) -> Any: ...
    def render_frames(self, plan: Any, pkg: Any, asset_paths: Mapping[str, Path], out_dir: Path, frames: Sequence[int]) -> Any: ...
    def encode(self, frame_files: Sequence[Path], fps: float, output_cfg: Any, out_path: Path) -> Path: ...
    def train_splat(self, plan: Any, pkg: Any, asset_paths: Mapping[str, Path], views_dir: Path, out_dir: Path) -> Any: ...


class RendaCore:
    """Real implementation backed by the installed `renda` package (imported lazily)."""

    def load_package(self, data):
        from renda import spec

        return spec.load_package(dict(data))

    def resolve(self, pkg, base_dir, fetch):
        from renda import assets

        return assets.resolve(pkg, base_dir, fetch)

    def plan(self, pkg, asset_paths, priority):
        from renda import planner

        return planner.plan(pkg, asset_paths, priority)

    def render_frames(self, plan, pkg, asset_paths, out_dir, frames):
        from renda import render

        # Converted scenes (.blend → .rir, IR → glTF) are keyed by content hash; keep them on the
        # shared assets Volume so each source file is converted once, not once per chunk.
        derived = Path(os.environ.get("RENDA_DERIVED_DIR", "/assets/derived"))
        cache_dir = derived if derived.parent.exists() else None
        return render.render_frames(plan, pkg, asset_paths, out_dir, list(frames), cache_dir=cache_dir)

    def train_splat(self, plan, pkg, asset_paths, views_dir, out_dir):
        from renda.splatbake.train import train_bake

        derived = Path(os.environ.get("RENDA_DERIVED_DIR", "/assets/derived"))
        return train_bake(plan, pkg, asset_paths, views_dir, out_dir, cache_dir=derived if derived.parent.exists() else None)

    def encode(self, frame_files, fps, output_cfg, out_path):
        from renda import spec, video

        if isinstance(output_cfg, Mapping):  # serialised for the encode container
            output_cfg = spec.Output.model_validate(dict(output_cfg))
        return video.encode(list(frame_files), fps, output_cfg, out_path)


def get(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from a pydantic model, dataclass or dict."""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)
