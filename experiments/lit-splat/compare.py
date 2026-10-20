"""EXPERIMENT: render splat files from a mesh package's camera (and an orbit of it) with renda's gsplat
engine and tile them into one labelled comparison image.

    uv run --project core python experiments/lit-splat/compare.py PACKAGE.renda.json OUT.png label=file.spz ...
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def orbit(cam: dict, deg: float) -> dict:
    p, t = np.array(cam["position"], float), np.array(cam["target"], float)
    v = p - t
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    v = np.array([c * v[0] + s * v[2], v[1], -s * v[0] + c * v[2]])  # about +Y
    return {**cam, "position": (t + v).round(4).tolist()}


def render(spz: Path, cam: dict, bg: str, res: tuple[int, int], work: Path) -> Image.Image:
    pkg = {"version": 1, "name": spz.stem, "scene": {"root": str(spz.resolve())}, "camera": cam,
           "environment": {"background": bg}, "render": {"engine": "gsplat", "resolution": list(res), "quality": "final"},
           "output": {"format": "png"}}
    pj = work / f"{spz.stem}-{abs(hash(json.dumps(cam))) % 10**6}.renda.json"
    pj.write_text(json.dumps(pkg))
    out = work / pj.stem
    renda = Path(sys.executable).parent / "renda"
    subprocess.run([str(renda), "render", str(pj), "-o", str(out)], check=True, capture_output=True,
                   env={**os.environ, "RENDA_SPLAT_BACKEND": "numpy"})
    return Image.open(next(out.rglob("*.png"))).convert("RGB")


def main() -> None:
    package, out = Path(sys.argv[1]), Path(sys.argv[2])
    entries = [a.split("=", 1) for a in sys.argv[3:]]
    src = json.loads(package.read_text())
    bg = src["environment"].get("background", "#ffffff")
    res = (800, 500)
    cams = [src["camera"], orbit(src["camera"], 140), orbit(src["camera"], -100)]
    work = Path(tempfile.mkdtemp(prefix="lit-compare-"))
    font = ImageFont.load_default(size=22)
    rows = []
    for label, path in entries:
        tiles = []
        for cam in cams:
            img = render(Path(path), cam, bg, res, work)
            tiles.append(img)
            print(label, "rendered", flush=True)
        row = Image.new("RGB", (res[0] * len(cams), res[1]))
        for i, t in enumerate(tiles):
            row.paste(t.resize(res), (i * res[0], 0))
        ImageDraw.Draw(row).text((14, 10), label, fill=(20, 20, 20), font=font)
        rows.append(row)
    grid = Image.new("RGB", (rows[0].width, res[1] * len(rows)))
    for i, r in enumerate(rows):
        grid.paste(r, (0, i * res[1]))
    grid.save(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
