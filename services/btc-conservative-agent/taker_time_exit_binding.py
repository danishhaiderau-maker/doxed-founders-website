"""Generic binding of a registry tile to taker-at-signal entry + time exit + catastrophic stop.

Everything is read from the bound lane's registry spec:

* ``entry_policy.direction_source`` chooses the tile's side: ``SCORE_LED_SIDE``
  trades the score-led side of the one shared AI call and
  ``INVERTED_SCORE_LED_SIDE`` the opposite side (only a score tie, invalid
  scores or an AI error refuse, unless the entry declares a commit rule via
  ``trades_raw_ai_no_trade`` / ``min_score_gap``, or
  ``trades_only_raw_ai_no_trade`` to trade only calls where the raw AI
  abstained); ``RANDOM_COIN_ON_COMMITTED_CALL`` admits the same calls as the
  commit rule but takes its side from a deterministic coin (sha256 of the
  entry's ``coin_salt`` and the shared call id), the execution-cost control;
  the cross-venue sources take their side from the per-second cross-venue
  evaluator.
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
import hashlib
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
    SHARED_AI_SIGNAL_DETAIL,
    PolicySpec,
    account_risk_quantity as _size,
    chase_due as _chase,
    dashboard_policy as _dashboard,
    entry_fields as _entry,
    exit_action as _exit,
    exit_config as _config,
    protection_chips,
    registry_protections,
    session_gated,
)

# Cross-venue tiles take their side from the per-second cross-venue evaluator
# (cross_venue_session_follow.py: either the lead or the premium rule), never
# from the shared AI call.
CROSS_VENUE_LEAD_OR_PREMIUM = "CROSS_VENUE_LEAD_OR_PREMIUM"
# Premium-only clock (cross_venue_premium.PremiumEvaluator): side toward the
# leaders when their premium leaves its trailing mean (Bitfinex convergence).
CROSS_VENUE_PREMIUM = "CROSS_VENUE_PREMIUM"
CROSS_VENUE_SOURCES = frozenset({CROSS_VENUE_LEAD_OR_PREMIUM, CROSS_VENUE_PREMIUM})
# Execution-cost control: the commit rule's calls with a deterministic coin side.
RANDOM_COIN_ON_COMMITTED_CALL = "RANDOM_COIN_ON_COMMITTED_CALL"
DIRECTION_SOURCES = (
    frozenset({"SCORE_LED_SIDE", "INVERTED_SCORE_LED_SIDE", RANDOM_COIN_ON_COMMITTED_CALL}) | CROSS_VENUE_SOURCES
)
_OPPOSITE = {"LONG": "SHORT", "SHORT": "LONG"}


def coin_side(salt: str, call_id: Any) -> str | None:
    """Deterministic coin for one shared call: even first sha256 byte LONG, odd SHORT; None without an id."""
    text = str(call_id or "").strip()
    if not text:
        return None
    digest = hashlib.sha256(f"{salt}|{text}".encode("utf-8")).digest()
    return "LONG" if digest[0] % 2 == 0 else "SHORT"


# Dashboard evidence badge per registry hypothesis status (honest-label strength).
EVIDENCE_BADGES = {
    "HINT_3D_WALK_FORWARD_CI_SPANS_0": "HINT — 3-day walk-forward, CI spans 0",
    "HINT_4D_NESTED_WF_CI_SPANS_0": "HINT — 4-day nested walk-forward, CI spans 0",
    "HINT_SHORT_RECHECK_CI_SPANS_0": "HINT — 1.3-day re-check, CI spans 0",
    "HINT_4D_REPLAY_CI_LOWER_NEAR_0": "HINT — 4-day replay, CI lower bound near 0",
    "HINT_4D_REPLAY_CI_SPANS_0": "HINT — 4-day replay, CI spans 0",
    "FREEZE21_HYPOTHESIS_HINT_CI_SPANS_0": "FREEZE21 hypothesis — HINT, CI spans 0",
    "FREEZE21_HYPOTHESIS_DESCRIPTIVE_ONLY": "FREEZE21 hypothesis — descriptive only, no replay",
    "FREEZE21_CONTROL": "FREEZE21 control — random side, measures execution cost",
}


def evidence_badge(tile: Mapping[str, Any]) -> str | None:
    status = str(((tile.get("presentation") or {}).get("hypothesis_result") or {}).get("status") or "")
    return EVIDENCE_BADGES.get(status)


def session_chips(entry: Mapping[str, Any]) -> list[str]:
    sessions = tuple(entry.get("allowed_sessions") or ())
    if not sessions or len(sessions) >= 3 or entry.get("direction_source") in CROSS_VENUE_SOURCES:
        return []
    return [f"Sessions {'+'.join(sessions)} only (UTC); other sessions stand aside"]


def committed_call_refusal(entry: Mapping[str, Any], raw: Mapping[str, Any],
                           admission: Mapping[str, Any], score_led: str) -> str | None:
    """Refusal reason when a commit-only tile must not trade this call, else None.

    Tiles whose entry keeps ``trades_raw_ai_no_trade`` true and ``min_score_gap``
    unset are never refused here.
    """
    min_gap = entry.get("min_score_gap")
    if entry.get("trades_raw_ai_no_trade", True) and min_gap is None:
        return None
    if not entry.get("trades_raw_ai_no_trade", True):
        raw_side = str(raw.get("raw_direction") or "").upper()
        if raw_side not in _OPPOSITE or raw.get("explicit_abstain"):
            return "RAW_AI_NO_TRADE"
        if raw.get("score_direction_mismatch") or raw_side != score_led:
            return "SCORE_DIRECTION_MISMATCH"
    if min_gap is not None:
        try:
            gap = float(admission.get("score_gap"))
        except (TypeError, ValueError):
            return "SCORE_GAP_BELOW_MIN"
        if not math.isfinite(gap) or gap < float(min_gap):
            return "SCORE_GAP_BELOW_MIN"
    return None


def signal_source_detail(tile: Mapping[str, Any]) -> str:
    """Strategy-box signal line derived from the tile's registry signal metadata."""
    if tile.get("uses_shared_ai_direction", True) and not tile.get("signal_clock"):
        return SHARED_AI_SIGNAL_DETAIL
    entry = tile.get("entry_policy") or {}
    clock = str(tile.get("signal_clock") or "own signal clock").replace("_", " ").capitalize()
    venues = "/".join(str(v).capitalize() for v in entry.get("leader_venues") or ())
    kind = str(entry.get("direction_source") or "signal").upper().removeprefix("CROSS_VENUE_").lower()
    if entry.get("lookback_sec"):
        window = f" over {entry['lookback_sec']}s"
    elif entry.get("premium_mean_window_sec"):
        window = f" relative to its {int(entry['premium_mean_window_sec']) // 60}-min mean"
    else:
        window = ""
    source = f" ({venues} {kind} vs Bitfinex{window})" if venues else ""
    role = str(entry.get("ai_decision_role") or "NONE").upper()
    ai = "no AI call" if role == "NONE" else f"AI role {role}, not the shared AI call"
    return f"{clock}{source}; {ai}; independent identity, order, position and ledger"


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
            **registry_protections(self.exit),
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
        if self.entry["direction_source"] in CROSS_VENUE_SOURCES:
            reason = "NOT_A_SHARED_AI_TILE"
        elif raw.get("ai_error"):
            reason = "AI_ERROR"
        elif not admission.get("applied"):
            reason = "SCORE_LED_TREATMENT_INACTIVE"
        elif not admission.get("accepted") or score_led not in _OPPOSITE:
            reason = str(admission.get("reason") or "SCORE_LED_SIDE_UNAVAILABLE")
        else:
            reason = committed_call_refusal(self.entry, raw, admission, score_led)
        source = self.entry["direction_source"]
        if reason is None and self.entry.get("trades_only_raw_ai_no_trade"):
            if str(raw.get("raw_direction") or "").upper() in _OPPOSITE:
                reason = "RAW_AI_COMMITTED"
        coin = None
        if reason is None and source == RANDOM_COIN_ON_COMMITTED_CALL:
            coin = coin_side(self.entry["coin_salt"], raw.get("shared_ai_call_id") or raw.get("trade_id"))
            if coin is None:
                reason = "NO_CALL_ID_FOR_COIN"
        accepted = reason is None
        if not accepted:
            direction = "NO_TRADE"
        elif source == RANDOM_COIN_ON_COMMITTED_CALL:
            direction = coin
        elif source == "INVERTED_SCORE_LED_SIDE":
            direction = _OPPOSITE[score_led]
        else:
            direction = score_led
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
            **({"coin_side": coin, "coin_salt": self.entry["coin_salt"]} if coin else {}),
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
        if session_gated(entry, signal_ts):
            return stand_aside("SESSION_GATED")
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
        payload = _dashboard(self.spec, signal_detail=signal_source_detail(tile))
        source = entry["direction_source"]
        if source in CROSS_VENUE_SOURCES:
            return self._cross_venue_dashboard_policy(payload, tile)
        max_open = int(exit_policy.get("max_open_positions") or 1)
        committed = not entry.get("trades_raw_ai_no_trade", True)
        inverted = source == "INVERTED_SCORE_LED_SIDE"
        random_side = source == RANDOM_COIN_ON_COMMITTED_CALL
        only_no_trade = bool(entry.get("trades_only_raw_ai_no_trade"))
        exit_chips = (
            [
                f"Ladder {self.spec.ladder_label}",
                "Stop = tighter of catastrophic stop and lock",
                "No break-even / trail / target beyond the ladder",
            ]
            if self.ladder else protection_chips(exit_policy)
        )
        payload["filter_chips"] = [
            "PAPER ONLY", *([evidence_badge(tile)] if evidence_badge(tile) else []),
            ("Side = deterministic coin (execution-cost control), never the AI" if random_side
             else "Side = opposite of score-led AI side" if inverted else "Side = score-led AI side"),
            *(["Only committed calls: explicit AI side matching the scores", "Never fades NO_TRADE"] if committed else []),
            *(["Only AI NO_TRADE calls: score-led side when the AI abstains",
               "Never trades an explicit AI LONG/SHORT"] if only_no_trade else []),
            *session_chips(entry),
            f"Taker cap {entry['taker_protection_bps']:g}bps, {entry['taker_ttl_sec']}s",
            f"Spread >{entry['max_spread_bps']:g}bps → stand aside",
            f"Stop {exit_policy['hard_stop_bps']:g}bp catastrophic",
            f"{int(exit_policy['max_duration_sec']) // 60}m time exit",
            *exit_chips,
            f"Max {max_open} open position" + ("s" if max_open > 1 else ""),
        ]
        trigger = "Shared three-minute call; " + (
            "the AI's committed calls (explicit LONG/SHORT matching the scores) with a random side from a "
            "deterministic coin; NO_TRADE, mismatches, ties and errors refuse" if random_side
            else "opposite of the AI's committed side (explicit LONG/SHORT matching the scores; NO_TRADE, mismatches, "
            "ties and errors refuse)" if committed and inverted
            else "score-led side only when the raw AI returned NO_TRADE (explicit LONG/SHORT, ties, invalid scores "
            "and errors refuse)" if only_no_trade
            else "opposite of the score-led side" if inverted else "score-led side"
        )
        payload["entry"].update({
            "trigger": trigger,
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
        return self._with_pre_registration(payload, tile)

    @staticmethod
    def _with_pre_registration(payload, tile):
        pre = tile.get("pre_registration")
        if pre:
            payload["pre_registration"] = {
                "hypothesis_id": pre["hypothesis_id"],
                "control_lane": pre.get("control_lane"),
                "honest_label": pre.get("honest_label"),
                "promotion": tile["promotion_criteria"],
                "kill": tile["kill_criteria"],
            }
        return payload

    def _cross_venue_dashboard_policy(self, payload, tile):
        entry, exit_policy = self.entry, self.exit
        venues = "/".join(v.capitalize() for v in entry["leader_venues"])
        hold = int(exit_policy["max_duration_sec"])
        sessions = "/".join(entry["allowed_sessions"])
        premium = (
            f"premium vs {int(entry['premium_mean_window_sec']) // 60}-min mean >=+"
            f"{entry['premium_long_threshold_bps']:g} / <={entry['premium_short_threshold_bps']:g}bp"
        )
        if entry["direction_source"] == CROSS_VENUE_PREMIUM:
            side_chip = f"Side = toward {venues} when their {premium} (Bitfinex convergence)"
            trigger = (
                f"Per-second cross-venue premium evaluator (no AI); at most one entry per "
                f"{int(entry.get('min_submit_interval_sec') or 5)}s; sessions {sessions}"
            )
        else:
            side_chip = (
                f"Side = {venues} lead >={entry['lead_threshold_bps']:g}bp over {entry['lookback_sec']}s OR "
                f"{premium}; opposite triggers -> no trade"
            )
            trigger = (
                f"Per-second cross-venue evaluator (no AI), this tile's own copy of the lead and premium rules; "
                f"trades only in the frozen UTC session map ({sessions})"
            )
        payload["filter_chips"] = [
            "PAPER ONLY", evidence_badge(tile) or "HINT",
            side_chip,
            f"Taker cap {entry['taker_protection_bps']:g}bps, {entry['taker_ttl_sec']}s",
            f"Spread >{entry['max_spread_bps']:g}bps → stand aside",
            f"Any feed >{entry['max_venue_age_sec']:g}s old → no trade",
            f"Stop {exit_policy['hard_stop_bps']:g}bp catastrophic",
            f"{hold}s time exit" if hold < 600 else f"{hold // 60}m time exit",
            *([] if not (exit_policy.get("breakeven") or exit_policy.get("trail") or exit_policy.get("early_cut"))
              else protection_chips(exit_policy)),
            f"Max {int(exit_policy.get('max_open_positions') or 1)} open position"
            + ("s" if int(exit_policy.get("max_open_positions") or 1) > 1 else ""),
        ]
        payload["entry"].update({
            "trigger": trigger,
            "entry_path": self.lane,
            "chase_detail": "No chase; one trigger-time marketable limit or stand-aside",
            "direction_source": entry["direction_source"],
            "signal_clock": entry["signal_clock"],
        })
        payload["exit"].update({
            "profile": exit_policy["family"],
            "fixed_time_exit": f"{hold}s" if hold < 600 else f"{hold // 60}m",
            "hard_stop_bps": exit_policy["hard_stop_bps"],
            "stop_fill": exit_policy["stop_fill"],
            "max_open_positions": exit_policy.get("max_open_positions"),
        })
        return self._with_pre_registration(payload, tile)
