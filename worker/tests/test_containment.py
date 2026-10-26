"""Containment of a compromised render: per-job credentials, minimal subprocess env, durable events, heartbeat."""

import hashlib
import hmac
import json
import time

import pytest
from pydantic import ValidationError

from renda_worker import dispatch, pipeline
from renda_worker.events import DURABLE_MAX_DELAY_S, EventSink, sign_timed
from renda_worker.models import JobRequest
from renda_worker.procenv import untrusted_env
from renda_worker.storage import MemoryStorage

from .conftest import Clock, FakeCore, Recorder, job_body

JOB_KEY = hmac.new(b"master", b"job:job_1", hashlib.sha256).hexdigest()
GRANT = {"accessKeyId": "ak", "secretAccessKey": "sk", "sessionToken": "st", "bucket": "renda",
         "endpoint": "https://acct.r2.cloudflarestorage.com", "prefixes": ["outputs/job_1/", "work/job_1/"]}


# ---------------------------------------------------------------- durable delivery

def test_durable_events_retry_for_about_ten_minutes_with_capped_backoff():
    sleeps = []

    def down(*a):
        raise ConnectionError("web is deploying")

    sink = EventSink("j", "https://x", "k", poster=down, sleep=sleeps.append)
    assert sink.done([], cached=False) is False
    assert sum(sleeps) == pytest.approx(600.0)
    assert max(sleeps) == DURABLE_MAX_DELAY_S
    assert sleeps[:4] == [0.5, 1.0, 2.0, 4.0]


@pytest.mark.parametrize("call", [lambda s: s.usage("cpu", 1.0, 0), lambda s: s.failed("x")])
def test_usage_and_failed_are_durable_too(call):
    statuses = iter([503] * 12 + [200])  # well past the old 5-attempt limit
    sink = EventSink("j", "https://x", "k", poster=lambda *a: next(statuses), sleep=lambda s: None)
    assert call(sink) is True


def test_status_and_warning_keep_the_short_schedule():
    sleeps = []
    sink = EventSink("j", "https://x", "k", poster=lambda *a: 503, sleep=sleeps.append)
    assert sink.status("rendering") is False
    assert len(sleeps) == 4 and sum(sleeps) == 7.5


def test_durable_event_rejected_with_4xx_is_not_retried_or_parked():
    calls = []
    sink = EventSink("j", "https://x", "k", poster=lambda *a: calls.append(1) or 401, sleep=lambda s: None,
                     defer_durable=True)
    assert sink.usage("cpu", 1.0, 0) is False
    assert len(calls) == 1 and sink.undelivered == []


def test_deferred_usage_is_parked_then_redelivered_by_the_orchestrator():
    chunk = EventSink("job_1", "https://x", JOB_KEY, poster=lambda *a: 503, sleep=lambda s: None, defer_durable=True)
    assert chunk.usage("gpu-l4", 12.5, 3) is False
    assert chunk.undelivered == [{"type": "usage", "idempotency_key": "job_1:3",
                                  "fields": {"hardware": "gpu-l4", "seconds": 12.5, "chunk": 3}}]

    rec = Recorder()
    orch = EventSink("job_1", "https://x", JOB_KEY, poster=rec, sleep=lambda s: None)
    # a forged/foreign entry can't change the job id or smuggle another event type
    orch.redeliver(chunk.undelivered + [{"type": "done", "fields": {"outputs": []}},
                                        {"type": "usage", "idempotency_key": "job_1:4", "fields": {"jobId": "other", "chunk": 4,
                                                                                                    "hardware": "cpu", "seconds": 1}}])
    orch.done([], cached=False)  # flushes redeliveries first
    assert [e["type"] for e in rec.events] == ["usage", "usage", "done"]
    assert {e["jobId"] for e in rec.events} == {"job_1"}
    url, body, headers = rec.calls[0]
    assert headers["idempotency-key"] == "job_1:3"
    assert headers["x-renda-signature"] == sign_timed(body, int(headers["x-renda-timestamp"]), JOB_KEY)


def test_chunk_usage_that_failed_to_deliver_reaches_web_before_done(tmp_path):
    core = FakeCore(frames=[1, 2], chunks=[[1], [2]])
    rec = Recorder()

    def flaky_for_usage(url, body, headers):
        return 503 if json.loads(body)["type"] == "usage" else 200

    storage = MemoryStorage()

    def make(sink):
        return pipeline.Deps(core=core, storage=storage, events=sink, asset_root=tmp_path / "assets",
                             download=lambda u, d: d.write_bytes(b"solid teapot"), clock=Clock(), work_root=tmp_path / "w")

    orch = make(EventSink("job_1", "https://x", JOB_KEY, poster=rec, sleep=lambda s: None))

    def run_chunks(runner, tasks):
        for t in tasks:
            assert t.job_key == JOB_KEY and t.storage is None  # credentials travel with the task
            yield pipeline.run_chunk(t, make(EventSink("job_1", "https://x", t.job_key, poster=flaky_for_usage,
                                                       sleep=lambda s: None, defer_durable=True)))

    orch.run_chunks = run_chunks
    out = pipeline.run_scene_job(JobRequest.model_validate(job_body(jobKey=JOB_KEY)), orch)
    assert out["status"] == "succeeded"
    types = [e["type"] for e in rec.events]
    assert {e["chunk"] for e in rec.of("usage")} == {0, 1, "orchestrate"}
    assert max(i for i, t in enumerate(types) if t == "usage") < types.index("done")


# ---------------------------------------------------------------- heartbeat

def test_heartbeat_is_one_signed_best_effort_attempt():
    calls = []

    def poster(url, body, headers):
        calls.append((body, headers))
        raise ConnectionError("down")

    sink = EventSink("job_1", "https://x", JOB_KEY, poster=poster, clock=lambda: 1_700_000_000.0)
    assert sink.heartbeat() is False
    (body, headers), = calls
    assert json.loads(body) == {"jobId": "job_1", "type": "heartbeat"}
    assert headers["x-renda-signature"] == sign_timed(body, 1_700_000_000, JOB_KEY)


def test_background_sink_heartbeats_every_minute_by_default():
    assert EventSink("j", "u", "k", background=True).heartbeat_interval == 60.0
    assert EventSink("j", "u", "k").heartbeat_interval is None


def test_orchestrator_heartbeats_while_chunks_are_queued(make_deps, recorder, tmp_path):
    """No chunk has started (GPU backlog) and nothing else is sent, yet heartbeats keep flowing."""
    deps = make_deps(FakeCore())
    deps.events.heartbeat_interval = 0.02
    deps.clock = time.monotonic

    def queued_then_run(runner, tasks):
        time.sleep(0.15)  # waiting for a GPU
        return [pipeline.run_chunk(t, deps) for t in tasks]

    deps.run_chunks = queued_then_run
    out = pipeline.run_scene_job(JobRequest.model_validate(job_body()), deps)
    assert out["status"] == "succeeded"
    beats = len(recorder.of("heartbeat"))
    assert beats >= 3
    time.sleep(0.08)
    assert len(recorder.of("heartbeat")) == beats  # stopped once the job finished


# ---------------------------------------------------------------- outputs, cache, credentials

def test_outputs_outside_the_job_prefix_fail_the_job(make_deps, recorder):
    deps = make_deps(FakeCore())

    def evil(runner, tasks):
        return [dispatch.ChunkResult(0, [1], [{"key": "outputs/someone_else/secret.png", "contentType": "image/png", "bytes": 1}])]

    deps.run_chunks = evil
    out = pipeline.run_scene_job(JobRequest.model_validate(job_body()), deps)
    assert out["status"] == "failed" and recorder.of("done") == []


def test_shared_result_cache_is_off_by_default(make_deps, recorder):
    storage = MemoryStorage()
    storage.objects["outputs/old/frame_0001.png"] = (b"poisoned", "image/png")
    storage.put_json("cache/v1/ck1.json", {"outputs": [{"key": "outputs/old/frame_0001.png", "contentType": "image/png", "bytes": 8}]})
    deps = make_deps(FakeCore(), storage=storage)
    deps.run_chunks = lambda runner, tasks: [pipeline.run_chunk(t, deps) for t in tasks]
    out = pipeline.run_scene_job(JobRequest.model_validate(job_body()), deps)
    assert out["cached"] is False
    assert storage.objects["outputs/job_1/frame_0001.png"][0] != b"poisoned"
    assert [k for k in storage.objects if k.startswith("cache/")] == ["cache/v1/ck1.json"]  # nothing new written


def test_job_credentials_ride_on_every_task():
    job = JobRequest.model_validate(job_body(jobKey=JOB_KEY, storage=GRANT))
    creds = dispatch.job_credentials(job)
    assert creds["job_key"] == JOB_KEY and creds["storage"]["sessionToken"] == "st"
    train = dispatch.build_train_task(job, {"splat_bake": {"train_hardware": "gpu-l4"}}, "/assets")
    assert train.job_key == JOB_KEY and train.storage["accessKeyId"] == "ak"
    assert "sk" not in repr(job) and JOB_KEY not in repr(job) and JOB_KEY not in repr(train)


def test_scoped_credentials_require_the_jobs_own_output_prefix():
    with pytest.raises(ValidationError):
        JobRequest.model_validate(job_body(storage=GRANT, outputPrefix="outputs/other_job/"))
    with pytest.raises(ValidationError):
        JobRequest.model_validate(job_body(jobKey="short"))


def test_r2_client_from_grant_uses_the_session_token():
    from renda_worker.storage import R2Storage

    s = R2Storage.from_grant(JobRequest.model_validate(job_body(storage=GRANT)).storage)
    creds = s.s3._request_signer._credentials
    assert (creds.access_key, creds.token, s.bucket) == ("ak", "st", "renda")


# ---------------------------------------------------------------- subprocess env

def test_untrusted_env_is_an_allowlist_without_secrets():
    src = {"PATH": "/bin", "HOME": "/root", "R2_SECRET_ACCESS_KEY": "x", "RENDA_WORKER_SECRET": "x", "RENDA_WORKER_TOKEN": "x",
           "AWS_SESSION_TOKEN": "x", "MODAL_TASK_ID": "x", "MODAL_IDENTITY_TOKEN": "x", "POSTHOG_KEY": "x",
           "NVIDIA_DRIVER_CAPABILITIES": "all", "XDG_CACHE_HOME": "/cache/xdg", "HYPERFRAMES_TELEMETRY_DISABLED": "1",
           "XDG_SECRET_TOKEN": "x", "RANDOM_THING": "x"}
    env = untrusted_env(keep_prefixes=("HYPERFRAMES_",), source=src)
    assert set(env) == {"PATH", "HOME", "TMPDIR", "NVIDIA_DRIVER_CAPABILITIES", "XDG_CACHE_HOME", "HYPERFRAMES_TELEMETRY_DISABLED"}


def test_hyperframes_cli_gets_the_minimal_env(monkeypatch, tmp_path):
    import subprocess

    seen = {}
    monkeypatch.setenv("RENDA_WORKER_SECRET", "master")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: seen.update(k) or subprocess.CompletedProcess(a, 0, "", ""))
    pipeline._subprocess_runner(["hyperframes", "render"], tmp_path, 10)
    assert "RENDA_WORKER_SECRET" not in seen["env"] and "PATH" in seen["env"]
