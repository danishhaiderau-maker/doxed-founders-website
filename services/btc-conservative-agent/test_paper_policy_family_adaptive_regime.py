"""Dedicated contract for the Dynamic Adaptive paper tile policy."""
import math

import pytest

import paper_policy_family_adaptive_regime as policy
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    RETIRED_TILE_LANES,
)

BASE = 65000.0
T0_MS = 1_790_000_000_000


def _candles(returns_bps, start=BASE):
    """1m candles (ms open ts) whose consecutive closes follow ``returns_bps``."""
    closes = [start]
    for r in returns_bps:
        closes.append(closes[-1] * math.exp(r / 1e4))
    return [
        [T0_MS + i * 60_000, c, c, c, c, 1.0]
        for i, c in enumerate(closes)
    ]


def _alternating(n, r_bps):
    return [r_bps if i % 2 == 0 else -r_bps for i in range(n)]


def _signal_ts(candles, after_close_sec=5.0):
    return candles[-1][0] / 1000.0 + 60.0 + after_close_sec


def _ai(direction="LONG", long_score=70, short_score=30):
    return {
        "raw_direction": direction,
        "raw_decision": "APPROVE",
        "long_score": long_score,
        "short_score": short_score,
    }


def _decide(candles, direction="LONG", *, bid=64990.0, ask=65000.0, atr=100.0,
            ai=None, bbo_age=1.0, signal_ts=None):
    ts = _signal_ts(candles) if signal_ts is None else signal_ts
    return policy.decide_entry(
        direction=direction,
        signal_ts=ts,
        candles_1m=candles,
        bid=bid,
        ask=ask,
        bbo_ts=ts - bbo_age,
        atr_abs=atr,
        reference_price=(bid + ask) / 2.0,
        ai_feature=_ai(direction) if ai is None else ai,
    )


CALM = _candles(_alternating(80, 2.0))
NORMAL = _candles(_alternating(80, 5.0))
EXTREME = _candles(_alternating(80, 10.0))
FAST_UP = _candles(_alternating(75, 1.0) + [3.0] * 5)


def test_registry_owns_exactly_this_paper_only_relay_ineligible_tile():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert ACTIVE_TILE_ORDER == (policy.LANE,)
    assert spec["paper_only"] is True
    assert spec["platform_relay_eligible"] is False
    assert spec["default_enabled"] is False
    assert spec["implementation_modules"] == ("paper_policy_family_adaptive_regime.py",)
    assert policy.POLICY_ID == spec["raw_policy_id"]
    assert policy.LANE not in RETIRED_TILE_LANES
    for retired in (
        "FAMILY_CHANDELIER_3", "FAMILY_ATR_TARGET_2_5", "FAMILY_ATR_TRAIL",
        "FAMILY_HYBRID_RUNNER", "FAMILY_MFE_GIVEBACK", "CONTINUOUS",
    ):
        assert retired in RETIRED_TILE_LANES
        assert retired not in COMBO_LANE_SPECS


def test_regime_thresholds_come_from_the_frozen_calibration():
    entry = policy.ENTRY
    assert policy.classify_regime(entry["calm_below_bps"] - 0.01) == "CALM"
    assert policy.classify_regime(entry["calm_below_bps"]) == "NORMAL"
    assert policy.classify_regime(entry["extreme_above_bps"]) == "NORMAL"
    assert policy.classify_regime(entry["extreme_above_bps"] + 0.01) == "EXTREME"
    assert policy.classify_regime(None) == "UNAVAILABLE"
    assert entry["calibration"]["p40_bps"] == pytest.approx(entry["calm_below_bps"], abs=0.01)
    assert entry["calibration"]["p90_bps"] == pytest.approx(entry["extreme_above_bps"], abs=0.01)


def test_calm_rests_a_maker_limit_one_tick_inside_the_touch():
    long_d = _decide(CALM, "LONG")
    assert long_d["regime"] == "CALM" and long_d["fast_move"] is False
    assert long_d["action"] == policy.ACTION_MAKER
    assert long_d["limit_price"] == pytest.approx(64991.0)
    assert long_d["entry_ttl_sec"] == policy.ENTRY["maker_ttl_sec"]
    short_d = _decide(CALM, "SHORT", ai=_ai("SHORT", 30, 70))
    assert short_d["action"] == policy.ACTION_MAKER
    assert short_d["limit_price"] == pytest.approx(64999.0)


def test_maker_never_crosses_a_one_tick_spread():
    d = _decide(CALM, "LONG", bid=64999.0, ask=65000.0)
    assert d["action"] == policy.ACTION_MAKER
    assert d["limit_price"] == pytest.approx(64999.0)


def test_normal_takes_with_a_bounded_protection_cap():
    long_d = _decide(NORMAL, "LONG")
    assert long_d["regime"] == "NORMAL"
    assert long_d["action"] == policy.ACTION_TAKER
    assert long_d["limit_price"] == pytest.approx(65033.0)
    assert long_d["entry_ttl_sec"] == policy.ENTRY["taker_ttl_sec"]
    short_d = _decide(NORMAL, "SHORT", ai=_ai("SHORT", 30, 70))
    assert short_d["action"] == policy.ACTION_TAKER
    assert short_d["limit_price"] == pytest.approx(64957.0)


def test_fast_move_in_signal_direction_upgrades_calm_to_taker_only_for_that_side():
    long_d = _decide(FAST_UP, "LONG")
    assert long_d["regime"] == "CALM" and long_d["fast_move"] is True
    assert long_d["action"] == policy.ACTION_TAKER
    assert long_d["reason"] == "FAST_MOVE_TAKER"
    short_d = _decide(FAST_UP, "SHORT", ai=_ai("SHORT", 30, 70))
    assert short_d["fast_move"] is False
    assert short_d["action"] == policy.ACTION_MAKER


def test_extreme_volatility_stands_aside():
    d = _decide(EXTREME, "LONG")
    assert d["regime"] == "EXTREME"
    assert d["action"] == policy.ACTION_STAND_ASIDE
    assert d["reason"] == "EXTREME_VOLATILITY"
    assert d["limit_price"] is None


@pytest.mark.parametrize("ai, reason", [
    (_ai("NO_TRADE", 60, 40), "AI_NO_TRADE"),
    ({"raw_direction": "", "long_score": 70, "short_score": 30}, "AI_NO_TRADE"),
    (_ai("LONG", 52, 48), "AI_SCORE_GAP_BELOW_MIN"),
    ({"raw_direction": "LONG"}, "AI_SCORES_UNAVAILABLE"),
    (_ai("SHORT", 30, 70), "AI_DIRECTION_CONFLICT"),
])
def test_ai_no_trade_weak_gap_and_conflict_never_trade(ai, reason):
    d = _decide(NORMAL, "LONG", ai=ai)
    assert d["action"] == policy.ACTION_STAND_ASIDE
    assert d["reason"] == reason


def test_score_gap_of_exactly_the_minimum_is_admitted():
    gap = policy.ENTRY["min_score_gap"]
    d = _decide(NORMAL, "LONG", ai=_ai("LONG", 50 + gap, 50))
    assert d["action"] == policy.ACTION_TAKER


def test_liquidation_guard_skips_wide_initial_stops():
    guard = policy.ENTRY["liquidation_guard_stop_bps"]
    ref = (64990.0 + 65000.0) / 2.0
    atr_at_guard = guard / 1e4 * ref / policy.SPEC.initial_stop_atr_k
    assert _decide(NORMAL, atr=atr_at_guard * 1.001)["reason"] == "LIQUIDATION_GUARD"
    assert _decide(NORMAL, atr=atr_at_guard * 0.99)["action"] == policy.ACTION_TAKER
    assert _decide(NORMAL, atr=0.0)["reason"] == "ATR_UNAVAILABLE"


def test_missing_or_stale_inputs_fail_closed():
    assert _decide(CALM[:20])["reason"] == "REGIME_WARMUP"
    stale_ts = _signal_ts(CALM, after_close_sec=policy.ENTRY["max_candle_staleness_sec"] + 1)
    assert _decide(CALM, signal_ts=stale_ts)["reason"] == "CANDLES_STALE"
    assert _decide(CALM, bid=0.0)["reason"] == "BBO_UNAVAILABLE"
    assert _decide(CALM, bid=65001.0, ask=65000.0)["reason"] == "BBO_UNAVAILABLE"
    assert _decide(CALM, bbo_age=policy.ENTRY["max_bbo_age_sec"] + 0.1)["reason"] == "BBO_STALE"
    assert _decide(CALM, "NO_TRADE")["reason"] == "NO_DIRECTION"


def test_unclosed_candles_are_never_used():
    ts = _signal_ts(CALM)
    forming = [int((ts - 30.0) * 1000), BASE * 1.05, BASE * 1.05, BASE * 1.05, BASE * 1.05, 1.0]
    base = _decide(CALM, signal_ts=ts)
    with_forming = _decide(CALM + [forming], signal_ts=ts)
    for key in ("rv15_bps", "fast_move_z", "regime", "action", "limit_price", "closed_candles"):
        assert with_forming[key] == base[key]


def test_entry_fields_follow_only_an_executable_decision():
    decision = _decide(NORMAL, "LONG")
    fields = policy.adaptive_entry_fields("LONG", 64995.0, decision)
    assert fields["structural_entry_valid"] is True
    assert fields["planned_limit_price"] == pytest.approx(decision["limit_price"])
    assert fields["ai_direct_limit"] == pytest.approx(decision["limit_price"])
    assert fields["entry_ttl_sec"] == decision["entry_ttl_sec"]
    assert fields["adaptive_liquidity_intent"] == "TAKER"
    assert fields["entry_path"] == "FAMILY_ADAPTIVE_REGIME"

    aside = _decide(EXTREME, "LONG")
    blocked = policy.adaptive_entry_fields("LONG", 64995.0, aside)
    assert blocked["structural_entry_valid"] is False
    assert blocked["planned_limit_price"] is None
    assert blocked["entry_reason"] == "ADAPTIVE_NO_ORDER_EXTREME_VOLATILITY"

    assert policy.entry_fields("LONG", 64995.0)["structural_entry_valid"] is False
    assert policy.adaptive_entry_fields("SHORT", 64995.0, decision)["structural_entry_valid"] is False


def test_exit_is_atr_trail_owned_by_generic_primitives():
    assert policy.SPEC.family == "ATR_TRAIL"
    assert policy.SPEC.initial_stop_atr_k == 1.5
    assert policy.SPEC.trail_activation_atr_k == 0.75
    assert policy.SPEC.trail_atr_k == 1.0
    assert policy.SPEC.chase_windows == ()
    chips = policy.dashboard_policy()["filter_chips"]
    assert chips[0] == "PAPER ONLY"
