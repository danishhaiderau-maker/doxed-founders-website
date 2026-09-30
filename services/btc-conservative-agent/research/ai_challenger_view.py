"""Server-rendered view of ai_challenger_report.json (LLM vs shadow challengers)."""

from __future__ import annotations

import html

REPORT_FILE = "ai_challenger_report.json"
SCHEMA = "ai_challenger_report_v1"
NO_DATA_TEXT = "no data yet"
VERDICT_COLOURS = {
    "LLM_BETTER": "#3fb950",
    "LLM_WORSE": "#f85149",
    "NO_DETECTABLE_DIFFERENCE": "#d29922",
    "NOT_ENOUGH_DATA": "#8b949e",
}


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _num(value, digits=2, signed=True) -> str:
    if value is None:
        return NO_DATA_TEXT
    try:
        v = float(value)
    except (TypeError, ValueError):
        return NO_DATA_TEXT
    return f"{v:+.{digits}f}" if signed else f"{v:.{digits}f}"


def _ci(ci) -> str:
    if not isinstance(ci, (list, tuple)) or len(ci) != 2 or ci[0] is None:
        return NO_DATA_TEXT
    return f"[{_num(ci[0])}, {_num(ci[1])}]"


def _table(table_id: str, headers, rows) -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return (f"<div class='wrap'><table id='{_esc(table_id)}'><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


def dead_input_alarm(report: dict | None) -> dict | None:
    """Current-cohort dead-input verdict from the report, for the alarm list."""
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        return None
    cohort = (report.get("cohorts") or {}).get(report.get("current_prompt_id") or "") or {}
    dead = cohort.get("dead_inputs") or {}
    if dead.get("status") != "DEAD_INPUT":
        return None
    fields = ", ".join(f"{d.get('path')}={d.get('kind')}x{d.get('calls')}"
                       for d in (dead.get("dead_fields") or [])[:8])
    return {"status": "DEAD_INPUT", "prompt_id": report.get("current_prompt_id"), "detail": fields}


def _comparisons(report: dict) -> str:
    rows = []
    for c in report.get("primary_comparisons") or []:
        verdict = str(c.get("verdict") or "")
        colour = VERDICT_COLOURS.get(verdict, "#c9d1d9")
        rows.append([
            _esc(c.get("challenger")),
            _esc(c.get("prompt_id")),
            _esc(f"{c.get('n')} / {c.get('clusters')}"),
            _esc(_num(c.get("mean_diff_net_bp"))),
            _esc(_ci(c.get("ci95"))),
            _esc(_num(c.get("p"), 4, signed=False)),
            _esc(_num(c.get("q_bh"), 4, signed=False)),
            f"<span style='color:{colour};font-weight:600'>{_esc(verdict)}</span>",
        ])
    if not rows:
        return f"<p class='sub'>{NO_DATA_TEXT}</p>"
    return _table("aiChallengerComparisons",
                  ("challenger", "prompt cohort", "calls / hour clusters", "LLM minus challenger (net bp)",
                   "95% CI (cluster bootstrap)", "p (CR1 t)", "BH q", "verdict"), rows)


def _markouts(cohort: dict, horizons) -> str:
    markouts = cohort.get("markouts") or {}
    names = list((markouts.get(str(horizons[0])) or {}).keys()) if horizons else []
    rows = []
    for name in names:
        cells = [_esc(name)]
        for h in horizons:
            e = (markouts.get(str(h)) or {}).get(name) or {}
            stat = e.get("net_vs_zero") or {}
            if not stat.get("n"):
                cells.append(NO_DATA_TEXT)
                continue
            cells.append(
                f"{_esc(_num(stat.get('mean')))} <span class='sub'>CI {_esc(_ci(stat.get('ci95')))} "
                f"q={_esc(_num(stat.get('q_bh'), 3, signed=False))} take={_esc(_num(e.get('take_rate'), 2, signed=False))} "
                f"hit={_esc(_num(e.get('hit_rate'), 2, signed=False))}</span>"
            )
        rows.append(cells)
    if not rows:
        return f"<p class='sub'>{NO_DATA_TEXT}</p>"
    heads = ["side source"] + [f"+{h // 60}m" if h >= 60 else f"+{h}s" for h in horizons]
    return _table("aiChallengerMarkouts", heads, rows)


def _geometry(cohort: dict) -> str:
    geo = cohort.get("geometry_proxy") or {}
    rows = []
    for lane, per in (geo.get("lanes") or {}).items():
        for name, e in per.items():
            stat = e.get("result_bp_filled") or {}
            rows.append([
                _esc(lane), _esc(name), _esc(e.get("sides_taken")),
                _esc(_num(e.get("fill_rate"), 2, signed=False)),
                _esc(_num(e.get("target_rate_of_filled"), 2, signed=False)),
                _esc(f"{_num(stat.get('mean'))} (n={stat.get('n')})"),
                _esc(_ci(stat.get("ci95"))),
            ])
    note = f"<p class='sub'>{_esc(geo.get('note') or '')}</p>"
    if not rows:
        return note + f"<p class='sub'>{NO_DATA_TEXT}</p>"
    return note + _table("aiChallengerGeometry",
                         ("tile", "side source", "sides", "fill rate", "target first (of filled)",
                          "result bp (filled)", "95% CI"), rows)


def _compact(cohort: dict) -> str:
    c = cohort.get("compact_v5") or {}
    items = [
        ("call states", c.get("call_states")),
        ("parse status", c.get("parse_status")),
        ("scored filled questions", c.get("scored_filled_questions")),
        ("base rate (target first)", c.get("base_rate_target_first")),
        ("Brier", c.get("brier")),
        ("Brier, constant 0.5", c.get("brier_constant_half")),
        ("Brier, in-sample climatology", c.get("brier_climatology_in_sample")),
        ("skill vs 0.5", c.get("brier_skill_vs_half")),
    ]
    rows = [[_esc(k), _esc(NO_DATA_TEXT if v in (None, {}, []) else v)] for k, v in items]
    return _table("aiChallengerCompact", ("compact v5", "value"), rows) + f"<p class='sub'>{_esc(c.get('note') or '')}</p>"


def _behaviour(cohort: dict) -> str:
    b = cohort.get("llm_behaviour") or {}
    agree = cohort.get("agreement_with_llm") or {}
    dead = cohort.get("dead_inputs") or {}
    rows = [
        ["calls", _esc(b.get("calls"))],
        ["raw NO_TRADE calls", _esc(b.get("raw_no_trade_calls"))],
        ["raw NO_TRADE but tiles admitted a side", _esc(b.get("raw_no_trade_but_tiles_admitted_side"))],
        ["abstain-respecting flat calls", _esc(b.get("abstain_respecting_flat_calls"))],
        ["AI error calls", _esc(b.get("ai_error_calls"))],
        ["win_prob status", _esc(b.get("win_prob_status"))],
        ["agreement with rule vote", _esc(_num((agree.get("rule_vote") or {}).get("agreement_rate"), 3, signed=False))],
        ["prompt input health", _esc(f"{dead.get('status')} {[d.get('path') for d in dead.get('dead_fields') or []][:8]}")],
    ]
    return _table("aiChallengerBehaviour", ("LLM behaviour", "value"), rows)


def render_ai_challenger_html(report: dict | None, *, evidence: dict | None, nav_links) -> str:
    report = report if isinstance(report, dict) else {}
    evidence = evidence or {}
    current = evidence.get("status") == "CURRENT_GENERATION" and report.get("schema") == SCHEMA
    if not current or report.get("status") == "ERROR":
        reason = report.get("error") or ", ".join(evidence.get("blockers") or []) or "report not published"
        banner = f"NO CURRENT DATA: {_esc(REPORT_FILE)} is not part of the current analyzer generation ({_esc(reason)})"
        colour = "#f85149"
    else:
        banner = (f"CURRENT generation {_esc(evidence.get('generated_at_display') or '')} · status "
                  f"{_esc(report.get('status'))} · epoch {_esc(report.get('epoch_id') or NO_DATA_TEXT)} · "
                  f"SHADOW ONLY, no orders; tile admission unchanged")
        colour = "#3fb950" if report.get("status") == "OK" else "#d29922"
    nav = " · ".join(f"<a href='{_esc(href)}'>{_esc(label)}</a>" for label, href in nav_links)
    method = report.get("method") or {}
    horizons = report.get("horizons_sec") or []
    sections = []
    for prompt_id, cohort in (report.get("cohorts") or {}).items():
        sections.append(
            f"<h2>Prompt cohort {_esc(prompt_id)}</h2>"
            f"<p class='sub'>{_esc(cohort.get('first_decision_utc'))} to {_esc(cohort.get('last_decision_utc'))}</p>"
            + _behaviour(cohort)
            + "<h3>Net markout vs zero (bp, NONE = flat)</h3>" + _markouts(cohort, horizons)
            + "<h3>Tile geometry proxy</h3>" + _geometry(cohort)
            + "<h3>Compact v5 shadow prompt</h3>" + _compact(cohort)
        )
    placebo = [p for p in report.get("random_placebo") or [] if p.get("flag") != "OK"]
    placebo_html = (f"<p style='color:#f85149'>Random placebo significant at {_esc([p['horizon_sec'] for p in placebo])}s: "
                    "treat all verdicts with suspicion.</p>") if placebo else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>AI vs Challengers</title>
<style>
body{{background:#0d1117;color:#c9d1d9;font-family:Segoe UI,Arial,sans-serif;margin:16px;}}
a{{color:#58a6ff}} table{{border-collapse:collapse;font-size:0.85em;margin:6px 0 14px 0}}
th,td{{border:1px solid #30363d;padding:4px 8px;text-align:left;vertical-align:top}}
th{{background:#161b22}} .sub{{color:#8b949e;font-size:0.85em}} .wrap{{overflow:auto}}
</style></head><body>
<nav class='sub'>{nav}</nav>
<h1>AI vs Challengers</h1>
<p id='aiChallengerBanner' style='color:{colour};font-weight:600'>{banner}</p>
{placebo_html}
<h2>LLM minus challenger at +{_esc((report.get('primary_horizon_sec') or 900) // 60)}m (net bp)</h2>
{_comparisons(report)}
<p class='sub'>{_esc(method.get('scoring'))}. {_esc(method.get('clustering'))}. {_esc(method.get('ci'))}.
{_esc(method.get('multiple_testing'))}. {_esc(method.get('gates'))}.</p>
{''.join(sections)}
</body></html>"""
