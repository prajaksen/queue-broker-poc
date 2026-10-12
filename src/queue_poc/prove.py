"""Runs the failure cases against real Postgres, Redis and Celery, then writes the results and the report.

    docker compose up -d --wait
    uv run python -m queue_poc.prove
"""
from __future__ import annotations

import datetime
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Short timings so the run takes about a minute. Set before config is imported; workers inherit them.
os.environ.setdefault("QUEUE_POC_STALE_SECONDS", "6")
os.environ.setdefault("QUEUE_POC_REPUBLISH_SECONDS", "4")
os.environ.setdefault("QUEUE_POC_SWEEP_SECONDS", "2")
os.environ.setdefault("QUEUE_POC_REMINDER_SECONDS", "2")

import celery as celery_package  # noqa: E402
import psycopg  # noqa: E402
import redis  # noqa: E402

from . import config, db, report, tasks  # noqa: E402

# Celery's default prefork pool fails on macOS with Python 3.13 (child processes start without the task
# table), so a laptop run uses threads. Linux containers use prefork.
POOL = os.environ.get("QUEUE_POC_POOL", "threads" if sys.platform == "darwin" else "prefork")
RESULTS = Path(os.environ.get("QUEUE_POC_RESULTS_DIR", "results"))
SCRATCH = Path(tempfile.mkdtemp(prefix="queue-poc-"))
running: list[subprocess.Popen[bytes]] = []
started_at = 0.0


def celery(*arguments: str) -> subprocess.Popen[bytes]:
    log = open(SCRATCH / f"celery-{len(list(SCRATCH.iterdir()))}.log", "wb")
    command = [sys.executable, "-m", "celery", "-A", "queue_poc.tasks", *arguments, "--loglevel=WARNING"]
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    running.append(process)
    return process


def worker(name: str, queues: str, concurrency: int) -> subprocess.Popen[bytes]:
    return celery(
        "worker", "-n", f"{name}@%h", "-Q", queues, "-c", str(concurrency), "-P", POOL,
        "--without-gossip", "--without-mingle",
    )


def beat() -> subprocess.Popen[bytes]:
    return celery("beat", "-s", str(SCRATCH / "beat-schedule"))


def kill(process: subprocess.Popen[bytes]) -> None:
    """SIGKILL the worker and its children: no chance to finish or hand the job back."""
    os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    running.remove(process)
    db.mark("chaos", "chaos", "worker killed")


def stop_all() -> None:
    for process in list(running):
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
    for process in list(running):
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    running.clear()


def wait_for(condition: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.2)
    return condition()


def statuses(ids: list[int]) -> list[str]:
    return [row["status"] for row in db.inbox_rows(ids)]


def all_done(ids: list[int]) -> bool:
    return all(status == "done" for status in statuses(ids))


def broker() -> redis.Redis:
    return redis.Redis.from_url(config.REDIS_URL)


def fresh() -> None:
    """Empty Postgres and Redis and start the case clock. Workers started before this are stopped."""
    global started_at
    stop_all()
    db.reset()
    broker().flushall()
    started_at = db.now()


def begin_after_warm_up(seconds: float = 4) -> None:
    """Let the workers connect, then restart the clock so the timeline starts at the first job."""
    global started_at
    time.sleep(seconds)
    db.reset()
    started_at = db.now()


Outcome = tuple[bool, str]


def worker_killed_mid_job() -> Outcome:
    fresh()
    victim = worker("mail-a", "mail", 1)
    begin_after_warm_up()
    job = tasks.enqueue("mail", "sleep=3")
    started = wait_for(lambda: statuses([job]) == ["running"], 20)
    time.sleep(1)
    kill(victim)
    after_kill = db.inbox(job)
    worker("mail-b", "mail", 1)
    worker("core", "default,outbox", 1)
    beat()
    began = time.monotonic()
    recovered = wait_for(lambda: all_done([job]), 40)
    row = db.inbox(job)
    return (
        bool(started and recovered and row["effects"] == 1 and row["attempts"] == 2),
        f"Row stayed '{after_kill['status']}' after the kill. The sweep queued it again and a second worker "
        f"finished it {time.monotonic() - began:.0f}s later. attempts={row['attempts']}, finished={row['effects']}.",
    )


def broker_wiped_with_jobs_queued() -> Outcome:
    fresh()
    jobs = [tasks.enqueue("mail", "sleep=0.1") for _ in range(5)]
    queued_before = broker().llen("mail")
    time.sleep(1)
    broker().flushall()
    db.mark("chaos", "chaos", "Redis wiped")
    queued_after = broker().llen("mail")
    worker("mail", "mail", 4)
    worker("core", "default,outbox", 1)
    beat()
    recovered = wait_for(lambda: all_done(jobs), 40)
    rows = db.inbox_rows(jobs)
    return (
        bool(queued_before == 5 and queued_after == 0 and recovered and all(r["effects"] == 1 for r in rows)),
        f"Redis held {queued_before} messages, then {queued_after} after the wipe. The sweep published all "
        f"{len(jobs)} again from Postgres; each finished once ({sum(r['effects'] for r in rows)} finishes).",
    )


def reminder_fires_once() -> Outcome:
    fresh()
    worker("core", "default,outbox", 2)
    beat()
    begin_after_warm_up()
    db.add_reminder("case-17", 3)
    fired = wait_for(lambda: [r["status"] for r in db.outbox_rows()] == ["sent"], 30)
    tasks.fire_reminders.apply_async(queue=config.DEFAULT_QUEUE)
    duplicate_key_refused = db.add_outbox("reminder:1", "follow-up", "case-17") is None
    time.sleep(3)
    rows = db.outbox_rows()
    return (
        bool(fired and len(rows) == 1 and rows[0]["sends"] == 1 and duplicate_key_refused),
        f"Beat fired the reminder after its due time and wrote {len(rows)} outbox row, sent "
        f"{rows[0]['sends'] if rows else 0} time. A second insert with the same key was "
        f"{'refused' if duplicate_key_refused else 'ACCEPTED'}.",
    )


def slow_queue_does_not_block_mail() -> Outcome:
    fresh()
    worker("extract", "extract", 1)
    worker("mail", "mail", 4)
    begin_after_warm_up()
    slow = [tasks.enqueue("extract", "sleep=3") for _ in range(4)]
    began = time.monotonic()
    mail = [tasks.enqueue("mail", "sleep=0.2") for _ in range(4)]
    mail_done = wait_for(lambda: all_done(mail), 10)
    mail_seconds = time.monotonic() - began
    slow_then = statuses(slow)
    wait_for(lambda: all_done(slow), 20)
    return (
        bool(mail_done and mail_seconds < 3 and "queued" in slow_then),
        f"4 mail jobs finished in {mail_seconds:.1f}s. At that moment the extract queue had "
        f"{slow_then.count('running')} running and {slow_then.count('queued')} waiting.",
    )


def duplicate_delivery_runs_once() -> Outcome:
    fresh()
    worker("mail", "mail", 4)
    begin_after_warm_up()
    job = tasks.enqueue("mail", "sleep=1")
    for _ in range(3):
        tasks.publish(job, "mail")
    wait_for(lambda: all_done([job]), 15)
    time.sleep(2)
    row = db.inbox(job)
    skipped = sum(1 for event in db.events() if event["event"] == "duplicate")
    return (
        bool(row["status"] == "done" and row["effects"] == 1 and row["attempts"] == 1 and skipped == 3),
        f"4 deliveries of the same row id: 1 claimed it, {skipped} were skipped. attempts={row['attempts']}, "
        f"finished={row['effects']}.",
    )


def failing_job_is_set_aside() -> Outcome:
    fresh()
    os.environ["QUEUE_POC_MAX_ATTEMPTS"] = "3"
    try:
        worker("mail", "mail", 1)
        worker("core", "default,outbox", 1)
        beat()
        begin_after_warm_up()
        job = tasks.enqueue("mail", "fail")
        dead = wait_for(lambda: statuses([job]) == ["dead"], 60)
        row = db.inbox(job)
    finally:
        del os.environ["QUEUE_POC_MAX_ATTEMPTS"]
    return (
        bool(dead and row["attempts"] == 3 and row["effects"] == 0),
        f"Failed {row['attempts']} times with a longer wait before each retry, then marked '{row['status']}' "
        f"with the error kept: {row['last_error']}.",
    )


CASES: list[dict[str, Any]] = [
    {
        "run": worker_killed_mid_job,
        "title": "A worker is killed mid-job",
        "did": "Started a 3-second job, then sent SIGKILL to the worker while it was running.",
        "expect": "The job runs again on another worker and finishes exactly once.",
    },
    {
        "run": broker_wiped_with_jobs_queued,
        "title": "Redis is wiped with jobs queued",
        "did": "Queued five jobs with no worker running, then deleted everything in Redis.",
        "expect": "All five jobs still finish, because their rows are in Postgres.",
    },
    {
        "run": reminder_fires_once,
        "title": "A timed reminder comes due",
        "did": "Stored a reminder due in 3 seconds, then tried to fire it twice and to add its send twice.",
        "expect": "It fires after the due time, from Postgres, and is sent once.",
    },
    {
        "run": slow_queue_does_not_block_mail,
        "title": "A slow queue has a backlog",
        "did": "Queued four 3-second extraction jobs on a one-worker queue, then four short mail jobs.",
        "expect": "Mail finishes straight away while extraction is still working through its backlog.",
    },
    {
        "run": duplicate_delivery_runs_once,
        "title": "The same job is delivered four times",
        "did": "Published one row id to the broker four times.",
        "expect": "One delivery claims the row; the other three are skipped.",
    },
    {
        "run": failing_job_is_set_aside,
        "title": "A job keeps failing",
        "did": "Queued a job that always raises, with the attempt limit set to 3.",
        "expect": "It is retried with a growing wait, then set aside for a person to look at.",
    },
]


def environment() -> dict[str, Any]:
    with psycopg.connect(config.DATABASE_URL) as connection:
        postgres = connection.execute("SHOW server_version").fetchone()[0]
    return {
        "ran_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "platform": f"{platform.system()} {platform.machine()}",
        "python": platform.python_version(),
        "celery": celery_package.__version__,
        "pool": POOL,
        "redis_server": broker().info("server")["redis_version"],
        "postgres_server": postgres,
        "settings": {
            "stale_seconds": config.STALE_SECONDS,
            "republish_seconds": config.REPUBLISH_SECONDS,
            "sweep_seconds": config.SWEEP_SECONDS,
            "reminder_seconds": config.REMINDER_SECONDS,
        },
    }


def snapshot() -> dict[str, Any]:
    ended = db.now()
    return {
        "seconds": round(ended - started_at, 2),
        "events": [
            {"subject": e["subject"], "event": e["event"], "detail": e["detail"], "t": round(max(e["t"] - started_at, 0), 3)}
            for e in db.events()
        ],
        "jobs": [
            {key: row[key] for key in ("id", "kind", "value", "status", "attempts", "effects", "last_error")}
            for row in db.inbox_rows()
        ],
        "outbox": [{key: row[key] for key in ("id", "idempotency_key", "status", "sends")} for row in db.outbox_rows()],
    }


def main() -> int:
    try:
        db.reset()
        broker().ping()
    except Exception as error:
        print(f"Postgres or Redis is not reachable ({type(error).__name__}: {error}).\nRun: docker compose up -d --wait")
        return 2
    cases = []
    try:
        for number, case in enumerate(CASES, start=1):
            passed, observed = case["run"]()
            cases.append(
                {"number": number, "title": case["title"], "did": case["did"], "expect": case["expect"],
                 "passed": passed, "observed": observed, **snapshot()}
            )
            print(f"{'PASS' if passed else 'FAIL'}  {number} {case['title']}\n      {observed}", flush=True)
    finally:
        stop_all()
    RESULTS.mkdir(parents=True, exist_ok=True)
    results = {"environment": environment(), "cases": cases}
    (RESULTS / "results.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    written = report.build(RESULTS)
    failed = [case for case in cases if not case["passed"]]
    print(f"\n{len(cases) - len(failed)} of {len(cases)} passed.")
    print(f"Report: {written}\nWorker logs: {SCRATCH}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
