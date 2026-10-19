"""Signed worker → server events (ARCHITECTURE.md §3 "Worker contract")."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Callable

log = logging.getLogger("renda.events")

SIGNATURE_HEADER = "x-renda-signature"
IDEMPOTENCY_HEADER = "idempotency-key"


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(body: bytes, secret: str, signature: str) -> bool:
    return hmac.compare_digest(sign(body, secret), signature or "")


def encode(event: dict[str, Any]) -> bytes:
    # Compact + sorted so the signed bytes are stable and easy to reproduce in tests.
    return json.dumps(event, separators=(",", ":"), sort_keys=True).encode()


Poster = Callable[[str, bytes, dict[str, str]], int]
"""(url, body, headers) -> HTTP status. Raises on network errors."""


def _requests_poster(url: str, body: bytes, headers: dict[str, str]) -> int:
    import requests

    return requests.post(url, data=body, headers=headers, timeout=15).status_code


class EventSink:
    """Posts events for one job. Retries 5xx/network errors with backoff; 4xx are not retried."""

    def __init__(
        self,
        job_id: str,
        callback_url: str,
        secret: str,
        poster: Poster | None = None,
        attempts: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.job_id = job_id
        self.callback_url = callback_url
        self.secret = secret
        self.poster = poster or _requests_poster
        self.attempts = attempts
        self.sleep = sleep

    def emit(self, type: str, idempotency_key: str | None = None, **fields: Any) -> bool:
        event = {"jobId": self.job_id, "type": type, **{k: v for k, v in fields.items() if v is not None}}
        body = encode(event)
        headers = {"content-type": "application/json", SIGNATURE_HEADER: sign(body, self.secret)}
        if idempotency_key:
            headers[IDEMPOTENCY_HEADER] = idempotency_key
        delay = 0.5
        for attempt in range(1, self.attempts + 1):
            try:
                status = self.poster(self.callback_url, body, headers)
                if status < 400:
                    return True
                if status < 500 and status != 429:
                    log.error("event %s rejected with %s", type, status)
                    return False
            except Exception as exc:  # network error
                log.warning("event %s attempt %d failed: %s", type, attempt, exc)
            if attempt < self.attempts:
                self.sleep(delay)
                delay = min(delay * 2, 8)
        log.error("event %s dropped after %d attempts", type, self.attempts)
        return False

    # Typed helpers so call sites match the contract exactly.
    def status(self, status: str, progress: float | None = None, engine: str | None = None, hardware: str | None = None) -> bool:
        return self.emit("status", status=status, progress=None if progress is None else round(progress, 4), engine=engine, hardware=hardware)

    def usage(self, hardware: str, seconds: float, chunk: int | str) -> bool:
        # Usage is money: idempotent per (job, chunk) so retries never double-bill.
        return self.emit("usage", idempotency_key=f"{self.job_id}:{chunk}", hardware=hardware, seconds=round(seconds, 3), chunk=chunk)

    def warning(self, message: str) -> bool:
        return self.emit("warning", message=message)

    def done(self, outputs: list[dict[str, Any]], cached: bool) -> bool:
        return self.emit("done", idempotency_key=f"{self.job_id}:done", outputs=outputs, cached=cached)

    def failed(self, error: str) -> bool:
        return self.emit("failed", idempotency_key=f"{self.job_id}:failed", error=error[:4000])
