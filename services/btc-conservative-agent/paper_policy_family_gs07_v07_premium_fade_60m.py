"""Tile 14 · GS-07 V07 (owner 2026-10-10): cross-venue premium fade on the 60-min mean, +/-2.5 bp, at most one entry per 300 s and 12/h, 60-min hold, 40 bp stop, up to six open (paper only, relay-ineligible)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA
from cross_venue_premium import PremiumEvaluator, PremiumRule
from taker_time_exit_binding import TakerTimeExitBinding

LANE = "FAMILY_GS07_V07_PREMIUM_FADE_60M"
_BINDING = TakerTimeExitBinding(LANE, "GS-07 V07 premium fade · Binance/Bybit premium vs 60-min mean +/-2.5 bp, taker, 60-min hold, 40 bp stop")
POLICY_ID = _BINDING.policy_id
POLICY_SIGNATURE = _BINDING.policy_signature
ENTRY = _BINDING.entry
EXIT = _BINDING.exit
SPEC = _BINDING.spec
ADAPTIVE_ENTRY = True
CHASE_STEP = SPEC.chase_step
MIN_CLOSED_CANDLES = _BINDING.MIN_CLOSED_CANDLES
SIGNAL_CLOCK = ENTRY["signal_clock"]
RULE = PremiumRule.from_policy(ENTRY, EXIT)

__all__ = ("ACTION_STAND_ASIDE", "ACTION_TAKER", "DECISION_SCHEMA")


def make_evaluator() -> PremiumEvaluator:
    return PremiumEvaluator(RULE, policy_id=POLICY_ID, policy_signature=POLICY_SIGNATURE)


lane_admission = _BINDING.lane_admission
decide_entry = _BINDING.decide_entry
decision_is_executable = _BINDING.decision_is_executable
adaptive_entry_fields = _BINDING.adaptive_entry_fields
entry_fields = _BINDING.entry_fields
chase_due = _BINDING.chase_due
account_risk_quantity = _BINDING.account_risk_quantity
exit_action = _BINDING.exit_action
exit_config = _BINDING.exit_config
dashboard_policy = _BINDING.dashboard_policy
