# Lit splat without rendering (experiment, not wired into renda)

Mesh → Gaussian splat with baked lighting and no training views or trainer. Imports renda internals, changes none.

1. Oversample the mesh surface (with base-colour alpha), drop candidates no outward ray escapes from, thin evenly
   to the budget (one per voxel) so every Gaussian sits on visible surface.
2. Patch base-colour alpha into Mitsuba opacity (renda's glTF path drops it), then path-trace the radiance leaving
   each Gaussian in 64 directions (only directions a camera could see it from) through renda's Mitsuba export.
3. Semi-transparent Gaussians: layered over geometry → opaque, traced mix; free-standing → keep alpha, colour
   un-premultiplied with a second ray traced past them.
4. Joint bilateral denoise over neighbours (normal + albedo), own albedo detail multiplied back in.
5. AgX tonemap (as renda's renders), per-Gaussian ridge fit to SH3.

    cd core && uv sync --extra mitsuba && uv pip install scipy
    OPENBLAS_NUM_THREADS=1 .venv/bin/python ../experiments/lit-splat/lit_splat.py PKG.renda.json OUT/ --points 1000000
    .venv/bin/python ../experiments/lit-splat/score.py PKG.renda.json REFERENCE.png OUT/ label=a.spz ...
    .venv/bin/python ../experiments/lit-splat/mitsuba_ref.py PKG.renda.json mitsuba.png   # the method's ceiling
    .venv/bin/python ../experiments/lit-splat/compare.py PKG.renda.json grid.png label=a.spz ...

## Porsche (testdata/showcase-public/porsche-cycles), PSNR vs the Cycles render at 1600×1000

| | views | Gaussians | file | frame | car |
|---|---|---|---|---|---|
| lit v1 (random 300k, no alpha, no denoise) | 0 | 247k | 5.5 MB | 19.4 | 16.8 |
| lit v3 (this) | 0 | 974k | 20.7 MB | 22.5 | 18.5 |
| trained `standard` | 128 Cycles | 254k | 7.1 MB | 26.9 | 22.7 |
| Mitsuba render of the same scene (ceiling) | – | – | – | 22.8 | 18.9 |

v3 took 231 s on a MacBook (Metal) under heavy load from other jobs: trace 151 s, denoise 40 s, fit 21 s.

Findings
- Number plate, antenna, floor shadow and logo now resolve; v1's look came from wasted samples (floor, interior),
  missing alpha, and undenoised per-Gaussian noise.
- Left: dark fringe at silhouettes and seams, grime on the rear glass, darker paint (Mitsuba material translation,
  also in the Mitsuba render), files ~3× the trained size.
- renda bug found: the Mitsuba engine ignores base-colour alpha (shadow planes, decals, light lenses render opaque),
  in renders and texel bakes.
- Run numpy single-threaded (OPENBLAS_NUM_THREADS=1): under machine load multithreaded BLAS made the fit 10-20× slower.

Next: use the lit bake as the starting point for `standard` training (lighting already right → fewer views/steps).

## Modal L4 benchmark (modal_bench.py, app `renda-lit-splat-bench`, volume `renda-lit-bench`)

Held-out PSNR on the same 16 Cycles views (every 8th rig camera, 512 spp, never trained on); billed L4 seconds.

| | lit | views | train | total | PSNR | SSIM |
|---|---|---|---|---|---|---|
| standard today (512 spp views) | – | 185 | 181 | ~372 | 33.47 | 0.975 |
| standard, draft views (32 spp + denoise) | – | 73 | 184 | ~263 | 33.54 | 0.974 |
| hybrid: lit 400k start, draft views, 3000 steps | 32 | 73 | 89 | ~197 | 33.43 | 0.975 |
| hybrid, 4000 steps (recommended) | 32 | 73 | 130 | ~238 | 34.09 | 0.978 |
| hybrid, 6000 steps | 32 | 73 | 198 | ~306 | 34.85 | 0.979 |
| control: mesh start, 48 views, 3000 steps | – | | 79 | | 29.77 | |
| lit start, 48 views, 3000 steps | 32 | | 94 | | 31.35 | |

- Lit start: +1.6 dB at equal views/steps; passes standard's final score at ~3500 steps.
- Draft views train as well as final ones for this package (which asks for quality final → 512 spp views).
- Views can't be cut much: 16/32/48 views → 29.8/30.1/31.4 dB. The win is fewer steps.
- train_patched.py: renda's trainer with SH init from the start splat, freeze/full-SH knobs (string patches, asserted).
