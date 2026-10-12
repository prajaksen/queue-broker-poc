from __future__ import annotations

import pytest

from queue_poc import config, db  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    try:
        db.reset()
    except Exception as error:
        pytest.skip(f"Postgres is not running: {type(error).__name__}")


def test_a_job_can_be_claimed_once():
    job = db.accept("mail", "sleep=0")
    assert db.claim(job)["attempts"] == 1
    assert db.claim(job) is None


def test_finishing_twice_counts_one_effect():
    job = db.accept("mail", "sleep=0")
    db.claim(job)
    assert db.finish(job, 1) is True
    assert db.finish(job, 1) is False
    assert db.inbox(job)["effects"] == 1


def test_a_failed_job_waits_before_its_retry():
    job = db.accept("mail", "fail")
    db.claim(job)
    assert db.fail(job, 1, "boom") == "queued"
    assert db.claim(job) is None, "the retry is not due yet"
    assert db.take_unpublished() == []


def test_a_job_is_set_aside_after_the_last_attempt(monkeypatch):
    monkeypatch.setattr(config, "MAX_ATTEMPTS", 1)
    job = db.accept("mail", "fail")
    db.claim(job)
    assert db.fail(job, 1, "boom") == "dead"


def test_sweep_takes_an_unpublished_job_once_per_interval():
    job = db.accept("extract", "sleep=0")
    assert db.take_unpublished() == [{"id": job, "kind": "extract"}]
    assert db.take_unpublished() == []


def test_a_running_job_is_stale_only_after_the_limit(monkeypatch):
    job = db.accept("mail", "sleep=0")
    db.claim(job)
    assert db.reset_stale() == 0
    monkeypatch.setattr(config, "STALE_SECONDS", 0)
    assert db.reset_stale() == 1
    assert db.inbox(job)["status"] == "queued"


def test_outbox_refuses_a_second_row_with_the_same_key_and_sends_once():
    first = db.add_outbox("reminder:1", "follow-up", "case-1")
    assert first is not None
    assert db.add_outbox("reminder:1", "follow-up", "case-1") is None
    assert db.send_outbox(first) is True
    assert db.send_outbox(first) is False
    assert [row["sends"] for row in db.outbox_rows()] == [1]


def test_an_overrun_attempt_cannot_finish_or_fail_the_retry(monkeypatch):
    job = db.accept("mail", "sleep=0")
    first = db.claim(job)["attempts"]
    monkeypatch.setattr(config, "STALE_SECONDS", 0)
    db.reset_stale()
    second = db.claim(job)["attempts"]
    assert db.finish(job, first) is False
    assert db.fail(job, first, "late") == "gone"
    assert db.inbox(job)["status"] == "running"
    assert db.finish(job, second) is True


def test_a_due_reminder_and_its_outbox_row_are_written_together():
    db.add_reminder("case-9", -1)
    (outbox_id,) = db.fire_due_reminders()
    assert db.fire_due_reminders() == []
    assert [(row["id"], row["idempotency_key"], row["payload"]) for row in db.outbox_rows()] == [
        (outbox_id, "reminder:1", "case-9")
    ]


def test_every_state_change_is_recorded_as_an_event():
    job = db.accept("mail", "sleep=0")
    attempt = db.claim(job)["attempts"]
    db.finish(job, attempt)
    assert [(event["subject"], event["event"]) for event in db.events()] == [
        (f"job:{job}", "accepted"), (f"job:{job}", "claimed"), (f"job:{job}", "finished"),
    ]
