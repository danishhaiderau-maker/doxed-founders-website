"""GS-06 (GS-20261005-06, FREEZE21B mid-epoch addition, corrected spec 2026-10-05 20:37): H-A committed-AI fade (Asia+EU, spread <= 3 bp) as a taker in every regime, VIOLENT reported as its own pre-registered cell with the old stand-aside as a shadow tag; HM F1 patient-fade exits (-12 bp/5 min cut, 40 bp stop, BE +25 -> +8, 2.5 ATR trail armed at max(25 bp, 3 ATR), 120-min backstop; no TP, no ladder); two open (paper only)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_MAKER, ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA
from regime_adaptive_binding import RegimeAdaptiveBinding

LANE = "FAMILY_GS06_COMMITTED_FADE_ATR_TP"
_BINDING = RegimeAdaptiveBinding(LANE, "GS-06 Committed fade + ATR TP")
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
