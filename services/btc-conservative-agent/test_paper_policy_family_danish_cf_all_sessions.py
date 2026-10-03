"""Dedicated contract for Danish — all sessions (Tile 7b): Danish A without the Asia+EU session gate."""
import paper_policy_family_danish_cf as danish_a
import paper_policy_family_danish_cf_all_sessions as policy
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
ASIA_TS = NOW + 12 * 3600


def _entry(module, ts, **kwargs):
    args = {"direction": "SHORT", "signal_ts": ts, "bid": 99_999.0, "ask": 100_001.0,
            "bbo_ts": ts - 1.0, "reference_price": 100_000.0, **kwargs}
    return module.decide_entry(**args)


def test_registry_owns_tile_three_paper_only_relay_ineligible_default_off():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[2] == policy.LANE and tile_number(policy.LANE) == 3
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["default_enabled"] is False
    assert spec["id_prefix"] == "dca" and spec["max_active_signals"] == 3
    assert spec["implementation_modules"] == ("paper_policy_family_danish_cf_all_sessions.py",)
    assert spec["dedicated_test_modules"] == ("test_paper_policy_family_danish_cf_all_sessions.py",)
    assert spec["admission_treatment"] == DANISH_CF_ADMISSION_POLICY_ID
    assert spec["entry_policy"]["allowed_sessions"] == ("ASIA", "EU", "US")
    assert policy.POLICY_SIGNATURE != danish_a.POLICY_SIGNATURE


def test_entry_differs_from_danish_a_only_in_sessions():
    mine = dict(COMBO_LANE_SPECS[policy.LANE]["entry_policy"])
    theirs = dict(COMBO_LANE_SPECS[danish_a.LANE]["entry_policy"])
    mine.pop("allowed_sessions"); theirs.pop("allowed_sessions")
    assert mine == theirs
    assert COMBO_LANE_SPECS[policy.LANE]["exit_policy"] == COMBO_LANE_SPECS[danish_a.LANE]["exit_policy"]


def test_us_session_trades_here_but_is_gated_on_danish_a():
    for ts in (NOW, US_TS, ASIA_TS):
        decision = _entry(policy, ts)
        assert decision["action"] == ACTION_MAKER and decision["limit_price"] == 100_100.0
    gated = _entry(danish_a, US_TS)
    assert gated["action"] == ACTION_STAND_ASIDE and gated["reason"] == "SESSION_GATED"
    spread = _entry(policy, US_TS, bid=99_990.0, ask=100_030.0)
    assert spread["action"] == ACTION_STAND_ASIDE and spread["reason"] == "SPREAD_ABOVE_MAX"


def test_confirm_to_market_and_exit_match_danish_a():
    args = {"direction": "SHORT", "signal_price": 100_000.0, "limit_price": 100_100.0,
            "bid": 99_969.0, "ask": 99_971.0, "confirmed_ts": None, "now": US_TS}
    verdict = policy.confirm_market_action(**args)
    assert verdict["action"] == "MARKET" and verdict == danish_a.confirm_market_action(**args)
    cut = policy.exit_action(entry=100_000.0, direction="SHORT", price=100_125.0, age_sec=120)
    assert cut is not None and cut.reason == "THESIS_FAST_CUT"


def test_dashboard_and_card_have_no_session_gate():
    chips = " ".join(policy.dashboard_policy()["filter_chips"])
    assert "PAPER ONLY" in chips and "Sessions" not in chips
    assert "taker within 5bp cap" in chips
    sections = tile_card_sections(policy.LANE)
    assert sections["entry"] and sections["exit"]["live"]
    assert any("$0.25 margin @100x" in line for line in sections["risk"])
