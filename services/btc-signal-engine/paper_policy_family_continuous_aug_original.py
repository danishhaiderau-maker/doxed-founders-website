"""Continuous (Aug-2026 original) — exact replica of the early-August 2026 Continuous tile (paper only).

Reference: Fly bot v15-typeb-opportunity-v2 at d018ef31 (archive
C:/Danish HD/Final-Bot-Local-Archive-2026-08-12), before the NO_TRADE prompt
(636fa9ca4, f0620b33b), the label-only demotion (#136) and the retirement
(#233).  Every rule below is ported from that revision; the deliberate
differences are listed in ``AUG_DIFFERENCES`` and surface on the card.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Mapping

from combo_pathway_config import COMBO_LANE_SPECS
from family_policy_common import (
    ExitAction,
    PolicySpec,
    dashboard_policy as _dashboard,
    entry_fields as _entry,
    marketable_quote_at_limit,
)

LANE = "FAMILY_CONTINUOUS_AUG_ORIGINAL"
_TILE = COMBO_LANE_SPECS[LANE]
POLICY_ID = _TILE["raw_policy_id"]
POLICY_SIGNATURE = _TILE["policy_signature"]
ENTRY = _TILE["entry_policy"]
EXIT = _TILE["exit_policy"]
LADDER = tuple(tuple(row) for row in _TILE["ladder"])

# The tile makes its own DeepSeek call after every shared three-minute call;
# it never reads the shared call's verdict.
OWN_AI_CALL = True
ADAPTIVE_ENTRY = False
EXIT_CONTEXT = True
CHASE_STEP = float(ENTRY["remaining_gap_step_pct"]) / 100.0

SPEC = PolicySpec(
    policy_id=POLICY_ID, lane=LANE, label=_TILE["label"], family=EXIT["family"],
    entry_offset_pct=float(ENTRY["offset_pct"]),
    chase_windows=tuple(ENTRY["chase_windows"]),
    chase_interval_sec=int(ENTRY["reprice_sec"]),
    chase_step=CHASE_STEP,
    entry_ttl_sec=int(_TILE["entry_ttl_sec"]),
    max_duration_sec=int(EXIT["max_duration_sec"]),
    hard_stop_margin_pct=float(EXIT["hard_stop_margin_pct"]),
    thesis_cut_margin_pct=float(EXIT["thesis_cut_margin_pct"]),
    trail_ladder=LADDER,
    ladder_label=_TILE["ladder_label"],
    ladder_profile_id=_TILE["ladder_profile_id"],
    margin_cap_usd=float(_TILE["requested_margin_usd"]),
)

# ---------------------------------------------------------------------------
# AI call: the August v3 prompt, verbatim.
# ---------------------------------------------------------------------------
PROMPT_ID = "shared_direction_adx_evidence_v3_20260721"
AUG_REQUESTED_MODEL = "deepseek-v4-flash"
AI_TEMPERATURE = 0.0
AI_PURPOSE = "trading_direction_continuous_aug"

AI_PROMPT_TEMPLATE = """
You are a direction classifier for short-duration BTC perpetual research.
Choose exactly one candidate side: LONG or SHORT. Never return NO_TRADE.
Do not estimate win probability, confidence, entries, exits, targets, or order prices.

Given the following market data:

{context}

Rank the evidence in this order:
1. multi-timeframe agreement and market structure;
2. trend health, ADX, and EMA alignment;
3. order flow: delta, imbalance, volume ratio, and velocity;
4. micro structure and support/resistance as timing context only.

Do not anchor on the regime label - derive direction from structure, order flow,
and EMA alignment in that order. A bullish EMA200 regime does NOT pre-qualify a
LONG; you must confirm with structure and order flow.

ADX measures trend strength, not direction. Apply it non-monotonically:
- ADX 25-30 is an empirically weak/ambiguous band. Narrow the directional score
  gap unless multi-timeframe structure, EMA alignment, and order flow all agree.
- ADX 30-35 is usable but can be late-cycle; require structure/order-flow support.
- ADX 40+ confirms strength but still cannot choose LONG versus SHORT by itself.
- ADX below 25 is not an automatic direction rejection; use the supplied
  structure and order flow to decide the higher-scoring candidate.

Score LONG and SHORT independently from 0 to 100. The direction must match the
higher score. Support alone is not a LONG reason and resistance alone is not a
SHORT reason. Strong counter-trend candidates require confirmed structure shift
and order-flow expansion. Use only supplied facts.

Return exactly one JSON object:
{{
  "direction": "LONG or SHORT",
  "long_score": 0,
  "short_score": 0,
  "reason": "One short sentence naming the decisive evidence"
}}
"""
RESEARCH_AI_PROMPT_ADDENDUM = """

RESEARCH DATA COLLECTION MODE (active):
- This is the one shared call made on the three-minute AI_SCAN cadence.
- CONTINUOUS and TYPE_B_HUNTER_V1 independently accept or reject the candidate afterward.
- Do not decide either tile's verdict and do not return any field beyond direction,
  long_score, short_score, and one short reason.
"""
PROMPT_SHA256 = hashlib.sha256(
    (AI_PROMPT_TEMPLATE + RESEARCH_AI_PROMPT_ADDENDUM).encode("utf-8")
).hexdigest()

# Key tree of the context August sent (ai_input_log.jsonl, 507 calls,
# 2026-08-09..11).  ``None`` is a leaf; a dict recurses; ``{}`` is a leaf
# whose whole value is passed through.
AUG_CONTEXT_SCHEMA: dict[str, Any] = {
    "ai_input_upgrade": {
        "bear_score_change_15m": None, "bull_score_change_15m": None,
        "distance_to_micro_resistance": None, "distance_to_micro_support": None,
        "edge_research_telemetry_only": None, "entry_stage": None,
        "entry_timing": {
            "distance_from_ema_pct": None, "distance_from_last_impulse_pct": None,
            "distance_from_last_pullback_pct": None, "distance_from_micro_sr_pct": None,
            "ret_5m_abs": None, "structure_extension": None,
        },
        "higher_low_detected": None, "historically_profitable_patterns": {},
        "liquidity_sweep_high": None, "liquidity_sweep_low": None,
        "lower_high_detected": None, "market_structure_shift": None,
        "micro_resistance": None,
        "micro_structure": {
            "micro_resistance": None, "micro_support": None, "near_micro_sr": None,
            "rejects_micro_sr": None, "structure_bias": None,
        },
        "micro_structure_confirmed": None, "micro_support": None,
        "mtf_informational_only": None, "pivot_count": None,
        "quality_score_components": {
            "edge_component": None, "micro_sr_component": None, "quality_score": None,
            "spread_component": None, "structure_component": None, "trend_component": None,
        },
        "regime_change_count_60m": None, "reversal_probability": None,
        "reversal_risk_note": None, "reversal_risk_score": None,
        "trend_health_detail": {"bear_score": None, "bull_score": None, "interpretation": None},
        "trend_health_state": None, "weaken_signals": None,
    },
    "avg_volume": None, "bear_score_change_15m": None, "body_ratio": None,
    "bull_score_change_15m": None, "candle_range": None, "data_quality": None,
    "delta": None, "delta_change": None, "dist_to_resistance": None,
    "dist_to_support": None, "distance_to_micro_resistance": None,
    "distance_to_micro_support": None, "edge_research_telemetry_only": None,
    "edge_score": None, "edge_threshold": None, "ema200": None, "ema21": None,
    "ema9": None, "ema_slope": None, "entry_stage": None,
    "funding": {
        "clamp_max": None, "clamp_min": None, "current_funding": None,
        "favors_long_when_negative": None, "favors_short_when_positive": None,
        "index_price": None, "interpretation": None, "interval_hours": None,
        "longs_pay": None, "mark_price": None, "next_funding_accrued": None,
        "next_funding_step": None, "next_time": None, "next_time_iso": None,
        "open_interest": None, "rate": None, "rate_pct_per_8h": None,
        "shorts_receive_when_positive": None, "source": None, "symbol": None,
        "updated_ts": None,
    },
    "higher_low_detected": None, "historically_profitable_patterns": {},
    "imbalance": None, "liquidity_sweep_high": None, "liquidity_sweep_low": None,
    "lower_high_detected": None,
    "market_context": {
        "ema_alignment": {
            "ema21_above_ema200": None, "ema21_slope_pct": None, "ema9_above_ema21": None,
            "ema9_slope_pct": None, "ema_spread_pct": None, "price_vs_ema200_pct": None,
            "price_vs_ema21_pct": None, "price_vs_ema9_pct": None,
            "stack_bear": None, "stack_bull": None,
        },
        "market_structure": {
            "hh_hl_sequence_active": None, "last_swing_high": None, "last_swing_low": None,
            "lh_ll_sequence_active": None, "pivot_count": None, "structure_bias": None,
            "structure_score": None, "swing_labels_last": None,
        },
        "multi_tf": {
            "agreement": None, "bear_tf_count": None, "bull_tf_count": None,
            "interpretation_note": None, "trends": {"15m": None, "1h": None, "4h": None},
        },
        "regime_label": None,
        "sr_context": {
            "dist_to_resistance_pct": None, "dist_to_support_pct": None,
            "role": None, "sr_state": None,
        },
        "trend_strength": {
            "adx": None, "mean_reversion_risk": None, "trend_score": None,
            "trending_market_adx_25_plus": None, "vwap_distance_pct": None,
        },
        "updated_ts": None,
    },
    "market_structure_shift": None, "micro_resistance": None,
    "micro_structure_confirmed": None, "micro_support": None,
    "mtf_informational_only": None, "pivot_count": None, "price": None,
    "quality_score_components": {
        "edge_component": None, "micro_sr_component": None, "quality_score": None,
        "spread_component": None, "structure_component": None, "trend_component": None,
    },
    "recent_high": None, "recent_low": None, "regime": None,
    "regime_change_count_60m": None, "ret_1m": None, "ret_5m": None,
    "reversal_probability": None, "reversal_risk_score": None, "sr_bias": None,
    "sr_state": None, "trade_id": None,
    "trend_health": {
        "base_state": None, "bear_score": None, "bull_score": None, "delta": None,
        "mtf_agreement": None, "structure_score": None, "trend_health": None,
        "trend_state": None, "ts": None, "velocity": None, "volume_ratio": None,
        "weaken_signals": None,
    },
    "trend_health_state": None, "velocity": None, "volume": None,
    "volume_ratio": None, "weaken_signals": None, "wick_ratio": None,
}
# These inputs were 0 on every August call (orderflow defects since fixed);
# the replica receives the populated values and flags them on every record.
AUG_ZERO_INPUTS = ("ret_1m", "ret_5m", "delta_change", "ai_input_upgrade.entry_timing.ret_5m_abs")

AUG_DIFFERENCES = (
    "Model: deepseek-v4-flash is a retired alias of deepseek-flash, the platform default; requested and served model are recorded per call",
    "Inputs: ret_1m, ret_5m and delta_change were 0 in August and are now populated (flagged per call)",
    "Fills: primary ledger uses the platform's realistic BBO/depth paper fill; the August touch fill is a labelled shadow",
    "Size: $0.25 paper margin at 100x (August $20 margin at 100x); same flat size every trade",
    "Capacity: tile cap of 10 active signals with the August $15 / 0.25% same-side duplicate rule (August pool was 20)",
    "Exit pricing: ladder and thesis exits book the side-correct trigger tick (August walked the book)",
)


def _project(value: Any, schema: Mapping[str, Any], path: str,
             dropped: list[str], missing: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, item in value.items():
        child = f"{path}{key}"
        if key not in schema:
            dropped.append(child)
            continue
        sub = schema[key]
        if isinstance(sub, dict) and sub and isinstance(item, dict):
            out[key] = _project(item, sub, child + ".", dropped, missing)
        else:
            out[key] = copy.deepcopy(item)
    for key, sub in schema.items():
        if key not in value:
            missing.append(f"{path}{key}")
    return out


def _lookup(ctx: Mapping[str, Any], dotted: str) -> Any:
    node: Any = ctx
    for part in dotted.split("."):
        if not isinstance(node, Mapping):
            return None
        node = node.get(part)
    return node


def project_context(ctx: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project today's AI context onto the August key schema, in context order."""
    dropped: list[str] = []
    missing: list[str] = []
    projected = _project(dict(ctx or {}), AUG_CONTEXT_SCHEMA, "", dropped, missing)
    populated = {}
    for key in AUG_ZERO_INPUTS:
        try:
            populated[key] = abs(float(_lookup(projected, key) or 0.0)) > 0.0
        except (TypeError, ValueError):
            populated[key] = False
    receipt = {
        "schema": "aug_input_projection_v1",
        "dropped_keys": sorted(dropped),
        "missing_keys": sorted(missing),
        "aug_zero_inputs_now_populated": populated,
    }
    return projected, receipt


def render_messages(ctx: Mapping[str, Any] | None) -> tuple[list[dict[str, str]], dict[str, Any]]:
    projected, receipt = project_context(ctx)
    prompt = AI_PROMPT_TEMPLATE.format(context=json.dumps(projected, indent=2, default=str))
    prompt += RESEARCH_AI_PROMPT_ADDENDUM
    receipt["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return [{"role": "user", "content": prompt}], receipt


# ---------------------------------------------------------------------------
# Response parsing and decision chain (August research mode).
# ---------------------------------------------------------------------------
EXECUTE_TIERS = frozenset({"STRONG_APPROVE", "APPROVE", "SOFT_APPROVE"})
MIN_SCORE_GAP = int(ENTRY["min_score_gap"])
MIN_SCORE_SUM = int(ENTRY["min_score_sum"])
R2_SPREAD_FLOOR = int(ENTRY["r2_spread_floor"])


def _json_blob(text: str) -> dict[str, Any]:
    if not text:
        return {}
    start = text.find("```json")
    if start >= 0:
        brace = text.find("{", start)
        end = text.find("```", brace + 1)
        chunk = text[brace:end if end > brace else len(text)]
        try:
            blob = json.loads(chunk.strip().rstrip("`"))
            return blob if isinstance(blob, dict) else {}
        except Exception:
            pass
    brace = text.find("{")
    while brace >= 0:
        try:
            candidate = json.loads(text[brace:text.find("}", brace) + 1])
            if isinstance(candidate, dict) and (
                "long_score" in candidate or "short_score" in candidate
                or "bull_score" in candidate or "reasons_for_trade" in candidate
                or "confidence" in candidate
            ):
                return candidate
        except Exception:
            pass
        brace = text.find("{", brace + 1)
    return {}


def _factors(text: str) -> dict[str, Any]:
    factors: dict[str, Any] = {
        "bull_score": 0, "bear_score": 0, "long_score": 0, "short_score": 0,
        "preferred_direction": None, "factor_parse_ok": False, "zero_score_reject": False,
    }
    blob = _json_blob(text)
    if blob:
        for key in ("bull_score", "bear_score", "long_score", "short_score"):
            try:
                factors[key] = int(blob.get(key, 0) or 0)
            except (TypeError, ValueError):
                factors[key] = 0
        if factors["long_score"] <= 0 and factors["bull_score"] > 0:
            factors["long_score"] = factors["bull_score"] * 10
        if factors["short_score"] <= 0 and factors["bear_score"] > 0:
            factors["short_score"] = factors["bear_score"] * 10
        pref = blob.get("preferred_direction") or blob.get("direction")
        if pref:
            factors["preferred_direction"] = str(pref).upper()
        factors["factor_parse_ok"] = True
    else:
        for key, label in (("long_score", "Long"), ("short_score", "Short")):
            match = re.search(rf"{label}\s*score:\s*(\d+)", text or "", re.IGNORECASE)
            if match:
                factors[key] = int(match.group(1))
        factors["factor_parse_ok"] = factors["long_score"] > 0 or factors["short_score"] > 0
    if factors["factor_parse_ok"] and factors["long_score"] + factors["short_score"] < MIN_SCORE_SUM:
        factors["zero_score_reject"] = True
    return factors


def tier_for(long_score: int, short_score: int, direction: str) -> str:
    direction = str(direction or "").upper()
    if direction not in ("LONG", "SHORT"):
        return "REJECT"
    gap = long_score - short_score if direction == "LONG" else short_score - long_score
    if gap < MIN_SCORE_GAP:
        return "REJECT"
    if gap >= 15:
        return "STRONG_APPROVE"
    if gap >= 10:
        return "APPROVE"
    return "SOFT_APPROVE"


def parse_response(text: str) -> dict[str, Any]:
    """Parse the tile's own call; the side is always the higher score."""
    text = str(text or "")
    blob = _json_blob(text)
    factors = _factors(text)
    match = re.search(r"Direction:\s*(LONG|SHORT|NO_TRADE)", text, re.IGNORECASE)
    raw = match.group(1).upper() if match else None
    if raw not in ("LONG", "SHORT"):
        candidate = blob.get("direction") or blob.get("preferred_direction") or factors.get("preferred_direction")
        if candidate:
            raw = str(candidate).upper()
    if raw not in ("LONG", "SHORT", "NO_TRADE"):
        raw = "NO_TRADE"
    long_score, short_score = factors["long_score"], factors["short_score"]
    if factors["zero_score_reject"]:
        direction, tier = "NO_TRADE", "REJECT"
    else:
        if long_score > short_score:
            direction = "LONG"
        elif short_score > long_score:
            direction = "SHORT"
        else:
            direction = raw if raw in ("LONG", "SHORT") else "LONG"
        tier = tier_for(long_score, short_score, direction)
    win_match = re.search(r"Win probability:\s*(\d+)", text)
    win_prob = int(win_match.group(1)) if win_match else 0
    if win_prob <= 0:
        conf_json = re.search(r'"confidence"\s*:\s*(\d+)', text)
        conf_line = re.search(r"Confidence:\s*(\d+)", text, re.IGNORECASE)
        fallback = int(conf_json.group(1)) if conf_json else (int(conf_line.group(1)) if conf_line else 0)
        if 0 < fallback <= 100:
            win_prob = fallback
    return {
        "direction": direction,
        "raw_direction": raw,
        "tier": tier,
        "win_prob": win_prob,
        "long_score": long_score,
        "short_score": short_score,
        "factors": factors,
        "reason": "AI_RETURNED_ZERO_SCORES" if factors["zero_score_reject"] else str(blob.get("reason") or ""),
        "parse_ok": bool(factors["factor_parse_ok"]),
    }


def _trend_hierarchy_gate(ctx: Mapping[str, Any], direction: str,
                          long_score: int, short_score: int) -> str | None:
    """August downgrade of weak counter-trend calls (ported as written).

    August read ``ema_alignment.stack`` although its context carried only
    ``stack_bull``/``stack_bear``; the replica keeps that read, so the gate
    fires exactly as often as it did then.
    """
    mc = ctx.get("market_context") or {}
    mtf = mc.get("multi_tf") or {}
    ms = mc.get("market_structure") or {}
    ts = mc.get("trend_strength") or {}
    adx = float(ts.get("adx") or ctx.get("adx") or 0)
    agree = str(mtf.get("agreement") or "").upper()
    bias = str(ms.get("bias") or ms.get("structure_bias") or "").upper()
    score = float(ms.get("structure_score") or 0)
    stack = str((mc.get("ema_alignment") or {}).get("stack") or "").upper()
    bear = agree == "BEAR_ALIGNED" and ("BEAR" in bias or score <= -2) and "BEAR" in stack and adx >= 25
    bull = agree == "BULL_ALIGNED" and ("BULL" in bias or score >= 2) and "BULL" in stack and adx >= 25
    shift = str(ctx.get("market_structure_shift") or "").upper()
    sr_bias = str(ctx.get("sr_bias") or "").upper()
    if direction == "LONG" and bear:
        weak = bool(ctx.get("higher_low_detected")) and "BULL" not in shift and "REVERSAL" not in shift
        if long_score < short_score + 10 or weak or (sr_bias == "SHORT_PREFERRED" and long_score < short_score + 5):
            return f"TREND_HIERARCHY_COUNTER_TREND_LONG long={long_score} short={short_score} adx={adx:.0f}"
    if direction == "SHORT" and bull:
        weak = bool(ctx.get("lower_high_detected")) and "BEAR" not in shift and "REVERSAL" not in shift
        if short_score < long_score + 10 or weak or (sr_bias == "LONG_PREFERRED" and short_score < long_score + 5):
            return f"TREND_HIERARCHY_COUNTER_TREND_SHORT long={long_score} short={short_score} adx={adx:.0f}"
    return None


def _structure_agreement_gate(ctx: Mapping[str, Any], direction: str) -> str | None:
    upgrade = ctx.get("ai_input_upgrade") or {}
    shift = str(upgrade.get("market_structure_shift") or ctx.get("market_structure_shift") or "").upper()
    score = float(((ctx.get("market_context") or {}).get("market_structure") or {}).get("structure_score") or 0)
    if direction == "LONG" and ("BEAR" in shift or score <= -2):
        return f"AI_LONG_VS_BEAR_STRUCTURE shift={shift} structure_score={score}"
    if direction == "SHORT" and ("BULL" in shift or score >= 2):
        return f"AI_SHORT_VS_BULL_STRUCTURE shift={shift} structure_score={score}"
    return None


def decide(ctx: Mapping[str, Any] | None, parsed: Mapping[str, Any]) -> dict[str, Any]:
    """August chain: tier -> trend hierarchy -> normalise -> structure -> R2 floor."""
    ctx = ctx or {}
    direction = str(parsed.get("direction") or "NO_TRADE").upper()
    long_score = int(parsed.get("long_score") or 0)
    short_score = int(parsed.get("short_score") or 0)
    tier = str(parsed.get("tier") or "REJECT").upper()
    reason = None
    if not parsed.get("parse_ok"):
        tier, reason = "REJECT", "AI_PARSE_FAILED"
    elif tier not in EXECUTE_TIERS:
        reason = parsed.get("reason") or f"AI_{tier}"
        if parsed.get("factors", {}).get("zero_score_reject"):
            reason = "AI_RETURNED_ZERO_SCORES"
        elif tier == "REJECT":
            reason = f"SCORE_GAP_BELOW_{MIN_SCORE_GAP}"
    else:
        reason = _trend_hierarchy_gate(ctx, direction, long_score, short_score)
        if reason:
            tier = "SOFT_REJECT"
        else:
            reason = _structure_agreement_gate(ctx, direction)
            if reason:
                tier = "REJECT"
            elif abs(long_score - short_score) < R2_SPREAD_FLOOR:
                tier, reason = "REJECT", f"R2_SPREAD_FLOOR_BLOCKED spread={abs(long_score - short_score)}<{R2_SPREAD_FLOOR}"
    accepted = tier in EXECUTE_TIERS
    return {
        "accepted": accepted,
        "direction": direction if accepted else "NO_TRADE",
        "candidate_direction": direction,
        "tier": tier,
        "reason": "AUG_EXECUTE_TIER_" + tier if accepted else str(reason),
        "long_score": long_score,
        "short_score": short_score,
    }


# ---------------------------------------------------------------------------
# Entry, chase and duplicate exposure.
# ---------------------------------------------------------------------------
def entry_fields(direction: str, reference_price: float) -> dict[str, Any]:
    fields = _entry(SPEC, direction, reference_price)
    fields.update({
        "chase_start_sec": int(ENTRY["chase_start_sec"]),
        "chase_end_sec": int(ENTRY["chase_max_age_sec"]),
        "aug_original_replica": True,
        "fill_model": ENTRY["fill_model"],
        "shadow_fill_model": ENTRY["shadow_fill_model"],
    })
    return fields


def chase_due(*, created_ts: float, last_chase_ts: float, now: float) -> bool:
    """August: reprice from creation every 60 s while the order is under 10 min old."""
    age = float(now) - float(created_ts or 0)
    return (
        float(ENTRY["chase_start_sec"]) <= age < float(ENTRY["chase_max_age_sec"])
        and float(now) - float(last_chase_ts or created_ts or 0) >= float(ENTRY["reprice_sec"])
    )


def chase_permitted(*, direction: str, limit_price: float, original_limit: float,
                    market_price: float) -> tuple[bool, str]:
    """August pre-checks the shared chase target computation does not make."""
    limit_price = float(limit_price or 0)
    market_price = float(market_price or 0)
    original_limit = float(original_limit or limit_price)
    if limit_price <= 0 or market_price <= 0:
        return False, "NO_PRICE"
    sign = 1 if str(direction).upper() == "LONG" else -1
    gap = (market_price - limit_price) * sign
    original_gap = (market_price - original_limit) * sign
    if gap <= float(ENTRY["near_fill_usd"]) or gap / market_price <= float(ENTRY["near_fill_pct"]) / 100.0:
        return False, "NEAR_FILL"
    if original_gap <= float(ENTRY["min_original_gap_usd"]):
        return False, "ORIGINAL_GAP_TOO_SMALL"
    return True, "OK"


def duplicate_exposure(direction: str, limit_price: float,
                       rows: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    """August same-side duplicate rule: within $15 or 0.25% of a resting limit or open entry."""
    direction = str(direction or "").upper()
    limit_price = float(limit_price or 0)
    if direction not in ("LONG", "SHORT") or limit_price <= 0:
        return None
    tol_usd = float(ENTRY["duplicate_tolerance_usd"])
    tol_pct = float(ENTRY["duplicate_tolerance_pct"]) / 100.0
    for row in rows:
        if str(row.get("direction") or "").upper() != direction:
            continue
        try:
            ref = float(row.get("reference_price") or 0)
        except (TypeError, ValueError):
            continue
        if ref <= 0:
            continue
        if abs(limit_price - ref) <= tol_usd or abs(limit_price - ref) / max(limit_price, ref, 1.0) < tol_pct:
            return {"trade_id": row.get("trade_id"), "source": row.get("source"), "reference_price": ref}
    return None


def account_risk_quantity(*, equity_usd: float, entry_price: float, atr_abs: float,
                          leverage: float = 100.0) -> dict[str, Any]:
    """August flat margin on every trade (no ATR or account-risk scaling)."""
    leverage = max(float(leverage or 0), 1.0)
    price = float(entry_price or 0)
    quantity = SPEC.margin_cap_usd * leverage / price if price > 0 else 0.0
    return {
        "quantity": quantity,
        "risk_budget_usd": SPEC.margin_cap_usd,
        "margin_cap_usd": SPEC.margin_cap_usd,
        "capped_by": "FLAT_MARGIN",
    }


def touch_shadow(order: Mapping[str, Any], *, price: float, bid: float, ask: float) -> bool:
    """August SIM_LIMIT touch: best quote, extreme since order, or last price at/through the limit."""
    limit = float(order.get("limit_price") or 0)
    if limit <= 0:
        return False
    side = str(order.get("side") or "").lower()
    if side == "buy":
        low = float(order.get("min_price_since_order", price) or price)
        return (float(ask or 0) > 0 and float(ask) <= limit) or low <= limit or float(price) <= limit
    if side == "sell":
        high = float(order.get("max_price_since_order", price) or price)
        return (float(bid or 0) > 0 and float(bid) >= limit) or high >= limit or float(price) >= limit
    return False


# ---------------------------------------------------------------------------
# Exits, in August order.
# ---------------------------------------------------------------------------
def _live_factor_scores(mc: Mapping[str, Any]) -> tuple[int, int]:
    ms = mc.get("market_structure") or {}
    ts = mc.get("trend_strength") or {}
    mtf = mc.get("multi_tf") or {}
    ema = mc.get("ema_alignment") or {}
    struct = float(ms.get("structure_score") or 0)
    bull = bear = 3
    if struct > 0:
        bull += min(4, int(struct))
    elif struct < 0:
        bear += min(4, int(-struct))
    if float(ts.get("adx") or 0) >= 25:
        if struct > 0:
            bull += 1
        elif struct < 0:
            bear += 1
    agree = mtf.get("agreement", "")
    if agree == "BULL_ALIGNED":
        bull += 2
    elif agree == "BEAR_ALIGNED":
        bear += 2
    vwap = float(ts.get("vwap_distance_pct") or 0)
    if vwap > 0:
        bull += 1
    elif vwap < 0:
        bear += 1
    slope = float(ema.get("ema_fast_slope_pct") or 0)
    if slope > 0:
        bull += 1
    elif slope < 0:
        bear += 1
    return bull, bear


def profit_lock_floor(peak_pct: float, *, conviction_spread: int = 0, direction: str = "",
                      trend_health: Mapping[str, Any] | None = None) -> float | None:
    """August _effective_profit_lock_floor: Scenario C rung, 40/10 floor, spread tighten."""
    peak = float(peak_pct)
    min_peak = float(EXIT["peak_never_loser_min_peak"])
    if peak < LADDER[0][0] and peak < min_peak:
        return None
    floor = None
    if peak >= LADDER[0][0]:
        for trigger, lock in LADDER:
            if peak >= trigger:
                floor = float(lock)
    if peak >= min_peak:
        floor = max(floor or 0.0, float(EXIT["peak_never_loser_floor"]))
    if floor is None:
        return None
    health = trend_health or {}
    spread = int(conviction_spread or 0)
    penalty = False
    if spread >= int(EXIT["spread_penalty_threshold"]):
        struct = float(health.get("structure_score") or 0)
        state = str(health.get("trend_state") or health.get("trend_health") or "")
        penalty = (
            state.endswith("_WEAKENING")
            or (direction == "SHORT" and struct <= -4)
            or (direction == "LONG" and struct >= 4)
            or spread >= 6
        )
    if penalty:
        floor = min(peak - 0.5, floor + float(EXIT["spread_penalty_lock_tighten_pct"]))
    return floor


def _price_at_margin(entry: float, sign: int, margin_pct: float, leverage: float) -> float:
    return entry * (1.0 + sign * margin_pct / (leverage * 100.0))


def exit_action(*, entry: float, direction: str, price: float, atr_abs: float = 0.0,
                atr_pct: float = 0.0, age_sec: float = 0.0, leverage: float = 100.0,
                remaining_fraction: float = 1.0, completed_partials=(),
                peak_price: float | None = None,
                exit_context: Mapping[str, Any] | None = None) -> ExitAction | None:
    entry = float(entry or 0)
    price = float(price or 0)
    direction = str(direction or "").upper()
    sign = 1 if direction == "LONG" else -1 if direction == "SHORT" else 0
    remaining = max(0.0, min(1.0, float(remaining_fraction or 0)))
    leverage = max(float(leverage or 0), 1.0)
    if entry <= 0 or price <= 0 or not sign or remaining <= 0:
        return None
    previous = float(peak_price if peak_price is not None else entry)
    peak = max(previous, price) if sign > 0 else min(previous, price)
    ctx = exit_context or {}
    unreal = sign * (price - entry) / entry * leverage * 100.0
    peak_pct = max(float(ctx.get("peak_pct") or 0.0), sign * (peak - entry) / entry * leverage * 100.0, unreal)
    stop_price = _price_at_margin(entry, sign, -float(EXIT["hard_stop_margin_pct"]), leverage)

    def close(reason: str, trigger: float) -> ExitAction:
        return ExitAction(reason, remaining, trigger, stop_price, 0.0, peak)

    if (
        ctx.get("early_fail_enabled", True)
        and not ctx.get("in_post_fill_grace")
        and unreal <= float(EXIT["early_fail_margin_pct"])
    ):
        return close("EARLY_FAIL", price)
    if (sign > 0 and price <= stop_price) or (sign < 0 and price >= stop_price):
        return close("STOP_LOSS", price)
    floor = profit_lock_floor(
        peak_pct, conviction_spread=int(ctx.get("conviction_spread") or 0),
        direction=direction, trend_health=ctx.get("trend_health"),
    )
    if floor is not None and peak_pct >= LADDER[0][0] and unreal <= floor:
        return close("PROFIT_LOCK_LADDER", price)
    thesis = ctx.get("entry_thesis") or {}
    if thesis and not (floor is not None and unreal > floor) \
            and unreal <= float(EXIT["thesis_exit_if_above_pct"]) and peak_pct < LADDER[0][0]:
        if unreal <= float(EXIT["thesis_cut_margin_pct"]):
            if peak_pct < float(EXIT["thesis_mfe_protect_pct"]):
                return close("THESIS_FAST_CUT", price)
        elif float(age_sec or 0) >= float(EXIT["thesis_min_age_sec"]):
            mc = ctx.get("market_context") or {}
            if _thesis_flipped(direction, thesis, mc):
                return close("THESIS_INVALIDATED", price)
    if float(age_sec or 0) > float(EXIT["max_duration_sec"]):
        return close("TIME_EXIT", price)
    return None


def _thesis_flipped(direction: str, thesis: Mapping[str, Any], mc: Mapping[str, Any]) -> bool:
    cur_bull, cur_bear = _live_factor_scores(mc)
    entry_bull = int(thesis.get("bull_score") or 0)
    entry_bear = int(thesis.get("bear_score") or 0)
    entry_mtf = thesis.get("mtf_agreement")
    cur_mtf = (mc.get("multi_tf") or {}).get("agreement")
    entry_struct = thesis.get("structure_score")
    cur_struct = (mc.get("market_structure") or {}).get("structure_score")
    m = int(EXIT["thesis_flip_margin"])
    decay = int(EXIT["thesis_decay_delta"])
    mixed = entry_mtf in ("MIXED", None, "")
    if direction == "LONG":
        flip = (
            cur_bear >= cur_bull + m
            or (cur_bull <= entry_bull - decay and cur_bear >= entry_bear + decay)
            or (cur_bull <= entry_bull - m and cur_bear >= entry_bear + m)
        )
        if not mixed and entry_mtf == "BULL_ALIGNED" and cur_mtf in ("BEAR_ALIGNED", "CONFLICTED"):
            flip = True
        if entry_struct is not None and cur_struct is not None and cur_struct <= -2 and entry_struct >= 2:
            flip = True
        return flip
    flip = (
        cur_bull >= cur_bear + m
        or (cur_bear <= entry_bear - decay and cur_bull >= entry_bull + decay)
        or (cur_bear <= entry_bear - m and cur_bull >= entry_bull + m)
    )
    if not mixed and entry_mtf == "BEAR_ALIGNED" and cur_mtf in ("BULL_ALIGNED", "CONFLICTED"):
        flip = True
    if entry_struct is not None and cur_struct is not None and cur_struct >= 2 and entry_struct <= -2:
        flip = True
    return flip


def exit_config(analyzer_sync_id: str) -> dict[str, Any]:
    return {
        "policy_snapshot_schema": "exit_policy_v1",
        "policy_source": "btc-conservative-agent",
        "policy_version": POLICY_ID,
        "raw_policy_id": POLICY_ID,
        "analyzer_sync_id": analyzer_sync_id,
        "exit_profile_id": POLICY_ID.split("|", 1)[-1],
        "family": SPEC.family,
        "hard_stop_margin_pct": SPEC.hard_stop_margin_pct,
        "thesis_cut_margin_pct": SPEC.thesis_cut_margin_pct,
        "trail_ladder": [list(row) for row in LADDER],
        "ladder_first_trigger_pct": LADDER[0][0],
        "ladder_first_lock_pct": LADDER[0][1],
        "ladder_label": SPEC.ladder_label,
        "ladder_profile_id": SPEC.ladder_profile_id,
        "peak_never_loser_min_peak": float(EXIT["peak_never_loser_min_peak"]),
        "peak_never_loser_floor": float(EXIT["peak_never_loser_floor"]),
        "early_fail_margin_pct": float(EXIT["early_fail_margin_pct"]),
        "post_fill_grace_sec": int(EXIT["post_fill_grace_sec"]),
        "thesis_mfe_protect_pct": float(EXIT["thesis_mfe_protect_pct"]),
        "thesis_min_age_sec": int(EXIT["thesis_min_age_sec"]),
        "exit_order": list(EXIT["exit_order"]),
        "path_end_sec": SPEC.max_duration_sec,
        "partial_reduction_required": False,
    }


def dashboard_policy() -> dict[str, Any]:
    payload = _dashboard(SPEC, signal_detail="Own DeepSeek call (Aug v3 prompt) after every shared three-minute call")
    payload["filter_chips"] = [
        "BASELINE BENCHMARK", "PAPER ONLY", "RELAY INELIGIBLE",
        "Aug v3 prompt · never NO_TRADE", "Gap ≥5 · L+S ≥50 · structure gate",
        f"Maker {SPEC.entry_offset_pct:g}% · chase 25% gap / 60 s / 10 min",
        "Scenario C ladder", "Thesis −12% (MFE 5%)", "SL 30% · early-fail −32%", "120m cap",
    ]
    payload["entry"].update({
        "trigger": "Own Aug-v3 DeepSeek call per shared three-minute call; higher score picks the side",
        "entry_path": "FAMILY_AUG_CONTINUOUS",
        "fill_path": ENTRY["fill_model"],
        "shadow_fill_path": ENTRY["shadow_fill_model"],
        "chase_detail": "25% of the remaining gap every 60 s while under 10 min; stops within $10/0.1% or after 90% of the gap",
    })
    payload["exit"].update({
        "fixed_time_exit": "120m",
        "early_fail_margin_pct": float(EXIT["early_fail_margin_pct"]),
        "peak_never_loser": f"{EXIT['peak_never_loser_min_peak']:g}/{EXIT['peak_never_loser_floor']:g}",
        "thesis_mfe_protect_pct": float(EXIT["thesis_mfe_protect_pct"]),
        "exit_order": list(EXIT["exit_order"]),
    })
    payload["strategy_detail"] = [
        f"Raw policy: {POLICY_ID}",
        "Exact replica of Aug-2026 Continuous (realistic fills; August touch-fill shadow alongside)",
        f"Prompt {PROMPT_ID} verbatim (sha256 {PROMPT_SHA256[:12]})",
        *AUG_DIFFERENCES,
        "Tile ON is paper eligibility only; relay remains fail-closed",
    ]
    payload["baseline_role"] = "BASELINE_BENCHMARK"
    return payload
