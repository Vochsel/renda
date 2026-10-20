"""Camera / lens / post effect support per engine, and the warnings a package earns for effects an engine
emulates, approximates or can't do. One table drives docs, `renda engines --json`, planner costs and warnings.

native        the engine does it itself
emulated      renda reproduces it around the engine (subframe accumulation, depth-of-field from the depth
              buffer) — same controls, close but not identical to a physical render
approximated  nearest available equivalent (e.g. ACES → Filmic in Blender)
unsupported   ignored, with a warning
Post effects (grade, vignette, chromatic aberration, distortion, bloom, grain, sharpen) are a shared stage
(renda.post) applied identically to every engine's output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .spec import RendaPackage

NATIVE, EMULATED, APPROX, NO = "native", "emulated", "approximated", "unsupported"

_BLENDER_TONEMAP = {"agx": NATIVE, "filmic": NATIVE, "standard": NATIVE, "neutral": NATIVE, "aces": APPROX}

MATRIX: dict[str, dict[str, object]] = {
    "blender-cycles": {
        "dof": NATIVE, "blades": NATIVE, "anamorphic": NATIVE, "filter": NATIVE, "motionBlur": NATIVE,
        "exposure": NATIVE, "tonemap": _BLENDER_TONEMAP,
    },
    "blender-eevee": {
        "dof": NATIVE, "blades": NATIVE, "anamorphic": NATIVE, "filter": NATIVE, "motionBlur": NATIVE,
        "exposure": NATIVE, "tonemap": _BLENDER_TONEMAP,
    },
    "blender-workbench": {
        "dof": NATIVE, "blades": NATIVE, "anamorphic": NATIVE, "filter": NATIVE, "motionBlur": EMULATED,
        "exposure": NATIVE, "tonemap": _BLENDER_TONEMAP,
    },
    "mitsuba": {
        "dof": NATIVE, "blades": NO, "anamorphic": NO, "filter": NATIVE, "motionBlur": EMULATED,
        "exposure": NATIVE, "tonemap": {"agx": NATIVE, "aces": NATIVE, "neutral": NATIVE, "standard": NATIVE, "filmic": APPROX},
    },
    "threejs": {
        "dof": EMULATED, "blades": EMULATED, "anamorphic": EMULATED, "filter": APPROX, "motionBlur": EMULATED,
        "exposure": NATIVE, "tonemap": {"agx": NATIVE, "aces": NATIVE, "neutral": NATIVE, "standard": NATIVE, "filmic": APPROX},
    },
    "gsplat": {
        "dof": EMULATED, "blades": EMULATED, "anamorphic": EMULATED, "filter": NO, "motionBlur": EMULATED,
        "exposure": EMULATED, "tonemap": {k: NO for k in ("agx", "aces", "neutral", "standard", "filmic")},
    },
}

_NOTES = {
    ("blender-cycles", "tonemap", "aces"): "Blender has no ACES view transform; used Filmic",
    ("blender-eevee", "tonemap", "aces"): "Blender has no ACES view transform; used Filmic",
    ("blender-workbench", "tonemap", "aces"): "Blender has no ACES view transform; used Filmic",
    ("mitsuba", "tonemap", "filmic"): "Filmic is approximated with a Hable curve",
    ("threejs", "tonemap", "filmic"): "Filmic is approximated with three.js Cineon tonemapping",
    ("gsplat", "tonemap", "*"): "splat colours are already display-referred, so no tonemap is applied",
    ("gsplat", "exposure", "*"): "exposure applied to the display-referred splat colours",
    ("mitsuba", "blades", "*"): "Mitsuba's thin lens is round; aperture blades ignored",
    ("mitsuba", "anamorphic", "*"): "Mitsuba's thin lens is round; anamorphic ratio ignored",
    ("mitsuba", "motionBlur", "*"): "motion blur emulated with subframes (samples split across them, same cost)",
    ("blender-workbench", "motionBlur", "*"): "Workbench has no motion blur; emulated with subframe accumulation",
    ("threejs", "dof", "*"): "depth of field emulated from the depth buffer",
    ("gsplat", "dof", "*"): "depth of field emulated from the splat depth",
    ("threejs", "motionBlur", "*"): "motion blur emulated with subframe accumulation",
    ("gsplat", "motionBlur", "*"): "motion blur emulated with subframe accumulation",
    ("threejs", "filter", "*"): "antialias filter approximated by the supersample downscale filter",
    ("gsplat", "filter", "*"): "gsplat's anti-aliased rasteriser ignores antialias filter settings",
}


def status(engine: str, effect: str, variant: str | None = None) -> str:
    v = MATRIX.get(engine, {}).get(effect, NO)
    if isinstance(v, dict):
        return v.get(variant or "", NO)
    return v  # type: ignore[return-value]


def requested(pkg: "RendaPackage") -> list[tuple[str, str | None]]:
    """(effect, variant) pairs this package asks for."""
    out: list[tuple[str, str | None]] = []
    lens = getattr(pkg.camera, "lens", None)
    if lens is not None and lens.fStop is not None:
        out.append(("dof", None))
        if lens.apertureBlades >= 3:
            out.append(("blades", None))
        if abs(lens.anamorphicRatio - 1) > 1e-6:
            out.append(("anamorphic", None))
    if pkg.render.antialias is not None:
        out.append(("filter", None))
    if pkg.render.motionBlur is not None:
        out.append(("motionBlur", None))
    if pkg.post is not None:
        if abs(pkg.post.exposure) > 1e-9:
            out.append(("exposure", None))
        if pkg.post.tonemap != "agx" or "tonemap" in pkg.post.model_fields_set:
            out.append(("tonemap", pkg.post.tonemap))
    return out


def warnings_for(pkg: "RendaPackage", engine: str) -> list[str]:
    out = []
    for effect, variant in requested(pkg):
        st = status(engine, effect, variant)
        if st == NATIVE:
            continue
        note = _NOTES.get((engine, effect, variant or "*")) or _NOTES.get((engine, effect, "*"))
        if note is None:
            label = f"{effect}={variant}" if variant else effect
            note = f"{label} is {st} on this engine"
        out.append(f"{engine}: {note}")
    if pkg.render.antialias is not None and pkg.render.antialias.filter == "blackman-harris" and engine == "mitsuba":
        out.append("mitsuba: no Blackman-Harris filter; used Lanczos")
    if pkg.render.antialias is not None and pkg.render.antialias.filter == "mitchell" and engine.startswith("blender"):
        out.append(f"{engine}: no Mitchell filter in Blender; used Gaussian")
    return out


def needs_emulated(pkg: "RendaPackage", engine: str, effect: str) -> bool:
    return any(e == effect and status(engine, e, v) == EMULATED for e, v in requested(pkg))


def frame_cost_factor(pkg: "RendaPackage", engine: str) -> float:
    """Multiplier on per-frame render time for the requested effects."""
    f = 1.0
    mb = pkg.render.motionBlur
    if mb is not None:
        st = status(engine, "motionBlur")
        if engine == "mitsuba":
            f *= 1.1  # samples are split across subframes
        elif st == EMULATED:
            f *= mb.subframes
        elif engine == "blender-eevee":
            f *= 2.0  # motion blur steps
        else:
            f *= 1.15
    lens = getattr(pkg.camera, "lens", None)
    if lens is not None and lens.fStop is not None:
        f *= 1.2 if status(engine, "dof") == NATIVE else 1.05
    from .ground import cost_factor

    return f * cost_factor(pkg, engine)


def subframe_offsets(pkg: "RendaPackage") -> list[float]:
    """Frame-time offsets sampled for emulated motion blur (midpoints of n equal shutter slices)."""
    mb = pkg.render.motionBlur
    if mb is None:
        return [0.0]
    n, s = mb.subframes, mb.shutter
    mids = [(i + 0.5) / n for i in range(n)]
    if mb.position == "start":
        return [m * s for m in mids]
    if mb.position == "end":
        return [(m - 1) * s for m in mids]
    return [(m - 0.5) * s for m in mids]


def matrix_rows() -> list[dict]:
    """Flat rows for docs / `renda engines --effects`."""
    rows = []
    for engine, eff in MATRIX.items():
        row = {"engine": engine}
        for k, v in eff.items():
            row[k] = v if not isinstance(v, dict) else {t: s for t, s in v.items()}
        row["post"] = "shared"
        rows.append(row)
    return rows
