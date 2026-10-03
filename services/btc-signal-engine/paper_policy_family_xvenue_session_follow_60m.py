"""Tile H10: cross-venue session-gated follow - Bitfinex taker in the direction of either the Binance/Bybit lead or premium trigger, only in the frozen UTC session map, 60-min hold, 40 bp catastrophic stop, up to three open (paper only)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA
from cross_venue_session_follow import SessionFollowEvaluator, SessionFollowRule
from taker_time_exit_binding import TakerTimeExitBinding

LANE = "FAMILY_XVENUE_SESSION_FOLLOW_60M"
_BINDING = TakerTimeExitBinding(LANE, "Cross-venue session follow · Binance/Bybit lead or premium, frozen session map, 60-min hold, 40 bp stop")
POLICY_ID = _BINDING.policy_id
POLICY_SIGNATURE = _BINDING.policy_signature
ENTRY = _BINDING.entry
EXIT = _BINDING.exit
SPEC = _BINDING.spec
ADAPTIVE_ENTRY = True
CHASE_STEP = SPEC.chase_step
MIN_CLOSED_CANDLES = _BINDING.MIN_CLOSED_CANDLES
SIGNAL_CLOCK = ENTRY["signal_clock"]
RULE = SessionFollowRule.from_policy(ENTRY, EXIT)

__all__ = ("ACTION_STAND_ASIDE", "ACTION_TAKER", "DECISION_SCHEMA")


def make_evaluator() -> SessionFollowEvaluator:
    return SessionFollowEvaluator(RULE, policy_id=POLICY_ID, policy_signature=POLICY_SIGNATURE)


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
