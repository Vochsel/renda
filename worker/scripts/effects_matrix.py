"""Render the camera/lens/post effects matrix on Modal with the worker's images — an ephemeral app, never
the deployed one — and write a labelled comparison grid.

    MODAL_IMAGE_BUILDER_VERSION=2025.06 uv run modal run scripts/effects_matrix.py --out ../testdata/effects

Rows = engines, columns = cases (plain, depth of field, motion blur, post, AA+tonemap). Scenes come from
testdata/scenes (the studio table for mesh engines, the garden splat for gsplat).
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("MODAL_IMAGE_BUILDER_VERSION", "2025.06")  # bookworm images (see deploy.sh)

import modal  # noqa: E402

_here = Path(__file__).resolve()
ROOT = _here.parents[2] if len(_here.parents) > 2 else Path("/")  # in the container this file sits in /root
if modal.is_local():
    sys.path.insert(0, str(ROOT / "worker"))

from renda_worker.app import blender_image, splat_image, threejs_image  # noqa: E402

app = modal.App(os.environ.get("RENDA_EFFECTS_APP", "renda-worker-b-effects"))
SCENES = ROOT / "testdata" / "scenes"
RES = [480, 270]

CASES: dict[str, dict] = {
    "plain": {},
    # low, close shot along the table so there's real depth in front of and behind the focus object
    "dof": {"_by_scene": {
        "scene.glb": {"camera": {"mode": "lookat", "position": [1.9, 0.45, 1.5], "target": [0.0, 0.3, -0.2],
                                 "lens": {"focalLengthMm": 50, "fStop": 1.2, "apertureBlades": 6, "focusTarget": "Sphere"}}},
        "splats/garden.ply": {"camera": {"mode": "lookat", "position": [3.2, 0.7, 3.2], "target": [0.0, 0.35, 0.0],
                                         "lens": {"focalLengthMm": 35, "fStop": 1.0, "apertureBlades": 6,
                                                  "focusTarget": [0.0, 0.3, 0.0]}}},
    }},
    "motion blur": {"animation": {"frames": [1, 24], "fps": 24, "turntable": {"revolutions": 1}},
                    "render": {"motionBlur": {"shutter": 1.0, "subframes": 12}}, "_frame": 4},
    "post": {"post": {"vignette": 0.6, "chromaticAberration": 0.8, "lensDistortion": -0.35, "filmGrain": 0.35,
                      "bloom": {"intensity": 1.0, "threshold": 0.7}, "temperature": 0.35, "contrast": 0.15,
                      "sharpen": 0.3}},
    "aa+aces": {"render": {"antialias": {"filter": "box", "filterWidth": 1.0}},
                "post": {"tonemap": "aces", "exposure": 0.7}},
}


def _package(scene: str, engine: str, case: dict) -> tuple[dict, int]:
    pkg = {"version": 1, "scene": {"root": scene}, "environment": {"hdri": "studio", "background": "#e9e7e2"},
           "render": {"engine": engine, "resolution": RES, "quality": "standard"}, "output": {"format": "png"}}
    for k, v in {**case, **case.get("_by_scene", {}).get(scene, {})}.items():
        if k.startswith("_"):
            continue
        pkg[k] = {**pkg.get(k, {}), **v} if isinstance(v, dict) and isinstance(pkg.get(k), dict) else v
    return pkg, case.get("_frame", 1)


def _render_cases(engines: list[str], scene: str) -> list[dict]:
    """Inside a container: render every case for every engine; returns PNG bytes + warnings + seconds."""
    import tempfile
    import time
    import traceback

    from renda.assets import resolve
    from renda.planner import plan
    from renda.render import render_frames
    from renda.spec import load_package

    out = []
    for engine in engines:
        for name, case in CASES.items():
            data, frame = _package(scene, engine, case)
            t0 = time.time()
            try:
                pkg = load_package(data)
                paths = resolve(pkg, Path("/td/scenes"))
                p = plan(pkg, paths, "standard")
                tmp = Path(tempfile.mkdtemp())
                r = render_frames(p, pkg, paths, tmp, [frame], Path(tempfile.mkdtemp()))
                png = next(f for f in r.files if Path(f).name.startswith("frame_"))
                out.append({"engine": engine, "case": name, "png": Path(png).read_bytes(),
                            "warnings": list(dict.fromkeys(p.warnings + r.warnings)), "seconds": round(time.time() - t0, 2)})
            except Exception:
                out.append({"engine": engine, "case": name, "png": None, "error": traceback.format_exc()[-1500:],
                            "seconds": round(time.time() - t0, 2)})
    return out


def _with_scenes(image: modal.Image) -> modal.Image:
    return image.add_local_dir(SCENES, "/td/scenes", ignore=["*.blend1"])


@app.function(image=_with_scenes(blender_image), gpu="L4", timeout=3600)
def blender_mitsuba(engines: list[str]) -> list[dict]:
    os.environ["XDG_CACHE_HOME"] = "/tmp/xdg"
    return _render_cases(engines, "scene.glb")


@app.function(image=_with_scenes(threejs_image), gpu="L4", timeout=3600)
def threejs() -> list[dict]:
    return _render_cases(["threejs"], "scene.glb")


@app.function(image=_with_scenes(splat_image), gpu="L4", timeout=3600)
def gsplat() -> list[dict]:
    return _render_cases(["gsplat"], "splats/garden.ply")


def contact_sheet(results: list[dict], path: Path) -> None:
    from PIL import Image, ImageDraw

    engines = list(dict.fromkeys(r["engine"] for r in results))
    cases = list(CASES)
    w, h = RES
    pad, head, side = 6, 22, 120
    sheet = Image.new("RGB", (side + len(cases) * (w + pad), head + len(engines) * (h + pad)), (20, 20, 22))
    d = ImageDraw.Draw(sheet)
    for j, c in enumerate(cases):
        d.text((side + j * (w + pad) + 4, 5), c, fill=(230, 230, 230))
    for i, e in enumerate(engines):
        d.text((6, head + i * (h + pad) + h // 2 - 6), e, fill=(230, 230, 230))
        for j, c in enumerate(cases):
            r = next((r for r in results if r["engine"] == e and r["case"] == c), None)
            x, y = side + j * (w + pad), head + i * (h + pad)
            if r and r.get("png"):
                sheet.paste(Image.open(io.BytesIO(r["png"])).convert("RGB").resize((w, h)), (x, y))
                d.text((x + 4, y + h - 14), f"{r['seconds']}s", fill=(255, 255, 255))
            else:
                d.rectangle([x, y, x + w, y + h], outline=(200, 60, 60))
                d.text((x + 6, y + 6), "failed", fill=(240, 90, 90))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, quality=85)


@app.local_entrypoint()
def main(out: str = "../testdata/effects", only: str = ""):
    calls = []
    if not only or "blender" in only or "mitsuba" in only:
        engines = [e for e in ("blender-cycles", "blender-eevee", "blender-workbench", "mitsuba") if not only or e in only]
        calls.append(blender_mitsuba.spawn(engines))
    if not only or "threejs" in only:
        calls.append(threejs.spawn())
    if not only or "gsplat" in only:
        calls.append(gsplat.spawn())
    results = [r for c in calls for r in c.get()]
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for r in results:
        report.append({k: v for k, v in r.items() if k != "png"})
        if r.get("png"):
            (out_dir / f"{r['engine']}--{r['case'].replace(' ', '_').replace('+', '_')}.png").write_bytes(r["png"])
    (out_dir / "report.json").write_text(json.dumps(report, indent=1))
    contact_sheet(results, out_dir / "effects_matrix.webp")
    for r in report:
        print(r["engine"], "|", r["case"], "|", r["seconds"], "s |", "FAILED" if r.get("error") else "ok", "|",
              "; ".join(r.get("warnings", []))[:200])
        if r.get("error"):
            print(r["error"])
