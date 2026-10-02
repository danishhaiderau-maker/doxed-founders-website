"""Server-rendered view of data_health_report.json and event_study_report.json."""

from __future__ import annotations

import html

REPORT_FILE = "data_health_report.json"
SCHEMA = "data_health_v1"
EVENT_STUDY_FILE = "event_study_report.json"
EVENT_STUDY_SCHEMA = "event_study_report_v1"
NO_DATA_TEXT = "no data yet"
STATUS_COLOURS = {
    "OK": "#3fb950", "DEGRADED": "#d29922", "STALE": "#f85149", "MISSING": "#8b949e",
    "LOCKBOX_ACCRUING": "#58a6ff", "CONFIRMED": "#3fb950", "CONFIRMED_CONTINUATION": "#3fb950",
    "CONFIRMED_REVERSAL": "#3fb950", "KILLED": "#f85149", "INSUFFICIENT_LOCKBOX_EVENTS": "#d29922",
}


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _num(value, digits=2) -> str:
    if value is None:
        return NO_DATA_TEXT
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return NO_DATA_TEXT


def _status(value) -> str:
    colour = STATUS_COLOURS.get(str(value), "#c9d1d9")
    return f"<span style='color:{colour};font-weight:600'>{_esc(value)}</span>"


def _table(table_id: str, headers, rows) -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return (f"<div class='wrap'><table id='{_esc(table_id)}'><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


def _streams(report: dict) -> str:
    rows = []
    for s in report.get("streams") or []:
        rows.append([
            _esc(s.get("stream")), _status(s.get("status")), _esc(_num(s.get("coverage_pct_24h"))),
            _esc(_num(s.get("staleness_sec"), 0)), _esc(_num(s.get("lag_vs_mirror_head_sec"), 0)),
            _esc(s.get("rows_24h")), _esc(s.get("rows")), _esc(s.get("unit") or s.get("cadence")),
            _esc(s.get("source_file")),
        ])
    return _table("dataHealthStreams", ("Stream", "Status", "Coverage % (24h)", "Stale s", "Lag vs head s",
                                        "Units 24h", "Rows", "Unit", "Source"), rows)


def _repaired(report: dict) -> str:
    replay = report.get("signal_replay") or {}
    rep = report.get("repaired_streams") or {}
    rows = [
        ["signal_replay", _esc(f"distinct complete {_num(replay.get('distinct_complete_pct'))}% "
                               f"({replay.get('distinct_complete')}/{replay.get('distinct_trades')}), "
                               f"censored by shutdown {replay.get('distinct_censored_shutdown')}, "
                               f"row complete {_num(replay.get('row_complete_pct'))}%")],
    ]
    for name, section in rep.items():
        rows.append([_esc(name), _esc(", ".join(f"{k}={v}" for k, v in section.items()))])
    return _table("dataHealthRepaired", ("Stream", "Quality"), rows)


def _event_studies(study: dict) -> str:
    if not isinstance(study, dict) or study.get("schema") != EVENT_STUDY_SCHEMA:
        return f"<p class='sub'>{NO_DATA_TEXT}</p>"
    rows = []
    for h in study.get("hypotheses") or []:
        lock = h.get("lockbox") or {}
        disc = h.get("discovery") or {}
        prim = ((disc.get("primary") or {}).get("abnormal") or {})
        scored = ((lock.get("primary") or {}).get("abnormal") or {}) if lock.get("scored") else {}
        rows.append([
            _esc(h.get("id")), _status(h.get("status")), _esc(h.get("metric")),
            _esc(h.get("primary_horizon_sec")),
            _esc(f"{lock.get('events_counted')}/{h.get('min_lockbox_events')}"),
            _esc(_num(lock.get("events_per_day"))), _esc(_num(lock.get("days_to_min_sample"), 1)),
            _esc(str(lock.get("end_utc") or "")[:10]),
            _esc(f"n={prim.get('n')} mean={_num(prim.get('mean'))} t={_num(prim.get('t'))}") if prim else NO_DATA_TEXT,
            _esc(f"n={scored.get('n')} mean={_num(scored.get('mean'))} t={_num(scored.get('t'))} "
                 f"q={_num(lock.get('primary_bh_q'), 4)}") if scored else "sealed until lockbox closes",
        ])
    return _table("eventStudies", ("Hypothesis", "Status", "Metric", "Primary s", "Lockbox events / min",
                                   "Events/day", "Days to min", "Lockbox ends", "Discovery (exploratory)",
                                   "Lockbox result"), rows)


def render_data_health_html(report: dict | None, study: dict | None, *, evidence: dict | None, nav_links) -> str:
    report = report if isinstance(report, dict) else {}
    evidence = evidence or {}
    current = evidence.get("status") == "CURRENT_GENERATION" and report.get("schema") == SCHEMA
    if not current or report.get("status") == "ERROR":
        reason = report.get("error") or ", ".join(evidence.get("blockers") or []) or "report not published"
        banner = f"NO CURRENT DATA: {_esc(REPORT_FILE)} is not part of the current analyzer generation ({_esc(reason)})"
        colour = "#f85149"
    else:
        counts = report.get("status_counts") or {}
        banner = (f"CURRENT generation {_esc(evidence.get('generated_at_display') or '')} · "
                  f"{_esc(', '.join(f'{k} {v}' for k, v in sorted(counts.items())))} · read-only, no orders")
        colour = "#3fb950" if report.get("status") == "OK" else "#d29922"
    nav = " · ".join(f"<a href='{_esc(href)}'>{_esc(label)}</a>" for label, href in nav_links)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Data Health</title>
<style>
body{{background:#0d1117;color:#c9d1d9;font-family:Segoe UI,Arial,sans-serif;margin:16px;}}
a{{color:#58a6ff}} table{{border-collapse:collapse;font-size:0.85em;margin:6px 0 14px 0}}
th,td{{border:1px solid #30363d;padding:4px 8px;text-align:left;vertical-align:top}}
th{{background:#161b22}} .sub{{color:#8b949e;font-size:0.85em}} .wrap{{overflow:auto}}
</style></head><body>
<nav class='sub'>{nav}</nav>
<h1>Data Health</h1>
<p id='dataHealthBanner' style='color:{colour};font-weight:600'>{banner}</p>
<h2>Streams (coverage, staleness, row counts)</h2>
{_streams(report)}
<p class='sub'>Coverage = observed units / expected units in the stream's trailing 24 h. Per-second feeds count only
seconds whose connection/freshness mask is up; stale or disconnected seconds are never forward-filled.
"Lag vs head" separates feed staleness from laptop mirror lag.</p>
<h2>Repaired streams</h2>
{_repaired(report)}
<h2>Pre-registered event studies</h2>
{_event_studies(study or {})}
<p class='sub'>Discovery rows are exploratory and cannot confirm a hypothesis. Lockbox events are counted while
the lockbox is open and scored once after it closes against the frozen kill rule
(diagnostics/PREREGISTERED-HYPOTHESES-20261002.md).</p>
</body></html>"""
