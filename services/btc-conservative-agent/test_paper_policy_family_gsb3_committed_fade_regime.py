"""Dedicated contract for FREEZE21B B3: H-A's committed fade with regime execution.

PHASE02 (2026-10-07): committed fade loses in VIOLENT, so B3 stands aside there
(quiet/trend only), matching the locked meta-rule that routes VIOLENT to
premium reversion.
"""
import paper_policy_family_gsb3_committed_fade_regime as policy
from combo_pathway_config import COMBO_LANE_SPECS
from gs_tile_contract_support import asia_ts, assert_dashboard, assert_paper_only_registry_tile, bar, decide


def test_registry_owns_a_shared_ai_fade_tile():
    spec = assert_paper_only_registry_tile(policy, index=6, prefix="gb3", hypothesis_id="GS-20261004-B3",
                                           bonferroni_k=3)
    entry = spec["entry_policy"]
    assert entry["direction_source"] == "INVERTED_SCORE_LED_SIDE"
    assert tuple(entry["fade_allowed_sessions"]) == ("ASIA", "EU") and entry["fade_max_spread_bps"] == 3.0


def test_admits_committed_calls_inverted():
    committed = {"decision": "APPROVE", "direction": "LONG", "raw_decision": "LONG", "raw_direction": "LONG",
                 "long_score": 70, "short_score": 40}
    view = policy.lane_admission(committed, {"applied": True, "accepted": True, "effective_direction": "LONG"})
    assert view["accepted"] is True and view["direction"] == "SHORT"


def test_regime_cells():
    ts = asia_ts()
    quiet = decide(policy, direction="SHORT", engine_bar=bar(), ts=ts)
    assert quiet["exec"]["kind"] == "TOUCH" and 60000.5 <= quiet["limit_price"] <= 60001.0  # passive, tick-rounded
    trend = decide(policy, direction="SHORT", engine_bar=bar(adx=30.0), ts=ts)
    assert trend["exec"]["kind"] == "OFFSET" and trend["exit_profile"] == "MOM_TREND"
    wide = decide(policy, direction="SHORT", engine_bar=bar(), ts=ts, bid=60000.0, ask=60025.0)
    assert wide["action"] == "STAND_ASIDE"


def test_stands_aside_in_violent():
    # PHASE02: committed fade loses in VIOLENT (H-A -3.63 bp/trade); the locked
    # meta-rule routes VIOLENT to premium reversion, so B3 must not trade there.
    ts = asia_ts()
    violent = decide(policy, direction="SHORT", engine_bar=bar(atr_pct=95.0, atr_bp=4.0), ts=ts)
    assert violent["action"] == "STAND_ASIDE"
    assert violent["reason"] == "REGIME_VIOLENT_STANDS_ASIDE"
    # The VIOLENT entry cell and momentum exit profile are gone from the registry.
    spec = COMBO_LANE_SPECS[policy.LANE]
    entry = spec["entry_policy"]
    assert "VIOLENT" not in entry["regime_exec"]
    assert entry["flip_indicator"].get("VIOLENT") is None
    exits = spec["exit_policy"]
    assert exits["regime_profiles"]["VIOLENT"] is None
    assert "MOM_VIOLENT" not in exits["profiles"]
    assert set(exits["profiles"]) == {"MOM_QUIET", "MOM_TREND"}


def test_missing_decision_falls_back_to_the_first_profile():
    action = policy.exit_action(entry=60000.0, direction="LONG", price=60000.0 * (1 - 0.0031), age_sec=400,
                                policy_state={}, entry_decision={}, fill_ts=0)
    assert action.reason.startswith("PHYSICAL_HARD_STOP_")


def test_dashboard_discloses_the_rule():
    assert_dashboard(policy, "GS-20261004-B3")
