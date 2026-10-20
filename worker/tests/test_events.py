import hashlib
import hmac
import json

from renda_worker.events import EventSink, encode, sign, sign_timed, verify


def test_sign_matches_reference_hmac():
    body = b'{"jobId":"j","type":"status"}'
    expected = "sha256=" + hmac.new(b"k", body, hashlib.sha256).hexdigest()
    assert sign(body, "k") == expected
    assert verify(body, "k", expected)
    assert not verify(body, "other", expected)
    assert not verify(body, "k", "")


def test_timed_signature_vector():
    # Same vector as web/test/hmac.test.ts, so both sides agree byte for byte.
    body = b'{"jobId":"j","type":"status"}'
    expected = "v1=" + hmac.new(b"k", b"1700000000." + body, hashlib.sha256).hexdigest()
    assert sign_timed(body, 1700000000, "k") == expected


def test_emitted_event_is_signed_over_timestamp_and_exact_body(recorder):
    EventSink("j1", "https://x/cb", "sec", poster=recorder, clock=lambda: 1700000123.9).usage("gpu-l4", 37.21234, 3)
    url, body, headers = recorder.calls[0]
    assert url == "https://x/cb"
    assert headers["x-renda-timestamp"] == "1700000123"
    assert headers["x-renda-signature"] == sign_timed(body, 1700000123, "sec")
    assert headers["idempotency-key"] == "j1:3"
    assert json.loads(body) == {"jobId": "j1", "type": "usage", "hardware": "gpu-l4", "seconds": 37.212, "chunk": 3}


def test_encode_is_stable():
    assert encode({"b": 1, "a": 2}) == b'{"a":2,"b":1}'


def test_retries_5xx_then_succeeds():
    statuses = iter([503, 500, 200])
    sleeps = []
    sink = EventSink("j", "https://x", "s", poster=lambda *a: next(statuses), sleep=sleeps.append)
    assert sink.status("rendering", progress=0.5)
    assert sleeps == [0.5, 1.0]


def test_does_not_retry_4xx():
    calls = []

    def poster(*a):
        calls.append(1)
        return 400

    assert not EventSink("j", "https://x", "s", poster=poster, sleep=lambda s: None).warning("w")
    assert len(calls) == 1


def test_network_errors_give_up_after_attempts():
    def poster(*a):
        raise ConnectionError("down")

    assert not EventSink("j", "https://x", "s", poster=poster, attempts=3, sleep=lambda s: None).done([], cached=False)


def test_meter_is_a_single_signed_attempt_and_never_raises():
    calls = []

    def poster(url, body, headers):
        calls.append((body, headers))
        raise ConnectionError("down")

    sink = EventSink("job_1", "https://api.test/events", "s3cret", poster=poster, clock=lambda: 1_700_000_000.0)
    assert sink.meter(0, "gpu-l4", 12.345) is False  # no exception, no retry
    assert len(calls) == 1
    body, headers = calls[0]
    event = json.loads(body)
    assert event == {"jobId": "job_1", "type": "meter", "chunk": 0, "hardware": "gpu-l4", "elapsedSeconds": 12.35, "at": 1_700_000_000.0}
    ts = int(headers["x-renda-timestamp"])
    assert headers["x-renda-signature"] == sign_timed(body, ts, "s3cret")


def test_meter_thread_heartbeats_until_stopped_and_is_off_by_default():
    import time

    sent = []
    sink = EventSink("job_1", "https://api.test/events", "s3cret", poster=lambda u, b, h: sent.append(json.loads(b)) or 200,
                     meter_interval=0.02)
    t0 = time.monotonic()
    m = sink.start_meter("train", "gpu-l4", lambda: time.monotonic() - t0)
    time.sleep(0.15)
    m.stop()
    n = len(sent)
    time.sleep(0.06)
    assert n >= 3 and len(sent) == n  # ticked, then stopped
    assert all(e["type"] == "meter" and e["chunk"] == "train" for e in sent)
    assert sent[-1]["elapsedSeconds"] >= sent[0]["elapsedSeconds"]

    quiet = []
    off = EventSink("job_1", "https://api.test/events", "s3cret", poster=lambda u, b, h: quiet.append(b) or 200)
    off.start_meter(0, "cpu", lambda: 1.0).stop()
    time.sleep(0.05)
    assert quiet == []  # synchronous sinks (tests, local) don't meter


def test_background_sink_meters_every_three_seconds_by_default():
    assert EventSink("j", "u", "s", background=True).meter_interval == 3.0
    assert EventSink("j", "u", "s").meter_interval is None
