from __future__ import annotations

import os

DATABASE_URL = os.environ.get("QUEUE_POC_DATABASE_URL", "postgresql://queue:queue@127.0.0.1:55432/queue")
REDIS_URL = os.environ.get("QUEUE_POC_REDIS_URL", "redis://127.0.0.1:56379/0")

# A running job older than this is taken to belong to a dead worker and is queued again.
# It must be longer than the longest job. It is also set as Celery's hard time limit, which the prefork
# pool enforces and the threads pool does not; finish() and fail() are tied to the attempt for that reason.
STALE_SECONDS = int(os.environ.get("QUEUE_POC_STALE_SECONDS", "300"))
# A queued job whose broker message is older than this is published again.
REPUBLISH_SECONDS = int(os.environ.get("QUEUE_POC_REPUBLISH_SECONDS", "60"))
SWEEP_SECONDS = float(os.environ.get("QUEUE_POC_SWEEP_SECONDS", "15"))
REMINDER_SECONDS = float(os.environ.get("QUEUE_POC_REMINDER_SECONDS", "60"))
MAX_ATTEMPTS = int(os.environ.get("QUEUE_POC_MAX_ATTEMPTS", "5"))
MAX_BACKOFF_SECONDS = 300

# One queue per kind of work, so a slow kind cannot hold up the others.
QUEUE_FOR_KIND = {"mail": "mail", "extract": "extract", "browser": "browser"}
DEFAULT_QUEUE = "default"
OUTBOX_QUEUE = "outbox"
