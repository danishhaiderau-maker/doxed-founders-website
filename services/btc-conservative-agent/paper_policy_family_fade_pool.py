"""FADE POOL (PHASE03, owner 2026-10-07): a pooled two-thesis book - committed AI fade and cross-venue premium reversion - with one composite exit stack, up to three open (paper only, relay-ineligible)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_MAKER, ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA
from cross_venue_premium import PremiumRule
from regime_adaptive_binding import FadePoolPremiumEvaluator, RegimeAdaptiveBinding

LANE = "FAMILY_FADE_POOL"
_BINDING = RegimeAdaptiveBinding(LANE, "Fade pool · committed fade + premium reversion, pooled exits, up to three open")
POLICY_ID = _BINDING.policy_id
POLICY_SIGNATURE = _BINDING.policy_signature
ENTRY = _BINDING.entry
EXIT = _BINDING.exit
SPEC = _BINDING.spec
ADAPTIVE_ENTRY = True
MARKET_EXIT_CONTEXT = True
# The pooled book follows its own pre-registered schedule; a later shared AI
# call never cancels them at fill time (the offline spec has no fill-time AI
# revalidation).
SKIP_FILL_REVALIDATION = True
CHASE_STEP = SPEC.chase_step
MIN_CLOSED_CANDLES = _BINDING.MIN_CLOSED_CANDLES
SIGNAL_CLOCK = ENTRY.get("signal_clock")
RULE = PremiumRule.from_policy(ENTRY, EXIT)

__all__ = ("ACTION_MAKER", "ACTION_STAND_ASIDE", "ACTION_TAKER", "DECISION_SCHEMA")


def make_evaluator() -> FadePoolPremiumEvaluator:
    return FadePoolPremiumEvaluator(RULE, policy_id=POLICY_ID, policy_signature=POLICY_SIGNATURE)


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
