import json
from pathlib import Path

from research.shadow_lane_comprehensive import build_shadow_lane_comprehensive_report


def _append(path: Path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _row(study, source, lane, entered, filled, pnl=None, reason="NO_FILL", ts="2026-08-21T00:00:00+00:00", regime="BULL"):
    return {
        "study_id": study,
        "source_trade_id": source,
        "shared_ai_call_id": source,
        "research_lane": lane,
        "policy_entered": entered,
        "filled": filled,
        "net_pnl_usd": pnl,
        "exit_reason": reason,
        "entry_features": {"regime": regime},
        "ts": ts,
        "executed": False,
    }


def test_comprehensive_shadow_report_dedupes_and_keeps_paired_lanes_separate(tmp_path):
    data = tmp_path / "data"
    reports = tmp_path / "reports"
    data.mkdir()
    _append(data / "research_events_v22.jsonl", [{"epoch_id": "epoch-current"}])
    _append(data / "type_b_adx_v3_shadow_decisions.jsonl", [
        {"study_id": "adx-reject", "accepted": False, "block_reason": "SPREAD_FLOOR", "ts": "2026-08-21T00:00:00+00:00"},
    ])
    rows = [
        _row("adx-accept", "scan-1", "TYPE_B_HUNTER_ADX_V3_SHADOW", True, True, 2.0, "PROFIT_LOCK_LADDER"),
        _row("v1-accept", "scan-1", "TYPE_B_HUNTER_V1", True, True, -1.0, "THESIS_FAST_CUT"),
        _row("adx-reject", "scan-2", "TYPE_B_HUNTER_ADX_V3_SHADOW", False, True, 9.0, "BUFFER_TRUNCATED"),
        _row("v1-cf", "scan-2", "TYPE_B_HUNTER_V1", False, False),
        # Same study/lane revised later: latest row wins deterministically.
        _row("v1-cf", "scan-2", "TYPE_B_HUNTER_V1", False, True, 3.0, "PROFIT_LOCK_LADDER", "2026-08-21T00:01:00+00:00"),
    ]
    _append(data / "shadow_lane_outcome.jsonl", rows)

    report = build_shadow_lane_comprehensive_report(str(data), str(reports))

    assert report["data_epoch_id"] == "epoch-current"
    assert report["coverage"] == {
        "deduped_lane_records": 4,
        "independent_shared_ai_episodes": 2,
        "paired_multi_lane_episodes": 2,
        "single_lane_episodes": 0,
        "provisional_exit_reasons": ["BUFFER_TRUNCATED"],
    }
    assert report["safety"]["executed_pnl_merged"] is False
    assert report["safety"]["paired_lane_pnl_additive"] is False
    by_cohort = {(row["research_lane"], row["classification"]): row for row in report["cohorts"]}
    assert by_cohort[("TYPE_B_HUNTER_ADX_V3_SHADOW", "POLICY_ENTERED_ACCEPTED")]["net_pnl_usd"] == 2.0
    assert by_cohort[("TYPE_B_HUNTER_V1", "POLICY_ENTERED_ACCEPTED")]["net_pnl_usd"] == -1.0
    rejected = by_cohort[("TYPE_B_HUNTER_ADX_V3_SHADOW", "POLICY_REJECTED_COUNTERFACTUAL")]
    assert rejected["provisional_excluded"] == 1
    assert rejected["completed_terminal_fills"] == 0
    assert rejected["net_pnl_usd"] == 0
    assert rejected["blockers"] == {"SPREAD_FLOOR": 1}
    assert by_cohort[("TYPE_B_HUNTER_V1", "CALIBRATION_COUNTERFACTUAL")]["net_pnl_usd"] == 3.0
    assert (reports / "shadow_lane_comprehensive_report.json").is_file()
    assert not (data / "shadow_lane_comprehensive_report.json").exists()


def test_missing_shadow_input_is_truthful_empty_report(tmp_path):
    report = build_shadow_lane_comprehensive_report(str(tmp_path / "missing"), str(tmp_path / "reports"))
    assert report["coverage"]["deduped_lane_records"] == 0
    assert report["coverage"]["independent_shared_ai_episodes"] == 0
    assert report["cohorts"] == []
    assert report["safety"]["live_policy_change_allowed"] is False
