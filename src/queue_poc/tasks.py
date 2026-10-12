from __future__ import annotations

import logging
import time
from typing import Any

from celery import Celery

from . import config, db

log = logging.getLogger("queue_poc")

app = Celery("queue_poc", broker=config.REDIS_URL)
app.conf.update(
    # Acknowledge after the work, so a job on a worker that dies is not silently dropped.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Take one job at a time; a worker must not sit on jobs another worker could run.
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    task_default_queue=config.DEFAULT_QUEUE,
    task_time_limit=config.STALE_SECONDS,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 3600},
    timezone="UTC",
    beat_schedule={
        "sweep": {"task": "queue_poc.sweep", "schedule": config.SWEEP_SECONDS},
        "reminders": {"task": "queue_poc.fire_reminders", "schedule": config.REMINDER_SECONDS},
    },
)


def publish(inbox_id: int, kind: str) -> None:
    queue = config.QUEUE_FOR_KIND.get(kind, config.DEFAULT_QUEUE)
    process_inbox.apply_async((inbox_id,), queue=queue)


def enqueue(kind: str, value: str) -> int:
    """Store the job, then tell the broker. A failed publish is repaired by the sweep."""
    inbox_id = db.accept(kind, value)
    try:
        publish(inbox_id, kind)
        db.mark_enqueued(inbox_id)
    except Exception:
        log.exception("could not publish inbox %s; the sweep will pick it up", inbox_id)
    return inbox_id


def work(row: dict[str, Any]) -> None:
    """Stand-in for real work. value is 'sleep=<seconds>' or 'fail'."""
    name, _, argument = row["value"].partition("=")
    if name == "fail":
        raise RuntimeError("simulated failure")
    if name == "sleep":
        time.sleep(float(argument))


@app.task(name="queue_poc.process_inbox")
def process_inbox(inbox_id: int) -> str:
    row = db.claim(inbox_id)
    if row is None:
        db.mark(f"job:{inbox_id}", "duplicate", "delivery skipped: the row was not claimable")
        return "skipped"
    try:
        work(row)
    except Exception as error:
        return db.fail(inbox_id, row["attempts"], f"{type(error).__name__}: {error}")
    return "done" if db.finish(inbox_id, row["attempts"]) else "lost"


@app.task(name="queue_poc.sweep")
def sweep() -> dict[str, int]:
    stale = db.reset_stale()
    rows = db.take_unpublished()
    for row in rows:
        publish(row["id"], row["kind"])
    for outbox_id in db.pending_outbox():
        send_outbox.apply_async((outbox_id,), queue=config.OUTBOX_QUEUE)
    return {"stale": stale, "published": len(rows)}


@app.task(name="queue_poc.fire_reminders")
def fire_reminders() -> int:
    outbox_ids = db.fire_due_reminders()
    for outbox_id in outbox_ids:
        send_outbox.apply_async((outbox_id,), queue=config.OUTBOX_QUEUE)
    return len(outbox_ids)


@app.task(name="queue_poc.send_outbox")
def send_outbox(outbox_id: int) -> str:
    return "sent" if db.send_outbox(outbox_id) else "skipped"
