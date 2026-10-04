"""GS-02 (PREREG-GS-20261004-02): NO_TRADE score-led follow with a regime-adaptive entry - taker in QUIET, post-only 1 ATR limit with 25% chase in windows 2-3 and a 30-min TTL in VIOLENT (ATR pct >= 66 or spread >= 2 bp); break-even 1.5 ATR, ATR trail 2 ATR armed at 2 ATR, 8 bp/5 min thesis cut, 35 bp stop, 60-min time stop, one open (paper only)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_MAKER, ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA
from regime_adaptive_binding import RegimeAdaptiveBinding

LANE = "FAMILY_GS02_NOTRADE_REGIME_ENTRY"
_BINDING = RegimeAdaptiveBinding(LANE, "GS-02 No-trade follow, regime entry")
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
