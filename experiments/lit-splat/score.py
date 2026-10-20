"""EXPERIMENT: render splats from the package camera at full resolution and score them against a reference
render (PSNR, whole frame and car crop), saving the renders and a side-by-side crop strip.

    uv run --project core python experiments/lit-splat/score.py PACKAGE.renda.json REFERENCE.png OUT_DIR label=file.spz ...
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from compare import render  # noqa: E402

CROPS = {"car": (300, 180, 1400, 720), "plate": (480, 560, 900, 700), "wheel+door": (900, 380, 1350, 700)}


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    return float(10 * np.log10(1.0 / max(np.mean((a - b) ** 2), 1e-12)))


def main() -> None:
    package, ref_path, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    src = json.loads(package.read_text())
    res = tuple(src["render"]["resolution"])
    ref = Image.open(ref_path).convert("RGB").resize(res)
    refa = np.asarray(ref, np.float32) / 255
    work = Path(tempfile.mkdtemp(prefix="lit-score-"))
    imgs = [("Cycles reference", ref)]
    for label, path in (a.split("=", 1) for a in sys.argv[4:]):
        img = render(Path(path), src["camera"], src["environment"].get("background", "#ffffff"), res, work)
        img.save(out / f"{Path(path).stem}-{label.split()[0].lower()}.png")
        a = np.asarray(img, np.float32) / 255
        x0, y0, x1, y1 = CROPS["car"]
        print(f"{label:28} PSNR frame {psnr(a, refa):5.2f} dB   car {psnr(a[y0:y1, x0:x1], refa[y0:y1, x0:x1]):5.2f} dB", flush=True)
        imgs.append((label, img))
    font = ImageFont.load_default(size=20)
    for name, box in CROPS.items():
        w, h = box[2] - box[0], box[3] - box[1]
        strip = Image.new("RGB", (w, h * len(imgs)), "white")
        for i, (label, img) in enumerate(imgs):
            strip.paste(img.crop(box), (0, i * h))
            ImageDraw.Draw(strip).text((10, i * h + 8), label, fill=(200, 40, 20), font=font)
        strip.save(out / f"crop-{name.replace('+', '-')}.png")


if __name__ == "__main__":
    main()
