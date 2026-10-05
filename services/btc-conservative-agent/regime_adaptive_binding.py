"""Binding of the GS-20261004 pre-registered rules (GS-01..04, B1..B3) to registry tiles.

Everything execution-defining is read from the bound lane's registry spec
(combo_pathway_config): the trigger source, the regime classifier, the
per-regime entry execution and the per-regime exit profile. Mechanics:

* trigger: the shared AI call (NO_TRADE follow / committed fade, admission
  inherited from TakerTimeExitBinding), H-C's cross-venue premium rule (own
  evaluator instance) or the 3 m CVD-divergence event
  (``CvdDivergenceEvaluator`` over regime_bars_3m.ENGINE);
* regime: the last closed 3 m Bitfinex bar available at the signal
  (regime_bars_3m.classify_regime); ATR in bp is frozen at the signal (4 bp
  when missing) and carried in the signal-time decision;
* entry: TAKER (marketable limit inside the 5 bp cap), TOUCH / OFFSET / DEEP /
  LIMIT_ATR post-only limits with the declared reprice schedule and, for
  TOUCH, the guarded taker fallback (``regime_entry_action``);
* exits: gs_regime_exit_stack.evaluate_tick with the regime's profile.

Nothing here places, changes or cancels an order or reads toggles/relay
state; the bot's paper lifecycle acts on the returned records.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import gs_regime_exit_stack as stack
import regime_bars_3m as bars3m
from adaptive_regime_entry import ACTION_MAKER, ACTION_STAND_ASIDE, ACTION_TAKER, DECISION_SCHEMA, price_tick
from combo_pathway_config import BAR_CLOSE_SIGNAL_CLOCK, COMBO_LANE_SPECS
from cross_venue_lead import STATUS_TRIGGER
from cross_venue_premium import PremiumEvaluator, PremiumRule
from family_policy_common import ExitAction, utc_session
from family_policy_common import dashboard_policy as _dash
from taker_time_exit_binding import (
    CROSS_VENUE_PREMIUM,
    CVD_DIVERGENCE_3M,
    TakerTimeExitBinding,
    evidence_badge,
    signal_source_detail,
)

TRIGGER_NOTRADE_FOLLOW = "NOTRADE_FOLLOW"
TRIGGER_COMMITTED_FADE = "COMMITTED_FADE"
TRIGGER_CVD = CVD_DIVERGENCE_3M
TRIGGER_PREMIUM = CROSS_VENUE_PREMIUM
REGIME_STATE_KEY = "policy_state"
_SIGN = {"LONG": 1, "SHORT": -1}


def reprice_ages(cell: Mapping[str, Any] | None) -> list[int]:
    """gslib.limit_entry schedule: every reprice_sec step inside each 5-minute window, after 0, before TTL."""
    if not cell or not cell.get("chase_windows"):
        return []
    out = set()
    step = int(cell["reprice_sec"])
    for w in sorted(int(x) for x in cell["chase_windows"]):
        x = (w - 1) * 300
        while x < w * 300:
            if x > 0:
                out.add(x)
            x += step
    ttl = int(cell["ttl_sec"])
    return sorted(x for x in out if x < ttl)


def passive_round(price: float, direction: str, tick: float) -> float:
    if tick <= 0:
        return float(price)
    if direction == "LONG":
        return math.floor(price / tick + 1e-9) * tick
    return math.ceil(price / tick - 1e-9) * tick


def taker_limit(direction: str, bid: float, ask: float, cap_bps: float, tick: float) -> float:
    """Marketable limit inside the protection cap, never behind the touch (TakerTimeExitBinding rule)."""
    cap = float(cap_bps) / 1e4
    if direction == "LONG":
        raw = ask * (1.0 + cap)
        return max(ask, math.floor(raw / tick) * tick)
    raw = bid * (1.0 - cap)
    return min(bid, math.ceil(raw / tick) * tick)


class RegimeAdaptiveBinding(TakerTimeExitBinding):
    MIN_CLOSED_CANDLES = 0
    MARKET_EXIT_CONTEXT = True

    def __init__(self, lane: str, label: str, engine: bars3m.RegimeBars3m | None = None):
        super().__init__(lane, label)
        self.engine = engine if engine is not None else bars3m.ENGINE
        self.classifier = self.entry.get("regime_classifier")
        self.regime_exec = dict(self.entry.get("regime_exec") or {})
        self.profiles = dict(self.exit["profiles"])
        self.regime_profiles = dict(self.exit["regime_profiles"])
        self.flip_indicator = dict(self.entry.get("flip_indicator") or {})
        self.regime_trigger = dict(self.entry.get("regime_trigger") or {})
        # Accepted entries per regime for regime-cell cadences (GS-05 TREND = H-C's 15 min / 4 per hour).
        self._cadence_log: dict[str, list[float]] = {}

    # ------------------------------------------------------------ admission
    def lane_admission(self, raw_ai, admission):
        if self.entry["direction_source"] == CVD_DIVERGENCE_3M:
            view = super().lane_admission(raw_ai, admission)
            return {**view, "accepted": False, "direction": "NO_TRADE",
                    "reason": "LANE_ADMISSION_NOT_A_SHARED_AI_TILE"}
        return super().lane_admission(raw_ai, admission)

    def trigger_kind(self, ai_feature: Mapping[str, Any] | None) -> str:
        feature = ai_feature or {}
        if feature.get("cvd_trigger_id"):
            return TRIGGER_CVD
        if any(str(k).endswith("xvp_trigger_id") for k in feature):
            return TRIGGER_PREMIUM
        source = self.entry["direction_source"]
        if source == "SCORE_LED_SIDE":
            return TRIGGER_NOTRADE_FOLLOW
        if source == "INVERTED_SCORE_LED_SIDE":
            return TRIGGER_COMMITTED_FADE
        return source

    def regime_at(self, signal_ts: float) -> tuple[str, dict]:
        bar = self.engine.latest(float(signal_ts))
        features = bars3m.regime_features(bar)
        if not self.classifier:
            return "QUIET", features
        regime = bars3m.classify_regime(
            features, violent_pct=self.classifier["violent_atr_pct_gte"],
            violent_spread_bp=self.classifier["violent_spread_bp_gte"],
            trend_adx=self.classifier.get("trend_adx_gte"),
        )
        return regime, features

    # ------------------------------------------------------------ entry
    def decide_entry(self, *, direction: str, signal_ts: float, candles_1m=None,
                     bid: float, ask: float, bbo_ts: float | None, atr_abs: float = 0.0,
                     reference_price: float = 0.0, ai_feature: Mapping[str, Any] | None = None) -> dict[str, Any]:
        entry = self.entry
        direction = str(direction or "").upper()
        bid = float(bid or 0); ask = float(ask or 0)
        mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0
        spread_bps = (ask - bid) / mid * 1e4 if mid > 0 and ask > bid else None
        bbo_age = float(signal_ts) - float(bbo_ts) if bbo_ts else None
        kind = self.trigger_kind(ai_feature)
        regime, features = self.regime_at(signal_ts)
        atr_bp = stack.atr_or_default(features.get("atr_bp"))
        record: dict[str, Any] = {
            "schema": DECISION_SCHEMA, "lane": self.lane, "policy_id": self.policy_id,
            "policy_signature": self.policy_signature, "direction": direction,
            "direction_source": entry["direction_source"], "trigger_kind": kind,
            "signal_ts": float(signal_ts), "regime": regime, "regime_features": features,
            "atr_bp": round(atr_bp, 6), "atr_bp_defaulted": features.get("atr_bp") is None,
            "exit_profile": self.regime_profiles.get(regime) or self.regime_profiles.get("QUIET"),
            "flip_indicator": self.flip_indicator.get(regime),
            "bid": bid or None, "ask": ask or None, "mid": mid or None,
            "reference_price": float(reference_price or 0) or None,
            "spread_bps": None if spread_bps is None else round(spread_bps, 4),
            "max_spread_bps": float(entry["max_spread_bps"]),
            "bbo_age_sec": None if bbo_age is None else round(bbo_age, 3),
            "tick": price_tick(mid or float(reference_price or 0)),
            "ai_feature": dict(ai_feature or {}), "ai_decision_role": entry["ai_decision_role"],
            "exec": None, "reprice_ages": [], "action": ACTION_STAND_ASIDE, "reason": None,
            "liquidity_intent": None, "limit_price": None, "entry_ttl_sec": None,
        }

        # GS-05 / GS-06 (``regime_audit``): every decision carries
        # ``regime_at_entry``; a regime stand-aside still runs the remaining
        # gates and records the taker order it would have sent (shadow only).
        audit = bool(entry.get("regime_audit"))
        shadow_reason = None
        if audit:
            record.update({
                "regime_at_entry": regime, "regime_at_signal": regime,
                "regime_cell": (entry.get("regime_cells") or {}).get(regime, regime),
                "pre60_side_bp": self.pre60_side_bp(direction, signal_ts, mid or float(reference_price or 0)),
            })
            if regime in tuple(entry.get("shadow_stand_aside_regimes") or ()):
                record["shadow_old_gate"] = {"would_stand_aside": True,
                                             "gate": f"META_RULE_{regime}_FADE_STAND_ASIDE",
                                             "meaning": "SHADOW_ONLY - traded as its own pre-registered cell"}

        def stand_aside(reason: str) -> dict[str, Any]:
            if shadow_reason is not None:
                record["shadow_would_have"] = {"would_submit": False, "blocked_by": reason}
                reason = shadow_reason
            record["reason"] = reason
            return record

        if direction not in _SIGN:
            return stand_aside("NO_DIRECTION")
        wanted = self.regime_trigger.get(regime)
        if wanted and wanted != kind:
            return stand_aside(f"REGIME_{regime}_TRADES_{wanted}_ONLY")
        cell = self.regime_exec.get(regime)
        if not cell:
            if not audit:
                return stand_aside(f"REGIME_{regime}_STANDS_ASIDE")
            shadow_reason = f"REGIME_{regime}_STANDS_ASIDE"
            cell = {"kind": "TAKER"}
        if bid <= 0 or ask <= 0 or ask <= bid:
            return stand_aside("BBO_UNAVAILABLE")
        if bbo_age is None or bbo_age > float(entry["max_bbo_age_sec"]):
            return stand_aside("BBO_STALE")
        if spread_bps > float(entry["max_spread_bps"]):
            return stand_aside("SPREAD_ABOVE_MAX")
        if kind == TRIGGER_COMMITTED_FADE and entry.get("fade_allowed_sessions"):
            if utc_session(signal_ts, entry.get("session_hours_utc")) not in tuple(entry["fade_allowed_sessions"]):
                return stand_aside("FADE_SESSION_GATED")
            if spread_bps > float(entry["fade_max_spread_bps"]):
                return stand_aside("FADE_SPREAD_ABOVE_MAX")
        tick = record["tick"]
        record["exec"] = dict(cell)
        cadence_block = self._cadence_block(regime, cell, float(signal_ts)) if shadow_reason is None else None
        if cadence_block:
            record["exec"] = None
            return stand_aside(cadence_block)
        if shadow_reason is not None:
            record["shadow_would_have"] = {
                "would_submit": True, "action": ACTION_TAKER, "liquidity_intent": "TAKER",
                "limit_price": round(taker_limit(direction, bid, ask, entry["taker_protection_bps"], tick), 8),
                "entry_ttl_sec": int(entry["taker_ttl_sec"]), "exit_profile": next(iter(self.profiles)),
                "meaning": "SHADOW_ONLY_NO_ORDER - the taker this tile would send outside its stand-aside regime",
            }
            record["exec"] = None
            record["reason"] = shadow_reason
            return record
        if cell.get("min_submit_interval_sec") or cell.get("max_submissions_per_hour"):
            self._cadence_log.setdefault(regime, []).append(float(signal_ts))
        if cell["kind"] == "TAKER":
            record.update({
                "action": ACTION_TAKER, "reason": f"{regime}_TAKER", "liquidity_intent": "TAKER",
                "limit_price": round(taker_limit(direction, bid, ask, entry["taker_protection_bps"], tick), 8),
                "entry_ttl_sec": int(entry["taker_ttl_sec"]),
            })
            return record
        sign = _SIGN[direction]
        ref = float(reference_price or 0) or mid
        raw = ref * (1.0 - sign * float(cell.get("offset_atr_k") or 0.0) * atr_bp / 1e4)
        raw = min(raw, bid) if sign > 0 else max(raw, ask)
        limit = passive_round(raw, direction, tick)
        record.update({
            "action": ACTION_MAKER, "reason": f"{regime}_{cell['kind']}_LIMIT", "liquidity_intent": "MAKER",
            "limit_price": round(limit, 8), "entry_ttl_sec": int(cell["order_ttl_sec"]),
            "reprice_ages": reprice_ages(cell),
        })
        return record

    def _cadence_block(self, regime: str, cell: Mapping[str, Any], now: float) -> str | None:
        gap = cell.get("min_submit_interval_sec")
        cap = cell.get("max_submissions_per_hour")
        if not gap and not cap:
            return None
        log = [ts for ts in self._cadence_log.get(regime, []) if now - ts < 3600]
        self._cadence_log[regime] = log
        if gap and log and now - log[-1] < float(gap):
            return f"{regime}_MIN_SUBMIT_INTERVAL"
        if cap and len(log) >= int(cap):
            return f"{regime}_HOURLY_SUBMISSION_CAP"
        return None

    def pre60_side_bp(self, direction: str, signal_ts: float, price: float) -> float | None:
        """Side-signed move (bp) from the last 3 m bar close available 60 min before the signal to ``price``."""
        sign = _SIGN.get(str(direction or "").upper())
        if not sign or not price:
            return None
        try:
            bar = self.engine.latest(float(signal_ts) - 3600.0)
            ref = float((bar or {}).get("c") or 0)
        except (TypeError, ValueError):
            return None
        if ref <= 0:
            return None
        return round(sign * (float(price) - ref) / ref * 1e4, 4)

    def regime_entry_action(self, *, order: Mapping[str, Any], decision: Mapping[str, Any] | None,
                            bid: float, ask: float, now: float, created_ts: float) -> dict[str, Any]:
        """HOLD / REPRICE / MARKET / DROP for one resting regime limit (state kept on ``order``)."""
        decision = decision or {}
        cell = decision.get("exec") or {}
        direction = str(decision.get("direction") or "").upper()
        if decision.get("action") != ACTION_MAKER or direction not in _SIGN or not cell:
            return {"action": "HOLD", "reason": "NOT_A_REGIME_LIMIT"}
        bid = float(bid or 0); ask = float(ask or 0)
        if bid <= 0 or ask <= bid:
            return {"action": "HOLD", "reason": "BBO_UNAVAILABLE"}
        age = float(now) - float(created_ts)
        tick = float(decision.get("tick") or price_tick((bid + ask) / 2.0))
        sign = _SIGN[direction]
        if age >= float(cell["ttl_sec"]):
            if cell["kind"] != "TOUCH":
                return {"action": "DROP", "reason": f"REGIME_{cell['kind']}_TTL_UNFILLED"}
            if order.get("regime_fallback_done"):
                return {"action": "HOLD", "reason": "FALLBACK_ALREADY_SENT"}
            mid0 = float(decision.get("mid") or 0)
            mid1 = (bid + ask) / 2.0
            drift = sign * (mid1 - mid0) / mid0 * 1e4 if mid0 > 0 else None
            atr_bp = stack.atr_or_default(decision.get("atr_bp"))
            if drift is None or drift > float(cell["fallback_favourable_atr_k"]) * atr_bp \
                    or drift < -float(cell["fallback_adverse_bp"]):
                return {"action": "DROP", "reason": "TOUCH_FALLBACK_SKIPPED_DRIFT", "drift_bp": drift}
            return {"action": "MARKET", "reason": "TOUCH_TAKER_FALLBACK", "drift_bp": round(drift, 4),
                    "limit_price": round(taker_limit(direction, bid, ask, self.entry["taker_protection_bps"], tick), 8)}
        ages = list(decision.get("reprice_ages") or ())
        due = sum(1 for x in ages if age >= x)
        done = int(order.get("regime_reprice_index") or 0)
        if due <= done:
            return {"action": "HOLD", "reason": "NOT_DUE"}
        current = float(order.get("limit_price") or decision.get("limit_price") or 0)
        touch = bid if sign > 0 else ask
        new = current + float(cell["gap_step"]) * (touch - current)
        new = min(new, touch) if sign > 0 else max(new, touch)
        new = passive_round(new, direction, tick)
        return {"action": "REPRICE" if abs(new - current) > 1e-9 else "STEP_NO_CHANGE",
                "reason": f"REGIME_{cell['kind']}_REPRICE_{due}", "limit_price": round(new, 8), "step_index": due}

    # ------------------------------------------------------------ exit
    def profile_for(self, decision: Mapping[str, Any] | None) -> tuple[str, dict]:
        name = (decision or {}).get("exit_profile")
        if name not in self.profiles:
            name = next(iter(self.profiles))
        return name, self.profiles[name]

    def exit_action(self, *, entry: float, direction: str, price: float, atr_abs: float = 0.0,
                    atr_pct: float = 0.0, age_sec: float = 0.0, leverage: float = 100.0,
                    remaining_fraction: float = 1.0, completed_partials=(), peak_price: float | None = None,
                    market_context: Mapping[str, Any] | None = None, policy_state: dict | None = None,
                    entry_decision: Mapping[str, Any] | None = None, fill_ts: float | None = None,
                    **_ignored) -> ExitAction | None:
        entry = float(entry or 0); price = float(price or 0)
        direction = str(direction or "").upper()
        sign = _SIGN.get(direction, 0)
        remaining = max(0.0, min(1.0, float(remaining_fraction or 0)))
        if entry <= 0 or price <= 0 or not sign or remaining <= 0:
            return None
        state = policy_state if policy_state is not None else {}
        if state.get("schema") != stack.STATE_SCHEMA:
            state.clear()
            state.update(stack.new_state())
        if "ladder_tp1" in tuple(completed_partials or ()):
            state["tp1_done"] = True
        name, profile = self.profile_for(entry_decision)
        atr_bp = (entry_decision or {}).get("atr_bp")
        state["profile"] = name
        flip_name = (entry_decision or {}).get("flip_indicator")
        ctx = market_context or {}
        bars = []
        if flip_name:
            for bar in ctx.get("bars") or ():
                bars.append({"available_ts": bar["available_ts"], "score": (bar.get("scores") or {}).get(flip_name, 0),
                             "prev_score": (bar.get("prev_scores") or {}).get(flip_name, 0)})
        cur = sign * (price - entry) / entry * 1e4
        hit = stack.evaluate_tick(
            profile, state, cur_bp=cur, age_sec=age_sec, atr_bp=atr_bp, shock=ctx, bars=bars,
            side_sign=sign, fill_ts=fill_ts,
        )
        peak = float(peak_price if peak_price is not None else price)
        if not hit:
            return None
        rule = hit["rule"]
        if rule == "HARD_STOP":
            reason = f"PHYSICAL_HARD_STOP_{float(profile['hard_bp']):g}PCT"
        elif rule == "TIME_BACKSTOP":
            reason = f"PATH_END_{int(profile['time_sec']) // 60}M"
        else:
            reason = f"GS_{rule}"
        book = entry * (1.0 + sign * float(hit["book_bp"]) / 1e4) if hit.get("maker") else price
        state["last_rule"] = rule
        if hit["partial"]:
            close = min(float(hit["close_fraction"]), remaining)
            return ExitAction(reason, close, book, None, remaining - close, peak, partial_key="ladder_tp1",
                              book_price=book, maker=True)
        return ExitAction(reason, remaining, book, None, 0.0, peak, book_price=book, maker=bool(hit.get("maker")))

    def exit_config(self, analyzer_sync_id):
        config = super().exit_config(analyzer_sync_id)
        config.update({"profiles": self.profiles, "regime_profiles": self.regime_profiles,
                       "exit_order": tuple(self.exit["exit_order"]), "family": self.exit["family"],
                       "partial_reduction_required": bool(self.exit.get("partial_take_profits"))})
        return config

    # ------------------------------------------------------------ dashboard
    def dashboard_policy(self):
        tile = COMBO_LANE_SPECS[self.lane]
        from tile_card_sections import regime_entry_lines, regime_exit_lines
        payload = _dash(self.spec, signal_detail=signal_source_detail(tile))
        entry, exit_policy = self.entry, self.exit
        chips = ["PAPER ONLY", evidence_badge(tile) or "GS pre-registered",
                 f"Pre-registration {tile['pre_registration']['hypothesis_id']}"]
        if entry.get("regime_classifier"):
            chips.extend(regime_entry_lines(entry)[:1])
        chips.extend(regime_exit_lines(exit_policy))
        max_open = int(tile.get("max_active_signals") or 1)
        chips.append(f"Max {max_open} open position" + ("s" if max_open > 1 else ""))
        payload["filter_chips"] = chips
        payload["entry"].update({
            "trigger": tile["signal_summary"],
            "entry_path": self.lane,
            "chase_detail": "; ".join(regime_entry_lines(entry)[1:]) if entry.get("regime_classifier")
            else "No chase; one signal-time marketable limit or stand-aside",
            "direction_source": entry["direction_source"],
            "signal_clock": entry.get("signal_clock") or ("SHARED_AI_CALL" + (" + BAR_CLOSE_3M_CVD_EVALUATOR"
                                                                              if entry.get("bar_clock_trigger") else "")),
            "cadence_label": ("No AI — per-second cross-venue evaluator" if entry.get("direction_source") == CROSS_VENUE_PREMIUM
                              else "No AI — 3-minute bar-close CVD evaluator" if entry.get("direction_source") == CVD_DIVERGENCE_3M
                              else "Shared 3-min AI call (TREND fade) + 3-minute bar-close CVD evaluator (QUIET/VIOLENT)"
                              if entry.get("bar_clock_trigger") else None),
        })
        payload["exit"].update({
            "profile": exit_policy["family"], "regime_profiles": exit_policy["regime_profiles"],
            "profiles": exit_policy["profiles"], "exit_order": exit_policy["exit_order"],
            "fixed_time_exit": f"{int(exit_policy['max_duration_sec']) // 60}m max",
            "hard_stop_bps": exit_policy["hard_stop_bps"], "stop_fill": exit_policy["stop_fill"],
            "take_profit_fill": exit_policy["take_profit_fill"], "max_open_positions": max_open,
        })
        return self._with_pre_registration(payload, tile)

    # ------------------------------------------------------------ evaluators
    def make_evaluator(self):
        source = self.entry["direction_source"]
        if source == CROSS_VENUE_PREMIUM:
            cls = PREMIUM_EVALUATOR_CLASSES[self.entry.get("evaluator_id_prefix") or GsPremiumEvaluator.ID_PREFIX]
            return cls(PremiumRule.from_policy(self.entry, self.exit),
                       policy_id=self.policy_id, policy_signature=self.policy_signature)
        if source == CVD_DIVERGENCE_3M or self.entry.get("bar_clock_trigger"):
            return CvdDivergenceEvaluator(policy_id=self.policy_id, policy_signature=self.policy_signature,
                                          engine=self.engine)
        raise ValueError(f"{self.lane}: no evaluator for {source}")


class GsPremiumEvaluator(PremiumEvaluator):
    """GS-01: H-C's premium rule in its own instance (no shadow file; H-C keeps xvp_shadow_signals.jsonl)."""

    ID_PREFIX = "gsxvp"
    SHADOW_FILE = None
    TRIGGER_FEATURE_KEY = "gsxvp_trigger"


class Gs5PremiumEvaluator(GsPremiumEvaluator):
    """GS-05: the same premium rule in a third instance; its own trigger-id namespace keeps its
    pre-entry receipts apart from GS-01's on the same second (cf. the B2 receipt collision)."""

    ID_PREFIX = "gs5xvp"
    TRIGGER_FEATURE_KEY = "gs5xvp_trigger"


PREMIUM_EVALUATOR_CLASSES = {cls.ID_PREFIX: cls for cls in (GsPremiumEvaluator, Gs5PremiumEvaluator)}


class CvdDivergenceEvaluator:
    """3 m CVD-divergence event trigger at each newly closed bar (GS-03 / B1 / B2)."""

    ID_PREFIX = "cvd"
    SHADOW_FILE = None
    # Every CVD trigger is a 3 m bar-close event, whichever tile consumes it
    # (GS-03 / B1 declare the clock on the tile; B2 is a shared-AI tile whose
    # QUIET/VIOLENT branch rides this clock via ``bar_clock_trigger``).
    SIGNAL_CLOCK = BAR_CLOSE_SIGNAL_CLOCK
    SIGNAL_KEY = "cvd_divergence_score"
    TRIGGER_FEATURE_KEY = "cvd_trigger"
    TRIGGER_FEATURE_FIELDS = ("trigger_id", "evaluated_ts", "side", "bar_close_ts", "cvd_divergence_score",
                              "bar_atr_bp", "bar_atr_pct_rank", "bar_adx", "bar_spread_bp", "bar_ok")
    MAX_BAR_AGE_SEC = 20.0

    def __init__(self, *, policy_id: str = "", policy_signature: str = "",
                 engine: bars3m.RegimeBars3m | None = None) -> None:
        self.engine = engine if engine is not None else bars3m.ENGINE
        self.policy_id, self.policy_signature = policy_id, policy_signature
        self._last_seq = None
        self.stats = {"bars_seen": 0, "events": 0, "stale_bars": 0, "last_bar_close_ts": None,
                      "last_trigger_id": None}

    def step(self, *, now: float, live=None, bfx_quotes=None, bfx_bbo_ts=None) -> tuple:
        bar = self.engine.latest(float(now))
        if bar is None:
            return {"status": "NO_BAR"}, None, []
        if self._last_seq is not None and bar["seq"] <= self._last_seq:
            return {"status": "NO_NEW_BAR"}, None, []
        first = self._last_seq is None
        self._last_seq = bar["seq"]
        self.stats["bars_seen"] += 1
        self.stats["last_bar_close_ts"] = bar["close_ts"]
        if first or float(now) - float(bar["available_ts"]) > self.MAX_BAR_AGE_SEC:
            self.stats["stale_bars"] += 1
            return {"status": "BAR_NOT_FRESH"}, None, []
        score = int((bar.get("events") or {}).get(bars3m.DIVERGENCE) or 0)
        if not score:
            return {"status": "NO_EVENT"}, None, []
        side = "LONG" if score > 0 else "SHORT"
        trigger_id = f"cvd-{int(bar['close_ts'])}-{side.lower()}"
        self.stats["events"] += 1
        self.stats["last_trigger_id"] = trigger_id
        row = {
            "trigger_id": trigger_id, "evaluated_ts": round(float(now), 3), "side": side,
            "bar_close_ts": bar["close_ts"], "cvd_divergence_score": score, "bar_atr_bp": bar.get("atr_bp"),
            "bar_atr_pct_rank": bar.get("atr_pct_rank"), "bar_adx": bar.get("adx"),
            "bar_spread_bp": bar.get("spread_bp"), "bar_ok": bar.get("bar_ok"),
            "policy_id": self.policy_id, "policy_signature": self.policy_signature,
        }
        return {"status": STATUS_TRIGGER, "trigger_id": trigger_id, "side": side}, row, []

    def snapshot(self) -> dict:
        return {"schema": "cvd_divergence_evaluator_v1", **dict(self.stats), "engine": self.engine.snapshot()}

