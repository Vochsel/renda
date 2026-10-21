"""Exception reporting to PostHog error tracking, with full Python stack traces.

Plain HTTP via `requests` (already in every image) rather than the PostHog SDK, so adding it doesn't
rebuild the heavy Blender/torch images. No-op without POSTHOG_KEY (set in the production
`renda-worker` secret only). Never sends package contents or URLs: exception type, message, frames,
job id and a few labels.
"""

from __future__ import annotations

import logging
import os
import traceback
import uuid

log = logging.getLogger(__name__)

_HOST = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com")
_APP_DIRS = ("renda_worker", "/renda/")  # in-app frames: our code, not libraries


def _frames(exc: BaseException) -> list[dict]:
    frames = []
    for f in traceback.extract_tb(exc.__traceback__):
        frames.append({
            "platform": "python",
            "filename": os.path.basename(f.filename),
            "abs_path": f.filename,
            "function": f.name,
            "lineno": f.lineno,
            "context_line": f.line,
            "in_app": any(d in f.filename for d in _APP_DIRS) and "site-packages" not in f.filename,
        })
    return frames


def exception_event(exc: BaseException, key: str, **props) -> dict:
    """The `$exception` capture payload, PostHog's format (one entry per exception in the chain)."""
    chain: list[BaseException] = []
    e: BaseException | None = exc
    while e is not None and e not in chain and len(chain) < 5:
        chain.append(e)
        e = e.__cause__ or e.__context__
    return {
        "api_key": key,
        "event": "$exception",
        "distinct_id": "renda-worker",
        "uuid": str(uuid.uuid4()),
        "properties": {
            "$exception_list": [
                {
                    "type": type(x).__name__,
                    "value": str(x)[:2000],
                    "mechanism": {"handled": True, "synthetic": False},
                    "stacktrace": {"type": "raw", "frames": _frames(x)},
                }
                for x in chain
            ],
            "$process_person_profile": False,
            "app": "renda",
            "source": "worker",
            "worker_app": os.environ.get("RENDA_APP_NAME", "renda-worker"),
            **{k: v for k, v in props.items() if v is not None},
        },
    }


def capture_exception(exc: BaseException, **props) -> None:
    """Best effort, short timeout: reporting must never slow down or break a failing job further."""
    key = os.environ.get("POSTHOG_KEY")
    if not key:
        return
    try:
        import requests

        requests.post(f"{_HOST}/i/v0/e/", json=exception_event(exc, key, **props), timeout=3)
    except Exception as e:  # noqa: BLE001
        log.warning("posthog capture failed: %s", e)
