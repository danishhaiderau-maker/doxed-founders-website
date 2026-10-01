"""Generic binding of a registry tile to adaptive entry + ATR Trail + profit lock.

A tile module binds its own lane; everything here is read from that lane's
registry spec, so two tiles sharing this primitive differ only by registry
identity and exit metadata (for example, whether a break-even rung is set).

Profit-lock rules are margin % at the tile's leverage:

* Scenario-C ladder: 8→5, 12→10, 19→17, 40→28, 60→45, 80→60, 100→75,
  150→120 (peak → locked);
* optional break-even rung: once peak margin return reaches the trigger, the
  stop may not sit below entry plus the round-trip cost buffer.

The effective stop is the most protective of the ATR stop/trail and the armed
lock. Paper exits book the side-correct BBO tick that crossed the stop, so any
gap past the lock is reported as slippage rather than hidden.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from adaptive_regime_entry import AdaptiveRegimeEntry
from combo_pathway_config import COMBO_LANE_SPECS
from family_policy_common import (
    PolicySpec,
    account_risk_quantity as _size,
    chase_due as _chase,
    dashboard_policy as _dashboard,
    entry_fields as _entry,
    exit_action as _exit,
    exit_config as _config,
)


class AdaptiveProfitLockBinding:
    def __init__(self, lane: str, label: str):
        spec = COMBO_LANE_SPECS[lane]
        self.lane = lane
        self.policy_id = spec["raw_policy_id"]
        self.policy_signature = spec["policy_signature"]
        self.entry = spec["entry_policy"]
        self.exit = spec["exit_policy"]
        self.ladder = tuple(tuple(row) for row in spec.get("ladder") or ())
        trigger = self.exit.get("breakeven_trigger_margin_pct")
        self.spec = PolicySpec(
            policy_id=self.policy_id, lane=lane, label=label,
            family=self.exit["family"], entry_offset_pct=0.0, chase_windows=(),
            chase_interval_sec=0, chase_step=0.0,
            entry_ttl_sec=int(self.entry["maker_ttl_sec"]),
            initial_stop_atr_k=float(self.exit["initial_stop_atr_k"]),
            trail_activation_atr_k=float(self.exit["trail_activation_atr_k"]),
            trail_atr_k=float(self.exit["trail_atr_k"]),
            hard_stop_margin_pct=float(self.exit["hard_stop_margin_pct"]),
            max_duration_sec=int(self.exit["max_duration_sec"]),
            trail_ladder=self.ladder,
            ladder_label=spec.get("ladder_label"),
            ladder_profile_id=spec.get("ladder_profile_id"),
            breakeven_trigger_margin_pct=None if trigger is None else float(trigger),
            breakeven_lock_margin_pct=float(self.exit.get("breakeven_lock_margin_pct") or 0.0),
        )
        self.adaptive = AdaptiveRegimeEntry(
            lane=lane, policy_id=self.policy_id, policy_signature=self.policy_signature,
            entry=self.entry, initial_stop_atr_k=float(self.spec.initial_stop_atr_k),
        )

    def classify_regime(self, rv_bps: float | None) -> str:
        return self.adaptive.classify_regime(rv_bps)

    def fast_move_z(self, closes: Sequence[float], direction: str) -> float | None:
        return self.adaptive.fast_move_z(closes, direction)

    def decide_entry(self, **kwargs) -> dict[str, Any]:
        return self.adaptive.decide_entry(**kwargs)

    def decision_is_executable(self, decision: Mapping[str, Any] | None, direction: str) -> bool:
        return self.adaptive.decision_is_executable(decision, direction)

    def adaptive_entry_fields(self, direction, reference_price, decision):
        return self.adaptive.entry_fields(
            _entry(self.spec, direction, reference_price), direction, decision, self.spec.entry_ttl_sec,
        )

    def entry_fields(self, direction, reference_price):
        return self.adaptive_entry_fields(direction, reference_price, None)

    def chase_due(self, *, created_ts, last_chase_ts, now):
        return _chase(self.spec, created_ts=created_ts, last_chase_ts=last_chase_ts, now=now)

    def account_risk_quantity(self, *, equity_usd, entry_price, atr_abs, leverage=100.0):
        return _size(self.spec, equity_usd=equity_usd, entry_price=entry_price, atr_abs=atr_abs, leverage=leverage)

    def exit_action(self, **kwargs):
        return _exit(self.spec, **kwargs)

    def exit_config(self, analyzer_sync_id):
        return _config(self.spec, analyzer_sync_id)

    def dashboard_policy(self):
        payload = _dashboard(self.spec)
        chips = self.adaptive.filter_chips(self.spec.hard_stop_margin_pct)
        lock_chips = []
        if self.spec.breakeven_trigger_margin_pct is not None:
            lock_chips.append(
                f"Break-even at +{self.spec.breakeven_trigger_margin_pct:g}% → lock "
                f"+{self.spec.breakeven_lock_margin_pct:g}%"
            )
        lock_chips += [
            f"Ladder {self.spec.ladder_label}",
            "Stop = tighter of ATR trail and lock",
            f"Max {int(self.exit.get('max_open_positions') or 1)} open position",
        ]
        chips[-2:-2] = lock_chips
        payload["filter_chips"] = chips
        payload["entry"].update(self.adaptive.dashboard_entry())
        payload["exit"]["profit_lock"] = self.exit["profit_lock"]
        payload["exit"]["lock_fill"] = self.exit["lock_fill"]
        payload["exit"]["max_open_positions"] = self.exit.get("max_open_positions")
        if "breakeven_cost_basis" in self.exit:
            payload["exit"]["breakeven_cost_basis"] = self.exit["breakeven_cost_basis"]
        pre = COMBO_LANE_SPECS[self.lane].get("pre_registration")
        if pre:
            payload["pre_registration"] = {
                "hypothesis_id": pre["hypothesis_id"],
                "control_lane": pre["control_lane"],
                "promotion": COMBO_LANE_SPECS[self.lane]["promotion_criteria"],
                "kill": COMBO_LANE_SPECS[self.lane]["kill_criteria"],
            }
        return payload
