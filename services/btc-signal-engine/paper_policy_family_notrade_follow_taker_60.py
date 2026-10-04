"""FREEZE21 H-B: NO_TRADE-lean follow with a taker entry - score-led side only when the raw AI abstained, marketable limit at the signal (5 bp cap, 3 s), 60-min hold, break-even and ATR trail, 40 bp catastrophic stop, up to ten open (paper only)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA
from taker_time_exit_binding import TakerTimeExitBinding

LANE = "FAMILY_NOTRADE_FOLLOW_TAKER_60"
_BINDING = TakerTimeExitBinding(LANE, "NO_TRADE-lean follow (taker) · score-led side on AI NO_TRADE, taker at signal, 60-min hold, BE + ATR trail, 40 bp stop")
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
