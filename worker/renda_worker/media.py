"""Output metadata for the dashboard gallery: dimensions, a small webp thumbnail and a tiny placeholder.

Best effort everywhere: a file Pillow can't read (EXR passes, odd formats) just gets no metadata, and a
failure here must never fail the render.
"""

from __future__ import annotations

import base64
import io
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .procenv import untrusted_env

log = logging.getLogger(__name__)

THUMB_MAX = 480
PLACEHOLDER_MAX = 16
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".gif"}


@dataclass
class Media:
    width: int
    height: int
    thumb: bytes  # webp, longest side <= THUMB_MAX
    placeholder: str  # data:image/webp;base64,… (a few hundred bytes)

    def fields(self, thumb_key: str) -> dict[str, Any]:
        return {"width": self.width, "height": self.height, "thumbKey": thumb_key, "placeholder": self.placeholder}


def _from_image(img) -> Media:
    from PIL import Image

    img.load()
    width, height = img.size
    if img.mode not in ("RGB", "RGBA"):
        # 16-bit depth/normal PNGs come in as I;16 / I: scale into 8-bit for a viewable thumbnail.
        if img.mode.startswith("I"):
            img = img.point(lambda v: v * (1 / 256)).convert("L")
        img = img.convert("RGBA" if "A" in img.getbands() or img.mode == "P" else "RGB")
    thumb = img.copy()
    thumb.thumbnail((THUMB_MAX, THUMB_MAX), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    thumb.save(buf, "WEBP", quality=80, method=4)
    tiny = img.copy()
    tiny.thumbnail((PLACEHOLDER_MAX, PLACEHOLDER_MAX), Image.Resampling.BILINEAR)
    tbuf = io.BytesIO()
    tiny.save(tbuf, "WEBP", quality=40)
    placeholder = "data:image/webp;base64," + base64.b64encode(tbuf.getvalue()).decode()
    return Media(width, height, buf.getvalue(), placeholder)


def image_media(path: Path) -> Media | None:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return _from_image(img)
    except Exception as exc:  # unreadable format (e.g. EXR) → no gallery metadata
        log.info("no media metadata for %s: %s", path.name, exc)
        return None


def video_media(path: Path, poster_frame: Path | None = None) -> Media | None:
    """Metadata for a video. Uses `poster_frame` (an already-rendered frame) when given, else extracts
    a frame ~40% in with ffmpeg (frame 0 of motion graphics is usually a blank pre-roll)."""
    if poster_frame is not None:
        m = image_media(poster_frame)
        if m is not None:
            return m
    try:
        out = path.with_suffix(".poster.png")
        seek = ["-ss", f"{dur * 0.4:.3f}"] if (dur := video_duration(path)) else []
        subprocess.run([_ffmpeg(), "-loglevel", "error", "-y", *seek, "-i", str(path), "-frames:v", "1", str(out)],
                       check=True, capture_output=True, timeout=120, env=untrusted_env())
        try:
            return image_media(out)
        finally:
            out.unlink(missing_ok=True)
    except Exception as exc:
        log.info("no poster for %s: %s", path.name, exc)
        return None


def video_duration(path: Path) -> float | None:
    """Duration in seconds from ffmpeg's stream banner, or None."""
    import re

    try:
        r = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=60,
                           env=untrusted_env())
    except Exception:
        return None
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None


def _ffmpeg() -> str:
    """Core's bundled ffmpeg where core is installed; the system one otherwise (hyperframes image)."""
    try:
        from renda.video import ffmpeg_binary

        return ffmpeg_binary()
    except ImportError:
        import shutil

        exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError("ffmpeg not found")
        return exe


def media_for(path: Path, poster_frame: Path | None = None) -> Media | None:
    return video_media(path, poster_frame) if path.suffix.lower() in VIDEO_SUFFIXES else image_media(path)


def thumb_key_for(output_key: str) -> str:
    """outputs/<job>/frame_0001.png → outputs/<job>/thumbs/frame_0001.webp"""
    head, _, name = output_key.rpartition("/")
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return f"{head}/thumbs/{stem}.webp" if head else f"thumbs/{stem}.webp"


def attach(obj: dict[str, Any], path: Path, storage, poster_frame: Path | None = None) -> dict[str, Any]:
    """Compute metadata for the local file behind output `obj`, upload its thumbnail, and add
    width/height/thumbKey/placeholder to `obj` (in place; also returned)."""
    media = media_for(path, poster_frame)
    if media is None:
        return obj
    key = thumb_key_for(obj["key"])
    tmp = path.with_name(path.name + ".thumb.webp")
    try:
        tmp.write_bytes(media.thumb)
        storage.put_file(tmp, key, "image/webp")
        obj.update(media.fields(key))
    except Exception as exc:
        log.warning("thumbnail upload failed for %s: %s", obj["key"], exc)
    finally:
        tmp.unlink(missing_ok=True)
    return obj
