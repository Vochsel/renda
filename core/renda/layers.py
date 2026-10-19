"""Package layering: `extends` and `scene.include[].package` references.

A port of spec/src/layers.ts (the canonical implementation); spec/fixtures/layers keeps the two
identical. See that file for the semantics. Loaders are injected so the resolver never touches the
filesystem or network itself; `local_loaders()` gives files + SSRF-safe https (see .netsafe).
"""

from __future__ import annotations

import copy
import json
import math
import posixpath
import re
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LIMITS = {"max_depth": 8, "max_layers": 64, "max_bytes": 1_000_000}

BUILTIN_HDRIS = {"studio", "sunset", "night"}
NULLABLE = {"environment.hdri", "render.samples"}
DISCRIMINATORS = ("mode", "type")
_SHA_URI = re.compile(r"^asset://([0-9a-f]{64})$")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_SAFE_KEY = re.compile(r"^[A-Za-z_$][\w$-]*$")
_NS = re.compile(r"^[A-Za-z0-9_-]+$")


class LayerError(ValueError):
    def __init__(self, code: str, message: str, layer: str, field: str | None = None):
        super().__init__(f"{layer}{f' ({field})' if field else ''}: {message}")
        self.code, self.layer, self.field, self.detail = code, layer, field, message


@dataclass
class Loaders:
    read_file: Callable[[str], str] | None = None
    fetch_https: Callable[[str], str] | None = None
    read_asset: Callable[[str], str] | None = None


@dataclass
class FlattenResult:
    package: dict[str, Any]
    provenance: dict[str, str] = field(default_factory=dict)
    layers: list[str] = field(default_factory=list)


def is_layered(doc: Any) -> bool:
    if not isinstance(doc, dict):
        return False
    if "extends" in doc:
        return True
    inc = doc.get("scene", {}).get("include") if isinstance(doc.get("scene"), dict) else None
    return isinstance(inc, list) and any(isinstance(i, dict) and "package" in i for i in inc)


def field_path(parent: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{parent}[{key}]"
    safe = bool(_SAFE_KEY.match(key))
    if not parent:
        return key if safe else f'["{key}"]'
    return f"{parent}.{key}" if safe else f'{parent}["{key}"]'


# -- refs + paths ----------------------------------------------------------------------------------


def _has_scheme(s: str) -> bool:
    return bool(_SCHEME.match(s))


def _is_abs(p: str) -> bool:
    return p.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\/]", p))


def _normalize(p: str) -> str:
    absolute = p.startswith("/")
    out: list[str] = []
    for part in p.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if out:
                out.pop()
        else:
            out.append(part)
    return ("/" if absolute else "") + "/".join(out)


def _dirname(p: str) -> str:
    p = p.replace("\\", "/")
    i = p.rfind("/")
    return "/" if i <= 0 else p[:i]


def _url_join(base: str, rel: str) -> str:
    # match WHATWG URL resolution for the cases we care about (relative paths, ../, absolute paths)
    return urllib.parse.urljoin(base, rel)


def _absolutize(uri: str, base: tuple[str, str]) -> str:
    kind, val = base
    if _has_scheme(uri):
        if uri.startswith("file://"):
            return urllib.parse.unquote(urllib.parse.urlparse(uri).path)
        return uri
    if _is_abs(uri):
        return uri
    if kind == "path":
        return _normalize(f"{val.replace(chr(92), '/')}/{uri}")
    if kind == "https":
        return _url_join(val, uri)
    return uri


def _parse_ref(raw: str, layer: str, fld: str) -> tuple[str, str, str | None]:
    """→ (kind, id, sha)."""
    m = _SHA_URI.match(raw)
    if m:
        return "asset", raw, m.group(1)
    if raw.startswith("asset://"):
        raise LayerError("layer_ref", f'asset:// refs must be asset://<64 hex sha256>, got "{raw}"', layer, fld)
    if raw.startswith("https://"):
        return "https", _canonical_url(raw), None
    if _has_scheme(raw):
        raise LayerError("layer_ref", f'unsupported scheme in "{raw}"; use https://, asset:// or a path', layer, fld)
    if not _is_abs(raw):
        raise LayerError("layer_ref", f'relative ref "{raw}" has no base to resolve against; use an https:// URL or asset:// URI', layer, fld)
    return "path", _normalize(raw.replace("\\", "/")), None


def _canonical_url(u: str) -> str:
    p = urllib.parse.urlsplit(u)
    path = p.path or "/"
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), path, p.query, p.fragment))


def _looks_like_path(v: str) -> bool:
    return _has_scheme(v) or "/" in v or "\\" in v or v.startswith(".") or bool(re.search(r"\.[A-Za-z0-9]{1,8}$", v))


# -- matrices (column-major lists, like glTF) -----------------------------------------------------

IDENTITY = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def mat_mul(a: list[float], b: list[float]) -> list[float]:
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            out[c * 4 + r] = sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4))
    return out


def transform_matrix(t: Any) -> list[float]:
    """T · Rz · Ry · Rx · S (same as core.ir.trs), column-major."""
    if not isinstance(t, dict):
        return list(IDENTITY)
    if isinstance(t.get("matrix"), list) and len(t["matrix"]) == 16:
        return [float(x) for x in t["matrix"]]
    tr = t.get("translate") or [0, 0, 0]
    rot = t.get("rotateDeg") or [0, 0, 0]
    sc = t.get("scale", 1)
    s = list(sc) if isinstance(sc, (list, tuple)) else [sc, sc, sc]
    rx, ry, rz = (math.radians(d) for d in rot)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    R = [
        [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
        [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
        [-sy, cy * sx, cy * cx],
    ]
    m = [0.0] * 16
    for r in range(3):
        for c in range(3):
            m[c * 4 + r] = R[r][c] * s[c]
    m[12], m[13], m[14], m[15] = float(tr[0]), float(tr[1]), float(tr[2]), 1.0
    return m


_FLIP = [1.0, 0, 0, 0, 0, -1.0, 0, 0, 0, 0, -1.0, 0, 0, 0, 0, 1.0]
_Z_TO_Y = [1.0, 0, 0, 0, 0, 0, -1.0, 0, 0, 1.0, 0, 0, 0, 0, 0, 1.0]
_Y_TO_Z = [1.0, 0, 0, 0, 0, 0, 1.0, 0, 0, -1.0, 0, 0, 0, 0, 0, 1.0]


def up_conversion(frm: str, to: str) -> list[float]:
    """'-Y' (COLMAP) flips to 'Y'; Z-up → Y-up is (x, y, z) → (x, z, -y)."""
    to_y = _FLIP if frm == "-Y" else _Z_TO_Y if frm == "Z" else IDENTITY
    from_y = _FLIP if to == "-Y" else _Y_TO_Z if to == "Z" else IDENTITY
    return mat_mul(from_y, to_y)


def _round(x: float) -> float | int:
    v = round(x * 1e10) / 1e10
    v = 0.0 if v == 0 else v
    return int(v) if float(v).is_integer() else v


def _is_identity(m: list[float]) -> bool:
    return all(abs(v - IDENTITY[i]) < 1e-12 for i, v in enumerate(m))


def _apply_point(m: list[float], p: list[float]) -> list:
    return [_round(m[r] * p[0] + m[4 + r] * p[1] + m[8 + r] * p[2] + m[12 + r]) for r in range(3)]


def _apply_direction(m: list[float], d: list[float]) -> list:
    v = [m[r] * d[0] + m[4 + r] * d[1] + m[8 + r] * d[2] for r in range(3)]
    n = math.hypot(*v) or 1.0
    return [_round(x / n) for x in v]


_FORMAT_UP = {"gltf": "Y", "threejs": "Y", "obj": "Y", "dae": "Y", "ply": "Y", "usd": "Y", "splat": "Y",
              "stl": "Z", "3mf": "Z", "blend": "Z", "fbx": "Z", "abc": "Z", "step": "Z", "iges": "Z"}
_EXT_FORMAT = {"glb": "gltf", "gltf": "gltf", "json": "threejs", "obj": "obj", "dae": "dae", "ply": "ply", "usd": "usd",
               "usda": "usd", "usdc": "usd", "usdz": "usd", "splat": "splat", "spz": "splat", "ksplat": "splat",
               "stl": "stl", "3mf": "3mf", "blend": "blend", "fbx": "fbx", "abc": "abc", "step": "step", "stp": "step",
               "iges": "iges", "igs": "iges"}


def _scene_up(scene: dict | None, assets: dict | None) -> str | None:
    if not isinstance(scene, dict):
        return None
    if isinstance(scene.get("upAxis"), str):
        return scene["upAxis"]
    ref = scene.get("root")
    if not isinstance(ref, str):
        return None
    asset = assets.get(ref) if isinstance(assets, dict) and isinstance(assets.get(ref), dict) else None
    if asset and isinstance(asset.get("format"), str) and asset["format"] in _FORMAT_UP:
        return _FORMAT_UP[asset["format"]]
    uri = asset["uri"] if asset and isinstance(asset.get("uri"), str) else ref
    m = re.search(r"\.([a-z0-9]+)$", re.sub(r"[?#].*$", "", uri.lower()))
    return _FORMAT_UP[_EXT_FORMAT[m.group(1)]] if m and m.group(1) in _EXT_FORMAT else None


# -- merge -----------------------------------------------------------------------------------------


def _drop_prov(prov: dict[str, str], path: str) -> None:
    for k in list(prov):
        if k == path or k.startswith(path + ".") or k.startswith(path + "["):
            del prov[k]


def _record_leaves(v: Any, path: str, label_of: Callable[[str], str], prov: dict[str, str]) -> None:
    if isinstance(v, dict) and v:
        for k, c in v.items():
            _record_leaves(c, field_path(path, k), label_of, prov)
    else:
        prov[path] = label_of(path)


def _merge_into(base: dict, over: dict, label_of: Callable[[str], str], prov: dict[str, str], path: str = "") -> None:
    for k, v in over.items():
        p = field_path(path, k)
        if v is None and p not in NULLABLE:
            base.pop(k, None)
            _drop_prov(prov, p)
            continue
        cur = base.get(k)
        clash = isinstance(cur, dict) and isinstance(v, dict) and any(d in cur and d in v and cur[d] != v[d] for d in DISCRIMINATORS)
        if isinstance(cur, dict) and isinstance(v, dict) and not clash:
            _merge_into(cur, v, label_of, prov, p)
        else:
            base[k] = copy.deepcopy(v)
            _drop_prov(prov, p)
            _record_leaves(v, p, label_of, prov)


def _deep_merge(*objs: dict | None) -> dict:
    out: dict = {}
    for o in objs:
        if isinstance(o, dict):
            _merge_into(out, o, lambda _p: "", {})
    return out


# -- resolver --------------------------------------------------------------------------------------


@dataclass
class _Ctx:
    loaders: Loaders
    max_depth: int
    max_layers: int
    max_bytes: int
    layers: list[str] = field(default_factory=list)
    cache: dict[str, FlattenResult] = field(default_factory=dict)


def _absolutize_layer(doc: dict, base: tuple[str, str]) -> dict:
    out = copy.deepcopy(doc)
    assets = out.get("assets") if isinstance(out.get("assets"), dict) else None
    own = set(assets or {})
    for a in (assets or {}).values():
        if isinstance(a, dict) and isinstance(a.get("uri"), str):
            a["uri"] = _absolutize(a["uri"], base)

    def fix(v: Any) -> Any:
        return _absolutize(v, base) if isinstance(v, str) and v not in own and _looks_like_path(v) else v

    scene = out.get("scene")
    if isinstance(scene, dict):
        if isinstance(scene.get("root"), str):
            scene["root"] = fix(scene["root"])
        for inc in scene.get("include") or []:
            if not isinstance(inc, dict):
                continue
            if isinstance(inc.get("asset"), str):
                inc["asset"] = fix(inc["asset"])
            if isinstance(inc.get("package"), str):
                inc["package"] = _absolutize(inc["package"], base)
    env = out.get("environment")
    if isinstance(env, dict) and isinstance(env.get("hdri"), str) and env["hdri"] not in BUILTIN_HDRIS:
        env["hdri"] = fix(env["hdri"])
    ext = out.get("extends")
    if isinstance(ext, str):
        out["extends"] = _absolutize(ext, base)
    elif isinstance(ext, list):
        out["extends"] = [_absolutize(e, base) if isinstance(e, str) else e for e in ext]
    return out


def _load_ref(kind: str, rid: str, sha: str | None, ctx: _Ctx, frm: str, fld: str) -> tuple[dict, tuple[str, str]]:
    if len(ctx.layers) >= ctx.max_layers:
        raise LayerError("layer_too_many", f"more than {ctx.max_layers} layers", frm, fld)
    ctx.layers.append(rid)
    L = ctx.loaders
    try:
        if kind == "path":
            if not L.read_file:
                raise LayerError("layer_not_allowed", f'local layer "{rid}" can\'t be read here; use an https:// URL or upload it and use its asset:// URI', frm, fld)
            text = L.read_file(rid)
        elif kind == "https":
            if not L.fetch_https:
                raise LayerError("layer_not_allowed", f'remote layers are disabled here ("{rid}")', frm, fld)
            text = L.fetch_https(rid)
        else:
            if not L.read_asset:
                raise LayerError("layer_not_allowed", f'asset:// layers can\'t be read here ("{rid}")', frm, fld)
            text = L.read_asset(sha or "")
    except LayerError:
        raise
    except Exception as e:  # noqa: BLE001 — surfaced with the layer + field
        raise LayerError("layer_fetch", f'couldn\'t load "{rid}": {e}', frm, fld) from e
    if len(text) > ctx.max_bytes:
        raise LayerError("layer_too_large", f'"{rid}" is larger than {ctx.max_bytes} bytes', frm, fld)
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise LayerError("layer_parse", f'"{rid}" is not valid JSON: {e}', frm, fld) from e
    if not isinstance(doc, dict):
        raise LayerError("layer_parse", f'"{rid}" must be a JSON object', frm, fld)
    base = ("path", _dirname(rid)) if kind == "path" else ("https", rid) if kind == "https" else ("asset", rid)
    return doc, base


def _flatten_ref(ref: tuple[str, str, str | None], ctx: _Ctx, stack: list[str], frm: str, fld: str) -> FlattenResult:
    kind, rid, sha = ref
    if rid in stack:
        raise LayerError("layer_cycle", "cycle: " + " → ".join([*stack, rid]), frm, fld)
    if len(stack) >= ctx.max_depth:
        raise LayerError("layer_depth", f"layers nest deeper than {ctx.max_depth}", frm, fld)
    hit = ctx.cache.get(rid)
    if hit is None:
        doc, base = _load_ref(kind, rid, sha, ctx, frm, fld)
        hit = _flatten_doc(doc, base, rid, ctx, [*stack, rid])
        ctx.cache[rid] = hit
    return FlattenResult(copy.deepcopy(hit.package), dict(hit.provenance), hit.layers)


def _flatten_doc(raw: dict, base: tuple[str, str], label: str, ctx: _Ctx, stack: list[str]) -> FlattenResult:
    own = _absolutize_layer(raw, base)
    acc: dict = {}
    prov: dict[str, str] = {}
    ext = own.pop("extends", None)
    refs = [] if ext is None else ext if isinstance(ext, list) else [ext]
    for i, r in enumerate(refs):
        fld = f"extends[{i}]" if isinstance(ext, list) else "extends"
        if not isinstance(r, str):
            raise LayerError("layer_invalid", "extends entries must be strings", label, fld)
        ref = _parse_ref(r, label, fld)
        sub = _flatten_ref(ref, ctx, stack, label, fld)
        _merge_into(acc, sub.package, lambda p, sub=sub, rid=ref[1]: sub.provenance.get(p, rid), prov)

    resolved, extra_lights, ref_paths, ref_lights = _resolve_references(own, acc, label, ctx, stack)
    _merge_into(acc, resolved, lambda _p: label, prov)
    if extra_lights:
        lights = acc.get("lights") if isinstance(acc.get("lights"), list) else []
        acc["lights"] = [*lights, *extra_lights]
        for i, who in enumerate(ref_lights):
            prov[f"lights[{len(lights) + i}]"] = who
    for k, v in ref_paths.items():
        _drop_prov(prov, k)
        prov[k] = v
    return FlattenResult(acc, prov, list(ctx.layers))


def _resolve_references(own: dict, under: dict, label: str, ctx: _Ctx, stack: list[str]):
    extra_lights: list = []
    ref_paths: dict[str, str] = {}
    ref_lights: list[str] = []
    scene = own.get("scene") if isinstance(own.get("scene"), dict) else None
    includes = scene.get("include") if scene and isinstance(scene.get("include"), list) else []
    if not any(isinstance(i, dict) and "package" in i for i in includes):
        return own, extra_lights, ref_paths, ref_lights

    out: list = []
    extra_assets: dict = {}
    own_assets = own.get("assets") if isinstance(own.get("assets"), dict) else {}
    for i, inc in enumerate(includes):
        if not (isinstance(inc, dict) and "package" in inc):
            out.append(inc)
            continue
        fld = f"scene.include[{i}].package"
        if not isinstance(inc["package"], str):
            raise LayerError("layer_invalid", "package must be a string", label, fld)
        ns = inc["as"] if isinstance(inc.get("as"), str) else f"ref{i}"
        if not _NS.match(ns):
            raise LayerError("layer_invalid", f'"as" must match [A-Za-z0-9_-]+, got "{ns}"', label, f"scene.include[{i}].as")
        ref = _parse_ref(inc["package"], label, fld)
        sub = _flatten_ref(ref, ctx, stack, label, fld).package
        sub_scene = sub.get("scene") if isinstance(sub.get("scene"), dict) else None
        if not sub_scene or not isinstance(sub_scene.get("root"), str):
            raise LayerError("layer_invalid", f'referenced package "{ref[1]}" has no scene.root', label, fld)
        sub_assets = sub.get("assets") if isinstance(sub.get("assets"), dict) else {}

        def rename(x: str, sub_assets=sub_assets, ns=ns) -> str:
            return f"{ns}.{x}" if x in sub_assets else x

        for aid, a in sub_assets.items():
            nid = f"{ns}.{aid}"
            if nid in extra_assets or nid in own_assets:
                raise LayerError("layer_invalid", f'asset id "{nid}" collides; set a different "as"', label, f"scene.include[{i}].as")
            extra_assets[nid] = copy.deepcopy(a)
            ref_paths[field_path("assets", nid)] = ref[1]

        unit = sub_scene.get("unitScale") if isinstance(sub_scene.get("unitScale"), (int, float)) else 1
        U = [unit, 0, 0, 0, 0, unit, 0, 0, 0, 0, unit, 0, 0, 0, 0, 1]
        placed = transform_matrix(inc.get("transform"))
        outer = placed if unit == 1 else mat_mul(placed, U)
        sub_up = _scene_up(sub_scene, sub_assets)
        root_scene = _deep_merge(under.get("scene") if isinstance(under.get("scene"), dict) else None, scene)
        root_assets = _deep_merge(under.get("assets") if isinstance(under.get("assets"), dict) else None, own_assets)
        raw_root_up = _scene_up(root_scene, root_assets)
        root_up = "Y" if raw_root_up == "-Y" else raw_root_up
        scoped = _deep_merge(sub_scene.get("materialOverrides") if isinstance(sub_scene.get("materialOverrides"), dict) else None)
        parent_ov = inc.get("materialOverrides") if isinstance(inc.get("materialOverrides"), dict) else None
        tr = inc.get("transform")
        keep_written = unit == 1 and tr is not None and not (isinstance(tr, dict) and tr.get("matrix"))

        def emit(asset: str, local: list[float], ov: dict, up: str | None, *, outer=outer, keep_written=keep_written, tr=tr, rid=ref[1]) -> None:
            e: dict = {"asset": asset}
            if keep_written:
                e["transform"] = copy.deepcopy(tr)
            elif not _is_identity(outer):
                e["transform"] = {"matrix": [_round(x) for x in outer]}
            if up:
                e["upAxis"] = up
            if not _is_identity(local):
                e["localTransform"] = {"matrix": [_round(x) for x in local]}
            if ov:
                e["materialOverrides"] = ov
            ref_paths[f"scene.include[{len(out)}]"] = rid
            out.append(e)

        emit(rename(sub_scene["root"]), IDENTITY, _deep_merge(scoped, parent_ov), sub_up)
        for si in sub_scene.get("include") or []:
            if not (isinstance(si, dict) and isinstance(si.get("asset"), str)):
                continue
            si_up = si.get("upAxis") if isinstance(si.get("upAxis"), str) else None
            local = transform_matrix(si.get("transform"))
            if si_up and sub_up and si_up != sub_up:
                local = mat_mul(local, up_conversion(si_up, sub_up))
            local = mat_mul(local, transform_matrix(si.get("localTransform")))
            ov = _deep_merge(scoped, si.get("materialOverrides") if isinstance(si.get("materialOverrides"), dict) else None, parent_ov)
            emit(rename(si["asset"]), local, ov, sub_up or si_up)

        light_m = mat_mul(outer, up_conversion(sub_up, root_up)) if sub_up and root_up else outer
        for light in sub.get("lights") or []:
            if not isinstance(light, dict):
                continue
            nl = copy.deepcopy(light)
            for k in ("position", "target"):
                if isinstance(nl.get(k), list):
                    nl[k] = _apply_point(light_m, nl[k])
            if isinstance(nl.get("direction"), list):
                nl["direction"] = _apply_direction(light_m, nl["direction"])
            extra_lights.append(nl)
            ref_lights.append(ref[1])

    doc = copy.deepcopy(own)
    doc["scene"]["include"] = out
    if extra_assets:
        doc["assets"] = {**extra_assets, **own_assets}
    return doc, extra_lights, ref_paths, ref_lights


def _base_from(base: str | Path | None) -> tuple[str, str]:
    if not base:
        return ("none", "")
    b = str(base)
    if b.startswith("https://"):
        return ("https", b)
    if b.startswith("asset://"):
        return ("asset", b)
    if _has_scheme(b):
        raise LayerError("layer_ref", f'unsupported base "{b}"', "<root>")
    return ("path", b)


def flatten(doc: Any, *, base: str | Path | None = None, label: str = "<root>", loaders: Loaders | None = None,
            max_depth: int = LIMITS["max_depth"], max_layers: int = LIMITS["max_layers"],
            max_bytes: int = LIMITS["max_bytes"]) -> FlattenResult:
    """Resolve `extends` and package references into one flat package (raw JSON, no defaults applied).
    Packages without either come back unchanged. Raises LayerError."""
    if not isinstance(doc, dict):
        raise LayerError("layer_parse", "a package must be a JSON object", label)
    if not is_layered(doc):
        return FlattenResult(copy.deepcopy(doc))
    ctx = _Ctx(loaders or Loaders(), max_depth, max_layers, max_bytes)
    r = _flatten_doc(doc, _base_from(base), label, ctx, [label])
    return FlattenResult(r.package, r.provenance, ctx.layers)


def local_loaders(**overrides: Any) -> Loaders:
    """Files + SSRF-safe https, for CLIs. asset:// layers need the API (pass read_asset)."""
    from .netsafe import safe_fetch_text

    loaders = Loaders(read_file=lambda p: Path(p).read_text(), fetch_https=safe_fetch_text)
    for k, v in overrides.items():
        setattr(loaders, k, v)
    return loaders


def flatten_file(path: str | Path, **kw: Any) -> FlattenResult:
    p = Path(path).resolve()
    doc = json.loads(p.read_text())
    label = posixpath.normpath(p.as_posix())
    return flatten(doc, base=str(p.parent), label=label, loaders=kw.pop("loaders", None) or local_loaders(), **kw)
