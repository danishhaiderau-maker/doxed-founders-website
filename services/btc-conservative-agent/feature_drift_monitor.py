#!/usr/bin/env python3
"""Track feature drift: 7d / 30d / current means for key research signals."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from statistics import mean

AI_INPUT_LOG = "ai_input_log.jsonl"
TREND_HEALTH_CSV = "trend_health.csv"
OUTPUT_FILE = "feature_drift_report.json"
FEATURES = ("delta", "volume_ratio", "velocity", "structure_score", "reversal_risk_score")


def _parse_ts(row: dict) -> float:
    for key in ("ts_epoch", "ts"):
        val = row.get(key)
        if val is None:
            continue
        if isinstance(val, (int, float)):
            return float(val)
        try:
            return datetime.fromisoformat(str(val).replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    return 0.0


def _load_ai_rows():
    rows = []
    if not os.path.isfile(AI_INPUT_LOG):
        return rows
    with open(AI_INPUT_LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            ctx = row.get("context") or {}
            upgrade = row.get("ai_input_upgrade") or ctx.get("ai_input_upgrade") or {}
            mc = ctx.get("market_context") or {}
            ms = mc.get("market_structure") or {}
            rows.append({
                "ts": _parse_ts(row),
                "delta": float(ctx.get("delta") or 0),
                "volume_ratio": float(ctx.get("volume_ratio") or 0),
                "velocity": float(ctx.get("velocity") or 0),
                "structure_score": float(ms.get("structure_score") or upgrade.get("structure_score") or 0),
                "reversal_risk_score": float(
                    upgrade.get("reversal_risk_score") or ctx.get("reversal_risk_score") or 0
                ),
            })
    return rows


def _window_mean(rows, feature: str, days: float):
    now = time.time()
    cutoff = now - days * 86400
    vals = [r[feature] for r in rows if r["ts"] >= cutoff]
    return round(mean(vals), 6) if vals else None


def build_report():
    rows = _load_ai_rows()
    report = {
        "generated_ts": datetime.now(timezone.utc).isoformat(),
        "sample_count": len(rows),
        "features": {},
    }
    for feat in FEATURES:
        report["features"][feat] = {
            "mean_7d": _window_mean(rows, feat, 7),
            "mean_30d": _window_mean(rows, feat, 30),
            "mean_current_24h": _window_mean(rows, feat, 1),
        }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return report


def main():
    report = build_report()
    print(f"Feature drift report -> {OUTPUT_FILE} ({report['sample_count']} AI samples)")
    for feat, stats in report["features"].items():
        print(
            f"  {feat}: 7d={stats['mean_7d']} 30d={stats['mean_30d']} "
            f"24h={stats['mean_current_24h']}"
        )


if __name__ == "__main__":
    main()
