"""Regression tests for leak-free Type-B entry fingerprint discovery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

import analyzer_research_engine_v62 as analyzer


def build_fixture(rows: int = 120) -> pd.DataFrame:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    payload = []
    for index in range(rows):
        slot = index % 10
        candidate = slot < 4
        is_type_b = slot in (0, 1, 2) if candidate else slot == 4
        payload.append({
            "close_ts": (start + timedelta(minutes=index)).isoformat(),
            "max_profit": 20.0 if is_type_b else 5.0,
            "net_pnl_usd": 2.0 if is_type_b else -1.0,
            "adx_at_entry": 25.0 if candidate else 35.0,
            "conviction_spread": 4.0,
            "features_volume_ratio": 0.9 if candidate else 0.3,
            "context_ema_slope": -0.1,
            "structure_score_at_entry": -3.0,
            "entry_mode_bucket": "CHASE_3PLUS",
            "final_direction": "SHORT",
        })
    return pd.DataFrame(payload)


def main() -> None:
    work = analyzer._enrich_trades_with_buckets(build_fixture())
    result = analyzer._type_b_entry_rule_analysis(work)
    readiness = result["predictor_readiness"]
    rules = result["predictor_rules"]

    assert readiness["total_trades"] == 120
    assert readiness["holdout_trades"] == 36
    assert readiness["status"] == "COLLECTING"
    assert readiness["validated_rules"] >= 1
    assert result["feature_coverage"]["adx"]["pct"] == 100.0

    candidate = next(rule for rule in rules if rule["rule"] == "ADX 18–<30")
    assert candidate["out_of_sample"] is True
    assert candidate["train_n"] >= analyzer.TYPE_B_RULE_MIN_TRAIN_N
    assert candidate["holdout_n"] >= analyzer.TYPE_B_RULE_MIN_HOLDOUT_N
    assert candidate["status"] == "HOLDOUT_POSITIVE"
    assert candidate["train_lift"] > 1.2
    assert candidate["holdout_lift"] > 1.2

    missing = build_fixture(30)
    missing["max_profit"] = None
    empty_result = analyzer._type_b_entry_rule_analysis(
        analyzer._enrich_trades_with_buckets(missing)
    )
    assert empty_result["predictor_readiness"]["total_trades"] == 0
    assert empty_result["predictor_readiness"]["status"] == "EARLY_COLLECTION"
    print("Type-B entry predictor tests passed")


if __name__ == "__main__":
    main()
