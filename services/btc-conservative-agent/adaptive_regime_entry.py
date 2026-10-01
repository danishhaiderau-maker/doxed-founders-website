"""Generic adaptive regime entry primitive shared by registry-owned tiles.

This module is execution infrastructure, not an active tile.  Each
``paper_policy_*.py`` binding owns one frozen ``AdaptiveRegimeEntry`` bound to
its own lane, policy id and policy signature, so a decision record can only be
executed by the tile that produced it.

The entry style is decided once, at signal time, from closed Bitfinex 1m
candles and the side-correct BBO observed at that moment:

* CALM (trailing RV below the frozen p40) and no fast move: rest a plain limit
  at the touch improved by at most ``maker_improve_ticks``, cancelled after
  ``maker_ttl_sec``.
* NORMAL (p40..p90) or a fast move in the signal direction: marketable limit at
  the opposite touch plus ``taker_protection_bps``, cancelled after
  ``taker_ttl_sec``.
* EXTREME (above the frozen p90): stand aside.
* Any initial ATR stop at or beyond ``liquidation_guard_stop_bps`` (100x
  liquidation is ~50 bps away): stand aside.

Missing or stale inputs stand aside. Raw AI approve/reject is recorded only as
a feature; the shared score-led direction is the sole admission input.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

DECISION_SCHEMA = "adaptive_entry_decision_v1"
ACTION_TAKER = "TAKER"
ACTION_MAKER = "MAKER"
ACTION_STAND_ASIDE = "STAND_ASIDE"


def _log_returns(closes: Sequence[float]) -> list[float] | None:
    values = [float(c) for c in closes]
    if len(values) < 2 or any(not math.isfinite(c) or c <= 0 for c in values):
        return None
    return [math.log(b / a) for a, b in zip(values, values[1:])]


def realized_vol_bps(closes: Sequence[float]) -> float | None:
    """sqrt(sum r^2) of consecutive 1m log returns, in bps."""
    returns = _log_returns(closes)
    if not returns:
        return None
    return math.sqrt(sum(r * r for r in returns)) * 1e4


def price_tick(price: float) -> float:
    """Bitfinex perpetual prices carry five significant digits."""
    price = float(price or 0)
    if price <= 0:
        return 0.0
    return 10.0 ** (math.floor(math.log10(price)) - 4)


def closed_closes(candles_1m: Sequence[Sequence[float]], signal_ts: float) -> tuple[list[float], float | None]:
    """Closes of candles whose minute ended at or before ``signal_ts`` (no lookahead)."""
    rows = []
    for row in candles_1m or ():
        try:
            open_ts = float(row[0]) / (1000.0 if float(row[0]) > 1e11 else 1.0)
            close = float(row[4])
        except (TypeError, ValueError, IndexError):
            continue
        if open_ts + 60.0 <= float(signal_ts):
            rows.append((open_ts, close))
    rows.sort()
    deduped: dict[float, float] = {}
    for open_ts, close in rows:
        deduped[open_ts] = close
    ordered = sorted(deduped.items())
    last_close_ts = ordered[-1][0] + 60.0 if ordered else None
    return [close for _, close in ordered], last_close_ts


def score_gap(ai_feature: Mapping[str, Any] | None) -> float | None:
    try:
        return abs(float(ai_feature["long_score"]) - float(ai_feature["short_score"]))
    except (TypeError, ValueError, KeyError):
        return None


@dataclass(frozen=True)
class AdaptiveRegimeEntry:
    lane: str
    policy_id: str
    policy_signature: str
    entry: Mapping[str, Any]
    initial_stop_atr_k: float

    @property
    def rv_window_min(self) -> int:
        return int(self.entry["rv_window_min"])

    @property
    def fast_lookback_min(self) -> int:
        return int(self.entry["fast_move_lookback_min"])

    @property
    def fast_sigma_window_min(self) -> int:
        return int(self.entry["fast_move_sigma_window_min"])

    @property
    def min_closed_candles(self) -> int:
        return max(self.rv_window_min, self.fast_sigma_window_min, self.fast_lookback_min) + 1

    def fast_move_z(self, closes: Sequence[float], direction: str) -> float | None:
        """Signed z-score of the last lookback return in the signal direction."""
        direction = str(direction).upper()
        sign = 1 if direction == "LONG" else -1 if direction == "SHORT" else 0
        window = self.fast_sigma_window_min
        lookback = self.fast_lookback_min
        if not sign or len(closes) < window + 1:
            return None
        returns = _log_returns(closes[-(window + 1):])
        if not returns:
            return None
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / max(len(returns) - 1, 1)
        sigma = math.sqrt(var)
        if sigma <= 0:
            return None
        move = math.log(float(closes[-1]) / float(closes[-1 - lookback]))
        return sign * move / (sigma * math.sqrt(lookback))

    def classify_regime(self, rv_bps: float | None) -> str:
        if rv_bps is None:
            return "UNAVAILABLE"
        if rv_bps > float(self.entry["extreme_above_bps"]):
            return "EXTREME"
        if rv_bps < float(self.entry["calm_below_bps"]):
            return "CALM"
        return "NORMAL"

    def ai_admission_block(self, ai_feature: Mapping[str, Any] | None) -> str | None:
        """Raw NO_TRADE or a weak score gap never trades; approve/reject stays a feature."""
        ai_feature = ai_feature or {}
        raw_direction = str(ai_feature.get("raw_direction") or "").upper()
        if self.entry["block_raw_ai_no_trade"] and raw_direction not in ("LONG", "SHORT"):
            return "AI_NO_TRADE"
        gap = score_gap(ai_feature)
        if gap is None:
            return "AI_SCORES_UNAVAILABLE"
        if gap < float(self.entry["min_score_gap"]):
            return "AI_SCORE_GAP_BELOW_MIN"
        return None

    def decide_entry(self, *, direction: str, signal_ts: float, candles_1m: Sequence[Sequence[float]],
                     bid: float, ask: float, bbo_ts: float | None, atr_abs: float,
                     reference_price: float, ai_feature: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Pure signal-time decision; every branch returns a complete, loggable record."""
        entry = self.entry
        direction = str(direction or "").upper()
        closes, last_close_ts = closed_closes(candles_1m, signal_ts)
        window = self.rv_window_min
        rv = realized_vol_bps(closes[-(window + 1):]) if len(closes) >= window + 1 else None
        z = self.fast_move_z(closes, direction)
        regime = self.classify_regime(rv)
        fast = z is not None and z >= float(entry["fast_move_z"])
        bid = float(bid or 0); ask = float(ask or 0)
        reference = float(reference_price or 0) or ((bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0)
        stop_bps = (
            float(self.initial_stop_atr_k) * float(atr_abs) / reference * 1e4
            if atr_abs and float(atr_abs) > 0 and reference > 0 else None
        )
        bbo_age = float(signal_ts) - float(bbo_ts) if bbo_ts else None
        candle_age = float(signal_ts) - last_close_ts if last_close_ts else None
        tick = price_tick(reference)
        record: dict[str, Any] = {
            "schema": DECISION_SCHEMA,
            "lane": self.lane,
            "policy_id": self.policy_id,
            "policy_signature": self.policy_signature,
            "direction": direction,
            "signal_ts": float(signal_ts),
            "regime": regime,
            "rv15_bps": None if rv is None else round(rv, 4),
            "calm_below_bps": float(entry["calm_below_bps"]),
            "extreme_above_bps": float(entry["extreme_above_bps"]),
            "fast_move_z": None if z is None else round(z, 4),
            "fast_move": bool(fast),
            "closed_candles": len(closes),
            "last_candle_close_ts": last_close_ts,
            "candle_age_sec": None if candle_age is None else round(candle_age, 3),
            "bid": bid or None,
            "ask": ask or None,
            "bbo_age_sec": None if bbo_age is None else round(bbo_age, 3),
            "tick": tick,
            "atr_abs": float(atr_abs or 0) or None,
            "stop_distance_bps": None if stop_bps is None else round(stop_bps, 4),
            "liquidation_guard_stop_bps": float(entry["liquidation_guard_stop_bps"]),
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

        if direction not in ("LONG", "SHORT"):
            return stand_aside("NO_DIRECTION")
        ai_block = self.ai_admission_block(ai_feature)
        record["score_gap"] = score_gap(ai_feature)
        if ai_block:
            return stand_aside(ai_block)
        if str((ai_feature or {}).get("raw_direction") or "").upper() != direction:
            return stand_aside("AI_DIRECTION_CONFLICT")
        if len(closes) < self.min_closed_candles or candle_age is None:
            return stand_aside("REGIME_WARMUP")
        if candle_age > float(entry["max_candle_staleness_sec"]):
            return stand_aside("CANDLES_STALE")
        if regime == "UNAVAILABLE":
            return stand_aside("REGIME_UNAVAILABLE")
        if bid <= 0 or ask <= 0 or ask <= bid:
            return stand_aside("BBO_UNAVAILABLE")
        if bbo_age is None or bbo_age > float(entry["max_bbo_age_sec"]):
            return stand_aside("BBO_STALE")
        if stop_bps is None:
            return stand_aside("ATR_UNAVAILABLE")
        if stop_bps >= float(entry["liquidation_guard_stop_bps"]):
            return stand_aside("LIQUIDATION_GUARD")
        if regime == "EXTREME":
            return stand_aside("EXTREME_VOLATILITY")

        long_side = direction == "LONG"
        if regime == "NORMAL" or fast:
            cap = float(entry["taker_protection_bps"]) / 1e4
            raw = ask * (1.0 + cap) if long_side else bid * (1.0 - cap)
            limit = math.ceil(raw / tick) * tick if long_side else math.floor(raw / tick) * tick
            record.update({
                "action": ACTION_TAKER,
                "reason": "FAST_MOVE_TAKER" if regime == "CALM" else "NORMAL_TAKER",
                "liquidity_intent": "TAKER",
                "limit_price": round(limit, 8),
                "entry_ttl_sec": int(entry["taker_ttl_sec"]),
            })
            return record

        improve = int(entry["maker_improve_ticks"]) * tick
        if long_side:
            limit = bid + improve if bid + improve < ask else bid
        else:
            limit = ask - improve if ask - improve > bid else ask
        record.update({
            "action": ACTION_MAKER,
            "reason": "CALM_MAKER",
            "liquidity_intent": "MAKER",
            "limit_price": round(limit, 8),
            "entry_ttl_sec": int(entry["maker_ttl_sec"]),
        })
        return record

    def decision_is_executable(self, decision: Mapping[str, Any] | None, direction: str) -> bool:
        return bool(
            isinstance(decision, Mapping)
            and decision.get("schema") == DECISION_SCHEMA
            and decision.get("policy_id") == self.policy_id
            and decision.get("action") in (ACTION_TAKER, ACTION_MAKER)
            and str(decision.get("direction") or "") == str(direction or "").upper()
            and float(decision.get("limit_price") or 0) > 0
            and int(decision.get("entry_ttl_sec") or 0) > 0
        )

    def entry_fields(self, base_fields: dict[str, Any], direction, decision,
                     fallback_ttl_sec: int) -> dict[str, Any]:
        """Overlay an executable decision on the generic family entry fields."""
        fields = dict(base_fields)
        ok = self.decision_is_executable(decision, direction)
        limit = float(decision["limit_price"]) if ok else None
        fields.update({
            "entry_path": self.lane,
            "entry_reason": (
                f"ADAPTIVE_{decision['regime']}_{decision['action']}" if ok
                else f"ADAPTIVE_NO_ORDER_{(decision or {}).get('reason') or 'DECISION_MISSING'}"
            ),
            "deterministic_entry_offset_pct": 0.0,
            "deterministic_initial_limit": limit,
            "ai_direct_limit": limit,
            "planned_limit_price": limit,
            "structural_entry_valid": ok,
            "entry_ttl_sec": int(decision["entry_ttl_sec"]) if ok else int(fallback_ttl_sec),
            "adaptive_entry_decision": dict(decision or {}),
            "adaptive_liquidity_intent": (decision or {}).get("liquidity_intent") if ok else None,
        })
        return fields

    def filter_chips(self, hard_stop_margin_pct: float) -> list[str]:
        entry = self.entry
        return [
            "PAPER ONLY",
            f"CALM <{entry['calm_below_bps']:g}bps → maker ≤{entry['maker_improve_ticks']} tick, {entry['maker_ttl_sec']}s",
            f"NORMAL/fast z≥{entry['fast_move_z']:g} → taker cap {entry['taker_protection_bps']:g}bps, {entry['taker_ttl_sec']}s",
            f"EXTREME >{entry['extreme_above_bps']:g}bps → stand aside",
            f"Stop ≥{entry['liquidation_guard_stop_bps']:g}bps → skip",
            f"Hard stop {hard_stop_margin_pct:g}%", "120m cap",
        ]

    def dashboard_entry(self) -> dict[str, Any]:
        return {
            "trigger": "Shared three-minute score-led direction; raw AI approve/reject is a logged feature only",
            "entry_path": self.lane,
            "fill_path": "CONSERVATIVE_BBO_DEPTH_PAPER_LIMIT",
            "chase_detail": "No chase; one signal-time taker, maker or stand-aside decision",
            "regime_feature": self.entry["regime_feature"],
            "calibration": dict(self.entry.get("calibration") or {}),
        }
