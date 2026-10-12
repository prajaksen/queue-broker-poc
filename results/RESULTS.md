# Results

**6 of 6 failure cases behaved as designed.**

Run 2026-10-12T20:21:13+00:00 on Darwin arm64, Python 3.13.15, Celery 5.6.3 (threads pool), Redis 7.4.10, Postgres 16.15.

This file is written by `python -m queue_poc.prove`. Open `report.html` in a browser for the same results with hover details and the full event tables.

| # | Case | Result | What was observed |
| --- | --- | --- | --- |
| 1 | A worker is killed mid-job | Passed | Row stayed 'running' after the kill. The sweep queued it again and a second worker finished it 12s later. attempts=2, finished=1. |
| 2 | Redis is wiped with jobs queued | Passed | Redis held 5 messages, then 0 after the wipe. The sweep published all 5 again from Postgres; each finished once (5 finishes). |
| 3 | A timed reminder comes due | Passed | Beat fired the reminder after its due time and wrote 1 outbox row, sent 1 time. A second insert with the same key was refused. |
| 4 | A slow queue has a backlog | Passed | 4 mail jobs finished in 0.4s. At that moment the extract queue had 1 running and 3 waiting. |
| 5 | The same job is delivered four times | Passed | 4 deliveries of the same row id: 1 claimed it, 3 were skipped. attempts=1, finished=1. |
| 6 | A job keeps failing | Passed | Failed 3 times with a longer wait before each retry, then marked 'dead' with the error kept: RuntimeError: simulated failure. |

## Timelines

One row per job, time left to right. Grey is waiting to run, blue is running, green is done, yellow is waiting to retry, red is set aside (done also carries a ✓, set aside a ✕). A thin tick is the row id being published to Redis, a small ring is a duplicate delivery that was skipped, and a dashed line is the fault being injected.

### 1. A worker is killed mid-job

- **What we did:** Started a 3-second job, then sent SIGKILL to the worker while it was running.
- **Expected:** The job runs again on another worker and finishes exactly once.
- **Observed:** Row stayed 'running' after the kill. The sweep queued it again and a second worker finished it 12s later. attempts=2, finished=1.

![Timeline for case 1](case-1.svg)

### 2. Redis is wiped with jobs queued

- **What we did:** Queued five jobs with no worker running, then deleted everything in Redis.
- **Expected:** All five jobs still finish, because their rows are in Postgres.
- **Observed:** Redis held 5 messages, then 0 after the wipe. The sweep published all 5 again from Postgres; each finished once (5 finishes).

![Timeline for case 2](case-2.svg)

### 3. A timed reminder comes due

- **What we did:** Stored a reminder due in 3 seconds, then tried to fire it twice and to add its send twice.
- **Expected:** It fires after the due time, from Postgres, and is sent once.
- **Observed:** Beat fired the reminder after its due time and wrote 1 outbox row, sent 1 time. A second insert with the same key was refused.

![Timeline for case 3](case-3.svg)

### 4. A slow queue has a backlog

- **What we did:** Queued four 3-second extraction jobs on a one-worker queue, then four short mail jobs.
- **Expected:** Mail finishes straight away while extraction is still working through its backlog.
- **Observed:** 4 mail jobs finished in 0.4s. At that moment the extract queue had 1 running and 3 waiting.

![Timeline for case 4](case-4.svg)

### 5. The same job is delivered four times

- **What we did:** Published one row id to the broker four times.
- **Expected:** One delivery claims the row; the other three are skipped.
- **Observed:** 4 deliveries of the same row id: 1 claimed it, 3 were skipped. attempts=1, finished=1.

![Timeline for case 5](case-5.svg)

### 6. A job keeps failing

- **What we did:** Queued a job that always raises, with the attempt limit set to 3.
- **Expected:** It is retried with a growing wait, then set aside for a person to look at.
- **Observed:** Failed 3 times with a longer wait before each retry, then marked 'dead' with the error kept: RuntimeError: simulated failure.

![Timeline for case 6](case-6.svg)
