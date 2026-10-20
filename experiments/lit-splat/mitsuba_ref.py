"""EXPERIMENT: render the exact scene the lit bake traces (renda's Mitsuba export + the alpha patch) from the
package camera. It's the ceiling for the lit bake: comparing both against Cycles separates the method's own
error from Mitsuba-vs-Cycles material differences.

    uv run --project core python experiments/lit-splat/mitsuba_ref.py PACKAGE.renda.json OUT.png [spp]
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor

sys.path.insert(0, str(Path(__file__).parent))
from lit_splat import alpha_to_opacity, mitsuba_scene  # noqa: E402

from renda import assets as assets_mod  # noqa: E402
from renda.images import linear_to_srgb, tonemap  # noqa: E402
from renda.ir.camera import resolve_cameras  # noqa: E402
from renda.scene import build_stage  # noqa: E402
from renda.spec import load_package  # noqa: E402


def main() -> None:
    package, out = Path(sys.argv[1]), Path(sys.argv[2])
    spp = int(sys.argv[3]) if len(sys.argv) > 3 else 256
    pkg = load_package(package)
    asset_paths = assets_mod.resolve(pkg, package.parent)
    stage, _ = build_stage(pkg, asset_paths)
    w, h = pkg.render.resolution
    cam = resolve_cameras(pkg, stage, [1])[1]
    scratch = Path(tempfile.mkdtemp(prefix="lit-ref-"))
    try:
        alpha_to_opacity(stage, scratch)

        def sensor(mi):
            return {"camera": {
                "type": "perspective", "fov": float(cam.fov_y_deg), "fov_axis": "y",
                "to_world": mi.ScalarTransform4f().look_at(origin=cam.position.tolist(), target=cam.target.tolist(), up=cam.up.tolist()),
                "film": {"type": "hdrfilm", "width": w, "height": h, "pixel_format": "rgba"},
                "sampler": {"type": "independent", "sample_count": spp}},
                "integrator": {"type": "path", "max_depth": 6, "hide_emitters": True}}  # backdrop is a flat colour

        scene, _ = mitsuba_scene(pkg, stage, asset_paths, scratch, 6, sensor)
        import mitsuba as mi

        img = np.array(mi.render(scene), dtype=np.float32)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    rgb, a = img[..., :3], img[..., 3:4]
    bg = np.array(ImageColor.getrgb(pkg.environment.background), np.float32)[:3] / 255  # display-space, as the splats
    disp = linear_to_srgb(tonemap(rgb, pkg.post.tonemap if pkg.post else "agx"))
    disp = disp * a + bg * (1 - a)
    Image.fromarray((np.clip(disp, 0, 1) * 255 + 0.5).astype(np.uint8)).save(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
