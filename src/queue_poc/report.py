from __future__ import annotations

import json
import math
import sys
from html import escape
from pathlib import Path
from typing import Any

STATE_AFTER = {
    "accepted": "queued", "reminder_set": "queued", "outbox_added": "queued", "stale": "queued",
    "claimed": "running", "retry": "waiting", "dead": "dead",
    "finished": "done", "fired": "done", "sent": "done",
}
STATE_LABEL = {
    "queued": "Waiting to run", "running": "Running", "waiting": "Waiting to retry",
    "done": "Done", "dead": "Set aside",
}
WORD = {"done": "done", "dead": "set aside"}
COLOR = {
    "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
    "axis": "#c3c2b7", "queued": "#b4b2a9", "running": "#2a78d6", "waiting": "#fab219", "done": "#0ca30c",
    "dead": "#d03b3b",
}
CSS = (
    f".bg{{fill:{COLOR['surface']}}}.ink{{fill:{COLOR['ink']}}}.ink2{{fill:{COLOR['ink2']}}}"
    f".muted{{fill:{COLOR['muted']}}}.onbar{{fill:{COLOR['surface']}}}"
    f".grid{{stroke:{COLOR['grid']}}}.axis{{stroke:{COLOR['axis']}}}.tick{{stroke:{COLOR['muted']}}}"
    f".chaos{{stroke:{COLOR['ink']}}}.ring{{stroke:{COLOR['ink']}}}.wire{{stroke:{COLOR['ink2']}}}"
    f".head{{fill:{COLOR['ink2']}}}.box{{fill:none;stroke:{COLOR['axis']};stroke-width:1.5}}"
    f".strong{{stroke:{COLOR['running']};stroke-width:2}}"
    + "".join(f".s-{state}{{fill:{COLOR[state]}}}" for state in STATE_LABEL)
    + "text{font:12px system-ui,-apple-system,'Segoe UI',sans-serif}"
)

WIDTH, LEFT, RIGHT, TOP, PITCH, BAR, AXIS = 760, 124, 70, 34, 24, 14, 30


def _svg(height: int, label: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}" '
        f'role="img" aria-label="{escape(label)}"><style>{CSS}</style>'
        f'<rect class="bg" width="{WIDTH}" height="{height}" rx="8"/>{body}</svg>'
    )


def _lane_label(subject: str, jobs: dict[int, dict[str, Any]]) -> str:
    kind, _, number = subject.partition(":")
    if kind == "job":
        return f"job {number} ({jobs.get(int(number), {}).get('kind', '')})"
    return f"{kind} {number}"


def _axis_step(end: float) -> float:
    for step in (0.5, 1, 2, 5, 10, 20, 30, 60):
        if end / step <= 9:
            return step
    return 120


def _end(case: dict[str, Any]) -> float:
    return max([case["seconds"]] + [event["t"] for event in case["events"]]) or 1


def lanes(case: dict[str, Any]) -> list[dict[str, Any]]:
    end = _end(case)
    jobs = {job["id"]: job for job in case["jobs"]}
    found: dict[str, dict[str, Any]] = {}
    for event in case["events"]:
        if event["subject"] == "chaos":
            continue
        lane = found.setdefault(
            event["subject"],
            {"label": _lane_label(event["subject"], jobs), "segments": [], "points": []},
        )
        state = STATE_AFTER.get(event["event"])
        if state is None:
            lane["points"].append({"t": event["t"], "kind": event["event"], "detail": event["detail"]})
            continue
        if lane["segments"]:
            lane["segments"][-1]["end"] = event["t"]
        lane["segments"].append({"state": state, "start": event["t"], "end": end, "detail": event["detail"]})
    return list(found.values())


def timeline_svg(case: dict[str, Any]) -> str:
    rows = lanes(case)
    step = _axis_step(_end(case))
    span = math.ceil(_end(case) / step) * step
    plot = WIDTH - LEFT - RIGHT
    bottom = TOP + len(rows) * PITCH

    def x(t: float) -> float:
        return LEFT + plot * t / span

    out = []
    for index in range(int(round(span / step)) + 1):
        t = index * step
        out.append(f'<line class="grid" x1="{x(t):.1f}" y1="{TOP - 6}" x2="{x(t):.1f}" y2="{bottom}" stroke-width="1"/>')
        out.append(f'<text class="muted" x="{x(t):.1f}" y="{bottom + 16}" text-anchor="middle">{t:g}s</text>')
    out.append(f'<line class="axis" x1="{LEFT}" y1="{bottom}" x2="{WIDTH - RIGHT}" y2="{bottom}" stroke-width="1"/>')

    for index, row in enumerate(rows):
        y = TOP + index * PITCH + (PITCH - BAR) / 2
        out.append(f'<text class="ink2" x="{LEFT - 10}" y="{y + BAR - 3}" text-anchor="end">{escape(row["label"])}</text>')
        for segment in row["segments"]:
            start = x(segment["start"])
            width = max(x(segment["end"]) - start - 2, 3)
            tip = (
                f'{row["label"]}, {STATE_LABEL[segment["state"]]}, {segment["start"]:.1f}s to {segment["end"]:.1f}s'
                + (f', {segment["detail"]}' if segment["detail"] else "")
            )
            out.append(
                f'<rect class="s-{segment["state"]}" x="{start:.1f}" y="{y:.1f}" width="{width:.1f}" height="{BAR}" rx="3">'
                f"<title>{escape(tip)}</title></rect>"
            )
            word = WORD.get(segment["state"])
            if word and width >= len(word) * 7 + 12:
                out.append(f'<text class="onbar" x="{start + 6:.1f}" y="{y + BAR - 3:.1f}" font-weight="600">{word}</text>')
            elif word:
                out.append(f'<text class="ink" x="{start + width + 5:.1f}" y="{y + BAR - 3:.1f}" font-weight="600">{word}</text>')
        last_ring = -100.0
        for point in row["points"]:
            px = x(point["t"])
            if point["kind"] == "duplicate":
                px = max(px, last_ring + 10)
                last_ring = px
                out.append(f'<circle class="ring" cx="{px:.1f}" cy="{y - 5:.1f}" r="3.5" fill="none" stroke-width="1.5"/>')
            elif point["kind"] == "published":
                out.append(
                    f'<line class="tick" x1="{px:.1f}" y1="{y - 4:.1f}" x2="{px:.1f}" y2="{y + BAR + 4:.1f}" stroke-width="1.5"/>'
                )

    for event in case["events"]:
        if event["subject"] != "chaos":
            continue
        cx = x(event["t"])
        anchor, dx = ("end", -6) if cx > WIDTH - 150 else ("start", 6)
        out.append(
            f'<line class="chaos" x1="{cx:.1f}" y1="{TOP - 20}" x2="{cx:.1f}" y2="{bottom}" stroke-width="1.5" stroke-dasharray="4 3"/>'
            f'<text class="ink" x="{cx + dx:.1f}" y="{TOP - 12}" text-anchor="{anchor}" font-weight="600">'
            f'{escape(event["detail"])} at {event["t"]:.1f}s</text>'
        )
    return _svg(bottom + AXIS, f'Timeline for case {case["number"]}: {case["title"]}', "".join(out))


def legend_svg() -> str:
    out, x, y = [], 16.0, 14.0

    def label(text: str) -> None:
        nonlocal x
        out.append(f'<text class="ink2" x="{x:.0f}" y="{y + 10:.0f}">{escape(text)}</text>')
        x += len(text) * 6.4 + 22

    for state, name in STATE_LABEL.items():
        out.append(f'<rect class="s-{state}" x="{x:.0f}" y="{y:.0f}" width="18" height="12" rx="3"/>')
        x += 24
        label(name)
    x, y = 16.0, 40.0
    out.append(f'<line class="tick" x1="{x + 4:.0f}" y1="{y - 2:.0f}" x2="{x + 4:.0f}" y2="{y + 14:.0f}" stroke-width="1.5"/>')
    x += 14
    label("Row id published to Redis")
    out.append(f'<circle class="ring" cx="{x + 4:.0f}" cy="{y + 6:.0f}" r="3.5" fill="none" stroke-width="1.5"/>')
    x += 14
    label("Duplicate delivery skipped")
    out.append(
        f'<line class="chaos" x1="{x + 4:.0f}" y1="{y - 2:.0f}" x2="{x + 4:.0f}" y2="{y + 14:.0f}" stroke-width="1.5" stroke-dasharray="4 3"/>'
    )
    x += 14
    label("Fault injected")
    return _svg(66, "Legend for the timelines", "".join(out))


def design_svg() -> str:
    def box(x: int, y: int, w: int, h: int, title: str, *lines: str, strong: bool = False) -> str:
        parts = [f'<rect class="{"box strong" if strong else "box"}" x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>']
        first = y + (h - 16 * len(lines)) / 2 + 3
        parts.append(f'<text class="ink" x="{x + w / 2}" y="{first:.0f}" text-anchor="middle" font-weight="600">{title}</text>')
        for offset, line in enumerate(lines, start=1):
            parts.append(f'<text class="ink2" x="{x + w / 2}" y="{first + 16 * offset:.0f}" text-anchor="middle">{line}</text>')
        return "".join(parts)

    def arrow(path: str, label: str = "", lx: int = 0, ly: int = 0, *, dashed: bool = False, anchor: str = "middle") -> str:
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        return (
            f'<path class="wire" d="{path}" fill="none" stroke-width="1.5" marker-end="url(#head)"{dash}/>'
            f'<text class="ink2" x="{lx}" y="{ly}" text-anchor="{anchor}">{label}</text>'
        )

    body = "".join([
        '<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path class="head" d="M0 0 L10 5 L0 10 z"/></marker></defs>',
        box(16, 46, 100, 48, "Caller", "webhook, API"),
        box(150, 46, 130, 48, "Receiver", "answers 202"),
        box(410, 24, 184, 92, "Postgres", "inbox, outbox, reminders", "the record of every job", strong=True),
        box(410, 214, 164, 64, "Redis", "carries row ids only"),
        box(150, 222, 130, 48, "Celery beat", "one process"),
        box(632, 196, 116, 100, "Celery workers", "4 for mail", "extract, browser", "outbox"),
        arrow("M116 70 H148"),
        arrow("M280 62 H408", "1. insert row", 344, 52),
        arrow("M268 94 L408 226", "2. publish id", 318, 172, anchor="end"),
        arrow("M280 252 H408", "sweep, reminders", 344, 272),
        arrow("M574 246 H630", "deliver", 602, 236),
        arrow("M690 196 V70 H596", "claim, work, finish", 604, 60, anchor="start"),
        arrow("M492 116 V212", "unfinished rows are", 502, 160, dashed=True, anchor="start"),
        '<text class="ink2" x="502" y="176">published again</text>',
    ])
    return _svg(
        330,
        "System design: a receiver stores each job in Postgres and publishes its id to Redis; Celery workers claim "
        "and finish the row in Postgres; Celery beat sweeps Postgres and publishes unfinished rows again",
        body,
    )


def _cell(text: Any) -> str:
    return escape(str(text)).replace("|", "\\|").replace("\n", " ")


def _case_section(case: dict[str, Any], images: str) -> list[str]:
    events = [
        f"| {event['t']:.2f}s | {_cell(event['subject'])} | {_cell(event['event'])} | {_cell(event['detail'])} |"
        for event in case["events"]
    ]
    return [
        f"### {case['number']}. {_cell(case['title'])}: {'passed' if case['passed'] else 'FAILED'}",
        "",
        "| | |",
        "| --- | --- |",
        f"| **What we did** | {_cell(case['did'])} |",
        f"| **Expected** | {_cell(case['expect'])} |",
        f"| **Observed** | {_cell(case['observed'])} |",
        "",
        f"![Timeline for case {case['number']}]({images}/case-{case['number']}.svg)",
        "",
        f"<details><summary>All {len(events)} events as a table</summary>",
        "",
        "| Time | Subject | Event | Detail |",
        "| --- | --- | --- | --- |",
        *events,
        "",
        "</details>",
        "",
    ]


def readme(results: dict[str, Any], images: str = "results") -> str:
    env, cases, settings = results["environment"], results["cases"], results["environment"]["settings"]
    passed = sum(1 for case in cases if case["passed"])
    lines = [
        "# Queue POC: does anything get lost?",
        "",
        "A job queue on Celery and Redis, with Postgres as the record. Each case below breaks something on purpose "
        "and shows what happened to the jobs.",
        "",
        f"# {passed} of {len(cases)}",
        "",
        "**failure cases behaved as designed**",
        "",
        f"<sub>Run {_cell(env['ran_at'])}, {_cell(env['platform'])}, Python {_cell(env['python'])}, "
        f"Celery {_cell(env['celery'])} ({_cell(env['pool'])} pool), Redis {_cell(env['redis_server'])}, "
        f"Postgres {_cell(env['postgres_server'])}</sub>",
        "",
        "## System design",
        "",
        f"![System design]({images}/design.svg)",
        "",
        "1. **A job is accepted when its row is committed in Postgres.** Publishing to Redis comes second and is allowed to fail.",
        "2. **Redis carries only the row id.** If Redis loses a message, the row is still there.",
        "3. **A worker claims the row before working**, so a second delivery of the same id is skipped.",
        "4. **A sweep runs every few seconds** and publishes again any row that is unfinished: never published, "
        "lost by Redis, or held by a worker that died.",
        "5. **Timed work is a row with a due time.** Nothing waits inside Redis.",
        "",
        "## The life of one job",
        "",
        "![Job life cycle: queued, running, done or dead](docs/job-lifecycle.png)",
        "",
        "## What happened in each case",
        "",
        "One row per job. Time runs left to right from the start of the case. Timings for this run were shortened: "
        f"a running job counts as abandoned after {settings['stale_seconds']}s, a queued row is published again after "
        f"{settings['republish_seconds']}s, and the sweep runs every {settings['sweep_seconds']:g}s.",
        "",
        f"![Legend]({images}/legend.svg)",
        "",
    ]
    for case in cases:
        lines += _case_section(case, images)
    lines += [
        "## What this run does not show",
        "",
        "1. Workers did placeholder work (a sleep, or a raised error). \"Finished once\" is proven for the database row; "
        "a real call to another system still needs its own idempotency key.",
        f"2. This run used Celery's **{_cell(env['pool'])}** pool on {_cell(env['platform'])}. Linux containers use the "
        "prefork pool; rerun there before relying on these results.",
        "3. One Redis with no persistence and no failover. Two beat processes running at once was not tested.",
        "",
        "## Run it",
        "",
        "Needs Docker and [uv](https://docs.astral.sh/uv/).",
        "",
        "```bash",
        "docker compose up -d --wait          # Postgres on 55432, Redis on 56379, both in memory",
        "uv run python -m queue_poc.prove     # about 90 seconds; rewrites this README and results/",
        "uv run pytest",
        "docker compose down",
        "```",
        "",
        "To rebuild this page from the last run without any services: `uv run python -m queue_poc.report`.",
        "",
        "## Worker rules",
        "",
        "| Rule | Where |",
        "| --- | --- |",
        "| Acknowledge after the work, never before | `task_acks_late`, `task_reject_on_worker_lost` in `tasks.py` |",
        "| Take one job at a time | `worker_prefetch_multiplier = 1` |",
        "| Claim the row before working; skip if it cannot be claimed | `db.claim` |",
        "| Finish and fail only your own attempt | `db.finish` and `db.fail` match on the attempt number |",
        "| One queue per kind of work | `QUEUE_FOR_KIND` in `config.py` |",
        "| A running row older than the limit is abandoned and queued again | `db.reset_stale`, `QUEUE_POC_STALE_SECONDS` |",
        "| The limit is longer than the longest job | Set per deployment; also the job's hard time limit |",
        "| Timed work is a due time in Postgres, read by beat | `reminders` table, `db.fire_due_reminders` |",
        "| A send is a row with a unique key, written with its cause | `outbox` table |",
        "| Exactly one beat process | Deployment |",
        "",
        "## Layout",
        "",
        "```",
        "src/queue_poc/",
        "  config.py     connection strings, limits, queue names",
        "  db.py         Postgres: inbox, outbox, reminders, and the event log this page is drawn from",
        "  tasks.py      Celery app and settings, the job task, sweep, reminders, outbox send",
        "  receiver.py   accept-then-publish HTTP endpoint",
        "  prove.py      the six failure cases, run for real",
        "  report.py     results.json to this README and the SVG images",
        "tests/          database rules and report building",
        "results/        raw data and images from the latest run",
        "docs/           the job life-cycle picture",
        "```",
        "",
    ]
    return "\n".join(lines)


def build(directory: Path, readme_path: Path | None = None) -> Path:
    results = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    (directory / "design.svg").write_text(design_svg(), encoding="utf-8")
    (directory / "legend.svg").write_text(legend_svg(), encoding="utf-8")
    for case in results["cases"]:
        (directory / f"case-{case['number']}.svg").write_text(timeline_svg(case), encoding="utf-8")
    readme_path = readme_path or directory.parent / "README.md"
    readme_path.write_text(readme(results, images=directory.name), encoding="utf-8")
    return readme_path


if __name__ == "__main__":
    print(build(Path(sys.argv[1] if len(sys.argv) > 1 else "results")))
