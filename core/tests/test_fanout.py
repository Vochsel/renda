"""Fan-out economics: shared fixture with the web estimator (spec/fixtures/estimator/fanout.json)."""

import json
from pathlib import Path

import pytest

from renda.planner import MAX_CONTAINERS, chunk_frames, fanout_count, wall_seconds

FIXTURE = Path(__file__).parents[2] / "spec" / "fixtures" / "estimator" / "fanout.json"


@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text())["cases"])
def test_fanout_matches_shared_fixture(case):
    got = fanout_count(case["frames"], case["loadSeconds"], case["frameSeconds"], case["priority"])
    assert got == case["containers"]


def test_rule_properties():
    # standard: load stays ≤ 10% of every chunk's work
    for n, load, frame in [(240, 7, 4), (500, 5, 0.8), (64, 8, 12)]:
        chunks = chunk_frames(list(range(n)), load, frame, "standard")
        if len(chunks) > 1:
            assert all(load / (len(c) * frame + load) <= 0.10 + 1e-9 for c in chunks[:-1])
    # economy: fewest containers; rush: at least as wide as standard, capped
    assert fanout_count(240, 7, 4, "economy") <= fanout_count(240, 7, 4, "standard") <= fanout_count(240, 7, 4, "rush")
    assert fanout_count(100_000, 1, 1, "rush") == MAX_CONTAINERS["rush"]
    # wall time falls as containers rise
    frames = list(range(240))
    w = {p: wall_seconds(chunk_frames(frames, 7, 4, p), 7, 4) for p in ("economy", "standard", "rush")}
    assert w["rush"] < w["standard"] < w["economy"]
