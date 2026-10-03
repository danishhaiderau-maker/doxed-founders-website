"""Tile H9: score-led follow on AI NO_TRADE calls - score-led side only when the raw AI abstains, maker limit 0.15% beyond the signal price chased 25% of the remaining gap every 3 min in minutes 10-25, 1 h TTL, 60-min hold, 40 bp catastrophic stop (paper only)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_MAKER, ACTION_STAND_ASIDE, DECISION_SCHEMA
from maker_chase_time_exit_binding import MakerChaseTimeExitBinding

LANE = "FAMILY_NOTRADE_FOLLOW_MAKER_60"
_BINDING = MakerChaseTimeExitBinding(LANE, "No-trade follow (maker) · score-led side on AI NO_TRADE, 0.15% maker + chase, 60-min hold, 40 bp stop")
POLICY_ID = _BINDING.policy_id
POLICY_SIGNATURE = _BINDING.policy_signature
ENTRY = _BINDING.entry
EXIT = _BINDING.exit
SPEC = _BINDING.spec
ADAPTIVE_ENTRY = True
CHASE_STEP = _BINDING.CHASE_STEP
MIN_CLOSED_CANDLES = _BINDING.MIN_CLOSED_CANDLES

__all__ = ("ACTION_MAKER", "ACTION_STAND_ASIDE", "DECISION_SCHEMA")

lane_admission = _BINDING.lane_admission
decide_entry = _BINDING.decide_entry
decision_is_executable = _BINDING.decision_is_executable
adaptive_entry_fields = _BINDING.adaptive_entry_fields
entry_fields = _BINDING.entry_fields
chase_due = _BINDING.chase_due
marketable_quote_at_limit = _BINDING.marketable_quote_at_limit
account_risk_quantity = _BINDING.account_risk_quantity
exit_action = _BINDING.exit_action
exit_config = _BINDING.exit_config
dashboard_policy = _BINDING.dashboard_policy
