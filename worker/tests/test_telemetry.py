from renda_worker import telemetry


def _boom():
    try:
        raise KeyError("inner")
    except KeyError as e:
        raise RuntimeError("render exploded") from e


def test_exception_event_has_chain_and_frames():
    try:
        _boom()
    except RuntimeError as exc:
        ev = telemetry.exception_event(exc, "phc_test", job_id="job_1", engine=None)
    props = ev["properties"]
    assert ev["event"] == "$exception" and ev["api_key"] == "phc_test"
    assert [x["type"] for x in props["$exception_list"]] == ["RuntimeError", "KeyError"]
    frames = props["$exception_list"][0]["stacktrace"]["frames"]
    assert frames and frames[-1]["function"] == "_boom" and frames[-1]["lineno"] > 0
    assert props["job_id"] == "job_1" and "engine" not in props
    assert props["$process_person_profile"] is False


def test_capture_is_noop_without_key(monkeypatch):
    monkeypatch.delenv("POSTHOG_KEY", raising=False)
    calls = []
    monkeypatch.setattr("requests.post", lambda *a, **k: calls.append(a))
    telemetry.capture_exception(RuntimeError("x"))
    assert calls == []
