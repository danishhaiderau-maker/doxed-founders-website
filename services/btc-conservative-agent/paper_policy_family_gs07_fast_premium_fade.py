"""GS-07 (PHASE03, owner 2026-10-07): fast cross-venue premium fade - the Binance/Bybit premium over Bitfinex leaving its own 60-second mean (+1.75 / -1.88 bp) fades back toward convergence; taker, 20-min scalp, late break-even +20 -> +5, ATR trail 1.5 armed at +2 ATR, 40 bp stop, up to two open (paper only, relay-ineligible)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA
from cross_venue_premium import PremiumRule
from regime_adaptive_binding import Gs7PremiumEvaluator
from taker_time_exit_binding import TakerTimeExitBinding

LANE = "FAMILY_GS07_FAST_PREMIUM_FADE"
_BINDING = TakerTimeExitBinding(LANE, "GS-07 Fast premium fade · Binance/Bybit premium vs 60s mean, taker, 20-min hold, 40 bp stop")
POLICY_ID = _BINDING.policy_id
POLICY_SIGNATURE = _BINDING.policy_signature
ENTRY = _BINDING.entry
EXIT = _BINDING.exit
SPEC = _BINDING.spec
ADAPTIVE_ENTRY = True
CHASE_STEP = SPEC.chase_step
MIN_CLOSED_CANDLES = _BINDING.MIN_CLOSED_CANDLES
SIGNAL_CLOCK = ENTRY.get("signal_clock")
RULE = PremiumRule.from_policy(ENTRY, EXIT)

__all__ = ("ACTION_STAND_ASIDE", "ACTION_TAKER", "DECISION_SCHEMA")


def make_evaluator() -> Gs7PremiumEvaluator:
    return Gs7PremiumEvaluator(RULE, policy_id=POLICY_ID, policy_signature=POLICY_SIGNATURE)


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
