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
