"""Dedicated contract for Tile H10: cross-venue lead-or-premium follow, frozen session map, 60-min hold."""
import calendar

import pytest

import bot
import cross_venue_session_follow as xvs
import paper_policy_family_xvenue_session_follow_60m as policy
from adaptive_regime_entry import ACTION_STAND_ASIDE, ACTION_TAKER
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    CROSS_VENUE_SIGNAL_CLOCK,
    RETIRED_POLICY_IDENTITIES,
    XVENUE_SESSION_FOLLOW_ADMISSION_POLICY_ID,
    tile_number,
    validate_tile_registry,
)
from cross_venue_lead import STATUS_BELOW, STATUS_STALE, STATUS_TRIGGER, LeadRule
from cross_venue_premium import PremiumRule

NOW = 1_790_000_000.0
EU_NOON = float(calendar.timegm((2026, 10, 5, 12, 0, 0)))


def _sub(status, side=None, anchor=100, **extra):
    return {"anchor_bucket_ts": anchor, "status": status, "side": side, "stale_reasons": [],
            "venue_bbo_age_s": {"binance": 0.2, "bybit": 0.3}, "collector_age_s": 0.1,
            "bfx_bbo_age_s": 0.2, "bid": 100.0, "ask": 100.01, "spread_bps": 1.0, **extra}


def _rule(sessions=("ASIA", "EU", "US")):
    entry = dict(COMBO_LANE_SPECS[policy.LANE]["entry_policy"], allowed_sessions=sessions)
    return xvs.SessionFollowRule.from_policy(entry, COMBO_LANE_SPECS[policy.LANE]["exit_policy"])


def test_registry_owns_a_paper_only_relay_ineligible_default_off_clock_tile():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[-1] == policy.LANE and tile_number(policy.LANE) == len(ACTIVE_TILE_ORDER)
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and spec["default_enabled"] is False
    assert spec["id_prefix"] == "xvs" and spec["max_active_signals"] == 3
    assert spec["signal_clock"] == CROSS_VENUE_SIGNAL_CLOCK and spec["uses_shared_ai_direction"] is False
    assert spec["path_end_sec"] == 3600 and spec["entry_ttl_sec"] == 3
    assert spec["admission_treatment"] == XVENUE_SESSION_FOLLOW_ADMISSION_POLICY_ID
    assert policy.POLICY_ID == spec["raw_policy_id"] not in RETIRED_POLICY_IDENTITIES
    others = {COMBO_LANE_SPECS[lane]["policy_signature"] for lane in ACTIVE_TILE_ORDER if lane != policy.LANE}
    assert policy.POLICY_SIGNATURE not in others
    assert spec["entry_policy"]["allowed_sessions"] == ("ASIA", "EU", "US")
    assert "META_SESSION" in spec["entry_policy"]["session_map_source"]


def test_rule_copies_the_generic_lead_and_premium_thresholds_with_a_sixty_minute_hold():
    rule = policy.RULE
    assert rule.hold_sec == 3600 and rule.lead.hold_sec == 3600 and rule.premium.hold_sec == 3600
    assert rule.lead.lead_threshold_bps == LeadRule().lead_threshold_bps
    assert rule.lead.lookback_sec == LeadRule().lookback_sec
    assert rule.premium.long_threshold_bps == PremiumRule().long_threshold_bps
    assert rule.premium.short_threshold_bps == PremiumRule().short_threshold_bps
    assert rule.premium.mean_window_sec == PremiumRule().mean_window_sec
    with pytest.raises(ValueError):
        _rule(sessions=("LONDON",))


@pytest.mark.parametrize("lead,premium,status,side,source", [
    (_sub(STATUS_TRIGGER, "LONG"), _sub(STATUS_BELOW), STATUS_TRIGGER, "LONG", "LEAD"),
    (_sub(STATUS_BELOW), _sub(STATUS_TRIGGER, "SHORT"), STATUS_TRIGGER, "SHORT", "PREMIUM"),
    (_sub(STATUS_TRIGGER, "LONG"), _sub(STATUS_TRIGGER, "LONG"), STATUS_TRIGGER, "LONG", "LEAD+PREMIUM"),
    (_sub(STATUS_TRIGGER, "LONG"), _sub(STATUS_TRIGGER, "SHORT"), xvs.STATUS_CONFLICT, None, None),
    (_sub(STATUS_BELOW, "LONG"), _sub(STATUS_BELOW), STATUS_BELOW, None, None),
    (_sub(STATUS_STALE, "LONG"), _sub(STATUS_STALE), STATUS_STALE, None, None),
])
def test_either_trigger_follows_and_opposite_triggers_never_trade(lead, premium, status, side, source):
    out = xvs.combine(lead, premium, _rule(), now=EU_NOON)
    assert (out["status"], out["side"], out["trigger_source"]) == (status, side, source)
    assert xvs.is_qualifying(out) is (side is not None)


def test_session_map_gates_triggers_outside_the_frozen_sessions():
    assert [xvs.utc_session(EU_NOON + h * 3600) for h in (-12, 0, 8)] == ["ASIA", "EU", "US"]
    out = xvs.combine(_sub(STATUS_TRIGGER, "LONG"), _sub(STATUS_BELOW), _rule(("ASIA", "US")), now=EU_NOON)
    assert out["status"] == xvs.STATUS_SESSION_GATED and out["session"] == "EU"
    assert xvs.is_qualifying(out)


def test_evaluator_logs_shadow_triggers_and_matures_sixty_minute_outcomes(monkeypatch):
    evaluator = policy.make_evaluator()
    assert evaluator.SHADOW_FILE == "xvs_shadow_signals.jsonl" != bot.XVL_SHADOW_FILE
    assert evaluator.ID_PREFIX == "xvs" and evaluator.TRIGGER_FEATURE_KEY == "xvs_trigger"
    anchor = int(EU_NOON) - 1
    monkeypatch.setattr(xvs, "lead_evaluate_second", lambda *a, **k: _sub(STATUS_TRIGGER, "LONG", anchor=anchor))
    monkeypatch.setattr(xvs, "premium_evaluate_second", lambda *a, **k: _sub(STATUS_BELOW, anchor=anchor))
    evaluation, row, outcomes = evaluator.step(now=EU_NOON, live=None, bfx_quotes={}, bfx_bbo_ts=None)
    assert evaluation["status"] == STATUS_TRIGGER and row["qualifies"] is True
    assert row["trigger_id"] == f"xvs-{anchor}" and row["trigger_source"] == "LEAD" and row["session"] == "EU"
    assert row["exit_bucket_ts"] == anchor + 1 + 3600 and outcomes == []
    quotes = {anchor + 1: (100.0, 100.01), anchor + 3601: (100.2, 100.21)}
    later = _sub(STATUS_BELOW, anchor=anchor + 3601)
    monkeypatch.setattr(xvs, "lead_evaluate_second", lambda *a, **k: later)
    monkeypatch.setattr(xvs, "premium_evaluate_second", lambda *a, **k: later)
    _, _, outcomes = evaluator.step(now=EU_NOON + 3601, live=None, bfx_quotes=quotes, bfx_bbo_ts=None)
    assert len(outcomes) == 1 and outcomes[0]["status"] == "OK" and outcomes[0]["hold_sec"] == 3600
    assert outcomes[0]["net_bp_after_spread"] > 0
    assert set(policy.make_evaluator().TRIGGER_FEATURE_FIELDS) <= set(row) | {"bfx_bid", "bfx_ask"}


def test_decide_entry_is_a_capped_taker_or_stand_aside():
    decision = policy.decide_entry(direction="LONG", signal_ts=NOW, bid=100_000.0, ask=100_001.0,
                                   bbo_ts=NOW - 0.5, reference_price=100_000.5)
    assert decision["action"] == ACTION_TAKER and decision["entry_ttl_sec"] == 3
    assert decision["limit_price"] <= 100_001.0 * 1.0005
    wide = policy.decide_entry(direction="LONG", signal_ts=NOW, bid=100_000.0, ask=100_050.0,
                               bbo_ts=NOW - 0.5, reference_price=100_025.0)
    assert wide["action"] == ACTION_STAND_ASIDE and wide["reason"] == "SPREAD_ABOVE_MAX"


def test_lane_admission_never_reads_the_shared_ai_call():
    view = policy.lane_admission({"raw_direction": "LONG"}, {"applied": True, "accepted": True,
                                                            "effective_direction": "LONG"})
    assert view["accepted"] is False and view["reason"].endswith("NOT_A_SHARED_AI_TILE")


def test_exit_and_dashboard_disclose_the_rule():
    assert policy.EXIT["max_duration_sec"] == 3600 and policy.EXIT["hard_stop_bps"] == 40.0
    assert policy.EXIT["max_open_positions"] == 3
    payload = policy.dashboard_policy()
    chips = " ".join(payload["filter_chips"])
    assert "PAPER ONLY" in chips and "HINT" in chips and "OR premium" in chips
    assert "opposite triggers -> no trade" in chips
    assert "60m time exit" in chips and "Max 3 open positions" in chips
    assert "ASIA/EU/US" in payload["entry"]["trigger"]
    assert payload["exit"]["fixed_time_exit"] == "60m"
    assert payload["pre_registration"]["hypothesis_id"] == "H10_XVENUE_SESSION_FOLLOW_60M_20261004"
