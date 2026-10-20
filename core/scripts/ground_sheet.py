"""Contact sheet for ground_grid.py output: rows = engines, columns = cases; transparency on a checkerboard.

    python scripts/ground_sheet.py GRID_DIR OUT.png
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

CASES = ["none", "catch_tr", "catch_bg", "catch_hdri", "solid", "reflective"]
W, H = 320, 240


def checker(w: int, h: int, s: int = 12) -> Image.Image:
    img = Image.new("RGB", (w, h), (200, 200, 200))
    d = ImageDraw.Draw(img)
    for y in range(0, h, s):
        for x in range(0, w, s):
            if (x // s + y // s) % 2:
                d.rectangle([x, y, x + s - 1, y + s - 1], fill=(235, 235, 235))
    return img


if __name__ == "__main__":
    grid, out = Path(sys.argv[1]), Path(sys.argv[2])
    flat = not any(p.is_dir() for p in grid.iterdir())  # ground_cloud.py writes <engine>-<scene>-<case>.png
    if flat:
        for p in grid.glob("*.png"):
            stem, case = next(((p.stem[: -len(c) - 1], c) for c in CASES if p.stem.endswith("-" + c)), (None, None))
            if stem:
                (grid / f"{stem}-{case}").mkdir(exist_ok=True)
                p.rename(grid / f"{stem}-{case}" / "frame_0001.png")
    engines = sorted({p.name.rsplit("-", 1)[0] for p in grid.iterdir() if p.is_dir()})
    sheet = Image.new("RGB", (W * len(CASES), (H + 16) * len(engines)), "white")
    d = ImageDraw.Draw(sheet)
    for r, eng in enumerate(engines):
        for c, case in enumerate(CASES):
            f = next((grid / f"{eng}-{case}").glob("*.png"), None) if (grid / f"{eng}-{case}").exists() else None
            x, y = c * W, r * (H + 16)
            d.text((x + 4, y + 2), f"{eng} · {case}", fill="black")
            if f is None:
                continue
            im = Image.open(f).convert("RGBA").resize((W, H))
            base = checker(W, H).convert("RGBA")
            base.alpha_composite(im)
            sheet.paste(base.convert("RGB"), (x, y + 16))
    sheet.save(out)
    print(out)
