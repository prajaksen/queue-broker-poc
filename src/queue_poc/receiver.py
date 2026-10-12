"""The front door: commit the row, publish its id, answer 202.

    uv run uvicorn queue_poc.receiver:create_app --factory --port 8030
    curl -X POST localhost:8030/jobs -H 'content-type: application/json' -d '{"kind":"mail","value":"sleep=1"}'
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel, Field

from . import db, tasks


class Job(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    value: str = Field(max_length=2_000)


def create_app() -> FastAPI:
    db.init()
    app = FastAPI(title="Queue POC receiver")

    @app.post("/jobs", status_code=202)
    def jobs(job: Job) -> dict[str, int]:
        # The row is committed before the 202. If this raises, the caller gets a 5xx and retries.
        return {"inbox_id": tasks.enqueue(job.kind, job.value)}

    return app
