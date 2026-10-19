"""Smoke-test the Renda CLI against the test set.

    uv run --project core python testdata/smoke.py                 # info every scene, validate every package
    uv run --project core python testdata/smoke.py --render        # + render every package (small, draft)
    uv run --project core python testdata/smoke.py --matrix        # + render every scene format × every engine
    uv run --project core python testdata/smoke.py --hyperframes   # + `hyperframes check` on the compositions

Writes testdata/out/{contact.png,matrix.png,results.json}. Exit code is non-zero if anything failed.
Everything goes through the real `renda` CLI with --json, as an agent would use it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
SCENES = sorted(
    [p for p in (HERE / "scenes").glob("*.*") if p.suffix not in (".mtl", ".bin", ".png")]
    + [HERE / "scenes" / "threejs" / "scene.json"]
    + [HERE / "scenes" / "splats" / n for n in ("garden.ply", "garden.splat", "garden.spz")]
)
SPLAT_SCENES = {p for p in SCENES if p.parent.name == "splats"}
PACKAGES = sorted((HERE / "packages").glob("*.renda.json"))
VIDEO_EXTS = {".mp4", ".webm", ".gif", ".mov"}


def renda(*args: str, timeout: int = 900) -> tuple[bool, dict, float]:
    """Run `renda <args> --json`; returns (ok, last JSON object printed, seconds)."""
    t = time.time()
    try:
        p = subprocess.run(["renda", *args, "--json"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, {"error": {"message": f"timed out after {timeout}s"}}, time.time() - t
    doc: dict = {}
    for line in reversed(p.stdout.strip().splitlines()):
        try:
            doc = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if not doc:
        doc = {"error": {"message": (p.stderr or p.stdout).strip()[-500:] or f"exit {p.returncode}"}}
    return p.returncode == 0 and "error" not in doc, doc, time.time() - t


def err(doc: dict) -> str:
    e = doc.get("error", {})
    return e.get("message", str(e)) if isinstance(e, dict) else str(e)


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def preview_frame(path: Path) -> Path:
    """A PNG to show on the contact sheet: the file itself, or the middle frame of a video."""
    if path.suffix.lower() not in VIDEO_EXTS:
        return path
    out = path.with_suffix(path.suffix + ".preview.png")
    probe = subprocess.run([ffmpeg(), "-i", str(path)], capture_output=True, text=True).stderr
    dur = 1.0
    for tok in probe.split("Duration: ")[1:2]:
        h, m, s = tok.split(",")[0].split(":")
        dur = int(h) * 3600 + int(m) * 60 + float(s)
    # libvpx-vp9 keeps alpha only through the libvpx decoder.
    pre = ["-c:v", "libvpx-vp9"] if path.suffix == ".webm" else []
    subprocess.run([ffmpeg(), "-loglevel", "error", "-y", *pre, "-ss", f"{dur / 2:.2f}", "-i", str(path),
                    "-frames:v", "1", str(out)], check=True)
    return out


def contact_sheet(tiles: list[tuple[str, str, Path | None, bool]], out: Path, cols: int, tile: int = 256) -> None:
    """tiles: (title, subtitle, image or None, ok). Transparent images are shown on a checkerboard."""
    from PIL import Image, ImageDraw, ImageFont

    label_h = 40
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile, rows * (tile + label_h)), (24, 24, 27))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 13)
    except OSError:
        font = ImageFont.load_default()
    checker = Image.new("RGB", (tile, tile), (200, 200, 200))
    cd = ImageDraw.Draw(checker)
    for y in range(0, tile, 16):
        for x in range(0, tile, 16):
            if (x // 16 + y // 16) % 2:
                cd.rectangle([x, y, x + 15, y + 15], fill=(150, 150, 150))
    for i, (title, sub, img, ok) in enumerate(tiles):
        x, y = (i % cols) * tile, (i // cols) * (tile + label_h)
        if img is not None and img.exists():
            im = Image.open(img)
            if im.mode in ("I;16", "I;16B", "I"):  # 16-bit passes (depth): scale to 8-bit to display
                im = im.point(lambda v: v / 257).convert("L")
            im.thumbnail((tile, tile))
            bg = checker.copy() if im.mode in ("RGBA", "LA", "P") else Image.new("RGB", (tile, tile), (0, 0, 0))
            off = ((tile - im.width) // 2, (tile - im.height) // 2)
            bg.paste(im.convert("RGBA"), off, im.convert("RGBA"))
            sheet.paste(bg, (x, y))
        else:
            draw.rectangle([x, y, x + tile - 1, y + tile - 1], fill=(60, 20, 20))
            draw.text((x + 8, y + tile // 2 - 8), "FAILED", fill=(255, 120, 120), font=font)
        draw.text((x + 6, y + tile + 4), title, fill=(255, 255, 255) if ok else (255, 120, 120), font=font)
        draw.text((x + 6, y + tile + 21), sub, fill=(160, 160, 170), font=font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)


def zod_validate(paths: list[Path]) -> dict[str, str | None]:
    """Validate with the canonical Zod schema too (needs spec/ built). Returns {path: error|None}."""
    dist = ROOT / "spec" / "dist" / "index.js"
    if not dist.exists() or not shutil.which("node"):
        return {}
    script = (
        f"import {{ safeParsePackage }} from {json.dumps(dist.as_uri())};"
        "import { readFileSync } from 'node:fs';"
        "const out = {};"
        "for (const p of process.argv.slice(1)) {"
        "  const r = safeParsePackage(JSON.parse(readFileSync(p, 'utf8')));"
        "  out[p] = r.success ? null : r.error.issues.map(i => i.path.join('.') + ': ' + i.message).join('; ');"
        "}"
        "console.log(JSON.stringify(out));"
    )
    p = subprocess.run(["node", "--input-type=module", "-e", script, *map(str, paths)], capture_output=True, text=True)
    return json.loads(p.stdout) if p.returncode == 0 else {str(x): p.stderr.strip()[-300:] for x in paths}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", action="store_true", help="render every package")
    ap.add_argument("--matrix", action="store_true", help="render every scene format with every engine")
    ap.add_argument("--hyperframes", action="store_true", help="run `hyperframes check` on compositions")
    ap.add_argument("--res", default="256x256")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--only", help="substring filter on scene/package names")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    results: dict = {"scenes": {}, "packages": {}, "renders": {}, "matrix": {}, "hyperframes": {}}
    failed: list[str] = []
    pick = (lambda p: args.only in p.name) if args.only else (lambda p: True)

    print("── renda info")
    for s in filter(pick, SCENES):
        ok, doc, _ = renda("info", str(s))
        name = str(s.relative_to(HERE / "scenes"))
        summary = (f"{doc.get('format')} meshes={doc.get('meshes')} tris={doc.get('triangles')} splats={doc.get('splats')}"
                   if ok else err(doc))
        if ok and not doc.get("triangles") and not doc.get("splats"):
            ok, summary = False, "loaded but has no triangles"
        results["scenes"][name] = {"ok": ok, "detail": summary}
        print(f"  {'✓' if ok else '✗'} {name:22} {summary}")
        if not ok:
            failed.append(f"info {name}")

    print("── renda validate (pydantic) + Zod")
    zod = zod_validate(list(filter(pick, PACKAGES)))
    for p in filter(pick, PACKAGES):
        ok, doc, _ = renda("validate", str(p))
        zerr = zod.get(str(p))
        detail = err(doc) if not ok else (f"zod: {zerr}" if zerr else "ok")
        ok = ok and not zerr
        results["packages"][p.name] = {"ok": ok, "detail": detail}
        print(f"  {'✓' if ok else '✗'} {p.name:36} {detail}")
        if not ok:
            failed.append(f"validate {p.name}")

    if args.render:
        print(f"── render packages at {args.res}, draft")

        def run_pkg(p: Path):
            out = OUT / "packages" / p.name.removesuffix(".renda.json")
            shutil.rmtree(out, ignore_errors=True)
            ok, doc, secs = renda("render", str(p), "-o", str(out), "--res", args.res, "--quality", "draft")
            return p, out, ok, doc, secs

        tiles = []
        with ThreadPoolExecutor(args.jobs) as ex:
            for p, out, ok, doc, secs in ex.map(run_pkg, filter(pick, PACKAGES)):
                outputs = [Path(o) for o in doc.get("outputs", [])]
                main_out = next((o for o in outputs if not any(t in o.name for t in (".alpha.", ".depth.", ".normal."))), None)
                preview = preview_frame(main_out) if ok and main_out else None
                name = p.name.removesuffix(".renda.json")
                engine = doc.get("engine", "?")
                results["renders"][name] = {"ok": ok, "engine": engine, "seconds": round(secs, 1),
                                            "outputs": [str(o.relative_to(HERE)) for o in outputs],
                                            "warnings": doc.get("warnings", []), "error": None if ok else err(doc)}
                print(f"  {'✓' if ok else '✗'} {name:24} {engine:18} {secs:5.1f}s  "
                      + (", ".join(o.name for o in outputs) if ok else err(doc)[:200]))
                for w in doc.get("warnings", []):
                    print(f"      ⚠ {w}")
                tiles.append((name, f"{engine} · {secs:.0f}s", preview, ok))
                # Extra passes get their own tiles so they can be eyeballed.
                for o in outputs:
                    if o is not main_out and o.suffix == ".png":
                        tiles.append((name, o.name.split(".")[-2] + " pass", o, ok))
                if not ok:
                    failed.append(f"render {name}")
        contact_sheet(tiles, OUT / "contact.png", cols=6)
        print(f"  → {OUT / 'contact.png'}")

    if args.matrix:
        _, eng, _ = renda("engines")
        engines = [e["name"] for e in eng.get("engines", []) if e.get("available")]
        w, h = (int(x) for x in args.res.split("x"))
        res = f"{w * 5 // 8}x{h * 5 // 8}"
        print(f"── matrix: {len(SCENES)} scenes × {len(engines)} engines at {res}")

        def run_cell(cell):
            s, e = cell
            if e == "gsplat" and s not in SPLAT_SCENES:  # gsplat only renders splats
                return s, e, None, {}, 0.0
            out = OUT / "matrix" / f"{s.name.replace('.', '_')}__{e}"
            shutil.rmtree(out, ignore_errors=True)
            ok, doc, secs = renda("render", str(s), "-o", str(out), "--engine", e, "--res", res, "--quality", "draft")
            return s, e, ok, doc, secs

        cells = [(s, e) for s in filter(pick, SCENES) for e in engines]
        tiles = []
        with ThreadPoolExecutor(args.jobs) as ex:
            for s, e, ok, doc, secs in ex.map(run_cell, cells):
                name = str(s.relative_to(HERE / "scenes"))
                if ok is None:
                    tiles.append((name, f"{e} · n/a", None, True))
                    continue
                outs = [Path(o) for o in doc.get("outputs", [])]
                results["matrix"].setdefault(name, {})[e] = {"ok": ok, "seconds": round(secs, 1),
                                                            "error": None if ok else err(doc)[:300],
                                                            "warnings": doc.get("warnings", [])}
                tiles.append((name, f"{e} · {secs:.0f}s", outs[0] if ok and outs else None, ok))
                if not ok:
                    failed.append(f"matrix {name} × {e}: {err(doc)[:160]}")
        contact_sheet(tiles, OUT / "matrix.png", cols=len(engines), tile=200)
        short = {e: e.replace("blender-", "") for e in engines}
        print("  " + "".ljust(20) + "".join(short[e].ljust(11) for e in engines))
        for name, row in results["matrix"].items():
            print("  " + name.ljust(20) + "".join(("–" if e not in row else "✓" if row[e]["ok"] else "✗").ljust(11)
                                                  for e in engines))
        print(f"  → {OUT / 'matrix.png'}")

    if args.hyperframes:
        print("── hyperframes check")
        for proj in sorted(p.parent for p in (HERE / "hyperframes").glob("*/index.html")):
            p = subprocess.run(["npx", "-y", "hyperframes@0.8.173", "check"], cwd=proj, capture_output=True, text=True)
            ok = p.returncode == 0 and "Check passed" in p.stdout
            results["hyperframes"][proj.name] = {"ok": ok}
            print(f"  {'✓' if ok else '✗'} {proj.name}")
            if not ok:
                failed.append(f"hyperframes {proj.name}")

    (OUT / "results.json").write_text(json.dumps(results, indent=2))
    print(f"\n{'FAILED' if failed else 'ALL PASSED'} — {len(failed)} failure(s)")
    for f in failed:
        print(f"  ✗ {f}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
