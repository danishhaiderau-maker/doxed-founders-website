"""Generic binding of a registry tile to a chased maker limit + time exit + catastrophic stop.

Same side selection, refusal, sizing and exit contract as ``TakerTimeExitBinding``;
only the entry differs from ``MakerTimeExitBinding``: the passive limit
``entry_policy.offset_pct`` beyond the decision-time price is repriced by the
shared family chase (``bot._apply_family_policy_chase``) inside the registry
``chase_windows`` (5-minute buckets), moving ``remaining_gap_step_pct`` of the
remaining gap every ``reprice_sec`` and never crossing the touch. The order
expires unfilled after ``maker_ttl_sec``; it never converts to a taker.

An entry may declare ``trades_only_raw_ai_no_trade``: the tile then trades the
score-led side only when the raw AI abstained (NO_TRADE) and refuses every call
where the AI committed to an explicit LONG/SHORT.
"""
from __future__ import annotations

from typing import Any, Mapping

from adaptive_regime_entry import ACTION_MAKER, AdaptiveRegimeEntry
from combo_pathway_config import COMBO_LANE_SPECS
from family_policy_common import (
    PolicySpec,
    dashboard_policy as _dashboard,
    marketable_quote_at_limit as _marketable,
    protection_chips,
    registry_protections,
)
from maker_time_exit_binding import MakerTimeExitBinding
from taker_time_exit_binding import (
    CROSS_VENUE_SOURCES,
    DIRECTION_SOURCES,
    TakerTimeExitBinding,
    evidence_badge,
    signal_source_detail,
)

MAKER_CHASE_ENTRY_MODE = "MAKER_LIMIT_OFFSET_CHASE"
_SIDES = ("LONG", "SHORT")


class MakerChaseTimeExitBinding(MakerTimeExitBinding):
    def __init__(self, lane: str, label: str):
        spec = COMBO_LANE_SPECS[lane]
        entry, exit_policy = spec["entry_policy"], spec["exit_policy"]
        if entry.get("mode") != MAKER_CHASE_ENTRY_MODE:
            raise ValueError(f"{lane}: entry mode {entry.get('mode')!r} is not {MAKER_CHASE_ENTRY_MODE}")
        if entry["direction_source"] not in DIRECTION_SOURCES - CROSS_VENUE_SOURCES:
            raise ValueError(f"{lane}: maker chase binding serves shared-AI tiles only")
        windows = tuple(int(w) for w in entry.get("chase_windows") or ())
        step = float(entry["remaining_gap_step_pct"]) / 100.0
        if not windows or not 0.0 < step <= 1.0 or int(entry["reprice_sec"]) <= 0:
            raise ValueError(f"{lane}: maker chase binding needs chase windows, a step and a reprice interval")
        if spec.get("ladder"):
            raise ValueError(f"{lane}: maker chase binding has no profit-lock ladder")
        self.lane = lane
        self.policy_id = spec["raw_policy_id"]
        self.policy_signature = spec["policy_signature"]
        self.admission_policy_id = spec["admission_treatment"]
        self.entry = entry
        self.exit = exit_policy
        self.ladder = ()
        self.spec = PolicySpec(
            policy_id=self.policy_id, lane=lane, label=label, family=exit_policy["family"],
            entry_offset_pct=float(entry["offset_pct"]), chase_windows=windows,
            chase_interval_sec=int(entry["reprice_sec"]), chase_step=step,
            entry_ttl_sec=int(entry["maker_ttl_sec"]), initial_stop_atr_k=None,
            hard_stop_margin_pct=float(exit_policy["hard_stop_margin_pct"]),
            max_duration_sec=int(exit_policy["max_duration_sec"]),
            margin_cap_usd=float(spec["requested_margin_usd"]),
            **registry_protections(exit_policy),
        )
        self.CHASE_STEP = step
        self._contract = AdaptiveRegimeEntry(
            lane=lane, policy_id=self.policy_id, policy_signature=self.policy_signature,
            entry=entry, initial_stop_atr_k=0.0,
        )

    def lane_admission(self, raw_ai: Mapping[str, Any] | None,
                       admission: Mapping[str, Any] | None) -> dict[str, Any]:
        verdict = TakerTimeExitBinding.lane_admission(self, raw_ai, admission)
        if not (verdict["accepted"] and self.entry.get("trades_only_raw_ai_no_trade")):
            return verdict
        raw_side = str((raw_ai or {}).get("raw_direction") or "").upper()
        if raw_side not in _SIDES:
            return verdict
        lane_ai = verdict["lane_ai"]
        lane_ai.update({
            "direction": "NO_TRADE", "candidate_direction": "NO_TRADE", "decision": "REJECT",
            "approved": False, "execution_tier": "REJECT", "research_soft": "REJECT",
            "effective_research_direction": "NO_TRADE",
        })
        return {"accepted": False, "direction": "NO_TRADE",
                "reason": "LANE_ADMISSION_RAW_AI_COMMITTED", "lane_ai": lane_ai}

    def decide_entry(self, **kwargs) -> dict[str, Any]:
        record = super().decide_entry(**kwargs)
        if record.get("action") == ACTION_MAKER:
            record["reason"] = "MAKER_OFFSET_AT_SIGNAL_THEN_CHASE"
        return record

    @staticmethod
    def marketable_quote_at_limit(*, direction: str, limit_price: float, bid: float, ask: float) -> bool:
        return _marketable(direction=direction, limit_price=limit_price, bid=bid, ask=ask)

    def dashboard_policy(self):
        tile = COMBO_LANE_SPECS[self.lane]
        entry, exit_policy = self.entry, self.exit
        payload = _dashboard(self.spec, signal_detail=signal_source_detail(tile))
        offset = float(entry["offset_pct"])
        windows = tuple(int(w) for w in entry["chase_windows"])
        window_text = "/".join(f"{w * 5}-{w * 5 + 5}m" for w in windows)
        step = float(entry["remaining_gap_step_pct"])
        reprice = int(entry["reprice_sec"])
        ttl_min = int(entry["maker_ttl_sec"]) // 60
        hold_min = int(exit_policy["max_duration_sec"]) // 60
        max_open = int(exit_policy.get("max_open_positions") or 1)
        only_no_trade = bool(entry.get("trades_only_raw_ai_no_trade"))
        inverted = entry["direction_source"] == "INVERTED_SCORE_LED_SIDE"
        payload["filter_chips"] = [
            "PAPER ONLY", evidence_badge(tile) or "HINT",
            "Side = opposite of score-led AI side" if inverted else "Side = score-led AI side",
            *(["Only AI NO_TRADE calls: score-led side when the AI abstains",
               "Never trades an explicit AI LONG/SHORT"] if only_no_trade else []),
            f"Maker limit {offset:g}% beyond the signal price, rests up to {ttl_min}m",
            f"Chase {step:g}% of the remaining gap every {reprice // 60}m in {window_text}, never past the touch",
            f"BBO older than {float(entry['max_bbo_age_sec']):g}s -> stand aside",
            f"Stop {exit_policy['hard_stop_bps']:g}bp catastrophic",
            f"{hold_min}m time exit after fill",
            *protection_chips(exit_policy),
            f"Max {max_open} signals pending or open",
        ]
        payload["entry"].update({
            "trigger": (
                "Shared three-minute call; score-led side only when the raw AI returned NO_TRADE "
                "(explicit LONG/SHORT, ties, invalid scores and errors refuse)" if only_no_trade
                else "Shared three-minute call; " + ("opposite of the score-led side" if inverted else "score-led side")
            ),
            "entry_path": self.lane,
            "chase_detail": (f"Passive limit {offset:g}% beyond the decision-time price; in {window_text} after "
                             f"submission it moves {step:g}% of the remaining gap every {reprice}s without crossing "
                             f"the touch; expires unfilled after {ttl_min}m"),
            "direction_source": entry["direction_source"],
            "liquidity_intent": "MAKER",
        })
        payload["exit"].update({
            "profile": exit_policy["family"],
            "fixed_time_exit": f"{hold_min}m",
            "hard_stop_bps": exit_policy["hard_stop_bps"],
            "stop_fill": exit_policy["stop_fill"],
            "max_open_positions": exit_policy.get("max_open_positions"),
        })
        return self._with_pre_registration(payload, tile)
