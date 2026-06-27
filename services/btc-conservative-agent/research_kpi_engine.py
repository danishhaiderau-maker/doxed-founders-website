#!/usr/bin/env python3
"""Research KPIs: false-reject rate, confidence calibration, AI-vs-shadow scorecard."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from statistics import mean

AI_CALIBRATION_FILE = "ai_confidence_calibration.jsonl"
SHADOW_OUTCOME_FILE = "shadow_outcome.jsonl"
SOFT_REJECT_FILE = "soft_reject_shadow.jsonl"
DECISIONS_FILE = "decisions_3factor.csv"
TRADES_FILE = "trades_3factor.csv"
CONFIDENCE_REPORT_FILE = "confidence_calibration_report.json"
FALSE_REJECT_FEATURES_FILE = "profitable_reject_features.json"


def _load_jsonl(path: str) -> list:
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def build_confidence_calibration_report(cwd: str = None) -> dict:
    cwd = cwd or os.getcwd()
    path = os.path.join(cwd, AI_CALIBRATION_FILE)
    rows = _load_jsonl(path)
    buckets = defaultdict(lambda: {"n": 0, "wins": 0, "sum_pnl": 0.0, "sources": defaultdict(int)})
    for r in rows:
        prob = int(r.get("ai_prob") or 0)
        lo = (prob // 5) * 5
        key = f"{lo}-{lo + 5}"
        pnl = float(r.get("net_pnl_usd") or 0)
        buckets[key]["n"] += 1
        buckets[key]["sum_pnl"] += pnl
        if pnl > 0:
            buckets[key]["wins"] += 1
        buckets[key]["sources"][r.get("source") or "unknown"] += 1
    table = []
    for key in sorted(buckets.keys(), key=lambda x: int(x.split("-")[0])):
        b = buckets[key]
        n = b["n"]
        table.append({
            "bucket": key,
            "n": n,
            "win_rate_pct": round(100.0 * b["wins"] / n, 1) if n else 0,
            "sum_pnl_usd": round(b["sum_pnl"], 2),
            "avg_pnl_usd": round(b["sum_pnl"] / n, 3) if n else 0,
            "sources": dict(b["sources"]),
        })
    report = {"cwd": cwd, "total_samples": len(rows), "buckets": table}
    out = os.path.join(cwd, "confidence_calibration_report.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return report


def build_ai_shadow_scorecard(cwd: str = None) -> dict:
    cwd = cwd or os.getcwd()
    shadows = _load_jsonl(os.path.join(cwd, SHADOW_OUTCOME_FILE))
    soft = _load_jsonl(os.path.join(cwd, SOFT_REJECT_FILE))
    approve_shadow = [s for s in shadows if "SOFT_REJECT" not in str(s.get("block_reason", ""))]
    reject_shadow = [s for s in shadows if "SOFT_REJECT" in str(s.get("block_reason", "")) or str(s.get("block_reason", "")).startswith("AI_")]
    def _agg(rows):
        pnls = [float(r.get("net_pnl_usd") or 0) for r in rows if r.get("filled")]
        return {
            "n": len(rows),
            "filled": len(pnls),
            "sum_pnl_usd": round(sum(pnls), 2),
            "avg_pnl_usd": round(mean(pnls), 3) if pnls else 0,
            "win_rate_pct": round(100 * sum(1 for p in pnls if p > 0) / len(pnls), 1) if pnls else 0,
        }
    reject_ids = {str(r.get("trade_id")) for r in soft}
    reject_shadow = [s for s in shadows if str(s.get("trade_id")) in reject_ids] or reject_shadow
    missed = [s for s in reject_shadow if float(s.get("net_pnl_usd") or 0) > 0]
    card = {
        "ai_rejects_tracked": len(soft),
        "reject_shadow": _agg(reject_shadow),
        "approve_shadow_blocked": _agg(approve_shadow),
        "missed_profit_usd": round(sum(float(s.get("net_pnl_usd") or 0) for s in missed), 2),
        "missed_winners": len(missed),
        "good_reject_blocks": sum(1 for s in reject_shadow if float(s.get("net_pnl_usd") or 0) <= 0),
    }
    return card


def build_false_reject_kpi(cwd: str = None) -> dict:
    cwd = cwd or os.getcwd()
    soft = _load_jsonl(os.path.join(cwd, SOFT_REJECT_FILE))
    shadows = {str(s.get("trade_id")): s for s in _load_jsonl(os.path.join(cwd, SHADOW_OUTCOME_FILE))}
    rejects = len(soft)
    profitable = 0
    for row in soft:
        tid = str(row.get("trade_id") or "")
        sh = shadows.get(tid, {})
        if float(sh.get("net_pnl_usd") or 0) > 0:
            profitable += 1
    rate = round(100.0 * profitable / rejects, 1) if rejects else 0.0
    return {
        "rejects": rejects,
        "profitable_rejects": profitable,
        "false_reject_rate_pct": rate,
        "missed_profit_usd": build_ai_shadow_scorecard(cwd).get("missed_profit_usd", 0),
    }


def refresh_all_research_kpis(cwd: str = None) -> dict:
    cwd = cwd or os.getcwd()
    try:
        from profitable_reject_analysis import export_profitable_reject_features, run_analysis
        run_analysis(cwd)
        patterns = export_profitable_reject_features(cwd)
    except Exception as e:
        patterns = {"error": str(e)}
    calibration = build_confidence_calibration_report(cwd)
    scorecard = build_ai_shadow_scorecard(cwd)
    false_reject = build_false_reject_kpi(cwd)
    payload = {
        "false_reject": false_reject,
        "ai_shadow_scorecard": scorecard,
        "confidence_calibration": calibration,
        "historically_profitable_patterns": patterns,
    }
    return payload


if __name__ == "__main__":
    import sys
    data = refresh_all_research_kpis(sys.argv[1] if len(sys.argv) > 1 else None)
    fr = data.get("false_reject") or {}
    print(f"false_reject_rate={fr.get('false_reject_rate_pct')}% missed=${fr.get('missed_profit_usd')}")
