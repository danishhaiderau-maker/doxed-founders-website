#!/usr/bin/env python3
"""Lightweight research dashboard from JSONL datasets (no server required)."""
from __future__ import annotations

import json
import os
from collections import defaultdict


def _load_jsonl(path: str) -> list:
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _wr(groups: dict) -> None:
    for label, items in sorted(groups.items(), key=lambda x: str(x[0])):
        if not items:
            continue
        wins = sum(1 for x in items if x in ("WIN", "win", True) or (isinstance(x, (int, float)) and x > 0))
        print(f"    {label}: n={len(items)} win_rate={wins / len(items) * 100:.1f}%")


def section_ai_calibration(rows):
    print("\n=== AI Win Rate by Probability Bucket ===")
    buckets = defaultdict(list)
    for r in rows:
        if r.get("schema") != "ai_calibration_v1":
            continue
        buckets[r.get("prob_bucket", "?")].append(r.get("actual"))
    _wr(buckets)


def section_reversal(rows):
    print("\n=== Reversal Risk vs Outcome ===")
    groups = defaultdict(list)
    for r in rows:
        if r.get("phase") == "start":
            continue
        risk = int(r.get("reversal_risk") or 0)
        bucket = f"{(risk // 10) * 10}-{(risk // 10) * 10 + 9}"
        result = r.get("result") or r.get("outcome")
        groups[bucket].append(result)
    _wr(groups)


def section_entry_stage(rows):
    print("\n=== Entry Stage Performance ===")
    groups = defaultdict(list)
    for r in rows:
        if r.get("schema") != "trade_lifecycle_v1":
            continue
        stage = r.get("entry_stage") or "UNKNOWN"
        pnl = float(r.get("net_pnl_usd") or 0)
        groups[stage].append(pnl)
    for label, pnls in sorted(groups.items()):
        wins = sum(1 for p in pnls if p > 0)
        print(f"    {label}: n={len(pnls)} win_rate={wins / len(pnls) * 100:.1f}% avg_pnl=${mean(pnls):.2f}")


def section_regime(rows):
    print("\n=== Market Regime Performance ===")
    groups = defaultdict(list)
    for r in rows:
        if r.get("schema") != "trade_lifecycle_v1":
            continue
        regime = r.get("market_regime") or "UNKNOWN"
        pnl = float(r.get("net_pnl_usd") or 0)
        groups[regime].append(pnl)
    for label, pnls in sorted(groups.items()):
        wins = sum(1 for p in pnls if p > 0)
        avg = sum(pnls) / len(pnls) if pnls else 0
        print(f"    {label}: n={len(pnls)} win_rate={wins / len(pnls) * 100:.1f}% avg_pnl=${avg:.2f}")


def section_trend_health(rows):
    print("\n=== Trend Health Performance ===")
    groups = defaultdict(list)
    for r in rows:
        if r.get("schema") != "trade_lifecycle_v1":
            continue
        th = r.get("trend_health_state") or "UNKNOWN"
        pnl = float(r.get("net_pnl_usd") or 0)
        groups[th].append(pnl)
    for label, pnls in sorted(groups.items()):
        wins = sum(1 for p in pnls if p > 0)
        avg = sum(pnls) / len(pnls) if pnls else 0
        print(f"    {label}: n={len(pnls)} win_rate={wins / len(pnls) * 100:.1f}% avg_pnl=${avg:.2f}")


def mean(vals):
    return sum(vals) / len(vals) if vals else 0.0


def main():
    cal = _load_jsonl("ai_confidence_calibration.jsonl")
    reversal = _load_jsonl("reversal_study.jsonl")
    lifecycle = _load_jsonl("trade_lifecycle.jsonl")
    print("=" * 60)
    print("3-Factor Research Dashboard")
    print("=" * 60)
    section_ai_calibration(cal)
    section_reversal(reversal)
    section_entry_stage(lifecycle)
    section_regime(lifecycle)
    section_trend_health(lifecycle)
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
