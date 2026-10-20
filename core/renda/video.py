"""Frame sequence → video via ffmpeg (system binary, else imageio-ffmpeg's bundled one)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .spec import Output

CONTENT_TYPES = {
    "mp4": "video/mp4", "webm": "video/webm", "gif": "image/gif", "mov": "video/quicktime",
    "png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "exr": "image/x-exr",
}


def ffmpeg_binary() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def default_codec(fmt: str) -> str:
    return {"mp4": "h264", "webm": "vp9", "gif": "gif", "mov": "prores"}[fmt]


def ffmpeg_args(fmt: str, codec: str, crf: int, transparent: bool) -> list[str]:
    even = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    if fmt == "gif" or codec == "gif":
        return ["-vf", "split[a][b];[a]palettegen=reserve_transparent=1[p];[b][p]paletteuse=dither=sierra2_4a", "-loop", "0"]
    if codec == "h264":
        return ["-vf", even, "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if codec == "h265":
        return ["-vf", even, "-c:v", "libx265", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p", "-tag:v", "hvc1", "-movflags", "+faststart"]
    if codec == "vp9":
        # libvpx defaults to its slowest mode (cpu-used 0); good/4 is several times faster for a barely
        # visible difference on short clips
        return ["-c:v", "libvpx-vp9", "-crf", str(crf + 12), "-b:v", "0", "-row-mt", "1", "-deadline", "good", "-cpu-used", "4",
                "-pix_fmt", "yuva420p" if transparent else "yuv420p"]
    if codec == "prores":
        return ["-c:v", "prores_ks", "-profile:v", "4" if transparent else "3",
                "-pix_fmt", "yuva444p10le" if transparent else "yuv422p10le"]
    raise ValueError(f"unknown codec {codec}")


def encode(frame_files: list[Path], fps: float, output_cfg: Output, out_path: Path) -> Path:
    """Encode frames (in the given order) to output_cfg.format. Returns out_path."""
    if not frame_files:
        raise ValueError("no frames to encode")
    fmt = output_cfg.format
    codec = output_cfg.video.codec or default_codec(fmt)
    if fmt == "mp4" and codec in ("vp9", "prores", "gif"):
        raise ValueError(f"codec {codec} can't go in .mp4; use h264 or h265")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seq = Path(tempfile.mkdtemp(prefix="renda-seq-"))
    ext = Path(frame_files[0]).suffix
    try:
        # sequential links so stepped/missing frame numbers don't confuse ffmpeg's pattern input
        for i, f in enumerate(frame_files):
            dst = seq / f"{i:06d}{ext}"
            try:
                os.symlink(Path(f).resolve(), dst)
            except OSError:
                shutil.copyfile(f, dst)
        cmd = [ffmpeg_binary(), "-y", "-hide_banner", "-loglevel", "error", "-framerate", f"{fps:g}",
               "-i", str(seq / f"%06d{ext}"), *ffmpeg_args(fmt, codec, output_cfg.video.crf, output_cfg.transparent),
               str(out_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[-2000:]}")
    finally:
        shutil.rmtree(seq, ignore_errors=True)
    return out_path
