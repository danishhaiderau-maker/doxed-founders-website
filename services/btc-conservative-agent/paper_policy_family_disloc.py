"""Tile 13 · DISLOC (owner 2026-10-11): Bitfinex dislocation vs the Binance/Bybit composite, maker at touch, 5 s cancel, 60 s / gap-closed exit, 40 bp stop, one open (paper only, relay-ineligible, self-simulated by disloc_engine)."""
from __future__ import annotations

from adaptive_regime_entry import ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA
from disloc_engine import DislocEngine, DislocRule, LANE_REF
from taker_time_exit_binding import TakerTimeExitBinding

LANE = "FAMILY_DISLOC_BFX_DISLOCATION_1M"
assert LANE_REF[0] == LANE
_BINDING = TakerTimeExitBinding(LANE, "DISLOC · Bitfinex vs Binance/Bybit dislocation, maker at touch, 5 s cancel, 60 s exit, 40 bp stop")
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
                        lane=LANE, variant="base", id_prefix="dsl")


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
