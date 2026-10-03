"""Generic binding of a registry tile to a resting maker limit + time exit + catastrophic stop.

Same side selection, refusal, exit, sizing and dashboard contract as
``TakerTimeExitBinding``; only the entry differs. The tile rests one passive
limit ``entry_policy.offset_pct`` away from the decision-time reference price in
the tile's direction (below it for LONG, above it for SHORT), never past the
touch, with no chase and no reprice, for ``entry_policy.maker_ttl_sec``. Fills
come only from the shared paper fill gate (REALISTIC_V1: trade-through or queue
consumption); an unfilled order expires and never converts to a taker.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from adaptive_regime_entry import (
    ACTION_MAKER,
    ACTION_STAND_ASIDE,
    DECISION_SCHEMA,
    AdaptiveRegimeEntry,
    price_tick,
)
from combo_pathway_config import COMBO_LANE_SPECS
from family_policy_common import (
    PolicySpec,
    dashboard_policy as _dashboard,
    protection_chips,
    registry_protections,
    session_gated,
)
from taker_time_exit_binding import (
    CROSS_VENUE_SOURCES,
    DIRECTION_SOURCES,
    TakerTimeExitBinding,
    evidence_badge,
    session_chips,
    signal_source_detail,
)

MAKER_ENTRY_MODE = "MAKER_LIMIT_OFFSET"
_OPPOSITE = {"LONG": "SHORT", "SHORT": "LONG"}


def passive_offset_limit(direction: str, reference_price: float, offset_pct: float,
                         bid: float, ask: float, tick: float) -> float | None:
    """Offset limit rounded away from the market and clamped behind the touch."""
    direction = str(direction or "").upper()
    if direction not in _OPPOSITE or reference_price <= 0 or tick <= 0 or bid <= 0 or ask <= bid:
        return None
    if direction == "LONG":
        raw = reference_price * (1.0 - offset_pct / 100.0)
        return min(bid, math.floor(raw / tick + 1e-9) * tick)
    raw = reference_price * (1.0 + offset_pct / 100.0)
    return max(ask, math.ceil(raw / tick - 1e-9) * tick)


class MakerTimeExitBinding(TakerTimeExitBinding):
    ENTRY_MODE = MAKER_ENTRY_MODE

    def __init__(self, lane: str, label: str):
        spec = COMBO_LANE_SPECS[lane]
        entry, exit_policy = spec["entry_policy"], spec["exit_policy"]
        if entry.get("mode") != self.ENTRY_MODE:
            raise ValueError(f"{lane}: entry mode {entry.get('mode')!r} is not {self.ENTRY_MODE}")
        if entry["direction_source"] not in DIRECTION_SOURCES - CROSS_VENUE_SOURCES:
            raise ValueError(f"{lane}: maker binding serves shared-AI tiles only")
        if tuple(entry.get("chase_windows") or ()):
            raise ValueError(f"{lane}: maker binding never chases")
        if spec.get("ladder"):
            raise ValueError(f"{lane}: maker binding has no profit-lock ladder")
        self.lane = lane
        self.policy_id = spec["raw_policy_id"]
        self.policy_signature = spec["policy_signature"]
        self.admission_policy_id = spec["admission_treatment"]
        self.entry = entry
        self.exit = exit_policy
        self.ladder = ()
        self.spec = PolicySpec(
            policy_id=self.policy_id, lane=lane, label=label, family=exit_policy["family"],
            entry_offset_pct=float(entry["offset_pct"]), chase_windows=(), chase_interval_sec=0,
            chase_step=0.0, entry_ttl_sec=int(entry["maker_ttl_sec"]), initial_stop_atr_k=None,
            hard_stop_margin_pct=float(exit_policy["hard_stop_margin_pct"]),
            max_duration_sec=int(exit_policy["max_duration_sec"]),
            margin_cap_usd=float(spec["requested_margin_usd"]),
            **registry_protections(exit_policy),
        )
        self._contract = AdaptiveRegimeEntry(
            lane=lane, policy_id=self.policy_id, policy_signature=self.policy_signature,
            entry=entry, initial_stop_atr_k=0.0,
        )

    def decide_entry(self, *, direction: str, signal_ts: float, candles_1m=None,
                     bid: float, ask: float, bbo_ts: float | None, atr_abs: float = 0.0,
                     reference_price: float = 0.0,
                     ai_feature: Mapping[str, Any] | None = None) -> dict[str, Any]:
        entry = self.entry
        direction = str(direction or "").upper()
        bid = float(bid or 0); ask = float(ask or 0)
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
        reference = float(reference_price or 0) or mid
        spread_bps = (ask - bid) / mid * 1e4 if mid > 0 and ask > bid else None
        bbo_age = float(signal_ts) - float(bbo_ts) if bbo_ts else None
        tick = price_tick(mid or reference)
        record: dict[str, Any] = {
            "schema": DECISION_SCHEMA,
            "lane": self.lane,
            "policy_id": self.policy_id,
            "policy_signature": self.policy_signature,
            "direction": direction,
            "direction_source": entry["direction_source"],
            "signal_ts": float(signal_ts),
            "regime": "NOT_USED",
            "bid": bid or None,
            "ask": ask or None,
            "reference_price": reference or None,
            "spread_bps": None if spread_bps is None else round(spread_bps, 4),
            "bbo_age_sec": None if bbo_age is None else round(bbo_age, 3),
            "tick": tick,
            "offset_pct": float(entry["offset_pct"]),
            "ai_feature": dict(ai_feature or {}),
            "ai_decision_role": entry["ai_decision_role"],
            "action": ACTION_STAND_ASIDE,
            "reason": None,
            "liquidity_intent": None,
            "limit_price": None,
            "entry_ttl_sec": None,
        }

        def stand_aside(reason: str) -> dict[str, Any]:
            record["reason"] = reason
            return record

        if direction not in _OPPOSITE:
            return stand_aside("NO_DIRECTION")
        if session_gated(entry, signal_ts):
            return stand_aside("SESSION_GATED")
        if bid <= 0 or ask <= 0 or ask <= bid:
            return stand_aside("BBO_UNAVAILABLE")
        if bbo_age is None or bbo_age > float(entry["max_bbo_age_sec"]):
            return stand_aside("BBO_STALE")
        if entry.get("max_spread_bps") is not None and spread_bps > float(entry["max_spread_bps"]):
            return stand_aside("SPREAD_ABOVE_MAX")
        limit = passive_offset_limit(direction, reference, float(entry["offset_pct"]), bid, ask, tick)
        if limit is None or limit <= 0:
            return stand_aside("REFERENCE_UNAVAILABLE")
        record.update({
            "action": ACTION_MAKER,
            "reason": "MAKER_OFFSET_AT_SIGNAL",
            "liquidity_intent": "MAKER",
            "limit_price": round(limit, 8),
            "entry_ttl_sec": int(entry["maker_ttl_sec"]),
        })
        return record

    def dashboard_policy(self):
        tile = COMBO_LANE_SPECS[self.lane]
        entry, exit_policy = self.entry, self.exit
        payload = _dashboard(self.spec, signal_detail=signal_source_detail(tile))
        offset = float(entry["offset_pct"])
        ttl_min = int(entry["maker_ttl_sec"]) // 60
        hold_min = int(exit_policy["max_duration_sec"]) // 60
        max_open = int(exit_policy.get("max_open_positions") or 1)
        committed = not entry.get("trades_raw_ai_no_trade", True)
        inverted = entry["direction_source"] == "INVERTED_SCORE_LED_SIDE"
        payload["filter_chips"] = [
            "PAPER ONLY", evidence_badge(tile) or "HINT",
            "Side = opposite of score-led AI side" if inverted else "Side = score-led AI side",
            *(["Only committed calls: explicit AI side matching the scores", "Never fades NO_TRADE"] if committed else []),
            *session_chips(entry),
            f"Maker limit {offset:g}% beyond the signal price, rests {ttl_min}m, no chase",
            f"BBO older than {float(entry['max_bbo_age_sec']):g}s → stand aside",
            f"Stop {exit_policy['hard_stop_bps']:g}bp catastrophic",
            f"{hold_min}m time exit after fill",
            *protection_chips(exit_policy),
            f"Max {max_open} open position" + ("s" if max_open > 1 else ""),
        ]
        payload["entry"].update({
            "trigger": (
                "Shared three-minute call; side is the opposite of the AI's committed side (explicit LONG/SHORT "
                "matching the scores; NO_TRADE, mismatches, ties and errors refuse)" if committed and inverted
                else "Shared three-minute call; " + ("opposite of the score-led side" if inverted else "score-led side")
            ),
            "entry_path": self.lane,
            "chase_detail": (f"No chase; one passive limit {offset:g}% beyond the decision-time price, never past "
                             f"the touch, expires unfilled after {ttl_min}m"),
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
