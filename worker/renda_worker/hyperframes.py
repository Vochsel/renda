"""HyperFrames (HTML composition → video) command building.

Local `hyperframes render` takes its size from the composition; `--resolution` only supersamples
to named presets with the same aspect, so arbitrary width/height become a warning instead.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import HyperframesPackage

HYPERFRAMES_VERSION = "0.8.173"

RESOLUTION_PRESETS = {
    (1920, 1080): "landscape",
    (1080, 1920): "portrait",
    (3840, 2160): "landscape-4k",
    (2160, 3840): "portrait-4k",
    (1080, 1080): "square",
    (2160, 2160): "square-4k",
}


def build_render_command(pkg: HyperframesPackage, project_dir: Path, out_path: Path) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    cmd = [
        "hyperframes", "render", str(project_dir),
        "--output", str(out_path),
        "--format", pkg.format,
        "--quality", pkg.quality,
        "--no-browser-gpu",  # CPU containers: software GL is the deterministic path
        "--workers", "auto",
    ]
    if pkg.composition:
        cmd += ["--composition", pkg.composition]
    if pkg.fps:
        fps = pkg.fps
        if pkg.format == "gif" and fps > 30:
            warnings.append("gif output caps fps at 30")
            fps = 30
        cmd += ["--fps", str(fps)]
    if pkg.width or pkg.height:
        preset = RESOLUTION_PRESETS.get((pkg.width or 0, pkg.height or 0))
        if preset:
            cmd += ["--resolution", preset]
        else:
            warnings.append(
                f"width/height {pkg.width}x{pkg.height} ignored: HyperFrames renders at the composition's own size "
                f"(supersampling presets: {', '.join(f'{w}x{h}' for w, h in RESOLUTION_PRESETS)})"
            )
    if pkg.variables:
        cmd += ["--variables", json.dumps(pkg.variables, separators=(",", ":"))]
    return cmd, warnings
