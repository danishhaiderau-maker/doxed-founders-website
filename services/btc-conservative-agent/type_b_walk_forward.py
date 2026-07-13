#!/usr/bin/env python3
"""Chronological, non-tuning validation for the Type B Hunter policy.

This command joins the pre-entry Type B AI input to its terminal shadow outcome,
then evaluates the *existing* policy on a held-out chronological window.  It
does not fit weights, alter thresholds, or consume the outcome label while
making a policy decision.  Its purpose is to prevent the common mistake of
optimising Type B on its own post-trade labels.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from type_b_hunter_v1 import LANE_ID, should_enter_type_b


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _entry_features(ai_row: dict[str, Any]) -> dict[str, Any]:
    """Return only pre-entry fields available when the policy makes a decision."""
    context = dict(ai_row.get("context") or {})
    context["market_context"] = context.get("market_context") or {}
    ai = ai_row.get("ai") or {}
    context["directional_spread"] = abs(
        _number(ai.get("bull_score")) - _number(ai.get("bear_score"))
    )
    context["spread"] = context["directional_spread"]
    context["edge_score"] = context.get("edge_score")
    return context


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    entries = [row for row in rows if row["policy_entered"]]
    pnl = round(sum(row["net_pnl_usd"] for row in entries), 2)
    wins = sum(1 for row in entries if row["net_pnl_usd"] > 0)
    type_b = sum(1 for row in entries if row["is_type_b"])
    count = len(entries)
    return {
        "joined_outcomes": len(rows),
        "policy_entries": count,
        "entry_rate_pct": round(100.0 * count / len(rows), 1) if rows else 0.0,
        "net_pnl_usd": pnl,
        "ev_per_close_usd": round(pnl / count, 2) if count else 0.0,
        "win_rate_pct": round(100.0 * wins / count, 1) if count else 0.0,
        "type_b_rate_pct": round(100.0 * type_b / count, 1) if count else 0.0,
        "counterfactual_outcomes": sum(1 for row in rows if not row["policy_entered"]),
    }


def evaluate(agent_dir: Path, holdout_fraction: float = 0.30) -> dict[str, Any]:
    ai_inputs = {
        str(row.get("trade_id")): row
        for row in _read_jsonl(agent_dir / "ai_input_log.jsonl")
        if str(row.get("research_lane") or "").upper() == LANE_ID and row.get("trade_id")
    }
    terminal: dict[str, dict[str, Any]] = {}
    for outcome in _read_jsonl(agent_dir / "shadow_lane_outcome.jsonl"):
        if str(outcome.get("research_lane") or "").upper() != LANE_ID:
            continue
        source_id = str(outcome.get("source_trade_id") or "")
        # Last terminal snapshot wins if a study was written more than once.
        if source_id and source_id in ai_inputs:
            terminal[str(outcome.get("trade_id") or outcome.get("study_id") or source_id)] = outcome

    joined: list[dict[str, Any]] = []
    for outcome in terminal.values():
        if not outcome.get("filled"):
            continue
        source_id = str(outcome.get("source_trade_id"))
        ai_row = ai_inputs[source_id]
        ai = ai_row.get("ai") or {}
        features = _entry_features(ai_row)
        entered, detail = should_enter_type_b(int(_number(ai.get("win_prob"))), features)
        joined.append({
            "ts_epoch": _number(ai_row.get("ts_epoch"), _number(outcome.get("ts_epoch"))),
            "policy_entered": bool(entered),
            "policy_score": _number(detail.get("score")),
            "net_pnl_usd": _number(outcome.get("net_pnl_usd")),
            # Label is report-only; it is never supplied to should_enter_type_b.
            "is_type_b": _number(outcome.get("max_profit_margin_pct")) >= 15.0,
        })
    joined.sort(key=lambda row: row["ts_epoch"])
    split = max(1, int(len(joined) * (1.0 - holdout_fraction))) if joined else 0
    train, holdout = joined[:split], joined[split:]
    holdout_summary = _summary(holdout)
    # A history containing only policy-entered shadows is selection-biased: it can
    # describe the current policy but cannot validate its calibration.  Promotion
    # requires terminal outcomes for signals the policy would have rejected too.
    verdict = "INSUFFICIENT_HOLDOUT"
    if not holdout_summary["counterfactual_outcomes"]:
        verdict = "INSUFFICIENT_COUNTERFACTUAL_COVERAGE"
    elif holdout_summary["policy_entries"] >= 30:
        verdict = "READY_FOR_REVIEW" if holdout_summary["ev_per_close_usd"] > 0 else "HOLD_NEGATIVE_EV"
    return {
        "schema": "type_b_walk_forward_v1",
        "policy": "existing fixed Type B policy; validation only; no fitting or threshold mutation",
        "lane": LANE_ID,
        "holdout_fraction": holdout_fraction,
        "ai_input_rows": len(ai_inputs),
        "joined_filled_outcomes": len(joined),
        "train": _summary(train),
        "holdout": holdout_summary,
        "verdict": verdict,
    }


def self_test() -> None:
    sample = [
        {"ts_epoch": float(i), "policy_entered": True, "net_pnl_usd": float(i - 3), "is_type_b": i % 2 == 0}
        for i in range(10)
    ]
    split = int(len(sample) * 0.7)
    assert [r["ts_epoch"] for r in sample[:split]] == list(range(7))
    assert _summary(sample[split:])["policy_entries"] == 3
    assert _summary(sample[split:])["joined_outcomes"] == 3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print(json.dumps({"ok": True, "test": "type_b_walk_forward_self_test"}))
        return 0
    report = evaluate(args.agent_dir.resolve())
    output = args.output or args.agent_dir.resolve() / "type_b_walk_forward_report.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
