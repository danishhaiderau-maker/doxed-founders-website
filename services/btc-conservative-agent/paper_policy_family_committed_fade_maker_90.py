"""Committed-call fade with a maker entry — opposite of an explicit AI side, passive limit 0.10% beyond the signal price resting 30 min without chase, 90-min hold, 40 bp catastrophic stop (paper only)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_MAKER, ACTION_STAND_ASIDE, DECISION_SCHEMA
from maker_time_exit_binding import MakerTimeExitBinding

LANE = "FAMILY_COMMITTED_FADE_MAKER_90"
_BINDING = MakerTimeExitBinding(LANE, "Committed fade (maker) · inverted committed AI side, 0.10% maker limit, 90-min hold, 40 bp stop")
POLICY_ID = _BINDING.policy_id
POLICY_SIGNATURE = _BINDING.policy_signature
ENTRY = _BINDING.entry
EXIT = _BINDING.exit
SPEC = _BINDING.spec
ADAPTIVE_ENTRY = True
CHASE_STEP = SPEC.chase_step
MIN_CLOSED_CANDLES = _BINDING.MIN_CLOSED_CANDLES

__all__ = ("ACTION_MAKER", "ACTION_STAND_ASIDE", "DECISION_SCHEMA")

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
