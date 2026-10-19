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


# Big files are fetched as parallel byte ranges: a single TLS stream from Modal to R2 tops out well below the
# link speed, and the coordinator runs on a fraction of a core, so per-connection throughput is the bottleneck.
PART_SIZE = 8 << 20
MAX_PARTS = 8
BLOCK = 4 << 20


def _session():
    import requests

    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=MAX_PARTS, pool_maxsize=MAX_PARTS)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _probe_size(session, url: str) -> int | None:
    """Total size if the server honours byte ranges (S3/R2 presigned GETs do), else None."""
    try:
        with session.get(url, headers={"Range": "bytes=0-0"}, stream=True, timeout=(10, 30)) as r:
            if r.status_code == 206:
                total = r.headers.get("Content-Range", "").rpartition("/")[2]
                return int(total) if total.isdigit() else None
    except Exception:
        return None
    return None


def _get_range(session, url: str, dest: Path, start: int, end: int) -> None:
    with session.get(url, headers={"Range": f"bytes={start}-{end}"}, stream=True, timeout=(10, 300)) as r:
        r.raise_for_status()
        if r.status_code != 206:
            raise IOError(f"server ignored Range for bytes={start}-{end}")
        fd = os.open(dest, os.O_WRONLY)
        try:
            pos = start
            for block in r.iter_content(BLOCK):
                os.pwrite(fd, block, pos)
                pos += len(block)
        finally:
            os.close(fd)
        if pos != end + 1:
            raise IOError(f"short read for bytes={start}-{end}: got {pos - start}")


def _http_download(url: str, dest: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    session = _session()
    size = _probe_size(session, url)
    if size and size > PART_SIZE:
        with open(dest, "wb") as f:
            f.truncate(size)
        n = min(MAX_PARTS, -(-size // PART_SIZE))
        step = -(-size // n)
        spans = [(i, min(i + step, size) - 1) for i in range(0, size, step)]
        with ThreadPoolExecutor(max_workers=n) as pool:
            for fut in [pool.submit(_get_range, session, url, dest, a, b) for a, b in spans]:
                fut.result()
        return
    with session.get(url, stream=True, timeout=(10, 300)) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for block in r.iter_content(BLOCK):
                f.write(block)


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
        scratch: Path | None = None,
        defer_commit: bool = False,
        clock: Callable[[], float] | None = None,
    ):
        self.root = Path(root)
        self.inputs = {k: str(v) for k, v in inputs.items()}
        self.hints = dict(hints or {})
        self.download = download
        self.on_new = on_new
        # Downloads land on local disk first (the Volume is network-backed: many small writes are slow),
        # then move into the Volume in one copy.
        self.scratch = Path(scratch) if scratch else None
        self.defer_commit = defer_commit
        self._pending = False
        # Time spent filling the cache on misses: infrastructure cost, excluded from what the user is billed.
        import time as _time

        self._clock = clock or _time.monotonic
        self.fetch_seconds = 0.0
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
        t0 = self._clock()
        try:
            return self._fill(uri, url, key, d, name, bool(m))
        finally:
            self.fetch_seconds += self._clock() - t0

    def _fill(self, uri: str, url: str, key: str, d: Path, name: str, verify: bool) -> Path:
        d.mkdir(parents=True, exist_ok=True)
        if self.scratch is not None:
            self.scratch.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.scratch or d, prefix=".dl-")
        os.close(fd)
        tmp_path = Path(tmp)
        try:
            self.download(url, tmp_path)
            if verify and _sha256(tmp_path) != key:
                raise ValueError(f"hash mismatch for {uri}")
            target = d / name
            if self.scratch is None:
                os.replace(tmp_path, target)
            else:
                staged = d / f".in-{os.getpid()}-{name}"
                shutil.copyfile(tmp_path, staged)  # one sequential write into the Volume
                os.replace(staged, target)
        finally:
            tmp_path.unlink(missing_ok=True)
        if self.defer_commit:
            self._pending = True
        elif self.on_new:
            self.on_new()
        return target

    def commit(self) -> None:
        """Commit once for everything fetched since the last commit (defer_commit=True)."""
        if self._pending and self.on_new:
            self.on_new()
        self._pending = False
