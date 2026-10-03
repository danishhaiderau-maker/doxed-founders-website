"""Danish — no early stop: the Danish confirmed fade (Asia+EU) without the conditional early cut — break-even +20->+5 bp, 90-min backstop, 40 bp stop (paper only)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_MAKER, ACTION_STAND_ASIDE, DECISION_SCHEMA
from maker_confirm_market_time_exit_binding import MakerConfirmMarketTimeExitBinding

LANE = "FAMILY_DANISH_CF_NOES"
_BINDING = MakerConfirmMarketTimeExitBinding(LANE, "Danish — no early stop · confirmed fade of committed AI calls, Asia+EU")
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
confirm_market_action = _BINDING.confirm_market_action
account_risk_quantity = _BINDING.account_risk_quantity
exit_action = _BINDING.exit_action
exit_config = _BINDING.exit_config
dashboard_policy = _BINDING.dashboard_policy
