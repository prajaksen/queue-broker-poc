"""Turns results/results.json into a self-contained HTML report, one SVG timeline per case and RESULTS.md.

    uv run python -m queue_poc.report            # rebuild from the last run, no services needed
"""
from __future__ import annotations

import json
import math
import sys
from html import escape
from pathlib import Path
from typing import Any

# What a subject's state becomes after each event. Events not listed here do not change the state.
STATE_AFTER = {
    "accepted": "queued", "reminder_set": "queued", "outbox_added": "queued", "stale": "queued",
    "claimed": "running", "retry": "waiting", "dead": "dead",
    "finished": "done", "fired": "done", "sent": "done",
}
STATE_LABEL = {
    "queued": "Waiting to run", "running": "Running", "waiting": "Waiting to retry",
    "done": "Done", "dead": "Set aside",
}
# Running is the one series colour; done / retry / set aside use the reserved status colours.
LIGHT = {
    "surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
    "axis": "#c3c2b7", "queued": "#b4b2a9", "running": "#2a78d6", "waiting": "#fab219", "done": "#0ca30c",
    "dead": "#d03b3b",
}
DARK = {
    "surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a",
    "axis": "#383835", "queued": "#6b6a65", "running": "#3987e5", "waiting": "#fab219", "done": "#0ca30c",
    "dead": "#d03b3b",
}

GLYPH = {"done": " ✓", "dead": " ✕"}
WIDTH, LEFT, RIGHT, TOP, PITCH, BAR, AXIS = 760, 124, 18, 34, 24, 14, 30


def _svg_css(palette: dict[str, str]) -> str:
    return (
        f".bg{{fill:{palette['surface']}}}.ink{{fill:{palette['ink']}}}.ink2{{fill:{palette['ink2']}}}"
        f".muted{{fill:{palette['muted']}}}.grid{{stroke:{palette['grid']}}}.axis{{stroke:{palette['axis']}}}"
        f".chaos{{stroke:{palette['ink']}}}.ring{{stroke:{palette['ink']}}}.tick{{stroke:{palette['muted']}}}.onbar{{fill:{palette['surface']}}}"
        + "".join(f".s-{state}{{fill:{palette[state]}}}" for state in STATE_LABEL)
        + "text{font:12px system-ui,-apple-system,'Segoe UI',sans-serif}"
    )


def _lane_label(subject: str, jobs: dict[int, dict[str, Any]]) -> str:
    kind, _, number = subject.partition(":")
    if kind == "job":
        return f"job {number} · {jobs.get(int(number), {}).get('kind', '')}"
    return f"{kind} {number}"


def _axis_step(end: float) -> float:
    for step in (0.5, 1, 2, 5, 10, 20, 30, 60):
        if end / step <= 9:
            return step
    return 120


def lanes(case: dict[str, Any]) -> list[dict[str, Any]]:
    """One lane per job, reminder or outbox row: its state segments and the instants worth marking."""
    end = max([case["seconds"]] + [event["t"] for event in case["events"]]) or 1
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


def timeline_svg(case: dict[str, Any], *, standalone: bool) -> str:
    rows = lanes(case)
    end = max([case["seconds"]] + [event["t"] for event in case["events"]]) or 1
    step = _axis_step(end)
    span = math.ceil(end / step) * step
    plot = WIDTH - LEFT - RIGHT
    height = TOP + len(rows) * PITCH + AXIS

    def x(t: float) -> float:
        return LEFT + plot * t / span

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}" '
        f'role="img" aria-label="Timeline for case {case["number"]}: {escape(case["title"])}">'
    ]
    if standalone:
        out.append(f"<style>{_svg_css(LIGHT)}</style><rect class='bg' width='{WIDTH}' height='{height}' rx='8'/>")
    bottom = TOP + len(rows) * PITCH
    ticks = int(round(span / step))
    for index in range(ticks + 1):
        t = index * step
        out.append(f'<line class="grid" x1="{x(t):.1f}" y1="{TOP - 6}" x2="{x(t):.1f}" y2="{bottom}" stroke-width="1"/>')
        out.append(f'<text class="muted" x="{x(t):.1f}" y="{bottom + 16}" text-anchor="middle">{t:g}s</text>')
    out.append(f'<line class="axis" x1="{LEFT}" y1="{bottom}" x2="{WIDTH - RIGHT}" y2="{bottom}" stroke-width="1"/>')

    for index, row in enumerate(rows):
        y = TOP + index * PITCH + (PITCH - BAR) / 2
        out.append(f'<text class="ink2" x="{LEFT - 10}" y="{y + BAR - 3}" text-anchor="end">{escape(row["label"])}</text>')
        for segment in row["segments"]:
            start, stop = x(segment["start"]), x(segment["end"])
            width = max(stop - start - 2, 3)
            tip = (
                f'{row["label"]} · {STATE_LABEL[segment["state"]]} · {segment["start"]:.1f}s to {segment["end"]:.1f}s'
                + (f' · {segment["detail"]}' if segment["detail"] else "")
            )
            out.append(
                f'<rect class="s-{segment["state"]}" x="{start:.1f}" y="{y:.1f}" width="{width:.1f}" height="{BAR}" rx="3">'
                f"<title>{escape(tip)}</title></rect>"
            )
            # Done and set aside differ by a glyph as well as by colour.
            glyph = {"done": "✓", "dead": "✕"}.get(segment["state"])
            if glyph and width >= 18:
                out.append(f'<text class="onbar" x="{start + 5:.1f}" y="{y + BAR - 3:.1f}" font-weight="700" pointer-events="none">{glyph}</text>')
            elif glyph:
                out.append(f'<text class="ink" x="{start + width + 4:.1f}" y="{y + BAR - 3:.1f}" font-weight="700">{glyph}</text>')
        last_cross = -100.0
        for point in row["points"]:
            px = x(point["t"])
            if point["kind"] == "duplicate":
                px = max(px, last_cross + 10)
                last_cross = px
                out.append(
                    f'<circle class="ring" cx="{px:.1f}" cy="{y - 5:.1f}" r="3.5" fill="none" stroke-width="1.5">'
                    f'<title>{escape(row["label"])} · duplicate delivery skipped at {point["t"]:.1f}s</title></circle>'
                )
            elif point["kind"] == "published":
                out.append(
                    f'<line class="tick" x1="{px:.1f}" y1="{y - 4:.1f}" x2="{px:.1f}" y2="{y + BAR + 4:.1f}" stroke-width="1.5">'
                    f'<title>{escape(row["label"])} · row id published to Redis at {point["t"]:.1f}s'
                    f'{" " + escape(point["detail"]) if point["detail"] else ""}</title></line>'
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
    out.append("</svg>")
    return "".join(out)


def design_svg(*, standalone: bool) -> str:
    """The system on one picture: Postgres holds the record, Redis only carries row ids."""
    def box(x: int, y: int, w: int, h: int, title: str, *lines: str, strong: bool = False) -> str:
        parts = [f'<rect class="{"box strong" if strong else "box"}" x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>']
        first = y + (h - 16 * len(lines)) / 2 + 3
        parts.append(f'<text class="ink" x="{x + w / 2}" y="{first:.0f}" text-anchor="middle" font-weight="600">{title}</text>')
        for offset, line in enumerate(lines, start=1):
            parts.append(f'<text class="ink2" x="{x + w / 2}" y="{first + 16 * offset:.0f}" text-anchor="middle">{line}</text>')
        return "".join(parts)

    def arrow(path: str, label: str, lx: int, ly: int, *, dashed: bool = False, anchor: str = "middle") -> str:
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        return (
            f'<path class="wire" d="{path}" fill="none" stroke-width="1.5" marker-end="url(#head)"{dash}/>'
            f'<text class="ink2" x="{lx}" y="{ly}" text-anchor="{anchor}">{label}</text>'
        )

    width, height = 760, 330
    css = (
        ".box{fill:none;stroke-width:1.5}text{font:12px system-ui,-apple-system,'Segoe UI',sans-serif}"
        if not standalone else
        f".bg{{fill:{LIGHT['surface']}}}.ink{{fill:{LIGHT['ink']}}}.ink2{{fill:{LIGHT['ink2']}}}"
        f".box{{fill:none;stroke:{LIGHT['axis']};stroke-width:1.5}}.strong{{stroke:{LIGHT['running']};stroke-width:2}}"
        f".wire{{stroke:{LIGHT['ink2']}}}.head{{fill:{LIGHT['ink2']}}}"
        "text{font:12px system-ui,-apple-system,'Segoe UI',sans-serif}"
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        'role="img" aria-label="System design: a receiver stores each job in Postgres and publishes its id to Redis; '
        'Celery workers claim and finish the row in Postgres; Celery beat sweeps Postgres and re-publishes unfinished rows">',
        f"<style>{css}</style>",
        '<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        '<path class="head" d="M0 0 L10 5 L0 10 z"/></marker></defs>',
    ]
    if standalone:
        parts.append(f"<rect class='bg' width='{width}' height='{height}' rx='8'/>")
    parts += [
        box(16, 46, 100, 48, "Caller", "webhook, API"),
        box(150, 46, 130, 48, "Receiver", "answers 202"),
        box(410, 24, 184, 92, "Postgres", "inbox · outbox · reminders", "the record of every job", strong=True),
        box(410, 214, 164, 64, "Redis", "carries row ids only"),
        box(150, 222, 130, 48, "Celery beat", "one process"),
        box(632, 196, 116, 100, "Celery workers", "mail ×4", "extract · browser", "outbox"),
        arrow("M116 70 H148", "", 0, 0),
        arrow("M280 62 H408", "1 · insert row", 344, 52),
        arrow("M268 94 L408 226", "2 · publish id", 318, 172, anchor="end"),
        arrow("M280 252 H408", "sweep · reminders", 344, 272),
        arrow("M574 246 H630", "deliver", 602, 236),
        arrow("M690 196 V70 H596", "claim · work · finish", 604, 60, anchor="start"),
        arrow("M492 116 V212", "unfinished rows are", 502, 160, dashed=True, anchor="start"),
        '<text class="ink2" x="502" y="176">published again</text>',
    ]
    parts.append("</svg>")
    return "".join(parts)


def _legend() -> str:
    items = "".join(
        f'<span class="key"><svg width="18" height="12" aria-hidden="true"><rect class="s-{state}" width="18" height="12" rx="3"/></svg>{label}{GLYPH.get(state, "")}</span>'
        for state, label in STATE_LABEL.items()
    )
    return (
        f'<div class="legend">{items}'
        '<span class="key"><svg width="10" height="16" aria-hidden="true"><line class="tick" x1="5" y1="0" x2="5" y2="16" stroke-width="1.5"/></svg>Row id published to Redis</span>'
        '<span class="key"><svg width="10" height="10" aria-hidden="true"><circle class="ring" cx="5" cy="5" r="3.5" fill="none" stroke-width="1.5"/></svg>Duplicate delivery skipped</span>'
        '<span class="key"><svg width="10" height="16" aria-hidden="true"><line class="chaos" x1="5" y1="0" x2="5" y2="16" stroke-width="1.5" stroke-dasharray="4 3"/></svg>Fault injected</span></div>'
    )


def _events_table(case: dict[str, Any]) -> str:
    rows = "".join(
        f"<tr><td class='num'>{event['t']:.2f}s</td><td>{escape(event['subject'])}</td>"
        f"<td>{escape(event['event'])}</td><td>{escape(event['detail'])}</td></tr>"
        for event in case["events"]
    )
    return (
        f"<details><summary>All {len(case['events'])} events as a table</summary>"
        "<div class='scroll'><table><thead><tr><th>Time</th><th>Subject</th><th>Event</th><th>Detail</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></details>"
    )


def _page_css() -> str:
    def tokens(palette: dict[str, str], page: str, card: str) -> str:
        return (
            f"--page:{page};--card:{card};--ink:{palette['ink']};--ink2:{palette['ink2']};--muted:{palette['muted']};"
            f"--grid:{palette['grid']};--axis:{palette['axis']};"
            + "".join(f"--{state}:{palette[state]};" for state in STATE_LABEL)
        )

    light, dark = tokens(LIGHT, "#f9f9f7", LIGHT["surface"]), tokens(DARK, "#0d0d0d", DARK["surface"])
    return f"""
:root{{{light}}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{{dark}}}}}
:root[data-theme="dark"]{{{dark}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:860px;margin:0 auto;padding:32px 16px 64px}}
h1{{font-size:26px;line-height:1.2;margin:0 0 6px}}
h2{{font-size:18px;margin:40px 0 12px}}
h3{{font-size:16px;margin:0}}
p{{margin:0 0 10px}}
.sub,.meta{{color:var(--ink2)}}
.meta{{font-size:13px}}
.verdict{{display:flex;gap:16px;align-items:baseline;flex-wrap:wrap;margin:20px 0 4px}}
.verdict strong{{font-size:40px;line-height:1}}
.card{{background:var(--card);border:1px solid var(--grid);border-radius:10px;padding:18px;margin:0 0 16px}}
.head{{display:flex;gap:10px;align-items:center;justify-content:space-between;margin-bottom:10px}}
.badge{{font-size:12px;font-weight:700;padding:3px 9px;border-radius:999px;border:1.5px solid;white-space:nowrap}}
.badge.pass{{border-color:var(--done)}}.badge.fail{{border-color:var(--dead)}}
dl{{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:0 0 12px}}
dt{{color:var(--muted)}}dd{{margin:0}}
.scroll{{overflow-x:auto}}
.scroll svg{{display:block;min-width:640px;width:100%;height:auto}}
svg text{{font:12px system-ui,-apple-system,"Segoe UI",sans-serif}}
.ink{{fill:var(--ink)}}.ink2{{fill:var(--ink2)}}.muted{{fill:var(--muted)}}
.grid{{stroke:var(--grid)}}.axis{{stroke:var(--axis)}}.chaos{{stroke:var(--ink)}}.ring{{stroke:var(--ink)}}.tick{{stroke:var(--muted)}}.onbar{{fill:var(--card)}}
.box{{stroke:var(--axis)}}.box.strong{{stroke:var(--running);stroke-width:2}}.wire{{stroke:var(--ink2)}}.head-fill,.head{{fill:var(--ink2)}}
.s-queued{{fill:var(--queued)}}.s-running{{fill:var(--running)}}.s-waiting{{fill:var(--waiting)}}.s-done{{fill:var(--done)}}.s-dead{{fill:var(--dead)}}
.legend{{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:var(--ink2);margin:0 0 16px}}
.key{{display:inline-flex;gap:6px;align-items:center}}
details{{margin-top:12px;font-size:13px}}summary{{cursor:pointer;color:var(--ink2)}}
table{{border-collapse:collapse;width:100%;margin-top:8px}}
th,td{{text-align:left;padding:4px 10px 4px 0;border-bottom:1px solid var(--grid);vertical-align:top}}
th{{color:var(--muted);font-weight:500}}.num{{font-variant-numeric:tabular-nums;white-space:nowrap}}
ol{{margin:0;padding-left:20px}}li{{margin-bottom:6px}}
code{{font:13px ui-monospace,SFMono-Regular,Menlo,monospace}}
"""


def html_report(results: dict[str, Any]) -> str:
    env, cases = results["environment"], results["cases"]
    passed = sum(1 for case in cases if case["passed"])
    cards = []
    for case in cases:
        badge = ("pass", "✓ Passed") if case["passed"] else ("fail", "✕ Failed")
        cards.append(
            f'<section class="card"><div class="head"><h3>{case["number"]}. {escape(case["title"])}</h3>'
            f'<span class="badge {badge[0]}">{badge[1]}</span></div>'
            f'<dl><dt>What we did</dt><dd>{escape(case["did"])}</dd>'
            f'<dt>Expected</dt><dd>{escape(case["expect"])}</dd>'
            f'<dt>Observed</dt><dd>{escape(case["observed"])}</dd></dl>'
            f'<div class="scroll">{timeline_svg(case, standalone=False)}</div>{_events_table(case)}</section>'
        )
    settings = env["settings"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Queue POC results</title><style>{_page_css()}</style></head><body><main>
<h1>Queue POC: does anything get lost?</h1>
<p class="sub">A job queue on Celery and Redis, with Postgres as the record. Each case below breaks something on purpose and shows what happened to the jobs.</p>
<div class="verdict"><strong>{passed} of {len(cases)}</strong><span>failure cases behaved as designed</span></div>
<p class="meta">Run {escape(env["ran_at"])} · {escape(env["platform"])} · Python {escape(env["python"])} · Celery {escape(env["celery"])} ({escape(env["pool"])} pool) · Redis {escape(env["redis_server"])} · Postgres {escape(env["postgres_server"])}</p>

<h2>System design</h2>
<div class="card"><div class="scroll">{design_svg(standalone=False)}</div>
<ol>
<li><b>A job is accepted when its row is committed in Postgres.</b> Publishing to Redis comes second and is allowed to fail.</li>
<li><b>Redis carries only the row id.</b> If Redis loses a message, the row is still there.</li>
<li><b>A worker claims the row before working</b>, so a second delivery of the same id is skipped.</li>
<li><b>A sweep runs every few seconds</b> and publishes again any row that is unfinished: never published, lost by Redis, or held by a worker that died.</li>
<li><b>Timed work is a row with a due time.</b> Nothing waits inside Redis.</li>
</ol></div>

<h2>What happened in each case</h2>
<p class="meta">One row per job. Time runs left to right from the start of the case. Hover a bar for details. Timings for this run were shortened: a running job counts as abandoned after {settings["stale_seconds"]}s, a queued row is published again after {settings["republish_seconds"]}s, and the sweep runs every {settings["sweep_seconds"]:g}s.</p>
{_legend()}
{"".join(cards)}

<h2>What this run does not show</h2>
<div class="card"><ol>
<li>Workers did placeholder work (a sleep, or a raised error). "Finished once" is proven for the database row; a real call to another system still needs its own idempotency key.</li>
<li>This run used Celery's <b>{escape(env["pool"])}</b> pool on {escape(env["platform"])}. Linux containers use the prefork pool; rerun there before relying on these results.</li>
<li>One Redis with no persistence and no failover. Two beat processes running at once was not tested.</li>
</ol></div>
<p class="meta">Rebuild this page from <code>results/results.json</code> with <code>uv run python -m queue_poc.report</code>, or rerun everything with <code>uv run python -m queue_poc.prove</code>.</p>
</main></body></html>
"""


def markdown_results(results: dict[str, Any]) -> str:
    env, cases = results["environment"], results["cases"]
    passed = sum(1 for case in cases if case["passed"])
    lines = [
        "# Results",
        "",
        f"**{passed} of {len(cases)} failure cases behaved as designed.**",
        "",
        f"Run {env['ran_at']} on {env['platform']}, Python {env['python']}, Celery {env['celery']} "
        f"({env['pool']} pool), Redis {env['redis_server']}, Postgres {env['postgres_server']}.",
        "",
        "This file is written by `python -m queue_poc.prove`. Open `report.html` in a browser for the same results "
        "with hover details and the full event tables.",
        "",
        "| # | Case | Result | What was observed |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {case['number']} | {case['title']} | {'Passed' if case['passed'] else '**Failed**'} | {case['observed']} |"
        for case in cases
    ]
    lines += [
        "",
        "## Timelines",
        "",
        "One row per job, time left to right. Grey is waiting to run, blue is running, green is done, "
        "yellow is waiting to retry, red is set aside (done also carries a ✓, set aside a ✕). A thin tick is the "
        "row id being published to Redis, a small ring is a duplicate delivery that was skipped, and a dashed "
        "line is the fault being injected.",
        "",
    ]
    for case in cases:
        lines += [
            f"### {case['number']}. {case['title']}",
            "",
            f"- **What we did:** {case['did']}",
            f"- **Expected:** {case['expect']}",
            f"- **Observed:** {case['observed']}",
            "",
            f"![Timeline for case {case['number']}](case-{case['number']}.svg)",
            "",
        ]
    return "\n".join(lines)


def build(directory: Path) -> Path:
    results = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    (directory / "design.svg").write_text(design_svg(standalone=True), encoding="utf-8")
    for case in results["cases"]:
        (directory / f"case-{case['number']}.svg").write_text(timeline_svg(case, standalone=True), encoding="utf-8")
    (directory / "RESULTS.md").write_text(markdown_results(results), encoding="utf-8")
    page = directory / "report.html"
    page.write_text(html_report(results), encoding="utf-8")
    return page


if __name__ == "__main__":
    print(build(Path(sys.argv[1] if len(sys.argv) > 1 else "results")))
