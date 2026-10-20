"""Event sink for isolated worker benchmarks (never production): records each job's worker events in a
Modal Dict so `bench_wall.py --sink` can report billed seconds per hardware class, including the trainer.

    RENDA_SINK_APP=renda-bench-sink-x uv run modal deploy scripts/bench_sink.py
"""

import os

import modal

NAME = os.environ.get("RENDA_SINK_APP", "renda-bench-sink")
EVENTS = "renda-bench-sink-events"  # fixed: containers don't see deploy-time env, so the Dict name can't depend on it
app = modal.App(NAME)
events = modal.Dict.from_name(EVENTS, create_if_missing=True)
image = modal.Image.debian_slim().pip_install("fastapi[standard]")


@app.function(image=image)
@modal.fastapi_endpoint(method="POST")
async def sink(request: dict):
    import time
    import uuid

    # one key per event: chunks post concurrently, so a read-modify-write list per job would drop events
    events[f"{request.get('jobId', '?')}|{time.time_ns()}|{uuid.uuid4().hex[:6]}"] = request
    return {"ok": True}
