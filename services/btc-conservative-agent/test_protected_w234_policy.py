from __future__ import annotations

import math
from pathlib import Path

import combo_pathway_config as config
import bot
import paper_policy_protected_w234 as policy


ROOT = Path(__file__).parent


def test_entry_is_independent_paper_only_and_uses_028_percent_anchor():
    long_entry = policy.entry_fields("LONG", 80_000)
    short_entry = policy.entry_fields("SHORT", 80_000)
    assert long_entry["planned_limit_price"] == 79_776.00
    assert short_entry["planned_limit_price"] == 80_224.00
    assert long_entry["paper_only"] is True
    assert long_entry["relay_eligible"] is False
    assert long_entry["policy_id"] == policy.POLICY_ID


def test_chase_is_due_only_every_180_seconds_in_w234_window():
    assert policy.chase_due(created_ts=0, last_chase_ts=600, now=779) is False
    assert policy.chase_due(created_ts=0, last_chase_ts=600, now=780) is True
    assert policy.chase_due(created_ts=0, last_chase_ts=1320, now=1500) is False


def test_atr_percent_fallback_uses_percentage_points_not_fraction():
    # 0.5 means 0.5%, therefore one ATR at 80k is $400 and 2.5 ATR is $1,000.
    assert policy.atr_distance(0, 0.5, 80_000) == 400
    assert policy.tp_price(80_000, "LONG", 0, 0.5) == 81_000
    assert policy.stop_price(80_000, "LONG", 0, 0.5) == 79_000


def test_config_and_policy_identity_match_and_live_copy_is_fail_closed():
    spec = config.COMBO_LANE_SPECS[config.RESEARCH_LANE_PROTECTED_W234]
    assert spec["raw_policy_id"] == policy.POLICY_ID
    assert math.isclose(spec["margin_usd"], 0.25)
    assert spec["platform_relay_eligible"] is False
    assert spec["paper_only"] is True


def test_runtime_enforces_protected_path_end_before_shared_natural_cycle():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    start = source.index("def _apply_protected_w234_atr_stop")
    end = source.index("def _apply_position_exits", start)
    block = source[start:end]
    assert "protected_w234_policy.PATH_END_SEC" in block
    assert 'close_position(pos, "PATH_END_120M")' in block
    exits_start = source.index("def _apply_position_exits")
    exits_end = source.index("CIRCUIT_BREAKER_REASONS", exits_start)
    exits = source[exits_start:exits_end]
    assert exits.index("_apply_protected_w234_atr_stop") < exits.index("_paper_natural_cycle_exits")


def test_protected_route_is_joined_under_its_own_dashboard_key():
    history = [{"shared_ai_call_id": "scan-protected"}]
    pending = [{
        "shared_ai_call_id": "scan-protected",
        "trade_id": "pwch-1",
        "research_lane": policy.LANE,
        "status": "PENDING",
        "limit_price": 79_776.0,
    }]
    enriched, _baseline_counts = bot._attach_patient_chase_routes(history, pending=pending)
    enriched, counts = bot._attach_patient_chase_routes(
        enriched,
        lane=policy.LANE,
        route_key="protected_w234_route",
        pending=pending,
    )
    assert enriched[0]["protected_w234_route"]["trade_id"] == "pwch-1"
    assert enriched[0]["patient_chase_route"]["status"] == "NOT_SELECTED"
    assert counts["pending"] == 1
    assert counts["selected_calls"] == 1
