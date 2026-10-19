"""Package + resolved asset paths → a ready-to-render Stage (and the inverse helpers)."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from . import hdri
from .ir import Light, Stage, load_stage, look_at
from .spec import RendaPackage, parse_color


def asset_path(pkg: RendaPackage, ref: str, asset_paths: dict[str, Path]) -> Path:
    if ref in asset_paths:
        return Path(asset_paths[ref])
    uri = pkg.asset_uri(ref)
    if uri in asset_paths:
        return Path(asset_paths[uri])
    raise KeyError(f"asset {ref!r} was not resolved (have: {sorted(asset_paths)})")


def root_format(pkg: RendaPackage, asset_paths: dict[str, Path]) -> str:
    from .formats import detect

    ref = pkg.scene.root
    if ref in pkg.assets and pkg.assets[ref].format:
        return pkg.assets[ref].format  # type: ignore[return-value]
    return detect(asset_path(pkg, ref, asset_paths))


def env_map_path(pkg: RendaPackage, asset_paths: dict[str, Path]) -> Path | None:
    ref = pkg.environment.hdri
    if not ref:
        return None
    if ref in hdri.BUILTINS and ref not in pkg.assets:
        return hdri.builtin_path(ref)
    return asset_path(pkg, ref, asset_paths)


def build_stage(pkg: RendaPackage, asset_paths: dict[str, Path], cache_dir: Path | None = None) -> tuple[Stage, list[str]]:
    """Load root + includes, apply unitScale/upAxis/materialOverrides. Returns (stage, warnings)."""
    warnings: list[str] = []
    root = asset_path(pkg, pkg.scene.root, asset_paths)
    fmt = pkg.assets[pkg.scene.root].format if pkg.scene.root in pkg.assets else None
    stage = load_stage(root, fmt if fmt in _SCENE else None, cache_dir, tessellation=pkg.scene.tessellation)
    warnings += stage.meta.pop("warnings", [])
    if pkg.scene.upAxis:
        stage.up_axis = pkg.scene.upAxis
    elif stage.up_axis == "-Y":
        warnings.append('splats: assumed COLMAP/OpenCV axes (Y down); if the render is upside down set scene.upAxis to "Y" (or "Z")')
    normalize_up(stage)
    for i, inc in enumerate(pkg.scene.include):
        ref = inc.asset
        ifmt = pkg.assets[ref].format if ref in pkg.assets else None
        sub = load_stage(asset_path(pkg, ref, asset_paths), ifmt if ifmt in _SCENE else None, cache_dir,
                         tessellation=pkg.scene.tessellation)
        warnings += sub.meta.pop("warnings", [])
        if inc.materialOverrides:
            apply_material_overrides(sub, inc.materialOverrides, warnings)
        # world = transform · C(include up → root up) · localTransform · content
        m = inc.localTransform.to_matrix() if inc.localTransform else np.eye(4)
        if inc.upAxis:
            sub.up_axis = inc.upAxis
            m = up_conversion(inc.upAxis, stage.up_axis) @ m
        else:
            normalize_up(sub)
        if inc.transform:
            m = inc.transform.to_matrix() @ m
        stage.merge(sub, m, prefix=f"inc{i}_")
    if pkg.scene.unitScale != 1:
        stage.apply_transform(np.diag([pkg.scene.unitScale] * 3 + [1.0]))
    apply_material_overrides(stage, pkg, warnings)
    return stage, warnings


_SCENE = {"blend", "usd", "gltf", "threejs", "fbx", "obj", "stl", "ply", "dae", "3mf", "abc", "splat", "step", "iges", "rir"}

# 180° about X: COLMAP/OpenCV (x right, y down, z forward) → three.js/glTF (x right, y up, z back)
FLIP_Y_DOWN = np.diag([1.0, -1.0, -1.0, 1.0])
# Z-up → Y-up: (x, y, z) → (x, z, -y), and its inverse
Z_TO_Y = np.array([[1.0, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1]])
Y_TO_Z = Z_TO_Y.T.copy()


def up_conversion(frm: str, to: str) -> np.ndarray:
    """Matrix taking coordinates in `frm` up-axis convention to `to` (same as spec/src/layers.ts)."""
    to_y = FLIP_Y_DOWN if frm == "-Y" else Z_TO_Y if frm == "Z" else np.eye(4)
    from_y = FLIP_Y_DOWN if to == "-Y" else Y_TO_Z if to == "Z" else np.eye(4)
    return from_y @ to_y


def normalize_up(stage: Stage) -> Stage:
    """Engines understand Y-up and Z-up; turn a Y-down ("-Y") stage into Y-up in place.
    Package coordinates (lookat cameras, lights, include transforms) are then Y-up too."""
    if stage.up_axis == "-Y":
        stage.apply_transform(FLIP_Y_DOWN)
        stage.up_axis = "Y"
    return stage


def apply_material_overrides(stage: Stage, overrides: RendaPackage | dict, warnings: list[str] | None = None) -> None:
    """Apply `{key: MaterialOverride}` (or a package's scene overrides) to a stage. Keys are exact
    material names, globs, /regex/flags or "*"; entries apply in order (later wins), after "*". An
    `object` pattern scopes an entry to materials used by matching objects (shared materials are
    split so other objects keep the original)."""
    from .engines.blender_scripts._match import compile_pattern
    from .textures import _ensure_materials, _scope_materials

    ovs = overrides.scene.materialOverrides if isinstance(overrides, RendaPackage) else overrides
    if not ovs:
        return
    _ensure_materials(stage)  # meshes with no material get the default so "*" reaches them
    ordered = sorted(ovs.items(), key=lambda kv: kv[0] != "*")
    for i, (key, ov) in enumerate(ordered):
        try:
            compile_pattern(key)
            if ov.object:
                compile_pattern(ov.object)
        except ValueError as e:
            raise ValueError(f"scene.materialOverrides: {e}") from None
        used = {m.material for m in stage.meshes}
        cands = {name for name in used if name in stage.materials and _key_matches(key, name)}
        edit = _scope_materials(stage, ov.object, cands, f"mo{i}")
        if not edit and warnings is not None and key != "*":
            warnings.append(f"materialOverrides[{key!r}] matched no materials (have: {', '.join(sorted(used)) or 'none'})")
        for name in edit.values():
            _apply_material_override(stage.materials[name], ov)


def _key_matches(key: str, name: str) -> bool:
    from .engines.blender_scripts._match import matches

    return key == name or matches(key, name)


def _apply_material_override(mat, ov) -> None:
    if ov.baseColor is not None:
        c = parse_color(ov.baseColor)
        mat.base_color = (c[0], c[1], c[2], c[3] if len(c) > 3 else mat.base_color[3])
        mat.base_color_tex = None
    if ov.metallic is not None:
        mat.metallic, mat.metallic_tex, mat.metallic_roughness_tex = ov.metallic, None, None
    if ov.roughness is not None:
        mat.roughness, mat.roughness_tex, mat.metallic_roughness_tex = ov.roughness, None, None
    if ov.emission is not None:
        mat.emission = tuple(parse_color(ov.emission)[:3])
        mat.emission_strength = mat.emission_strength or 1.0
    if ov.emissionStrength is not None:
        mat.emission_strength = ov.emissionStrength
    if ov.opacity is not None:
        mat.opacity = ov.opacity
    if ov.transmission is not None:
        mat.transmission = ov.transmission
    if ov.ior is not None:
        mat.ior = ov.ior


def package_lights(pkg: RendaPackage, stage: Stage) -> list[Light]:
    """The package's `lights` as IR lights (emitting down local -Z)."""
    up = stage.up_vector()
    out: list[Light] = []
    for i, p in enumerate(pkg.lights):
        color = tuple(parse_color(p.color, (1.0, 1.0, 1.0))[:3])
        if p.type == "sun":
            d = np.asarray(p.direction, dtype=np.float64)
            out.append(Light(f"pkg_sun_{i}", "sun", color, p.intensity, look_at([0, 0, 0], d, up)))
        elif p.type == "point":
            m = np.eye(4)
            m[:3, 3] = p.position
            out.append(Light(f"pkg_point_{i}", "point", color, p.intensity, m, radius=p.radius))
        elif p.type == "spot":
            out.append(Light(f"pkg_spot_{i}", "spot", color, p.intensity, look_at(p.position, p.target, up), angle_deg=p.angleDeg))
        elif p.type == "area":
            out.append(Light(f"pkg_area_{i}", "area", color, p.intensity, look_at(p.position, p.target, up), size=tuple(p.size)))
    return out


# Formats that carry a complete render setup (camera, world lighting).
SETUP_FORMATS = {"blend"}


def native_setup(pkg: RendaPackage, fmt: str) -> tuple[bool, bool]:
    """(use the file's camera, keep the file's world) when the package didn't set them explicitly.

    A .blend already has a camera and lighting the artist chose; the package defaults
    (auto framing + studio HDRI) only apply if the package author asked for them.
    Relies on pydantic's fields_set, so callers must parse the package as submitted
    (not a defaults-filled copy).
    """
    if fmt not in SETUP_FORMATS:
        return False, False
    explicit = pkg.model_fields_set
    return "camera" not in explicit, "environment" not in explicit
