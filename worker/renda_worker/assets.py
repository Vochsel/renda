"""Content-addressed asset cache on the `renda-assets` Volume.

Layout: <root>/<sha[:2]>/<sha>/<filename>. An asset is downloaded once per Volume and every
later job (any container) reuses it; `asset://` blobs are verified against their hash.
https:// assets are keyed by sha256(url) since we don't know their content hash up front.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlparse

ASSET_RE = re.compile(r"^asset://([0-9a-f]{64})$")

# Package `format` → extension, so engines that sniff by extension still work for asset:// blobs.
FORMAT_EXT = {
    "blend": ".blend", "usd": ".usdz", "gltf": ".glb", "threejs": ".json", "fbx": ".fbx",
    "obj": ".obj", "stl": ".stl", "ply": ".ply", "dae": ".dae", "3mf": ".3mf", "abc": ".abc",
    "step": ".step", "iges": ".iges", "rir": ".rir", "hdr": ".hdr", "exr": ".exr", "png": ".png", "jpg": ".jpg", "webp": ".webp",
    "mp4": ".mp4", "zip": ".zip",
}

Downloader = Callable[[str, Path], None]
"""(url, dest) -> None; writes the response body to dest."""


def _http_download(url: str, dest: Path) -> None:
    import requests

    with requests.get(url, stream=True, timeout=(10, 300)) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for block in r.iter_content(1 << 20):
                f.write(block)


def _safe_download(url: str, dest: Path) -> None:
    from renda_worker.netsafe import safe_download

    safe_download(url, dest)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _name_from_url(url: str) -> str | None:
    name = unquote(Path(urlparse(url).path).name)
    return name if name and "." in name and len(name) < 200 else None


def name_hints(package: Mapping[str, Any]) -> dict[str, str]:
    """uri -> filename from the package's `assets[*].format` (used when the URL has no extension)."""
    hints: dict[str, str] = {}
    for asset_id, asset in (package.get("assets") or {}).items():
        uri, fmt = asset.get("uri"), asset.get("format")
        if uri and fmt in FORMAT_EXT:
            hints[uri] = f"{asset_id}{FORMAT_EXT[fmt]}"
    return hints


class AssetCache:
    def __init__(
        self,
        root: Path,
        inputs: Mapping[str, str],
        hints: Mapping[str, str] | None = None,
        download: Downloader = _http_download,
        on_new: Callable[[], None] | None = None,
        download_untrusted: Downloader | None = None,
    ):
        self.root = Path(root)
        self.inputs = {k: str(v) for k, v in inputs.items()}
        self.hints = dict(hints or {})
        self.download = download
        # User-supplied https:// URLs (not our presigned R2 links) go through the SSRF-safe fetcher.
        self.download_untrusted = download_untrusted or _safe_download
        self.on_new = on_new
        self.hits = 0
        self.misses = 0

    def _dir(self, key: str) -> Path:
        return self.root / key[:2] / key

    def fetch(self, uri: str) -> Path:
        """Return a local path for `uri` (asset://<sha> or https://), downloading on a miss."""
        m = ASSET_RE.match(uri)
        if m:
            key, url = m.group(1), self.inputs.get(uri)
            if url is None:
                raise KeyError(f"no presigned url for {uri}; the server must list every asset:// in `inputs`")
        elif uri.startswith("https://"):
            key, url = hashlib.sha256(uri.encode()).hexdigest(), uri
        else:
            raise ValueError(f"unsupported asset uri {uri!r} (expected asset://<sha256> or https://)")

        d = self._dir(key)
        name = self.hints.get(uri) or _name_from_url(url) or (_name_from_url(uri) if not m else None) or "blob"
        existing = [p for p in d.iterdir() if not p.name.startswith(".")] if d.is_dir() else []
        if existing:
            self.hits += 1
            target = d / name
            if not target.exists():
                # Same bytes, different name hint: hardlink so extension sniffing still works.
                try:
                    os.link(existing[0], target)
                except OSError:
                    shutil.copyfile(existing[0], target)
            return target

        self.misses += 1
        d.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".dl-")
        os.close(fd)
        tmp_path = Path(tmp)
        try:
            (self.download if m else self.download_untrusted)(url, tmp_path)
            if m and _sha256(tmp_path) != key:
                raise ValueError(f"hash mismatch for {uri}")
            target = d / name
            os.replace(tmp_path, target)
        finally:
            tmp_path.unlink(missing_ok=True)
        if self.on_new:
            self.on_new()
        return target
