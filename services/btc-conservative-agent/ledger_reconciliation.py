"""One reconciliation of trade counts and PnL across the mirror ledger, the analyzer
cohort and (laptop watcher side) Fly's live trades list.

Canonical Win %: wins (exact after-cost net PnL > 0) over every closed trade of
the tile, breakeven included in the denominator -- the same definition as Fly's
``_win_rate_pct``. The exact PnL comes from the terminal cost receipt; the
cent-rounded CSV column is reported alongside only to show the display drift.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

SCHEMA = "ledger_reconciliation_v1"
REPORT_FILE = "ledger_reconciliation.json"
WIN_PCT_DEFINITION = "wins (exact after-cost net PnL > 0) / closed trades, breakeven in denominator"
WIN_PCT_SOURCE = "analyzer ledger_reconciliation (exact terminal cost receipt PnL)"
PNL_TOLERANCE_USD = 0.01
# Same set as bot.STATS_EXCLUDED_EXIT_REASONS: forced closes (deploy flatten, admin
# force-flat) are not strategy outcomes and never count on Fly's tiles or ledger.
FORCED_EXIT_REASONS = frozenset({"ADMIN_MANUAL_CLOSE", "ADMIN_FORCE_FLAT", "CIRCUIT_BREAKER_ADMIN_MANUAL"})
GREEN, AMBER, RED = "GREEN", "AMBER", "RED"


def win_pct(wins: int, closed: int) -> Optional[float]:
    return round(100.0 * int(wins) / int(closed), 1) if closed else None


def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _close_ts(row: Mapping[str, Any]) -> str:
    for key in ("close_ts", "exit_ts", "closed_at", "ts"):
        value = row.get(key)
        if value not in (None, "") and str(value).lower() != "nan":
            return str(value)
    return ""


def parse_ts(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = re.sub(r"(\.\d{6})\d+", r"\1", str(value).replace("Z", "+00:00"))  # ns -> us (py<3.11)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()


def lane_totals(rows: Iterable[Mapping[str, Any]], lanes: Iterable[str], pnl_key: str = "pnl_exact") -> dict:
    out = {str(l).upper(): {"n": 0, "wins": 0, "losses": 0, "flat": 0, "net_pnl_usd": 0.0} for l in lanes}
    for row in rows:
        lane = str(row.get("research_lane") or "").upper()
        if lane not in out:
            continue
        pnl = _num(row.get(pnl_key)) or 0.0
        bucket = out[lane]
        bucket["n"] += 1
        bucket["net_pnl_usd"] += pnl
        bucket["wins" if pnl > 0 else "losses" if pnl < 0 else "flat"] += 1
    for bucket in out.values():
        bucket["net_pnl_usd"] = round(bucket["net_pnl_usd"], 6)
        bucket["win_pct"] = win_pct(bucket["wins"], bucket["n"])
    return out


def build_report(
    *,
    raw_rows: Iterable[Mapping[str, Any]],
    cohort_ids: Iterable[str],
    quarantine_rows: Iterable[Mapping[str, Any]],
    lanes: Iterable[str],
    epoch_id: str = "",
    source_data_through: Optional[str] = None,
    generated_at: Optional[str] = None,
) -> dict:
    """Mirror ledger rows (exact PnL applied) vs the analyzer current cohort.

    ``raw_rows`` are every ledger row the analyzer loaded; ``pnl_exact`` /
    ``pnl_cents`` / ``net_pnl_basis`` keys are expected (falling back to
    ``net_pnl_usd``). Rows in registry lanes of the current epoch that are
    neither in the cohort nor quarantined are an unexplained drop (RED).
    """
    lanes = [str(l).upper() for l in lanes]
    lane_set = set(lanes)
    cohort = {str(t) for t in cohort_ids}
    quarantine = {str(q.get("trade_id") or ""): str(q.get("reason") or "") for q in quarantine_rows}
    raw = list(raw_rows)
    data_through = source_data_through or max((_close_ts(r) for r in raw), default="") or None
    trades: list[dict] = []
    seen: set[str] = set()
    for row in reversed(raw):
        tid = str(row.get("trade_id") or "")
        lane = str(row.get("research_lane") or "").upper()
        row_epoch = str(row.get("epoch_id") or "").strip()
        if not tid or tid in seen or lane not in lane_set:
            continue
        if epoch_id and row_epoch and row_epoch.lower() != "nan" and row_epoch != epoch_id:
            continue
        seen.add(tid)
        exact = _num(row.get("pnl_exact", row.get("net_pnl_usd")))
        cents = _num(row.get("pnl_cents", row.get("net_pnl_usd_csv_cents", row.get("net_pnl_usd"))))
        trades.append({
            "trade_id": tid,
            "research_lane": lane,
            "close_ts": _close_ts(row),
            "pnl_exact": exact,
            "pnl_cents": cents,
            "net_pnl_basis": str(row.get("net_pnl_basis") or ""),
            "in_cohort": tid in cohort,
            "quarantine_reason": quarantine.get(tid) or None,
        })
    trades.sort(key=lambda r: (r["close_ts"], r["trade_id"]))
    unexplained = [t for t in trades if not t["in_cohort"] and not t["quarantine_reason"]]
    not_exact = [t for t in trades if t["in_cohort"] and t["net_pnl_basis"] not in ("", "TERMINAL_COST_RECEIPT_EXACT")]
    cohort_rows = [t for t in trades if t["in_cohort"]]
    analyzer = lane_totals(cohort_rows, lanes)
    mirror = lane_totals(trades, lanes)
    cents_basis = lane_totals(cohort_rows, lanes, pnl_key="pnl_cents")
    reasons: list[str] = []
    level = GREEN
    if unexplained:
        level = RED
        reasons.append(f"{len(unexplained)} current-epoch tile trades dropped from the cohort without a "
                       f"quarantine reason: {[t['trade_id'] for t in unexplained[:5]]}")
    if not_exact:
        level = RED if level == RED else AMBER
        reasons.append(f"{len(not_exact)} cohort trades lack an exact terminal-cost PnL "
                       f"(Win % falls back to cent-rounded values): {[t['trade_id'] for t in not_exact[:5]]}")
    drift = {lane: {"win_pct_exact": analyzer[lane]["win_pct"], "win_pct_cents": cents_basis[lane]["win_pct"]}
             for lane in lanes if analyzer[lane]["win_pct"] != cents_basis[lane]["win_pct"]}
    return {
        "schema": SCHEMA,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "epoch_id": epoch_id or None,
        "source_data_through": data_through,
        "level": level,
        "reasons": reasons,
        "win_pct_definition": WIN_PCT_DEFINITION,
        "win_pct_source": WIN_PCT_SOURCE,
        "lanes": lanes,
        "analyzer_cohort": analyzer,
        "mirror_ledger": mirror,
        "quarantined": {lane: sum(1 for t in trades if t["research_lane"] == lane and t["quarantine_reason"])
                        for lane in lanes},
        "unexplained_drops": unexplained,
        "cents_display_drift": drift,
        "trades": trades,
    }


def _fly_rows(fly_trades: Iterable[Mapping[str, Any]], lanes: set[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in fly_trades or []:
        if not isinstance(row, Mapping):
            continue
        tid = str(row.get("trade_id") or "")
        lane = str(row.get("research_lane") or "").upper()
        if not tid or lane not in lanes:
            continue
        pnl = None
        for key in ("net_pnl_usd", "net", "pnl"):
            pnl = _num(row.get(key))
            if pnl is not None:
                break
        out[tid] = {"trade_id": tid, "research_lane": lane, "close_ts": _close_ts(row), "pnl": pnl,
                    "exit_reason": str(row.get("exit_reason") or "").strip().upper()}
    return out


def mirror_lane_rows(rows: Iterable[Mapping[str, Any]], lanes: Iterable[str], epoch_id: str = "") -> dict[str, dict]:
    """Latest row per trade id of the live mirror ledger, current epoch and tile lanes only."""
    lane_set = {str(l).upper() for l in lanes}
    out: dict[str, dict] = {}
    for row in rows or []:
        tid = str(row.get("trade_id") or "")
        lane = str(row.get("research_lane") or "").upper()
        row_epoch = str(row.get("epoch_id") or "").strip()
        if not tid or lane not in lane_set:
            continue
        if epoch_id and row_epoch and row_epoch.lower() != "nan" and row_epoch != epoch_id:
            continue
        out[tid] = {"trade_id": tid, "research_lane": lane, "close_ts": _close_ts(row),
                    "pnl": _num(row.get("net_pnl_usd")),
                    "exit_reason": str(row.get("exit_reason") or "").strip().upper()}
    return out


def _in_fly_scope(row: Mapping[str, Any], cutoff_ts: Optional[float]) -> bool:
    """Fly's tile scope: closes since its epoch cutoff, forced closes excluded."""
    if row.get("exit_reason") in FORCED_EXIT_REASONS:
        return False
    if cutoff_ts is not None:
        ts = parse_ts(row.get("close_ts"))
        if ts is not None and ts < cutoff_ts:
            return False
    return True


def _fly_lane_totals(fly_state: Mapping[str, Any], lane: str) -> dict:
    ledger = ((fly_state.get("lane_pnl_ledger") or {}).get(lane) or {}) if isinstance(fly_state, Mapping) else {}
    spec_stats: Mapping[str, Any] = {}
    for spec in ((fly_state.get("pathway_lane_specs") or {}).get("lanes") or []) if isinstance(fly_state, Mapping) else []:
        if isinstance(spec, Mapping) and str(spec.get("lane") or "").upper() == lane:
            spec_stats = spec.get("session_stats") or {}
    closes = ledger.get("closes")
    if closes is None:
        closes = spec_stats.get("real_fills")
    return {
        "closes": int(closes) if _num(closes) is not None else None,
        "pnl": _num(ledger.get("net_pnl_usd", spec_stats.get("net_pnl_real"))),
        "ledger_wins": int(ledger["wins"]) if _num(ledger.get("wins")) is not None else None,
        "tile_win_pct": _num(spec_stats.get("win_rate_pct")),
    }


def compare_with_fly(report: Mapping[str, Any], fly_state: Optional[Mapping[str, Any]],
                     mirror_rows: Optional[Iterable[Mapping[str, Any]]] = None,
                     *, mirror_lag_grace_sec: float = 120.0) -> dict:
    """Analyzer report verdict plus Fly live per-tile totals vs the live mirror ledger.

    Fly's ``trades`` list holds only the newest few closes, so per-tile counts come from
    ``lane_pnl_ledger`` (fallback: tile ``session_stats``). Expected Fly closes = mirror
    closes + listed Fly closes newer than the mirror. When the listed window does not
    reach back to the mirror head the count is only a lower bound.
    """
    lanes = [str(l).upper() for l in report.get("lanes") or []]
    level = report.get("level") or GREEN
    reasons = list(report.get("reasons") or [])
    breakdown: dict[str, Any] = {"analyzer_level": report.get("level"), "watermark": report.get("source_data_through")}
    if not isinstance(fly_state, Mapping) or mirror_rows is None:
        missing = "Fly /api/state" if not isinstance(fly_state, Mapping) else "mirror ledger"
        return {"level": RED if level == RED else AMBER, "reasons": reasons + [f"{missing} unavailable"],
                "breakdown": breakdown, "fly_compared": False}
    cutoff_ts = parse_ts(fly_state.get("fresh_epoch_cutoff_utc") or fly_state.get("trade_scope_cutoff_utc"))
    mirror_all = mirror_lane_rows(mirror_rows, lanes, str(report.get("epoch_id") or ""))
    mirror_head = max((parse_ts(r["close_ts"]) or 0.0 for r in mirror_all.values()), default=0.0) or None
    mirror = {t: r for t, r in mirror_all.items() if _in_fly_scope(r, cutoff_ts)}
    listed_all = _fly_rows(fly_state.get("trades") or [], set(lanes))
    listed = {t: r for t, r in listed_all.items() if _in_fly_scope(r, cutoff_ts)}
    listed_ts = [parse_ts(r["close_ts"]) for r in listed.values() if parse_ts(r["close_ts"]) is not None]
    fly_oldest = min(listed_ts) if listed_ts else None
    covered = fly_oldest is not None and mirror_head is not None and fly_oldest <= mirror_head
    newer = {t: r for t, r in listed.items() if t not in mirror_all}
    missing_in_mirror = sorted(
        t for t, r in newer.items()
        if mirror_head is not None and (parse_ts(r["close_ts"]) or 0.0) <= mirror_head - mirror_lag_grace_sec
    )
    analyzer = report.get("analyzer_cohort") or {}
    per_lane: dict[str, dict] = {}
    count_bad: list[str] = []
    pnl_bad: list[str] = []
    for lane in lanes:
        fly = _fly_lane_totals(fly_state, lane)
        m_rows = [r for r in mirror.values() if r["research_lane"] == lane]
        n_rows = [r for r in newer.values() if r["research_lane"] == lane]
        expected = len(m_rows) + len(n_rows)
        expected_pnl = sum(r["pnl"] or 0.0 for r in m_rows + n_rows)
        a = analyzer.get(lane) or {}
        per_lane[lane] = {
            "fly_n": fly["closes"], "mirror_n": len(m_rows), "fly_newer_than_mirror": len(n_rows),
            "analyzer_n": a.get("n"), "quarantined": (report.get("quarantined") or {}).get(lane, 0),
            "fly_pnl": fly["pnl"], "mirror_pnl_cents": round(expected_pnl, 4), "analyzer_pnl": a.get("net_pnl_usd"),
            "win_pct": a.get("win_pct"), "fly_tile_win_pct": fly["tile_win_pct"],
            "fly_ledger_win_pct_cents": win_pct(fly["ledger_wins"], fly["closes"]) if fly["ledger_wins"] is not None
            and fly["closes"] else None,
            "bounded": covered,
        }
        if fly["closes"] is None:
            continue
        diff = fly["closes"] - expected
        if diff < 0 or (covered and diff != 0) or (not covered and diff > 3):
            count_bad.append(f"{lane}: Fly {fly['closes']} vs mirror {len(m_rows)}+{len(n_rows)} newer")
            # Beyond the listed window the gap can be plain mirror lag.
            hard = diff < 0 or (covered and abs(diff) > 1)
            level = RED if hard or level == RED else AMBER
        elif covered and fly["pnl"] is not None and abs(fly["pnl"] - expected_pnl) > PNL_TOLERANCE_USD + 0.005 * expected:
            pnl_bad.append(f"{lane}: Fly ${fly['pnl']} vs mirror ${round(expected_pnl, 2)}")
            level = RED if level == RED else AMBER
    if count_bad:
        reasons.append("trade counts differ: " + "; ".join(count_bad))
    if pnl_bad:
        reasons.append("PnL differs beyond tolerance: " + "; ".join(pnl_bad))
    if missing_in_mirror:
        level = RED if len(missing_in_mirror) > 1 else (RED if level == RED else AMBER)
        reasons.append(f"{len(missing_in_mirror)} Fly closes older than the mirror head are missing from the "
                       f"mirror ledger: {missing_in_mirror[:5]}")
    breakdown.update({
        "per_lane": per_lane, "missing_in_mirror": missing_in_mirror, "mirror_head": mirror_head,
        "fly_listed": len(listed), "fly_oldest_listed": fly_oldest, "bounded": covered,
        "fly_scope_cutoff_ts": cutoff_ts,
        "mirror_out_of_fly_scope": len(mirror_all) - len(mirror),
    })
    return {"level": level, "reasons": reasons, "breakdown": breakdown, "fly_compared": True}
