"""Result cache keys: identical package + asset content + engine versions → identical output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .spec import RendaPackage

CACHE_KEY_VERSION = 1
COSMETIC_FIELDS = ("$schema", "name")


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def cache_key(pkg: RendaPackage, asset_hashes: dict[str, str], engine_versions: dict[str, str]) -> str:
    """sha256 over the canonical package with asset uris replaced by content hashes.

    `asset_hashes` maps asset id or uri → sha256. Renaming/moving a file doesn't bust
    the cache; changing its bytes does. Cosmetic fields (name, $schema) are ignored.
    """
    data = pkg.dump()
    for k in COSMETIC_FIELDS:
        data.pop(k, None)
    for aid, asset in data.get("assets", {}).items():
        h = asset_hashes.get(aid) or asset_hashes.get(asset["uri"]) or asset.get("sha256")
        if h:
            asset["uri"] = f"sha256:{h}"
            asset.pop("sha256", None)

    def swap(ref: str | None) -> str | None:
        if ref is None or ref in data.get("assets", {}):
            return ref
        h = asset_hashes.get(ref)
        return f"sha256:{h}" if h else ref

    data["scene"]["root"] = swap(data["scene"]["root"])
    for inc in data["scene"].get("include", []):
        inc["asset"] = swap(inc["asset"])
    if data.get("environment", {}).get("hdri"):
        data["environment"]["hdri"] = swap(data["environment"]["hdri"])
    doc = {"v": CACHE_KEY_VERSION, "package": data, "engines": dict(sorted(engine_versions.items()))}
    return hashlib.sha256(canonical_json(doc).encode()).hexdigest()
