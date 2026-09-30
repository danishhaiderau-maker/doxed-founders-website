"""Server-rendered view of tile_evidence_points_report.json (fill worlds, did vs missed,
AI usefulness, fresh collection, quarantine receipt and the n>=30 after-cost EV ranking)."""

from __future__ import annotations

import html

from research.tile_evidence_points import MIN_RANK_SAMPLE, REPORT_FILE

NO_DATA_TEXT = "no data yet"
WORLDS = ("FILLED", "PARTIAL", "NO_FILL", "EXPIRED", "ADMIN_CANCELLED", "UNRESOLVED_OR_RESTING")


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _num(value, digits=4, signed=True):
    if value is None:
        return NO_DATA_TEXT
    try:
        v = float(value)
    except (TypeError, ValueError):
        return NO_DATA_TEXT
    return f"{v:+.{digits}f}" if signed else f"{v:.{digits}f}"


def _ci(ci):
    if not isinstance(ci, (list, tuple)) or len(ci) != 2:
        return ""
    return f"95% CI [{_num(ci[0])}, {_num(ci[1])}]"


def _gated(summary: dict | None, *, min_n: int = MIN_RANK_SAMPLE) -> str:
    """Outcome cell: mean/close with n; below the gate it is descriptive and labelled."""
    summary = summary or {}
    n = int(summary.get("n") or 0)
    if not n:
        rows = summary.get("rows")
        return NO_DATA_TEXT + (f" ({rows} rows, none computable)" if rows else "")
    text = f"{_num(summary.get('mean_usd'))} $/close (n={n}, total {_num(summary.get('total_usd'))})"
    extra = _ci(summary.get("ci95_usd"))
    if n < min_n:
        return _esc(text) + f"<div class='sub warn'>NOT ENOUGH DATA (n&lt;{min_n}), descriptive only</div>"
    return _esc(text) + (f"<div class='sub'>{_esc(extra)}</div>" if extra else "")


def _table(table_id: str, headers, rows) -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<div class='wrap'><table id='{table_id}'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"


def _reconciliation(tile: dict) -> str:
    rec = tile.get("no_fill_source_reconciliation") or {}
    if not rec:
        return ""
    return (f"<div class='sub'>both ledgers {_esc(rec.get('in_both_ledgers', 0))} · expired-orders only "
            f"{_esc(rec.get('expired_orders_ledger_only', 0))} · lifecycle only {_esc(rec.get('lifecycle_ledger_only', 0))}"
            f" · disagreements {_esc(rec.get('world_disagreements', 0))}</div>")


def _label(section: dict, lane: str) -> str:
    return f"<strong>{_esc(section.get('label') or lane)}</strong><div class='sub'>{_esc(lane)}</div>"


def render_evidence_points_html(report: dict | None, *, evidence: dict | None, nav_links) -> str:
    report = report if isinstance(report, dict) else {}
    evidence = evidence or {}
    current = evidence.get("status") == "CURRENT_GENERATION" and report.get("schema") == "tile_evidence_points_v1"
    order = [lane for lane in report.get("tile_order") or []]
    if not current or report.get("status") == "ERROR":
        reason = report.get("error") or ", ".join(evidence.get("blockers") or []) or "report not published"
        banner = f"NO CURRENT DATA: {_esc(REPORT_FILE)} is not part of the current analyzer generation ({_esc(reason)})"
        colour = "#f85149"
    else:
        banner = (f"CURRENT generation {_esc(evidence.get('generated_at_display') or '')} · epoch "
                  f"{_esc(report.get('epoch_id') or NO_DATA_TEXT)} · v2 data from "
                  f"{_esc(report.get('v2_data_start_utc') or NO_DATA_TEXT)} to {_esc(report.get('data_end_utc') or NO_DATA_TEXT)}")
        colour = "#3fb950"
    sections = []
    if current and report.get("status") != "ERROR":
        fw = report.get("fill_worlds") or {}
        rows = []
        for lane in order:
            t = fw.get(lane) or {}
            worlds = t.get("worlds") or {}
            rows.append([_label(t, lane), _esc(t.get("orders_submitted", NO_DATA_TEXT))]
                        + [_esc(worlds.get(w, 0)) for w in WORLDS]
                        + [_esc(t.get("v3_lifecycle_no_fill_terminals", NO_DATA_TEXT)) + _reconciliation(t)])
        sections.append("<h2>4 · Fill worlds per tile order</h2>" + _table(
            "evidenceFillWorlds", ["Tile", "Orders", *WORLDS, "v3 NO_FILL terminals"], rows)
            + "<p class='sub'>PARTIAL and NO_FILL are shown even when zero. ADMIN_CANCELLED are orders cancelled by a "
              "deploy/operator boundary, not by the strategy.</p>")
        rows = []
        for lane in order:
            cf = (fw.get(lane) or {}).get("unfilled_counterfactual") or {}
            for world in ("EXPIRED", "NO_FILL", "ADMIN_CANCELLED"):
                w = cf.get(world) or {}
                rows.append([_label(fw.get(lane) or {}, lane), _esc(world),
                             _gated(w.get("market_at_signal")), _gated(w.get("market_at_expiry"))])
        sections.append("<h3>Counterfactual outcomes for orders that did not fill</h3>" + _table(
            "evidenceNoFillCounterfactual", ["Tile", "World", "Market at signal", "Market at expiry"], rows))

        dvm = report.get("did_vs_missed") or {}
        rows = []
        for lane in order:
            t = dvm.get(lane) or {}
            ex = t.get("executed") or {}
            skipped = t.get("skipped") or {}
            skipped_html = "<br>".join(f"{_esc(reason)}: {_gated(s)}" for reason, s in skipped.items()) or NO_DATA_TEXT
            rows.append([_label(t, lane), _gated(ex.get("actual_after_cost")), _gated(ex.get("market_at_signal")),
                         _gated(t.get("missed_unfilled")), _gated(t.get("missed_admin_cancelled")), skipped_html])
        sections.append("<h2>5 · Did vs missed (same yardstick)</h2>" + _table(
            "evidenceDidVsMissed", ["Tile", "Executed: actual after cost", "Executed: market at signal",
                                    "Missed: expired unfilled", "Missed: admin-cancelled", "Skipped by reason"], rows)
            + f"<p class='sub'>{_esc((report.get('counterfactual_basis') or {}).get('yardstick') or '')}</p>")

        ai = report.get("ai_usefulness") or {}
        rows = []
        for lane in order:
            t = ai.get(lane) or {}
            delta = t.get("ai_filtered_minus_rules_only_usd")
            rows.append([_label(t, lane), _gated(t.get("ai_approved_same_direction")), _gated(t.get("ai_rejected")),
                         _esc(_num(t.get("rules_only_total_usd"))), _esc(_num(t.get("ai_filtered_total_usd"))),
                         _esc(_num(delta)) + f"<div class='sub'>{_esc(t.get('gate') or NO_DATA_TEXT)}</div>",
                         _esc(t.get("unlinked_trades", NO_DATA_TEXT))])
        sections.append("<h2>7 · AI usefulness per tile</h2>" + _table(
            "evidenceAiUsefulness", ["Tile", "AI-approved trades", "AI-rejected trades", "Rules-only total $",
                                     "AI-filtered total $", "AI-filtered minus rules-only", "Unlinked"], rows)
            + f"<p class='sub'>The comparison is gated on the smaller arm; below n={MIN_RANK_SAMPLE} it is "
              "NOT ENOUGH DATA and must not drive a decision.</p>")

        fc = report.get("fresh_collection") or {}
        tiles = fc.get("tiles") or {}
        rows = []
        for lane in order:
            t = tiles.get(lane) or {}
            rows.append([_label(t, lane), _esc(t.get("closed_current_epoch", NO_DATA_TEXT)),
                         _esc(f"{t.get('strategy_exits', 0)} / {t.get('forced_exits', 0)}"),
                         _esc(t.get("closes_per_hour_since_v2_start", NO_DATA_TEXT)),
                         _esc(t.get("closes_per_hour_last_2h", NO_DATA_TEXT)),
                         _esc(t.get("last_close_utc") or NO_DATA_TEXT),
                         _esc(len(t.get("distinct_policy_signatures") or {})),
                         _esc("DRIFT" if t.get("identity_drift") else "none"),
                         _esc(f"{t.get('unresolved_orders', 0)} / {t.get('orphan_orders_past_ttl', 0)}")])
        sections.append("<h2>9 · Fresh collection on the current epoch</h2>" + _table(
            "evidenceFreshCollection", ["Tile", "Closed", "Strategy / forced exits", "Closes/h since v2",
                                        "Closes/h last 2h", "Last close (UTC)", "Policy signatures",
                                        "Identity drift", "Unresolved / orphan orders"], rows))

        q = report.get("quarantine_receipt") or {}
        ev_ex = q.get("ev_ranking_exclusions") or {}
        prec = q.get("net_pnl_precision") or {}
        items = [
            f"Current trade rows: {_esc(q.get('trade_rows_current', NO_DATA_TEXT))}; quarantined: "
            f"{_esc(q.get('trade_rows_quarantined', NO_DATA_TEXT))} {_esc(q.get('trade_quarantine_by_reason') or {})}",
            f"Forced exits excluded from EV ranking ({_esc(', '.join(ev_ex.get('reasons') or []))}): "
            f"{_esc(ev_ex.get('forced_exits_by_tile') or {})}. {_esc(ev_ex.get('policy') or '')}",
            f"Admin-cancelled orders by tile: {_esc(q.get('admin_cancelled_orders_by_tile') or {})}",
            f"Non-tile no-fill rows excluded: {_esc(q.get('no_fill_rows_quarantined') or {})}",
            f"Lifecycle rows excluded: {_esc(q.get('lifecycle_rows_excluded') or {})}",
            f"Net PnL basis: {_esc(prec.get('basis_counts') or {})}; cent-rounded zero rows in the CSV: "
            f"{_esc(prec.get('csv_rounding_zero_rows', NO_DATA_TEXT))}. {_esc(prec.get('policy') or '')}",
        ]
        sections.append("<h2>14 · Quarantine receipt</h2><ul id='evidenceQuarantine'>"
                        + "".join(f"<li>{i}</li>" for i in items) + "</ul>")

        ev = report.get("ev_ranking") or {}
        rows = []
        for r in ev.get("rows") or []:
            costs = r.get("cost_components_usd") or {}
            cost_text = ", ".join(f"{k} {_num(v, 5, signed=False)}" for k, v in costs.items()) or NO_DATA_TEXT
            if r.get("status") == "RANKED":
                status = f"RANK {_esc(r.get('rank'))}<div class='sub'>{_esc(r.get('rank_confidence') or '')}</div>"
            else:
                status = (f"<span class='warn'>NOT ENOUGH DATA</span><div class='sub'>"
                          f"{_esc(r.get('closes_needed'))} more strategy exits needed</div>")
            allc = r.get("all_closes_descriptive") or {}
            rows.append([_esc(r.get("label") or r.get("lane")), status, _esc(r.get("n")),
                         _esc(_num(r.get("mean_usd"))) + f"<div class='sub'>{_esc(_ci(r.get('ci95_usd')))}</div>",
                         _esc(r.get("forced_exits_excluded", 0)),
                         _esc(f"n={allc.get('n', 0)}, mean {_num(allc.get('mean_usd'))}"),
                         _esc(cost_text)])
        sections.append(f"<h2>13 · After-cost EV ranking (n&ge;{MIN_RANK_SAMPLE} strategy exits)</h2>" + _table(
            "evidenceEvRanking", ["Tile", "Status", "n (strategy exits)", "Mean $/close", "Forced exits excluded",
                                  "All closes (descriptive)", "Cost components $"], rows)
            + f"<p class='sub'>{_esc(ev.get('basis') or '')}. Ranked tiles: {_esc(ev.get('ranked_count', 0))}.</p>")

        gaps = report.get("evidence_gaps") or []
        gap_html = "".join(f"<li><strong>{_esc(g.get('code'))}</strong><div class='sub'>{_esc(g.get('detail'))}</div></li>"
                           for g in gaps) or "<li>No evidence gaps detected.</li>"
        sections.append(f"<h2>Evidence gaps</h2><ul id='evidenceGaps'>{gap_html}</ul>")
    nav = " · ".join(f"<a href=\"{_esc(href)}\">{_esc(label)}</a>" for label, href in nav_links)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="180">
<title>Research Dashboard · Evidence points</title>
<style>
body{{background:#0d1117;color:#c9d1d9;font-family:-apple-system,Segoe UI,Arial,sans-serif;margin:0;padding:16px 20px;}}
h1{{font-size:1.35rem;margin:0 0 4px;}} h2{{font-size:1.05rem;margin:22px 0 8px;color:#58a6ff;}} h3{{font-size:.95rem;margin:14px 0 6px;}}
a{{color:#58a6ff;}} .sub{{color:#8b949e;font-size:.78rem;margin-top:2px;}} .warn{{color:#d29922;}}
.banner{{border:2px solid {colour};color:{colour};padding:10px 14px;border-radius:8px;font-weight:700;margin:10px 0;}}
table{{border-collapse:collapse;width:100%;font-size:.84rem;}}
th,td{{border-bottom:1px solid #21262d;padding:6px 8px;text-align:left;vertical-align:top;}}
th{{color:#8b949e;font-weight:600;font-size:.74rem;text-transform:uppercase;}}
ul{{padding-left:18px;}} li{{margin:6px 0;}} .wrap{{overflow-x:auto;}}
</style></head><body>
<h1>Research Dashboard · Evidence points</h1>
<div class="sub"><a href="/">Decision</a> · <a href="/api/evidence-points">JSON</a></div>
<div class="banner" id="evidenceFreshness">{banner}</div>
{''.join(sections)}
<p class="sub">Paper research only. Nothing on this page changes a tile, the relay or live policy.</p>
<h2>More</h2><p>{nav}</p>
</body></html>"""