from __future__ import annotations

import json

from queue_poc import report

CASE = {
    "number": 1, "title": "A <worker> is killed", "did": "d", "expect": "e", "observed": "o", "passed": True,
    "seconds": 10.0,
    "events": [
        {"subject": "job:1", "event": "accepted", "detail": "mail sleep=3", "t": 0.0},
        {"subject": "job:1", "event": "published", "detail": "", "t": 0.1},
        {"subject": "job:1", "event": "claimed", "detail": "attempt 1", "t": 0.2},
        {"subject": "chaos", "event": "chaos", "detail": "worker killed", "t": 1.0},
        {"subject": "job:1", "event": "stale", "detail": "worker stopped mid-job", "t": 7.0},
        {"subject": "job:1", "event": "claimed", "detail": "attempt 2", "t": 7.5},
        {"subject": "job:1", "event": "finished", "detail": "", "t": 9.0},
    ],
    "jobs": [{"id": 1, "kind": "mail", "value": "sleep=3", "status": "done", "attempts": 2, "effects": 1, "last_error": None}],
    "outbox": [],
}
RESULTS = {
    "environment": {
        "ran_at": "2026-01-01T00:00:00+00:00", "platform": "Linux x86_64", "python": "3.13", "celery": "5",
        "pool": "prefork", "redis_server": "7", "postgres_server": "16",
        "settings": {"stale_seconds": 6, "republish_seconds": 4, "sweep_seconds": 2.0, "reminder_seconds": 2.0},
    },
    "cases": [CASE],
}


def test_a_lane_is_cut_into_one_segment_per_state():
    (lane,) = report.lanes(CASE)
    assert lane["label"] == "job 1 (mail)"
    assert [(s["state"], s["start"], s["end"]) for s in lane["segments"]] == [
        ("queued", 0.0, 0.2), ("running", 0.2, 7.0), ("queued", 7.0, 7.5), ("running", 7.5, 9.0), ("done", 9.0, 10.0),
    ]
    assert [point["kind"] for point in lane["points"]] == ["published"]


def test_build_writes_every_output_and_escapes_text(tmp_path):
    results = tmp_path / "results"
    results.mkdir()
    (results / "results.json").write_text(json.dumps(RESULTS))
    page = report.build(results)
    assert page == tmp_path / "README.md"
    assert sorted(p.name for p in results.iterdir()) == ["case-1.svg", "design.svg", "legend.svg", "results.json"]
    text = page.read_text()
    assert "### 1. A &lt;worker&gt; is killed: passed" in text and "<worker>" not in text
    assert "# 1 of 1" in text
    assert "![Timeline for case 1](results/case-1.svg)" in text
    assert "| 1.00s | chaos | chaos | worker killed |" in text
    assert "worker killed at 1.0s" in (results / "case-1.svg").read_text()
