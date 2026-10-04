"""FREEZE21 control: random-direction twin of H-A - the same committed calls, Asia+EU gate, taker entry and exits, with the side from a deterministic coin per shared call; its mean measures pure execution cost (paper only)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA
from taker_time_exit_binding import TakerTimeExitBinding

LANE = "FAMILY_RANDOM_CONTROL_TAKER_90"
_BINDING = TakerTimeExitBinding(LANE, "Random control (taker) · coin side on H-A's committed calls, identical exits, measures execution cost")
POLICY_ID = _BINDING.policy_id
POLICY_SIGNATURE = _BINDING.policy_signature
ENTRY = _BINDING.entry
EXIT = _BINDING.exit
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
