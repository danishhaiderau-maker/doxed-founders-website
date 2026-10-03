"""Dedicated contract for Danish — no early stop: Danish A without the conditional early cut (recorded as a shadow exit only)."""
import pytest

import paper_policy_family_danish_cf as danish_a
import paper_policy_family_danish_cf_noes as policy
from adaptive_regime_entry import ACTION_MAKER, ACTION_STAND_ASIDE
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    DANISH_CF_ADMISSION_POLICY_ID,
    tile_card_sections,
    tile_number,
    validate_tile_registry,
)

NOW = 1_790_000_000.0  # 14:13 UTC (EU session)
US_TS = NOW + 4 * 3600


def test_registry_owns_tile_two_paper_only_relay_ineligible_default_off():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[1] == policy.LANE and tile_number(policy.LANE) == 2
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["default_enabled"] is False
    assert spec["id_prefix"] == "dcn" and spec["max_active_signals"] == 3
    assert spec["implementation_modules"] == ("paper_policy_family_danish_cf_noes.py",)
    assert spec["dedicated_test_modules"] == ("test_paper_policy_family_danish_cf_noes.py",)
    assert spec["admission_treatment"] == DANISH_CF_ADMISSION_POLICY_ID
    assert policy.POLICY_SIGNATURE != danish_a.POLICY_SIGNATURE


def test_entry_is_identical_to_danish_a():
    assert COMBO_LANE_SPECS[policy.LANE]["entry_policy"] == COMBO_LANE_SPECS[danish_a.LANE]["entry_policy"]
    assert policy.decide_entry(direction="LONG", signal_ts=NOW, bid=99_999.0, ask=100_001.0,
                               bbo_ts=NOW - 1.0, reference_price=100_000.0)["action"] == ACTION_MAKER
    gated = policy.decide_entry(direction="LONG", signal_ts=US_TS, bid=99_999.0, ask=100_001.0,
                                bbo_ts=US_TS - 1.0, reference_price=100_000.0)
    assert gated["action"] == ACTION_STAND_ASIDE and gated["reason"] == "SESSION_GATED"


def test_confirm_to_market_matches_danish_a():
    for bid, ask in ((100_009.0, 100_011.0), (100_029.0, 100_031.0), (100_090.0, 100_092.0)):
        args = {"direction": "LONG", "signal_price": 100_000.0, "limit_price": 99_900.0,
                "bid": bid, "ask": ask, "confirmed_ts": None, "now": NOW}
        assert policy.confirm_market_action(**args) == danish_a.confirm_market_action(**args)


def test_exit_has_no_live_early_cut_and_records_it_as_shadow_only():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert spec["exit_policy"]["early_cut"] is None
    assert spec["live_exit_order"] == ("HARD_STOP", "BREAKEVEN_LOCK", "TIME_EXIT")
    assert spec["early_cut_shadow_reason"]
    assert policy.SPEC.thesis_cut_margin_pct is None
    assert policy.SPEC.breakeven_trigger_margin_pct == 20.0 and policy.SPEC.breakeven_lock_margin_pct == 5.0
    # The -12 bp / 5 min drawdown that cuts Danish A stays open here.
    assert danish_a.exit_action(entry=100_000.0, direction="LONG", price=99_875.0, age_sec=120) is not None
    assert policy.exit_action(entry=100_000.0, direction="LONG", price=99_875.0, age_sec=120) is None
    hard = policy.exit_action(entry=100_000.0, direction="LONG", price=99_590.0, age_sec=60)
    assert hard is not None and "STOP" in hard.reason


def test_dashboard_and_card_show_the_early_cut_as_shadow_only():
    chips = " ".join(policy.dashboard_policy()["filter_chips"])
    assert "PAPER ONLY" in chips and "Break-even armed at +20bp" in chips
    assert "Early cut" not in chips
    sections = tile_card_sections(policy.LANE)
    assert not any("early cut" in line.lower() for line in sections["exit"]["live"])
    assert any("$0.25 margin @100x" in line for line in sections["risk"])


@pytest.mark.parametrize("ai,accepted", [
    ({"raw_direction": "LONG", "raw_decision": "APPROVE", "direction": "LONG", "decision": "APPROVE",
      "long_score": 70, "short_score": 30}, True),
    ({"raw_direction": "NO_TRADE", "raw_decision": "REJECT", "direction": "NO_TRADE", "decision": "REJECT",
      "long_score": 62, "short_score": 38}, False),
])
def test_admission_fades_only_committed_calls(ai, accepted):
    from combo_pathway_config import resolve_score_led_paper_admission

    admission = resolve_score_led_paper_admission(
        ai, score_led_enabled=True, research_mode=True, forced_paper=True,
        live_armed=False, bitfinex_live_enabled=False,
    )
    view = policy.lane_admission(ai, admission)
    assert view["accepted"] is accepted
    if accepted:
        assert view["direction"] == "SHORT"
