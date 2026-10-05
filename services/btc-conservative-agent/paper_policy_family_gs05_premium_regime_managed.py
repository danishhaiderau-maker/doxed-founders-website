"""GS-05 (GS-20261005-05, FREEZE21B mid-epoch addition): H-C/GS-01 cross-venue premium trigger (own evaluator gs5xvp), QUIET stands aside (shadow would-have row), TREND / VIOLENT Bitfinex taker; GS_SIMPLE_V1 + ladder TP1 50% at max(6, 1 ATR), BE max(6, 1.5 ATR) -> +2, maker TP max(10, 2.5 ATR), 1.5 ATR trail, 8 bp/5 min cut, 35 bp stop, 60 min (TREND) / 45 min (VIOLENT), one open (paper only)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_MAKER, ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA
from regime_adaptive_binding import RegimeAdaptiveBinding

LANE = "FAMILY_GS05_PREMIUM_REGIME_MANAGED"
_BINDING = RegimeAdaptiveBinding(LANE, "GS-05 Premium regime-managed")
POLICY_ID = _BINDING.policy_id
POLICY_SIGNATURE = _BINDING.policy_signature
ENTRY = _BINDING.entry
EXIT = _BINDING.exit
SPEC = _BINDING.spec
ADAPTIVE_ENTRY = True
MARKET_EXIT_CONTEXT = True
# Resting regime limits follow their own pre-registered schedule; a later
# shared AI call never cancels them at fill time (the offline spec has no
# fill-time AI revalidation).
SKIP_FILL_REVALIDATION = True
CHASE_STEP = SPEC.chase_step
MIN_CLOSED_CANDLES = _BINDING.MIN_CLOSED_CANDLES
SIGNAL_CLOCK = ENTRY.get("signal_clock")

__all__ = ("ACTION_MAKER", "ACTION_STAND_ASIDE", "ACTION_TAKER", "DECISION_SCHEMA")

lane_admission = _BINDING.lane_admission
decide_entry = _BINDING.decide_entry
regime_entry_action = _BINDING.regime_entry_action
decision_is_executable = _BINDING.decision_is_executable
adaptive_entry_fields = _BINDING.adaptive_entry_fields
entry_fields = _BINDING.entry_fields
chase_due = _BINDING.chase_due
account_risk_quantity = _BINDING.account_risk_quantity
exit_action = _BINDING.exit_action
exit_config = _BINDING.exit_config
dashboard_policy = _BINDING.dashboard_policy
make_evaluator = _BINDING.make_evaluator
