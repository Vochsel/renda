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
TIMESTAMP_HEADER = "x-renda-timestamp"
IDEMPOTENCY_HEADER = "idempotency-key"


def sign(body: bytes, secret: str) -> str:
    """Legacy untimed signature (still verified by the server during rollouts)."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def sign_timed(body: bytes, timestamp: int, secret: str) -> str:
    """`v1=` HMAC over "<timestamp>.<body>"; the server rejects timestamps outside ±5 minutes."""
    return "v1=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def verify(body: bytes, secret: str, signature: str) -> bool:
    return hmac.compare_digest(sign(body, secret), signature or "")


def encode(event: dict[str, Any]) -> bytes:
    # Compact + sorted so the signed bytes are stable and easy to reproduce in tests.
    return json.dumps(event, separators=(",", ":"), sort_keys=True).encode()


Poster = Callable[[str, bytes, dict[str, str]], int]
"""(url, body, headers) -> HTTP status. Raises on network errors."""


_SESSION = None


def _requests_poster(url: str, body: bytes, headers: dict[str, str]) -> int:
    # One pooled keep-alive session per container: a fresh TLS handshake per event added ~0.3–0.8 s of
    # billed time to every status update.
    global _SESSION
    if _SESSION is None:
        import requests

        _SESSION = requests.Session()
    return _SESSION.post(url, data=body, headers=headers, timeout=15).status_code


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
        clock: Callable[[], float] = time.time,
        background: bool = False,
        meter_interval: float | None = None,
    ):
        self.job_id = job_id
        self.callback_url = callback_url
        self.secret = secret
        self.poster = poster or _requests_poster
        self.attempts = attempts
        self.sleep = sleep
        self.clock = clock
        # background=True: status/warning events go out on one ordered worker thread so they don't add
        # latency to billed work; usage/done/failed flush that queue first and stay synchronous.
        self.background = background
        self._queue = None
        # Live cost heartbeats ("meter" events). Off unless background delivery is on (production) or an
        # interval is given explicitly (tests); they never touch billing.
        self.meter_interval = meter_interval if meter_interval is not None else (3.0 if background else None)

    def _enqueue(self, type: str, **fields: Any) -> bool:
        if not self.background:
            return self.emit(type, **fields)
        import queue
        import threading

        if self._queue is None:
            self._queue = queue.Queue()

            def work() -> None:
                while True:
                    t, f = self._queue.get()
                    try:
                        self.emit(t, **f)
                    finally:
                        self._queue.task_done()

            threading.Thread(target=work, daemon=True).start()
        self._queue.put((type, fields))
        return True

    def flush(self) -> None:
        """Wait until every queued event has been delivered (keeps event order intact)."""
        if self._queue is not None:
            self._queue.join()

    def emit(self, type: str, idempotency_key: str | None = None, **fields: Any) -> bool:
        event = {"jobId": self.job_id, "type": type, **{k: v for k, v in fields.items() if v is not None}}
        body = encode(event)
        base = {"content-type": "application/json"}
        if idempotency_key:
            base[IDEMPOTENCY_HEADER] = idempotency_key
        delay = 0.5
        for attempt in range(1, self.attempts + 1):
            # Re-signed per attempt: retries after backoff carry a fresh timestamp.
            ts = int(self.clock())
            headers = {**base, TIMESTAMP_HEADER: str(ts), SIGNATURE_HEADER: sign_timed(body, ts, self.secret)}
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

    def meter(self, chunk: int | str, hardware: str, elapsed_seconds: float) -> bool:
        """One best-effort heartbeat: single attempt, no retry, never raises. Not billing — usage events are."""
        event = {"jobId": self.job_id, "type": "meter", "chunk": chunk, "hardware": hardware,
                 "elapsedSeconds": round(max(elapsed_seconds, 0.0), 2), "at": round(self.clock(), 3)}
        body = encode(event)
        ts = int(self.clock())
        headers = {"content-type": "application/json", TIMESTAMP_HEADER: str(ts), SIGNATURE_HEADER: sign_timed(body, ts, self.secret)}
        try:
            return self.poster(self.callback_url, body, headers) < 400
        except Exception as exc:
            log.debug("meter dropped: %s", exc)
            return False

    def start_meter(self, chunk: int | str, hardware: str, elapsed: Callable[[], float]) -> "Meter":
        """Heartbeat every `meter_interval` s on a daemon thread until `.stop()`; a no-op when metering is off."""
        m = Meter(self, chunk, hardware, elapsed, self.meter_interval)
        m.start()
        return m

    # Typed helpers so call sites match the contract exactly.
    def status(self, status: str, progress: float | None = None, engine: str | None = None, hardware: str | None = None) -> bool:
        return self._enqueue("status", status=status, progress=None if progress is None else round(progress, 4), engine=engine, hardware=hardware)

    def usage(self, hardware: str, seconds: float, chunk: int | str) -> bool:
        # Usage is money: idempotent per (job, chunk) so retries never double-bill.
        self.flush()
        return self.emit("usage", idempotency_key=f"{self.job_id}:{chunk}", hardware=hardware, seconds=round(seconds, 3), chunk=chunk)

    def warning(self, message: str) -> bool:
        return self._enqueue("warning", message=message)

    def done(self, outputs: list[dict[str, Any]], cached: bool) -> bool:
        self.flush()
        return self.emit("done", idempotency_key=f"{self.job_id}:done", outputs=outputs, cached=cached)

    def failed(self, error: str) -> bool:
        self.flush()
        return self.emit("failed", idempotency_key=f"{self.job_id}:failed", error=error[:4000])


class Meter:
    """Daemon thread posting `meter` heartbeats for one billed container.

    Sends are synchronous on this thread, so a slow callback can only delay the next tick (ticks that fall
    behind are skipped, never queued) — rendering and billing events are never blocked by it.
    """

    def __init__(self, sink: EventSink, chunk: int | str, hardware: str, elapsed: Callable[[], float], interval: float | None):
        self.sink, self.chunk, self.hardware, self.elapsed, self.interval = sink, chunk, hardware, elapsed, interval
        self._stop = None

    def start(self) -> None:
        if not self.interval:
            return
        import threading

        self._stop = threading.Event()

        def loop() -> None:
            while not self._stop.wait(self.interval):
                try:
                    self.sink.meter(self.chunk, self.hardware, self.elapsed())
                except Exception:  # elapsed() must never take the render down
                    pass

        threading.Thread(target=loop, daemon=True, name=f"meter-{self.chunk}").start()

    def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
