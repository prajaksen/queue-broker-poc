from __future__ import annotations

import os

DATABASE_URL = os.environ.get("QUEUE_POC_DATABASE_URL", "postgresql://queue:queue@127.0.0.1:55432/queue")
REDIS_URL = os.environ.get("QUEUE_POC_REDIS_URL", "redis://127.0.0.1:56379/0")

STALE_SECONDS = int(os.environ.get("QUEUE_POC_STALE_SECONDS", "300"))
REPUBLISH_SECONDS = int(os.environ.get("QUEUE_POC_REPUBLISH_SECONDS", "60"))
SWEEP_SECONDS = float(os.environ.get("QUEUE_POC_SWEEP_SECONDS", "15"))
REMINDER_SECONDS = float(os.environ.get("QUEUE_POC_REMINDER_SECONDS", "60"))
MAX_ATTEMPTS = int(os.environ.get("QUEUE_POC_MAX_ATTEMPTS", "5"))
MAX_BACKOFF_SECONDS = 300

QUEUE_FOR_KIND = {"mail": "mail", "extract": "extract", "browser": "browser"}
DEFAULT_QUEUE = "default"
OUTBOX_QUEUE = "outbox"
