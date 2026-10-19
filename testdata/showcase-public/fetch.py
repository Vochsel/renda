#!/usr/bin/env python3
"""Download the public showcase assets into ./assets (gitignored). See CREDITS.md for licenses.

    SKETCHFAB_API_TOKEN=... python fetch.py        # needs a (free) Sketchfab account token
    python fetch.py --only lizard raccoon          # a subset

Splats from SuperSplat ship as PlayCanvas .sog and are converted to .spz with
`npx -y @playcanvas/splat-transform` (Node required).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"

SKETCHFAB = {  # file name: model uid
    "porsche.glb": "8568d9d14a994b9cae59499f0dbed21e",
    "samurai.glb": "26ccafaddb2745ceb56ae5cfc65bfed5",
    "camera.glb": "9de66868d0f240e985da00c9480bfc82",
    "craftsman.glb": "0e8ff87ffaa24731b2474badebca870d",
    "venus.glb": "53082b5d6cef4c34a9701a2a24f58075",
    "monstera.glb": "1ab9bf841df04c07b1819be596327629",
}
PLAYCANVAS = "https://raw.githubusercontent.com/playcanvas/engine/main/examples/assets/splats/"
SOG = {"apartment.spz": PLAYCANVAS + "apartment.sog", "knock-community-hall.spz": PLAYCANVAS + "knock-community-hall.sog"}
NIANTIC = "https://raw.githubusercontent.com/nianticlabs/spz/main/samples/"
DIRECT = {"hornedlizard.spz": NIANTIC + "hornedlizard.spz", "racoonfamily.spz": NIANTIC + "racoonfamily.spz"}


def sketchfab(uid: str, dest: Path) -> None:
    token = os.environ.get("SKETCHFAB_API_TOKEN")
    if not token:
        sys.exit("set SKETCHFAB_API_TOKEN (https://sketchfab.com/settings/password → API token)")
    req = urllib.request.Request(f"https://api.sketchfab.com/v3/models/{uid}/download", headers={"Authorization": f"Token {token}"})
    links = json.load(urllib.request.urlopen(req, timeout=60))
    if "glb" not in links:
        sys.exit(f"{uid}: no .glb download offered")
    urllib.request.urlretrieve(links["glb"]["url"], dest)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="substring filter on file names")
    args = ap.parse_args()
    ASSETS.mkdir(exist_ok=True)
    wanted = lambda name: not args.only or any(o in name for o in args.only)  # noqa: E731

    for name, uid in SKETCHFAB.items():
        if wanted(name) and not (ASSETS / name).exists():
            print("sketchfab", name)
            sketchfab(uid, ASSETS / name)
    for name, url in DIRECT.items():
        if wanted(name) and not (ASSETS / name).exists():
            print("download", name)
            urllib.request.urlretrieve(url, ASSETS / name)
    for name, url in SOG.items():
        if wanted(name) and not (ASSETS / name).exists():
            print("download + convert", name)
            with tempfile.TemporaryDirectory() as tmp:
                sog = Path(tmp) / "scene.sog"
                urllib.request.urlretrieve(url, sog)
                subprocess.run(["npx", "-y", "@playcanvas/splat-transform@3.10.1", str(sog), str(ASSETS / name)], check=True)


if __name__ == "__main__":
    main()
