from pathlib import Path

import paper_policy_offset029 as policy
import pathway_lab_validation
from combo_pathway_config import (
    COMBO_EXECUTION_LANES,
    COMBO_LANE_SPECS,
    COMBO_TILE_DISPLAY_ORDER,
    RESEARCH_CANDIDATE_LANE,
    RESEARCH_LANE_OFFSET_029_ATR_TP_25,
    RESEARCH_LANE_TYPE_B_HUNTER_V1,
)


def test_exact_anchor_and_chase_windows():
    assert policy.initial_limit("LONG", 70_000) == 69_797.0
    assert policy.initial_limit("SHORT", 70_000) == 70_203.0
    assert not policy.chase_due(created_ts=0, last_chase_ts=0, now=599)
    assert policy.chase_due(created_ts=0, last_chase_ts=0, now=600)
    assert not policy.chase_due(created_ts=0, last_chase_ts=1_440, now=1_499)
    assert policy.chase_due(created_ts=0, last_chase_ts=1_439, now=1_499)
    assert not policy.chase_due(created_ts=0, last_chase_ts=1_439, now=1_500)


def test_chase_uses_side_correct_bbo_not_last_trade_touch():
    assert not policy.marketable_quote_at_limit(
        direction="LONG", limit_price=99.0, bid=98.5, ask=100.0
    )
    assert policy.marketable_quote_at_limit(
        direction="LONG", limit_price=99.0, bid=98.5, ask=99.0
    )
    assert not policy.marketable_quote_at_limit(
        direction="SHORT", limit_price=101.0, bid=100.0, ask=101.5
    )
    assert policy.marketable_quote_at_limit(
        direction="SHORT", limit_price=101.0, bid=101.0, ask=101.5
    )


def test_atr_target_and_path_end_are_exact():
    assert policy.atr_target(100, "LONG", atr_abs=2) == 105
    assert policy.atr_target(100, "SHORT", atr_abs=2) == 95
    assert policy.exit_decision(
        entry=100, direction="LONG", price=105, atr_abs=2, atr_pct=0, age_sec=30,
    ) == ("ATR_TP_2_5X", 105)
    assert policy.exit_decision(
        entry=100, direction="LONG", price=99, atr_abs=2, atr_pct=0, age_sec=7_200,
    ) == ("PATH_END_120M", 105)
    assert policy.exit_decision(
        entry=100, direction="LONG", price=99, atr_abs=0, atr_pct=0, age_sec=7_199,
    ) == (None, None)


def test_active_roster_contains_patient_chase_family_and_not_retired_type_b():
    assert COMBO_EXECUTION_LANES[0] == RESEARCH_LANE_OFFSET_029_ATR_TP_25
    assert len(COMBO_EXECUTION_LANES) == 3
    assert COMBO_TILE_DISPLAY_ORDER == COMBO_EXECUTION_LANES
    assert RESEARCH_CANDIDATE_LANE == RESEARCH_LANE_OFFSET_029_ATR_TP_25
    spec = COMBO_LANE_SPECS[RESEARCH_LANE_OFFSET_029_ATR_TP_25]
    assert spec["raw_policy_id"] == policy.POLICY_ID
    assert spec["paper_only"] is False
    assert spec["platform_relay_eligible"] is True
    assert spec["uses_shared_ai_direction"] is True
    assert spec["is_independent_ai"] is False
    assert spec["is_legacy"] is False
    assert RESEARCH_LANE_TYPE_B_HUNTER_V1 not in COMBO_EXECUTION_LANES
    assert COMBO_LANE_SPECS[RESEARCH_LANE_TYPE_B_HUNTER_V1]["is_legacy"] is True


def test_startup_shared_ai_validator_accepts_frozen_four_tile_roster():
    report = pathway_lab_validation.run_independent_v1_post_ai_spawn_validation()
    assert report["verdict"] == "PASS"
    assert report["lanes"] == list(COMBO_EXECUTION_LANES)
    assert all(check["passed"] for check in report["checks"])


def test_bot_adapter_is_paper_first_and_separately_relay_allowlisted():
    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    relay_block = source.split("PLATFORM_RELAY_ELIGIBLE_LANES =", 1)[1].split(")", 1)[0]
    assert "RESEARCH_LANE_OFFSET_029_ATR_TP_25" in relay_block
    assert "if lane in PAPER_ONLY_RESEARCH_LANES:\n        return EXEC_MODE_PAPER" in source
    assert "return _apply_offset_029_atr_exit(pos, price, now)" in source
    assert "is_patient_chase_lane" in source
    chase_adapter = source[
        source.index("def _apply_offset_029_policy_chase(") :
        source.index("def microstructure_capture_loop(")
    ]
    assert "offset029_policy.marketable_quote_at_limit(" in chase_adapter
    assert "_pending_limit_touched(" not in chase_adapter


def test_dashboard_copy_is_truthful_and_complete():
    view = policy.dashboard_policy()
    joined = " ".join(view["strategy_detail"])
    assert "No Scenario C ladder" in joined
    assert "separately armed" in joined
    assert "120m PATH_END" in joined
    assert "0.29%" in joined


def test_rendered_tile_has_exact_policy_copy_without_retired_defaults():
    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    assert '"0.29% Patient Chase" if offset_029' in source
    assert '"Tests patient entry quality and ATR-normalized profit capture"' in source
    assert '"Paper-only; no protective hard stop. Path-end losses and execution uncertainty remain"' in source
    assert '"Entry: 0.29% patient maker anchor vs Continuous 0.10% benchmark"' in source
    assert '"Exit: 2.5× frozen 3m ATR target; otherwise 120m path end"' in source
