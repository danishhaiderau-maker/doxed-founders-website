"""Generic binding of a registry tile to taker-at-signal entry + time exit + catastrophic stop.

Everything is read from the bound lane's registry spec:

* ``entry_policy.direction_source`` chooses the tile's side from the one shared
  AI call. ``SCORE_LED_SIDE`` trades the score-led side; ``INVERTED_SCORE_LED_SIDE``
  trades the opposite side. Only a score tie, invalid scores or an AI error
  refuse; raw AI NO_TRADE and a small score gap are logged features, not gates.
* Entry is one marketable limit at the signal (ask/bid plus a protection cap,
  short TTL). The tile stands aside when the quoted spread or the BBO age exceeds
  its registry limits.
* Exit is the time limit or the catastrophic price stop. A tile whose registry
  spec declares a ``ladder`` also arms that profit-lock ladder (margin % peak →
  locked at the tile's leverage); the effective stop is the more protective of
  the catastrophic stop and the armed lock. Stops and locks book the
  side-correct quote that crossed them, so gap-through is reported as realised
  loss rather than hidden.
"""
from __future__ import annotations

import copy
import math
from typing import Any, Mapping

from adaptive_regime_entry import (
    ACTION_STAND_ASIDE,
    ACTION_TAKER,
    DECISION_SCHEMA,
    AdaptiveRegimeEntry,
    price_tick,
)
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

DIRECTION_SOURCES = frozenset({"SCORE_LED_SIDE", "INVERTED_SCORE_LED_SIDE"})
_OPPOSITE = {"LONG": "SHORT", "SHORT": "LONG"}


class TakerTimeExitBinding:
    MIN_CLOSED_CANDLES = 0

    def __init__(self, lane: str, label: str):
        spec = COMBO_LANE_SPECS[lane]
        self.lane = lane
        self.policy_id = spec["raw_policy_id"]
        self.policy_signature = spec["policy_signature"]
        self.admission_policy_id = spec["admission_treatment"]
        self.entry = spec["entry_policy"]
        self.exit = spec["exit_policy"]
        if self.entry["direction_source"] not in DIRECTION_SOURCES:
            raise ValueError(f"{lane}: unknown direction_source {self.entry['direction_source']}")
        self.ladder = tuple(tuple(row) for row in spec.get("ladder") or ())
        self.spec = PolicySpec(
            policy_id=self.policy_id, lane=lane, label=label,
            family=self.exit["family"], entry_offset_pct=0.0, chase_windows=(),
            chase_interval_sec=0, chase_step=0.0,
            entry_ttl_sec=int(self.entry["taker_ttl_sec"]),
            initial_stop_atr_k=None,
            hard_stop_margin_pct=float(self.exit["hard_stop_margin_pct"]),
            max_duration_sec=int(self.exit["max_duration_sec"]),
            margin_cap_usd=float(spec["requested_margin_usd"]),
            trail_ladder=self.ladder,
            ladder_label=spec.get("ladder_label") if self.ladder else None,
            ladder_profile_id=spec.get("ladder_profile_id") if self.ladder else None,
        )
        # Reuses the adaptive decision contract so the generic lifecycle adapter
        # and analyzer treat this tile's signal-time record identically.
        self._contract = AdaptiveRegimeEntry(
            lane=lane, policy_id=self.policy_id, policy_signature=self.policy_signature,
            entry=self.entry, initial_stop_atr_k=0.0,
        )

    def lane_admission(self, raw_ai: Mapping[str, Any] | None,
                       admission: Mapping[str, Any] | None) -> dict[str, Any]:
        """This tile's own view of the shared call; never mutates the shared row."""
        raw = dict(raw_ai or {})
        admission = dict(admission or {})
        score_led = str(admission.get("effective_direction") or "").upper()
        reason = None
        if raw.get("ai_error"):
            reason = "AI_ERROR"
        elif not admission.get("applied"):
            reason = "SCORE_LED_TREATMENT_INACTIVE"
        elif not admission.get("accepted") or score_led not in _OPPOSITE:
            reason = str(admission.get("reason") or "SCORE_LED_SIDE_UNAVAILABLE")
        accepted = reason is None
        direction = (
            (_OPPOSITE[score_led] if self.entry["direction_source"] == "INVERTED_SCORE_LED_SIDE" else score_led)
            if accepted else "NO_TRADE"
        )
        lane_ai = copy.deepcopy(raw)
        lane_ai.update({
            "raw_direction": str(raw.get("raw_direction") or raw.get("direction") or "UNKNOWN").upper(),
            "raw_decision": str(raw.get("raw_decision") or raw.get("decision") or "UNKNOWN").upper(),
            "direction": direction,
            "candidate_direction": direction,
            "decision": "APPROVE" if accepted else "REJECT",
            "approved": accepted,
            "execution_tier": "APPROVE" if accepted else "REJECT",
            "research_soft": "APPROVE" if accepted else "REJECT",
            "direction_source": self.entry["direction_source"],
            "score_led_direction": score_led or None,
            "effective_research_admission": copy.deepcopy(admission),
            "effective_research_admission_policy_id": self.admission_policy_id,
            "effective_research_direction": direction,
        })
        return {
            "accepted": accepted,
            "direction": direction,
            "reason": "LANE_ADMISSION_" + (self.entry["direction_source"] if accepted else reason),
            "lane_ai": lane_ai,
        }

    def decide_entry(self, *, direction: str, signal_ts: float, candles_1m=None,
                     bid: float, ask: float, bbo_ts: float | None, atr_abs: float = 0.0,
                     reference_price: float = 0.0,
                     ai_feature: Mapping[str, Any] | None = None) -> dict[str, Any]:
        entry = self.entry
        direction = str(direction or "").upper()
        bid = float(bid or 0); ask = float(ask or 0)
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
        spread_bps = (ask - bid) / mid * 1e4 if mid > 0 and ask > bid else None
        bbo_age = float(signal_ts) - float(bbo_ts) if bbo_ts else None
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
            "spread_bps": None if spread_bps is None else round(spread_bps, 4),
            "max_spread_bps": float(entry["max_spread_bps"]),
            "bbo_age_sec": None if bbo_age is None else round(bbo_age, 3),
            "tick": price_tick(mid or float(reference_price or 0)),
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
        if bid <= 0 or ask <= 0 or ask <= bid:
            return stand_aside("BBO_UNAVAILABLE")
        if bbo_age is None or bbo_age > float(entry["max_bbo_age_sec"]):
            return stand_aside("BBO_STALE")
        if spread_bps > float(entry["max_spread_bps"]):
            return stand_aside("SPREAD_ABOVE_MAX")
        cap = float(entry["taker_protection_bps"]) / 1e4
        tick = record["tick"]
        long_side = direction == "LONG"
        # Round inside the cap (never past it) but never behind the touch.
        raw = ask * (1.0 + cap) if long_side else bid * (1.0 - cap)
        limit = max(ask, math.floor(raw / tick) * tick) if long_side else min(bid, math.ceil(raw / tick) * tick)
        record.update({
            "action": ACTION_TAKER,
            "reason": "TAKER_AT_SIGNAL",
            "liquidity_intent": "TAKER",
            "limit_price": round(limit, 8),
            "entry_ttl_sec": int(entry["taker_ttl_sec"]),
        })
        return record

    def decision_is_executable(self, decision, direction) -> bool:
        return self._contract.decision_is_executable(decision, direction)

    def adaptive_entry_fields(self, direction, reference_price, decision):
        return self._contract.entry_fields(
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
        config = _config(self.spec, analyzer_sync_id)
        config["hard_stop_bps"] = float(self.exit["hard_stop_bps"])
        config["stop_fill"] = self.exit["stop_fill"]
        return config

    def dashboard_policy(self):
        tile = COMBO_LANE_SPECS[self.lane]
        entry, exit_policy = self.entry, self.exit
        payload = _dashboard(self.spec)
        side = (
            "Side = opposite of score-led AI side"
            if entry["direction_source"] == "INVERTED_SCORE_LED_SIDE" else "Side = score-led AI side"
        )
        max_open = int(exit_policy.get("max_open_positions") or 1)
        exit_chips = (
            [
                f"Ladder {self.spec.ladder_label}",
                "Stop = tighter of catastrophic stop and lock",
                "No break-even / trail / target beyond the ladder",
            ]
            if self.ladder else ["No ladder / break-even / trail / target"]
        )
        payload["filter_chips"] = [
            "PAPER ONLY", side,
            f"Taker cap {entry['taker_protection_bps']:g}bps, {entry['taker_ttl_sec']}s",
            f"Spread >{entry['max_spread_bps']:g}bps → stand aside",
            f"Stop {exit_policy['hard_stop_bps']:g}bp catastrophic",
            f"{int(exit_policy['max_duration_sec']) // 60}m time exit",
            *exit_chips,
            f"Max {max_open} open position" + ("s" if max_open > 1 else ""),
        ]
        payload["entry"].update({
            "trigger": (
                "Shared three-minute call; side is the opposite of the score-led side "
                "(raw AI NO_TRADE and small gaps still trade; only ties/errors refuse)"
                if entry["direction_source"] == "INVERTED_SCORE_LED_SIDE"
                else "Shared three-minute call; score-led side"
            ),
            "entry_path": self.lane,
            "chase_detail": "No chase; one signal-time marketable limit or stand-aside",
            "direction_source": entry["direction_source"],
        })
        payload["exit"].update({
            "profile": exit_policy["family"],
            "fixed_time_exit": f"{int(exit_policy['max_duration_sec']) // 60}m",
            "hard_stop_bps": exit_policy["hard_stop_bps"],
            "stop_fill": exit_policy["stop_fill"],
            "max_open_positions": exit_policy.get("max_open_positions"),
        })
        if self.ladder:
            payload["exit"].update({
                "profit_lock": exit_policy["profit_lock"],
                "lock_fill": exit_policy["lock_fill"],
                "ladder": self.spec.ladder_label,
            })
        pre = tile.get("pre_registration")
        if pre:
            payload["pre_registration"] = {
                "hypothesis_id": pre["hypothesis_id"],
                "control_lane": pre["control_lane"],
                "honest_label": pre.get("honest_label"),
                "promotion": tile["promotion_criteria"],
                "kill": tile["kill_criteria"],
            }
        return payload
