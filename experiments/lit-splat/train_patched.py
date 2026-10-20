"""EXPERIMENT: renda's splat trainer (renda.splatbake.train.train), patched at import time rather than forked, so it
tracks core. Patches (each asserted to apply):
- start from the init's SH bands (renda starts shN at zero, which would throw away the lit bake's gloss);
- FREEZE / FULL_SH knobs: frozen-position steps, and train all SH bands from step 0 (they're already fitted);
- EVAL_ONLY: no optimisation, just the held-out evaluation (to score a splat as-is).
"""

from __future__ import annotations

import inspect
import types

import numpy as np

import renda.splatbake.train as _orig

PATCHES = [
    ('params["shN"] = torch.nn.Parameter(torch.zeros((len(init), k_rest, 3), device=dev))',
     'params["shN"] = torch.nn.Parameter(t(_sh_rest(init, k_rest)))'),
    ("freeze = min(FREEZE_STEPS, steps // 3)", "freeze = min(_KNOBS['freeze'], steps // 3)"),
    ("sh_deg = min(max(step - freeze, 0) // 1000, deg)",
     "sh_deg = deg if _KNOBS['full_sh'] else min(max(step - freeze, 0) // 1000, deg)"),
    ("    for step in range(steps):\n", "    for step in range(0 if _KNOBS['eval_only'] else steps):\n"),
]

_KNOBS = {"freeze": _orig.FREEZE_STEPS, "full_sh": False, "eval_only": False}


def _sh_rest(init, k_rest: int) -> np.ndarray:
    out = np.zeros((len(init), k_rest, 3), np.float32)
    if init.sh_rest is not None and init.sh_rest.size:
        k = min(k_rest, init.sh_rest.shape[1])
        out[:, :k] = init.sh_rest[:, :k]
    return out


def _build():
    src = inspect.getsource(_orig)
    for old, new in PATCHES:
        assert src.count(old) == 1, f"trainer patch no longer applies: {old!r}"
        src = src.replace(old, new)
    mod = types.ModuleType("renda.splatbake._train_patched")
    mod.__package__ = "renda.splatbake"
    mod.__dict__.update({"_KNOBS": _KNOBS, "_sh_rest": _sh_rest})
    exec(compile(src, _orig.__file__ + " (patched)", "exec"), mod.__dict__)
    return mod


_mod = _build()


def train(init, images, cams, settings, scene_radius, *, freeze: int | None = None, full_sh: bool = False,
          eval_only: bool = False, log=print):
    _KNOBS.update(freeze=_orig.FREEZE_STEPS if freeze is None else freeze, full_sh=full_sh, eval_only=eval_only)
    return _mod.train(init, images, cams, settings, scene_radius, log=log)
