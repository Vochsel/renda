"""Asset uri resolution: ./relative, /absolute, file://, https://, asset://<sha256>."""

from __future__ import annotations

import hashlib
import os
import urllib.parse
from collections.abc import Callable
from pathlib import Path

from . import hdri
from .spec import RendaPackage


class AssetError(ValueError):
    pass


def cache_root() -> Path:
    d = Path(os.environ.get("RENDA_CACHE_DIR", Path.home() / ".cache" / "renda")) / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_remote(uri: str) -> bool:
    return uri.startswith(("http://", "https://", "asset://"))


def download(uri: str) -> Path:
    """Default fetcher for https:// (cached by url hash). asset:// needs the cloud, so it's an error here."""
    if uri.startswith("asset://"):
        raise AssetError(f"{uri} is a cloud asset; pass a fetch() that maps asset:// to a download url")
    import httpx

    name = Path(urllib.parse.urlparse(uri).path).name or "asset"
    dest = cache_root() / hashlib.sha256(uri.encode()).hexdigest()[:24] / name
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", uri, follow_redirects=True, timeout=120) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    tmp.rename(dest)
    return dest


def referenced(pkg: RendaPackage) -> dict[str, str]:
    """Every asset the package needs: {id-or-uri: uri}. Built-in HDRIs are excluded."""
    refs: dict[str, str] = {aid: a.uri for aid, a in pkg.assets.items()}
    inline = [pkg.scene.root, *(i.asset for i in pkg.scene.include), *(t.source for t in pkg.scene.textureOverrides)]
    if pkg.environment.hdri and pkg.environment.hdri not in hdri.BUILTINS:
        inline.append(pkg.environment.hdri)
    for ref in inline:
        if ref not in pkg.assets:
            refs[ref] = ref
    return refs


def resolve(pkg: RendaPackage, base_dir: str | Path, fetch: Callable[[str], Path] | None = None) -> dict[str, Path]:
    """Map asset ids (and inline uris) → local paths. Remote uris go through `fetch` (default: download)."""
    base = Path(base_dir)
    out: dict[str, Path] = {}
    for key, uri in referenced(pkg).items():
        if is_remote(uri):
            p = Path((fetch or download)(uri))
        elif uri.startswith("file://"):
            p = Path(urllib.parse.unquote(urllib.parse.urlparse(uri).path))
        else:
            p = Path(uri)
            if not p.is_absolute():
                p = base / p
        if not p.exists():
            raise AssetError(f"asset {key!r}: {p} does not exist")
        out[key] = p
        if key != uri:
            out.setdefault(uri, p)
    return out


def local_files(pkg: RendaPackage, base_dir: str | Path) -> dict[str, Path]:
    """Assets that are local files (and so need uploading for a cloud render): {id-or-uri: path}."""
    base = Path(base_dir)
    out = {}
    for key, uri in referenced(pkg).items():
        if is_remote(uri):
            continue
        p = Path(urllib.parse.unquote(urllib.parse.urlparse(uri).path)) if uri.startswith("file://") else Path(uri)
        out[key] = p if p.is_absolute() else base / p
    return out
