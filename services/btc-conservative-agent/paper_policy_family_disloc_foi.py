"""Tile 14 · DISLOC-FOI (owner 2026-10-11, Strategist C3): dislocation LONG + funding T2 + falling OI, maker at touch, 5 s cancel, 60 s / gap-closed exit, 40 bp stop, one open (paper only, relay-ineligible, self-simulated by disloc_engine)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA
from disloc_engine import DislocEngine, DislocRule
from taker_time_exit_binding import TakerTimeExitBinding

LANE = "FAMILY_DISLOC_FOI_LONG_1M"
_BINDING = TakerTimeExitBinding(LANE, "DISLOC-FOI · dislocation long + funding + falling OI, maker at touch, 5 s cancel, 60 s exit")
POLICY_ID = _BINDING.policy_id
POLICY_SIGNATURE = _BINDING.policy_signature
ENTRY = _BINDING.entry
EXIT = _BINDING.exit
SPEC = _BINDING.spec
ADAPTIVE_ENTRY = True
CHASE_STEP = SPEC.chase_step
MIN_CLOSED_CANDLES = _BINDING.MIN_CLOSED_CANDLES
SIGNAL_CLOCK = ENTRY["signal_clock"]
RULE = DislocRule(ENTRY, EXIT)
SELF_SIMULATED = True

__all__ = ("ACTION_STAND_ASIDE", "ACTION_TAKER", "DECISION_SCHEMA")


def make_evaluator() -> DislocEngine:
    return DislocEngine(RULE, policy_id=POLICY_ID, policy_signature=POLICY_SIGNATURE,
                        lane=LANE, variant="foi", id_prefix="dsf")


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
