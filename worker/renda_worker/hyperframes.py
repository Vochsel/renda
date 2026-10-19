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


MEDIA_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".gif", ".png", ".jpg", ".jpeg", ".webp", ".avif", ".svg",
              ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".glb"}
INPUTS_DIR = "renda-inputs"

_MAGIC = [(b"\x1aE\xdf\xa3", ".webm"), (b"GIF8", ".gif"), (b"\x89PNG", ".png"), (b"\xff\xd8\xff", ".jpg"),
          (b"ID3", ".mp3"), (b"glTF", ".glb")]


def _sniff_ext(path: Path) -> str:
    head = path.read_bytes()[:64]
    if head[4:8] == b"ftyp":
        return ".mov" if head[8:10] == b"qt" else ".mp4"
    if head[:4] == b"RIFF":
        return {b"WEBP": ".webp", b"WAVE": ".wav"}.get(head[8:12], "")
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            return ext
    if b"<svg" in head or head.lstrip().startswith(b"<?xml"):
        return ".svg"
    return ""


def _url_ext(url: str) -> str:
    from urllib.parse import urlparse

    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in MEDIA_EXTS else ""


def localize_variables(variables: dict | None, project_dir: Path, fetch) -> tuple[dict | None, list[str]]:
    """Download media referenced by variables into the project and point the variables at the local copy.

    `asset://<sha>` values are always treated as media (uploads); `https://` values only when the URL path has a
    media extension, so ordinary link text stays text. Chrome/ffmpeg then read a local file instead of a long
    presigned URL, and frame extraction works offline inside the render.
    """
    from .models import ASSET_URI

    if not variables:
        return variables, []
    out, warnings = dict(variables), []
    for key, value in variables.items():
        if not isinstance(value, str):
            continue
        is_asset = bool(ASSET_URI.match(value))
        if not (is_asset or (value.startswith("https://") and _url_ext(value))):
            continue
        src = Path(fetch(value))
        ext = _url_ext(value) or (src.suffix.lower() if src.suffix.lower() in MEDIA_EXTS else "") or _sniff_ext(src)
        if not ext:
            warnings.append(f"variable {key!r}: couldn't tell what kind of media {value[:60]} is; passed as-is")
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)[:64] or "input"
        dest = project_dir / INPUTS_DIR / f"{safe}{ext}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())
        out[key] = f"{INPUTS_DIR}/{dest.name}"
    return out, warnings


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
