"""Postgres is the record. Every state change also writes an `events` row in the same statement,
which is what the report draws its timelines from."""
from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox (
    id BIGSERIAL PRIMARY KEY,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    effects INTEGER NOT NULL DEFAULT 0,
    run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
    enqueued_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS inbox_open ON inbox(status, run_after) WHERE status IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS outbox (
    id BIGSERIAL PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    sends INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS reminders (
    id BIGSERIAL PRIMARY KEY,
    case_ref TEXT NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'waiting',
    fired_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    subject TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""


def _connect() -> psycopg.Connection[dict[str, Any]]:
    return psycopg.connect(config.DATABASE_URL, row_factory=dict_row)


def _all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _connect() as connection:
        cursor = connection.execute(sql, params)
        return cursor.fetchall() if cursor.description else []


def _one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = _all(sql, params)
    return rows[0] if rows else None


def init() -> None:
    with _connect() as connection:
        connection.execute(_SCHEMA)


def reset() -> None:
    init()
    _all("TRUNCATE inbox, outbox, reminders, events RESTART IDENTITY")


def now() -> float:
    row = _one("SELECT extract(epoch FROM clock_timestamp())::float8 AS t")
    assert row is not None
    return row["t"]


def mark(subject: str, event: str, detail: str = "") -> None:
    _all("INSERT INTO events(subject, event, detail) VALUES(%s, %s, %s)", (subject, event, detail))


def events() -> list[dict[str, Any]]:
    return _all("SELECT subject, event, detail, extract(epoch FROM at)::float8 AS t FROM events ORDER BY id")


# Inbox: the record of work that was accepted. The broker only carries the row id.


def accept(kind: str, value: str) -> int:
    row = _one(
        "WITH new AS (INSERT INTO inbox(kind, value) VALUES(%s, %s) RETURNING id), "
        "logged AS (INSERT INTO events(subject, event, detail) SELECT 'job:' || id, 'accepted', %s FROM new) "
        "SELECT id FROM new",
        (kind, value, f"{kind} {value}"),
    )
    assert row is not None
    return row["id"]


def mark_enqueued(inbox_id: int) -> None:
    _all(
        "WITH changed AS (UPDATE inbox SET enqueued_at = now() WHERE id = %s RETURNING id) "
        "INSERT INTO events(subject, event) SELECT 'job:' || id, 'published' FROM changed",
        (inbox_id,),
    )


def claim(inbox_id: int) -> dict[str, Any] | None:
    return _one(
        "WITH changed AS ("
        "  UPDATE inbox SET status = 'running', attempts = attempts + 1, started_at = now() "
        "  WHERE id = %s AND status = 'queued' AND run_after <= now() RETURNING *"
        "), logged AS ("
        "  INSERT INTO events(subject, event, detail) SELECT 'job:' || id, 'claimed', 'attempt ' || attempts FROM changed"
        ") SELECT * FROM changed",
        (inbox_id,),
    )


def finish(inbox_id: int, attempt: int) -> bool:
    row = _one(
        "WITH changed AS ("
        "  UPDATE inbox SET status = 'done', finished_at = now(), effects = effects + 1 "
        "  WHERE id = %s AND status = 'running' AND attempts = %s RETURNING id"
        "), logged AS (INSERT INTO events(subject, event) SELECT 'job:' || id, 'finished' FROM changed) "
        "SELECT id FROM changed",
        (inbox_id, attempt),
    )
    return row is not None


def fail(inbox_id: int, attempt: int, error: str) -> str:
    row = _one(
        "WITH changed AS ("
        "  UPDATE inbox SET last_error = %s, enqueued_at = NULL, "
        "  status = CASE WHEN attempts >= %s THEN 'dead' ELSE 'queued' END, "
        "  run_after = now() + make_interval(secs => LEAST(power(2, attempts), %s)) "
        "  WHERE id = %s AND status = 'running' AND attempts = %s RETURNING id, status, last_error"
        "), logged AS ("
        "  INSERT INTO events(subject, event, detail) "
        "  SELECT 'job:' || id, CASE status WHEN 'dead' THEN 'dead' ELSE 'retry' END, last_error FROM changed"
        ") SELECT status FROM changed",
        (error[:500], config.MAX_ATTEMPTS, config.MAX_BACKOFF_SECONDS, inbox_id, attempt),
    )
    return row["status"] if row else "gone"


def reset_stale() -> int:
    rows = _all(
        "WITH changed AS ("
        "  UPDATE inbox SET enqueued_at = NULL, last_error = 'worker stopped mid-job', "
        "  status = CASE WHEN attempts >= %s THEN 'dead' ELSE 'queued' END "
        "  WHERE status = 'running' AND started_at < now() - make_interval(secs => %s) RETURNING id, status"
        "), logged AS ("
        "  INSERT INTO events(subject, event, detail) "
        "  SELECT 'job:' || id, CASE status WHEN 'dead' THEN 'dead' ELSE 'stale' END, 'worker stopped mid-job' FROM changed"
        ") SELECT id FROM changed",
        (config.MAX_ATTEMPTS, config.STALE_SECONDS),
    )
    return len(rows)


def take_unpublished() -> list[dict[str, Any]]:
    return _all(
        "WITH changed AS ("
        "  UPDATE inbox SET enqueued_at = now() WHERE id IN ("
        "    SELECT id FROM inbox WHERE status = 'queued' AND run_after <= now() "
        "    AND (enqueued_at IS NULL OR enqueued_at < now() - make_interval(secs => %s)) "
        "    ORDER BY id LIMIT 500 FOR UPDATE SKIP LOCKED"
        "  ) RETURNING id, kind"
        "), logged AS ("
        "  INSERT INTO events(subject, event, detail) SELECT 'job:' || id, 'published', 'by the sweep' FROM changed"
        ") SELECT id, kind FROM changed ORDER BY id",
        (config.REPUBLISH_SECONDS,),
    )


def inbox(inbox_id: int) -> dict[str, Any] | None:
    return _one("SELECT * FROM inbox WHERE id = %s", (inbox_id,))


def inbox_rows(ids: list[int] | None = None) -> list[dict[str, Any]]:
    if ids is None:
        return _all("SELECT * FROM inbox ORDER BY id")
    return _all("SELECT * FROM inbox WHERE id = ANY(%s) ORDER BY id", (ids,))


# Outbox: something that must leave the system exactly once.


def add_outbox(idempotency_key: str, kind: str, payload: str) -> int | None:
    row = _one(
        "WITH new AS ("
        "  INSERT INTO outbox(idempotency_key, kind, payload) VALUES(%s, %s, %s) "
        "  ON CONFLICT (idempotency_key) DO NOTHING RETURNING id, idempotency_key"
        "), logged AS ("
        "  INSERT INTO events(subject, event, detail) SELECT 'outbox:' || id, 'outbox_added', idempotency_key FROM new"
        ") SELECT id FROM new",
        (idempotency_key, kind, payload),
    )
    return row["id"] if row else None


def send_outbox(outbox_id: int) -> bool:
    # The real worker calls the external system between claim and mark, passing the idempotency key.
    row = _one(
        "WITH changed AS ("
        "  UPDATE outbox SET status = 'sent', sent_at = now(), sends = sends + 1 "
        "  WHERE id = %s AND status = 'pending' RETURNING id"
        "), logged AS (INSERT INTO events(subject, event) SELECT 'outbox:' || id, 'sent' FROM changed) "
        "SELECT id FROM changed",
        (outbox_id,),
    )
    return row is not None


def pending_outbox() -> list[int]:
    return [row["id"] for row in _all("SELECT id FROM outbox WHERE status = 'pending' ORDER BY id")]


def outbox_rows() -> list[dict[str, Any]]:
    return _all("SELECT * FROM outbox ORDER BY id")


# Reminders: a due time in Postgres, not a long countdown in the broker.


def add_reminder(case_ref: str, seconds_from_now: float) -> int:
    row = _one(
        "WITH new AS ("
        "  INSERT INTO reminders(case_ref, due_at) VALUES(%s, now() + make_interval(secs => %s)) RETURNING id"
        "), logged AS ("
        "  INSERT INTO events(subject, event, detail) SELECT 'reminder:' || id, 'reminder_set', %s FROM new"
        ") SELECT id FROM new",
        (case_ref, seconds_from_now, f"due in {seconds_from_now:g}s"),
    )
    assert row is not None
    return row["id"]


def fire_due_reminders() -> list[int]:
    """Marks due reminders fired and writes their outbox rows in one statement, so neither can exist alone."""
    rows = _all(
        "WITH fired AS ("
        "  UPDATE reminders SET status = 'fired', fired_at = now() "
        "  WHERE status = 'waiting' AND due_at <= now() RETURNING id, case_ref"
        "), box AS ("
        "  INSERT INTO outbox(idempotency_key, kind, payload) "
        "  SELECT 'reminder:' || id, 'follow-up', case_ref FROM fired "
        "  ON CONFLICT (idempotency_key) DO NOTHING RETURNING id, idempotency_key"
        "), logged_fired AS ("
        "  INSERT INTO events(subject, event) SELECT 'reminder:' || id, 'fired' FROM fired"
        "), logged_box AS ("
        "  INSERT INTO events(subject, event, detail) SELECT 'outbox:' || id, 'outbox_added', idempotency_key FROM box"
        ") SELECT id FROM box"
    )
    return [row["id"] for row in rows]
