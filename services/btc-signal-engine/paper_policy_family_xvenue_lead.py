"""Tile 3: cross-venue lead — follow a Binance/Bybit lead over Bitfinex, taker, 60-s hold, 40 bp catastrophic stop (paper only)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA
from cross_venue_lead import LeadRule
from taker_time_exit_binding import TakerTimeExitBinding

LANE = "FAMILY_XVENUE_LEAD_60S"
_BINDING = TakerTimeExitBinding(LANE, "Cross-venue lead · Binance/Bybit ≥8 bp lead over 10 s, 60-s hold, 40 bp stop")
POLICY_ID = _BINDING.policy_id
POLICY_SIGNATURE = _BINDING.policy_signature
ENTRY = _BINDING.entry
EXIT = _BINDING.exit
SPEC = _BINDING.spec
ADAPTIVE_ENTRY = True
CHASE_STEP = SPEC.chase_step
MIN_CLOSED_CANDLES = _BINDING.MIN_CLOSED_CANDLES
SIGNAL_CLOCK = ENTRY["signal_clock"]
RULE = LeadRule.from_policy(ENTRY, EXIT)

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
