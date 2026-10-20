"""Render the scene.ground comparison grid locally: engine × {none, catcher transparent/colour/HDRI, solid, reflective}.

    python scripts/ground_grid.py OUT_DIR SCENE ENGINE [case ...]
"""

import sys
from pathlib import Path

CASES = {
    "none": {},
    "catch_tr": {"ground": {"mode": "shadowCatcher"}, "background": "transparent"},
    "catch_bg": {"ground": {"mode": "shadowCatcher"}, "background": "#e8e4dc"},
    "catch_hdri": {"ground": {"mode": "shadowCatcher"}},
    "solid": {"ground": {"mode": "solid", "color": "#cfc8bd"}, "background": "#e8e4dc"},
    "reflective": {"ground": {"mode": "reflective", "color": "#202020", "reflectivity": 0.8}, "background": "#e8e4dc"},
}


def package(scene: str, engine: str, case: dict, res=(320, 240), quality="draft") -> dict:
    env = {"hdri": "studio"}
    if "background" in case:
        env["background"] = case["background"]
    scene_d = {"root": scene}
    if "ground" in case:
        scene_d["ground"] = case["ground"]
    return {"version": 1, "scene": scene_d, "environment": env,
            "render": {"engine": engine, "resolution": list(res), "quality": quality},
            "output": {"format": "png", "transparent": case.get("background") == "transparent"}}


if __name__ == "__main__":
    from renda.render import run_local
    from renda.spec import load_package

    out, scene, engine = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    for name in sys.argv[4:] or list(CASES):
        pkg = load_package(package(scene, engine, CASES[name]))
        run = run_local(pkg, Path(scene).parent, out / f"{engine}-{name}")
        print(name, [p.name for p in run.outputs], run.warnings[:3])
