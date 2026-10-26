"""Minimal environments for subprocesses that handle untrusted content (HyperFrames CLI → headless Chrome,
ffmpeg on user media). Mirrors core's renda/procenv.py; kept separate because the HyperFrames image has no core.

An explicit allowlist, not "the environment minus secrets": whatever lands in a container's environment
later (a new Modal secret, a token set by a library) stays out of the subprocess by default.
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Iterable, Mapping

BASE_KEYS = ("PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "USER", "LOGNAME",
             "SHELL", "TERM", "LD_LIBRARY_PATH", "SSL_CERT_FILE", "SSL_CERT_DIR", "FONTCONFIG_PATH", "FONTCONFIG_FILE")
# Renderer/driver knobs that are never secrets. Matched by prefix.
BASE_PREFIXES = ("XDG_", "NVIDIA_", "CUDA_", "__GL_", "__EGL_", "MESA_", "LIBGL_", "GALLIUM_", "EGL_", "DRJIT_", "OCIO")
# Dropped even when an allowlisted prefix matches.
SECRETISH = re.compile(r"SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE|API_?KEY|ACCESS_?KEY|^AWS_|^R2_|^POSTHOG_|^RENDA_WORKER", re.I)


def untrusted_env(keep_prefixes: Iterable[str] = (), extra: Mapping[str, str] | None = None,
                  source: Mapping[str, str] | None = None) -> dict[str, str]:
    src = os.environ if source is None else source
    prefixes = tuple(BASE_PREFIXES) + tuple(keep_prefixes)
    env = {k: v for k, v in src.items()
           if (k in BASE_KEYS or k.startswith(prefixes)) and not SECRETISH.search(k)}
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    env.setdefault("HOME", src.get("HOME") or tempfile.gettempdir())
    env.setdefault("TMPDIR", tempfile.gettempdir())
    env.update(extra or {})
    return env
