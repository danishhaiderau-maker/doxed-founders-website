"""Tile 3: Tile 2 plus a break-even rung, so a winner that reaches it never closes below entry + cost (paper only)."""
from __future__ import annotations

from adaptive_profit_lock_binding import AdaptiveProfitLockBinding
from adaptive_regime_entry import ACTION_MAKER, ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA

LANE = "FAMILY_ADAPTIVE_REGIME_LADDER_BE"
_BINDING = AdaptiveProfitLockBinding(LANE, "Adaptive regime entry + ATR trail + Scenario-C ladder + break-even")
POLICY_ID = _BINDING.policy_id
POLICY_SIGNATURE = _BINDING.policy_signature
ENTRY = _BINDING.entry
EXIT = _BINDING.exit
LADDER = _BINDING.ladder
SPEC = _BINDING.spec
ADAPTIVE = _BINDING.adaptive
ADAPTIVE_ENTRY = True
CHASE_STEP = SPEC.chase_step
MIN_CLOSED_CANDLES = ADAPTIVE.min_closed_candles

__all__ = ("ACTION_MAKER", "ACTION_STAND_ASIDE", "ACTION_TAKER", "DECISION_SCHEMA")

classify_regime = _BINDING.classify_regime
fast_move_z = _BINDING.fast_move_z
decide_entry = _BINDING.decide_entry
decision_is_executable = _BINDING.decision_is_executable
adaptive_entry_fields = _BINDING.adaptive_entry_fields
entry_fields = _BINDING.entry_fields
chase_due = _BINDING.chase_due
account_risk_quantity = _BINDING.account_risk_quantity
exit_action = _BINDING.exit_action
exit_config = _BINDING.exit_config
dashboard_policy = _BINDING.dashboard_policy
