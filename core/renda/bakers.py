"""GLB jobs (output.format "glb"): plain conversion, or a light bake with Cycles, Mitsuba or three.js."""

from __future__ import annotations

import re
import time

import numpy as np

from .bake import BakeError, albedo_and_emission, finish, prepare, prepare_plain, rasterize, write_glb
from .engines.base import RenderContext, RenderResult


def output_path(ctx: RenderContext):
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", ctx.pkg.name or "").strip("-")[:80]
    return ctx.out_dir / f"{name or ('baked' if ctx.pkg.output.bake else 'model')}.glb"


def run_model_job(ctx: RenderContext) -> RenderResult:
    from .scene import build_stage

    t0 = time.time()
    pkg, plan = ctx.pkg, ctx.plan
    warnings: list[str] = []
    stage, w = build_stage(pkg, ctx.asset_paths, ctx.cache_dir)
    warnings += w
    out = output_path(ctx)
    bake = pkg.output.bake
    if bake is None:
        layout = prepare_plain(stage)
        warnings += layout.warnings
        write_glb(layout, stage, out, None)
        return RenderResult([out], warnings, time.time() - t0)

    if pkg.camera.mode != "auto" or pkg.animation:
        warnings.append("bake: camera and animation settings don't apply to bakes and were ignored")
    layout = prepare(stage, bake.size, bake.uvs, bake.margin)
    warnings += layout.warnings
    texels = rasterize(layout)
    mask = texels.mask()
    engine = plan.engine
    if engine == "blender-cycles":
        from .engines.blender import bake_atlas

        linear = bake_atlas(ctx, stage, layout, warnings)
    elif engine == "mitsuba":
        from .engines.mitsuba import bake_atlas

        linear, valid = bake_atlas(ctx, stage, layout, texels, warnings)
        mask = mask & valid
    elif engine == "threejs":
        from .engines.threejs import bake_atlas

        light, covered = bake_atlas(ctx, stage, layout, warnings)
        if bake.mode == "unlit":
            albedo, emission = albedo_and_emission(stage, layout, texels)
            linear = np.zeros_like(light)
            linear[texels.rows, texels.cols] = albedo * light[texels.rows, texels.cols] + emission
        else:
            linear = light
        mask = mask & covered if covered.any() else mask
        warnings.append("threejs: baked direct light + environment with shadows; no interreflections "
                        "(use blender-cycles or mitsuba for global illumination)")
    else:
        raise BakeError(f"{engine} can't bake; use blender-cycles, mitsuba or threejs")
    finish(layout, stage, linear, bake.mode, out, bake.textureFormat, bake.margin, mask,
           extras={"renda": {"bake": {"engine": engine, "mode": bake.mode, "size": bake.size, "samples": plan.samples}}})
    return RenderResult([out], warnings, time.time() - t0)
