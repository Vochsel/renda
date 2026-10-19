import hashlib
import hmac
import json

from renda_worker.events import EventSink, encode, sign, verify


def test_sign_matches_reference_hmac():
    body = b'{"jobId":"j","type":"status"}'
    expected = "sha256=" + hmac.new(b"k", body, hashlib.sha256).hexdigest()
    assert sign(body, "k") == expected
    assert verify(body, "k", expected)
    assert not verify(body, "other", expected)
    assert not verify(body, "k", "")


def test_emitted_event_is_signed_over_exact_body(recorder):
    EventSink("j1", "https://x/cb", "sec", poster=recorder).usage("gpu-l4", 37.21234, 3)
    url, body, headers = recorder.calls[0]
    assert url == "https://x/cb"
    assert verify(body, "sec", headers["x-renda-signature"])
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
