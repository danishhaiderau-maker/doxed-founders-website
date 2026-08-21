import json
from pathlib import Path

from research.shadow_lane_comprehensive import build_shadow_lane_comprehensive_report


BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def _append(path: Path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _row(study, source, lane, entered, filled, pnl=None, reason="NO_FILL", ts="2026-08-21T00:00:00+00:00", regime="BULL", epoch="epoch-current", signed=True):
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
        "collection_epoch_id": epoch,
        "policy_signature": "shadow-policy-current" if signed else None,
        "policy_epoch_id": "shadow-policy-epoch-current" if signed else None,
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
        "all_preserved_lane_records": 4,
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


def test_shadow_report_excludes_legacy_foreign_and_malformed_identity_from_current(tmp_path):
    data = tmp_path / "data"
    reports = tmp_path / "reports"
    data.mkdir()
    (data / "research_session.json").write_text(
        json.dumps({"collector_v22_epoch_id": "epoch-current"}), encoding="utf-8"
    )
    _append(data / "shadow_lane_outcome.jsonl", [
        _row("current", "scan-1", "CONTINUOUS", True, True, 2.0),
        _row("legacy", "scan-2", "CONTINUOUS", True, True, 99.0, epoch=None, signed=False),
        _row("foreign", "scan-3", "CONTINUOUS", True, True, 88.0, epoch="epoch-old"),
        _row("malformed", "scan-4", "CONTINUOUS", True, True, 77.0, signed=False),
    ])

    report = build_shadow_lane_comprehensive_report(str(data), str(reports))

    assert report["schema"] == "shadow_lane_comprehensive_v2"
    assert report["epoch_scope"] == {
        "selected_epoch_id": "epoch-current",
        "current_signed_rows": 1,
        "legacy_unscoped_rows": 1,
        "foreign_epoch_rows": 1,
        "malformed_current_identity_rows": 1,
        "qualification_blocked": True,
        "blockers": ["FOREIGN_EPOCH_SHADOW_ROWS", "MALFORMED_CURRENT_SHADOW_IDENTITY"],
    }
    assert report["coverage"]["deduped_lane_records"] == 1
    assert report["coverage"]["all_preserved_lane_records"] == 4
    assert report["cohorts"][0]["net_pnl_usd"] == 2.0
    assert report["cohorts"][0]["policy_signatures"] == ["shadow-policy-current"]
    assert report["legacy_unscoped_cohorts"][0]["net_pnl_usd"] == 99.0


def test_missing_shadow_input_is_truthful_empty_report(tmp_path):
    report = build_shadow_lane_comprehensive_report(str(tmp_path / "missing"), str(tmp_path / "reports"))
    assert report["coverage"]["deduped_lane_records"] == 0
    assert report["coverage"]["independent_shared_ai_episodes"] == 0
    assert report["cohorts"] == []
    assert report["safety"]["live_policy_change_allowed"] is False


def test_shadow_writer_freezes_epoch_and_policy_identity_before_finalization():
    assert "def _shadow_policy_identity" in BOT_SOURCE
    assert '"collection_epoch_id": policy_identity.get("collection_epoch_id")' in BOT_SOURCE
    assert '"policy_signature": policy_identity.get("policy_signature")' in BOT_SOURCE
    assert '"policy_epoch_id": policy_identity.get("policy_epoch_id")' in BOT_SOURCE
    assert '"collection_epoch_id": buf.get("collection_epoch_id")' in BOT_SOURCE
    assert '"policy_signature": buf.get("policy_signature")' in BOT_SOURCE
    assert '"policy_epoch_id": buf.get("policy_epoch_id")' in BOT_SOURCE
    assert 'else "continuous_shared_direction_gap_v1"' in BOT_SOURCE
