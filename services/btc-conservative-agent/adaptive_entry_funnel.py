"""Adaptive entry funnel: every signal-time decision scored against one counterfactual.

Each adaptive decision (taker, maker or stand-aside) is joined by
``shared_ai_call_id`` to the taker-at-signal counterfactual that the runtime
records for every admitted directional call, and to the paper trade or expiry
it produced. Stand-asides, expiries and refused orders therefore carry the same
"what an immediate taker entry would have marked" number as filled trades, so
standing aside can be scored against trading. Rows are split by the stamped
``bot_version``; superseded stack versions are reported as quarantined cohorts
and never mixed into the current cohort. Tiles share one AI call, so the
current cohort is also broken down per tile lane: each tile is its own cohort.
"""
from __future__ import annotations

import csv
import json
import math
import os
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

SCHEMA = "adaptive_entry_funnel_v1"
REPORT_FILE = "adaptive_entry_funnel_report.json"
DECISIONS_FILE = "adaptive_entry_decisions.jsonl"
COUNTERFACTUAL_LATENCY_SEC = 1.0
HORIZONS = ("60s", "300s")
UNSTAMPED_COHORT = "UNSTAMPED"
# Stack versions whose adaptive decisions never reached the order path. Decision
# rows were first stamped with bot_version in v2, so unstamped rows are v1.
SUPERSEDED_COHORTS = {
    "v31-dynamic-adaptive-paper-v1": "ADAPTIVE_DECISION_PLUMBING_DEFECT",
    "v31-dynamic-adaptive-paper-v2": "ADAPTIVE_DECISION_PLUMBING_DEFECT",
    "v31-dynamic-adaptive-paper-v3": "SUPERSEDED_SINGLE_TILE_STACK",
    "v31-dynamic-adaptive-ladder-paper-v4": "SUPERSEDED_FOUR_TILE_STACK",
    UNSTAMPED_COHORT: "ADAPTIVE_DECISION_PLUMBING_DEFECT",
}


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _read_jsonl(path: str) -> list[dict[str, Any]]:
    rows = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def _read_csv(path: str) -> list[dict[str, Any]]:
    try:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _summary(values: Iterable[float]) -> dict[str, Any]:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {"n": 0, "mean_bps": None, "median_bps": None, "positive_rate": None}
    mid = len(vals) // 2
    median = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0
    return {
        "n": len(vals),
        "mean_bps": round(sum(vals) / len(vals), 4),
        "median_bps": round(median, 4),
        "positive_rate": round(sum(1 for v in vals if v > 0) / len(vals), 4),
    }


def _counterfactual_markouts(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        if _finite(row.get("latency_sec")) != COUNTERFACTUAL_LATENCY_SEC:
            continue
        marks = row.get("markouts") or {}
        values = {
            horizon: _finite((marks.get(horizon) or {}).get("markout_exit_touch_bps"))
            for horizon in HORIZONS
        }
        out[str(row.get("shared_ai_call_id") or "")] = values
    return out


def _outcomes(trades, expired, lanes) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in expired:
        lane = str(row.get("research_lane") or "").upper()
        if lane in lanes and row.get("shared_ai_call_id"):
            out[(lane, str(row["shared_ai_call_id"]))] = {"outcome": "EXPIRED", "net_pnl_usd": None}
    for row in trades:
        lane = str(row.get("research_lane") or "").upper()
        if lane in lanes and row.get("shared_ai_call_id"):
            out[(lane, str(row["shared_ai_call_id"]))] = {
                "outcome": "FILLED_CLOSED",
                "net_pnl_usd": _finite(row.get("net_pnl_usd")),
                "exit_reason": row.get("exit_reason"),
            }
    return out


def build_report(*, decisions, taker_counterfactuals, trades, expired,
                 current_version: str) -> dict[str, Any]:
    lanes = {str(d.get("research_lane") or d.get("lane") or "").upper() for d in decisions} - {""}
    cf = _counterfactual_markouts(taker_counterfactuals)
    outcomes = _outcomes(trades, expired, lanes)
    cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in decisions:
        lane = str(decision.get("research_lane") or decision.get("lane") or "").upper()
        call_id = str(decision.get("shared_ai_call_id") or "")
        action = str(decision.get("action") or "UNKNOWN")
        if action == "STAND_ASIDE":
            outcome = {"outcome": "STOOD_ASIDE", "net_pnl_usd": None}
        else:
            outcome = outcomes.get((lane, call_id)) or {"outcome": "NO_TRADE_OR_EXPIRY_ROW", "net_pnl_usd": None}
        cohorts[str(decision.get("bot_version") or UNSTAMPED_COHORT)].append({
            "lane": lane,
            "action": action,
            "reason": decision.get("reason"),
            "regime": decision.get("regime"),
            "outcome": outcome["outcome"],
            "net_pnl_usd": outcome.get("net_pnl_usd"),
            "cf": cf.get(call_id) or {},
        })

    def cohort_view(rows):
        groups = defaultdict(list)
        for row in rows:
            groups[(row["action"], row["outcome"])].append(row)
        return {
            "decisions": len(rows),
            "by_action": dict(Counter(r["action"] for r in rows)),
            "by_reason": dict(Counter(str(r["reason"]) for r in rows)),
            "by_regime": dict(Counter(str(r["regime"]) for r in rows)),
            "by_outcome": dict(Counter(r["outcome"] for r in rows)),
            "counterfactual_coverage": round(
                sum(1 for r in rows if r["cf"].get("60s") is not None) / len(rows), 4
            ) if rows else None,
            "stand_aside_vs_trade": [
                {
                    "action": action,
                    "outcome": outcome,
                    "n": len(group),
                    "realized_net_pnl_usd": round(sum(r["net_pnl_usd"] or 0.0 for r in group), 6),
                    **{
                        f"taker_counterfactual_{h}": _summary(r["cf"].get(h) for r in group)
                        for h in HORIZONS
                    },
                }
                for (action, outcome), group in sorted(groups.items())
            ],
        }

    current = cohorts.pop(current_version, [])
    quarantined = {
        version: {"reason": SUPERSEDED_COHORTS.get(version, "NON_CURRENT_STACK_VERSION"), **cohort_view(rows)}
        for version, rows in sorted(cohorts.items())
    }
    return {
        "schema": SCHEMA,
        "current_version": current_version,
        "counterfactual": {
            "basis": "taker_signal_counterfactual_v1 depth-VWAP entry, side-correct exit touch",
            "latency_sec": COUNTERFACTUAL_LATENCY_SEC,
            "horizons": list(HORIZONS),
            "reading": "Positive mean means an immediate taker entry would have gained; for stand-asides that is the cost of standing aside",
        },
        "current_cohort": cohort_view(current),
        "current_cohort_by_lane": {
            lane: cohort_view([row for row in current if row["lane"] == lane])
            for lane in sorted({row["lane"] for row in current})
        },
        "quarantined_cohorts": quarantined,
    }


def build_report_from_paths(*, decisions_path, counterfactual_path, trades_path,
                            expired_path, current_version) -> dict[str, Any]:
    report = build_report(
        decisions=_read_jsonl(decisions_path),
        taker_counterfactuals=_read_jsonl(counterfactual_path),
        trades=_read_csv(trades_path),
        expired=_read_csv(expired_path),
        current_version=current_version,
    )
    report["inputs"] = {
        "decisions": os.path.abspath(decisions_path),
        "taker_counterfactuals": os.path.abspath(counterfactual_path),
        "trades": os.path.abspath(trades_path),
        "expired": os.path.abspath(expired_path),
    }
    return report
