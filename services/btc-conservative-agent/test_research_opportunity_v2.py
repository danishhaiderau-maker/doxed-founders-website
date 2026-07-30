import tempfile
from datetime import datetime, timezone

import research_opportunity_v2 as opportunity_module
from research_opportunity_v2 import (
    append_event,
    child_event,
    feature_quality,
    entry_fingerprint,
    materialize,
    opportunity_event,
    precise_adx_bucket,
    rolling_holdout_analysis,
    summarize,
    summarize_lane_verdicts,
)


def _features():
    return {
        "price": 64000, "adx": 27.4, "adx_slope_3": 2.1, "plus_di": 31, "minus_di": 19,
        "di_separation": 12, "atr": 110, "volatility_percentile": 61,
        "volume_ratio": 0.72, "volume_percentile": 54, "ret_1m": 0.001,
        "ret_5m": 0.003, "ema_slope": 2.5, "structure": -2,
        "momentum": 0.2, "delta": -3, "imbalance": 0.2,
        "velocity": -0.001, "directional_spread": 5, "long_score": 20,
        "short_score": 80, "funding_rate_pct_8h": 0.01,
        "session_utc": "US", "is_weekend": False,
    }


def run():
    assert precise_adx_bucket(27.4) == "ADX_25_30"
    assert precise_adx_bucket(None) == "ADX_MISSING"
    base = opportunity_event(
        opportunity_id="scan-1",
        ts="2026-07-26T00:00:00Z",
        direction="SHORT",
        entry_features=_features(),
        mode="PAPER",
        bot_version="v2",
        analyzer_sync_id="v2",
        policy_version="p1",
    )
    rows = [
        base,
        child_event(
            event="OUTCOME", opportunity_id="scan-1",
            ts="2026-07-26T00:10:00Z", lane="CONTINUOUS", mode="PAPER",
            trade_id="cont-1",
            payload={"filled": True, "net_pnl_usd": 1.2, "max_mfe_pct": 16},
        ),
        child_event(
            event="OUTCOME", opportunity_id="scan-1",
            ts="2026-07-26T00:10:00Z", lane="TYPE_B_HUNTER_V1",
            mode="PAUSED_SHADOW", trade_id="tb-1",
            payload={"filled": True, "net_pnl_usd": 2.0, "max_mfe_pct": 18},
        ),
    ]
    opportunities = materialize(rows)
    assert len(opportunities) == 1
    assert opportunities[0]["modes"] == ["PAPER", "PAUSED_SHADOW"]
    report = summarize(opportunities)
    assert report["independent_opportunities"] == 1
    assert report["filled_opportunities"] == 1
    assert report["net_pnl_usd"] == 1.2
    assert report["type_b_outcomes"] == 1
    assert materialize([
        child_event(
            event="OUTCOME", opportunity_id="orphan-child",
            ts="2026-07-26T00:11:00Z", lane="CONTINUOUS", mode="PAPER",
            trade_id="orphan-trade",
            payload={"filled": True, "net_pnl_usd": 99, "max_mfe_pct": 99},
        )
    ]) == []
    fingerprint = entry_fingerprint(opportunities[0])
    assert fingerprint["adx_5"] == "ADX_25_30"
    assert fingerprint["directional_di"] == "OPPOSED_10P"
    quality = feature_quality({"adx": 20})
    assert quality["valid_for_holdout"] is False
    assert "volume_percentile" in quality["critical_missing"]
    invalid = _features()
    invalid["adx"] = float("nan")
    invalid.pop("is_weekend")
    invalid_quality = feature_quality(invalid)
    assert "adx" in invalid_quality["critical_missing"]
    assert "is_weekend" in invalid_quality["missing"]
    assert invalid_quality["valid_for_holdout"] is False

    verdicts = summarize_lane_verdicts([
        child_event(
            event="LANE_VERDICT",
            opportunity_id="scan-verdict-1",
            ts="2026-07-26T00:00:01Z",
            lane="CONTINUOUS",
            payload={"accepted": True, "reason": "APPROVE"},
        ),
        child_event(
            event="LANE_VERDICT",
            opportunity_id="scan-verdict-1",
            ts="2026-07-26T00:00:02Z",
            lane="CONTINUOUS",
            payload={"accepted": True, "reason": "APPROVE"},
        ),
        child_event(
            event="LANE_VERDICT",
            opportunity_id="scan-verdict-1",
            ts="2026-07-26T00:00:03Z",
            lane="TYPE_B_HUNTER_V1",
            payload={"accepted": False, "reason": "ADX_FLOOR"},
        ),
        child_event(
            event="LANE_VERDICT",
            opportunity_id="scan-before-session",
            ts="2026-07-25T00:00:00Z",
            lane="CONTINUOUS",
            payload={"accepted": True, "reason": "APPROVE"},
        ),
    ], since_epoch=datetime(2026, 7, 26, tzinfo=timezone.utc))
    assert verdicts["unique_verdicts"] == 2
    assert verdicts["lanes"]["CONTINUOUS"]["evaluated"] == 1
    assert verdicts["lanes"]["CONTINUOUS"]["accepted"] == 1
    assert verdicts["lanes"]["TYPE_B_HUNTER_V1"]["rejected"] == 1
    assert (
        verdicts["by_opportunity"]["scan-verdict-1"]["TYPE_B_HUNTER_V1"]["reason"]
        == "ADX_FLOOR"
    )

    mixed_mode_events = []
    for mode, runner_count in (("PAPER", 5), ("PAUSED_SHADOW", 1)):
        for idx in range(10):
            oid = f"mode-{mode.lower()}-{idx}"
            features = _features()
            features["volume_ratio"] = 1.6
            mixed_mode_events.extend([
                opportunity_event(
                    opportunity_id=oid,
                    ts=f"2026-07-01T00:{idx:02d}:00Z",
                    direction="SHORT",
                    entry_features=features,
                    mode=mode,
                    bot_version="v2",
                    analyzer_sync_id="v2",
                    policy_version="p1",
                ),
                child_event(
                    event="OUTCOME",
                    opportunity_id=oid,
                    ts=f"2026-07-01T00:{idx:02d}:30Z",
                    lane="CONTINUOUS",
                    mode=mode,
                    trade_id=f"cont-{mode.lower()}-{idx}",
                    payload={
                        "filled": True,
                        "net_pnl_usd": 1 if idx < runner_count else -1,
                        "max_mfe_pct": 18 if idx < runner_count else 4,
                    },
                ),
            ])
    mode_rules = opportunity_module._candidate_rules(
        materialize(mixed_mode_events),
        min_n=1,
    )
    paper_volume = next(
        rule for rule in mode_rules
        if rule["rule_key"] == "evidence_mode=PAPER AND volume_ratio=VOLR_150P"
    )
    shadow_volume = next(
        rule for rule in mode_rules
        if rule["rule_key"] == "evidence_mode=PAUSED_SHADOW AND volume_ratio=VOLR_150P"
    )
    assert paper_volume["mode_baseline_pct"] == 50.0
    assert shadow_volume["mode_baseline_pct"] == 10.0
    assert paper_volume["lift"] == 1.0
    assert shadow_volume["lift"] == 1.0

    rolling_events = []
    for idx in range(220):
        features = _features()
        is_runner = idx % 3 == 0
        features["volume_ratio"] = 1.6 if is_runner else 0.6
        oid = f"scan-{idx:03d}"
        rolling_events.extend([
            opportunity_event(
                opportunity_id=oid,
                ts=f"2026-07-{1 + idx // 24:02d}T{idx % 24:02d}:00:00Z",
                direction="SHORT",
                entry_features=features,
                mode="PAPER",
                bot_version="v2",
                analyzer_sync_id="v2",
                policy_version="p1",
            ),
            child_event(
                event="OUTCOME",
                opportunity_id=oid,
                ts=f"2026-07-{1 + idx // 24:02d}T{idx % 24:02d}:10:00Z",
                lane="CONTINUOUS",
                mode="PAPER",
                trade_id=f"cont-{idx:03d}",
                payload={
                    "filled": True,
                    "net_pnl_usd": 2.0 if is_runner else -0.5,
                    "max_mfe_pct": 18.0 if is_runner else 4.0,
                },
            ),
        ])
    rolling = rolling_holdout_analysis(materialize(rolling_events))
    assert rolling["eligible_outcomes"] == 220
    assert rolling["windows_completed"] >= 3
    assert rolling["manual_review_ready"] is True
    assert rolling["production_entry_gate_ready"] is False
    assert any(
        "volume_ratio=VOLR_150P" in rule["rule_key"]
        for rule in rolling["validated_rules"]
    )
    original_max = opportunity_module.MAX_EVENT_FILE_BYTES
    try:
        opportunity_module.MAX_EVENT_FILE_BYTES = 200
        with tempfile.TemporaryDirectory() as tmp:
            for index in range(4):
                append_event(tmp, opportunity_event(
                    opportunity_id=f"rotation-{index}",
                    ts=f"2026-07-26T0{index}:00:00Z",
                    direction="SHORT",
                    entry_features=_features(),
                    mode="PAPER",
                    bot_version="v2",
                    analyzer_sync_id="v2",
                    policy_version="p1",
                ))
            assert len(opportunity_module.load_events(tmp)) == 4
    finally:
        opportunity_module.MAX_EVENT_FILE_BYTES = original_max
    print("PASS: Type-B Research V2 opportunity, holdout, orphan, and rotation checks")


if __name__ == "__main__":
    run()
