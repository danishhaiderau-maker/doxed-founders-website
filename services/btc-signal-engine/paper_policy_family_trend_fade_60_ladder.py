"""Trend Fade 60 + Profit Lock: Trend Fade 60 entry, Scenario-C ladder, 40 bp stop, 60-min backstop, up to 5 open (paper only)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA
from taker_time_exit_binding import TakerTimeExitBinding

LANE = "FAMILY_TREND_FADE_60_LADDER"
_BINDING = TakerTimeExitBinding(
    LANE, "Trend Fade 60 + Profit Lock · inverted AI side, Scenario-C ladder, 40 bp stop, 60-min backstop",
)
POLICY_ID = _BINDING.policy_id
POLICY_SIGNATURE = _BINDING.policy_signature
ENTRY = _BINDING.entry
EXIT = _BINDING.exit
LADDER = _BINDING.ladder
SPEC = _BINDING.spec
ADAPTIVE_ENTRY = True
CHASE_STEP = SPEC.chase_step
MIN_CLOSED_CANDLES = _BINDING.MIN_CLOSED_CANDLES

__all__ = ("ACTION_STAND_ASIDE", "ACTION_TAKER", "DECISION_SCHEMA")

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
