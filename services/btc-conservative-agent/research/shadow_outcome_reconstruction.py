"""Read-only reconstruction of shadow/counterfactual outcomes.

Does not modify raw evidence files. Does not change live strategy parameters.
Missing evidence stays UNKNOWN. Zero is never used as a stand-in for no-trade.
Shadow fills are estimates; authenticated Bitfinex fills are facts.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

HORIZONS_SEC = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "60m": 3600,
    "120m": 7200,
}
BPS_OFFSETS = (0, 5, 8, 10, 12, 15, 20)
CLUSTER_BOUNDARIES_PCT = (0.05, 0.09, 0.12, 0.15)
THESIS_CUTS = (-8.0, -10.0, -12.0, -14.0, -16.0)
HARD_STOP_MARGIN_PCT = 13.0
CURRENT_THESIS_CUT = -12.0
CURRENT_CLUSTER_BOUNDARY_PCT = 0.09
CURRENT_LADDER = ((4, 2), (5, 3), (8, 5), (12, 10), (19, 17), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120))
ALT_LADDER = ((4, 2), (5, 3), (8, 5), (19, 17), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120))

NEVER_EXECUTABLE = "NEVER_EXECUTABLE"
EXECUTABLE_BUT_BLOCKED = "EXECUTABLE_BUT_BLOCKED"
EXECUTABLE_AFTER_EXPIRY = "EXECUTABLE_AFTER_EXPIRY"
PARTIALLY_EXECUTABLE = "PARTIALLY_EXECUTABLE"
EXECUTABLE_ONLY_AFTER_CHASE = "EXECUTABLE_ONLY_AFTER_CHASE"
EXECUTABLE_AT_ORIGINAL_LIMIT = "EXECUTABLE_AT_ORIGINAL_LIMIT"

FACT = "FACT"
EXECUTABLE_COUNTERFACTUAL = "EXECUTABLE_COUNTERFACTUAL"
ESTIMATED_FILL_PROBABILITY = "ESTIMATED_FILL_PROBABILITY"
UNKNOWN = "UNKNOWN"

CERTAIN_VERDICTS = {"EXECUTABLE"}
ESTIMATED_VERDICTS = {"INSUFFICIENT_RECENT_EXECUTION"}
NON_EXEC_VERDICTS = {"INSUFFICIENT_EXECUTABLE_DEPTH", "BOOK_STALE", "GENERATION_MISMATCH", "INVALID_ORDER"}


def _num(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _direction(row, observations=None):
    for candidate in (
        row.get("direction"),
        row.get("dir"),
        row.get("scenario"),
        (row.get("source_order_market_evidence") or {}).get("direction"),
        (observations[0].get("direction") if observations else None),
    ):
        text = str(candidate or "").upper()
        if text in {"LONG", "BUY"}:
            return "LONG"
        if text in {"SHORT", "SELL"}:
            return "SHORT"
    return None


def quote_touch(observation, limit, direction):
    quote = _num(observation.get("side_correct_executable_quote"))
    limit = _num(limit)
    if quote is None or quote <= 0 or limit is None or limit <= 0 or not direction:
        return None
    if direction == "LONG":
        return quote <= limit
    return quote >= limit


def same_observed_limit(observation, limit):
    observed = _num(observation.get("limit_price") or observation.get("current_limit_price"))
    limit = _num(limit)
    if observed is None or limit is None:
        return False
    return abs(observed - limit) < 0.005


def classify_observation(observation, limit, requested_qty, direction):
    """Return (certainty, status, details) for one timestamp at one limit."""
    qty = _num(requested_qty, 0) or 0
    touch = quote_touch(observation, limit, direction)
    visible = _num(observation.get("visible_executable_qty"))
    aggressor = _num(observation.get("recent_executable_aggressor_qty"))
    details = {
        "observed_at": observation.get("observed_at"),
        "observed_at_ts": observation.get("observed_at_ts"),
        "limit_price": _num(limit),
        "limit_generation": observation.get("limit_generation"),
        "quote": _num(observation.get("side_correct_executable_quote")),
        "visible_executable_qty": visible,
        "recent_executable_aggressor_qty": aggressor,
        "requested_quantity": qty,
        "fill_gate_verdict": observation.get("fill_gate_verdict"),
        "evidence_id": observation.get("observed_at") or observation.get("observed_at_ts"),
    }
    if same_observed_limit(observation, limit):
        verdict = str(observation.get("fill_gate_verdict") or "").upper()
        if verdict == "EXECUTABLE":
            partial = bool(qty > 0 and visible is not None and visible + 1e-12 < qty)
            details["partial"] = partial
            details["available_quantity"] = visible
            return FACT, (PARTIALLY_EXECUTABLE if partial else "FULLY_EXECUTABLE"), details
        if verdict == "INSUFFICIENT_RECENT_EXECUTION":
            details["partial"] = bool(qty > 0 and visible is not None and visible + 1e-12 < qty)
            details["available_quantity"] = visible
            return ESTIMATED_FILL_PROBABILITY, "BOOK_EXECUTABLE_NO_AGGRESSOR", details
        if verdict in NON_EXEC_VERDICTS:
            status = "QUOTE_TOUCH" if touch else "NO_QUOTE_TOUCH"
            if verdict == "INSUFFICIENT_EXECUTABLE_DEPTH" and touch:
                status = "QUOTE_TOUCH_INSUFFICIENT_DEPTH"
            return FACT, status, details
        return UNKNOWN, "UNRECOGNIZED_VERDICT", details
    if touch is None:
        return UNKNOWN, "MISSING_QUOTE", details
    if not touch:
        return ESTIMATED_FILL_PROBABILITY, "NO_QUOTE_TOUCH", details
    details["depth_at_alternative_limit"] = UNKNOWN
    details["aggressor_at_alternative_limit"] = UNKNOWN
    return ESTIMATED_FILL_PROBABILITY, "QUOTE_TOUCH_ONLY", details


def first_match(observations, limit, requested_qty, direction, want):
    for observation in observations:
        certainty, status, details = classify_observation(observation, limit, requested_qty, direction)
        if status in want:
            return certainty, status, details
    return None, None, None


def observations_from(row):
    nested = row.get("source_order_market_evidence") if isinstance(row.get("source_order_market_evidence"), dict) else {}
    if nested.get("observations"):
        evidence = dict(nested)
        rows = list(nested.get("observations") or [])
    elif isinstance(row.get("observations"), list) and row.get("observations"):
        evidence = dict(row)
        rows = list(row.get("observations"))
    else:
        evidence = dict(nested)
        rows = []
    rows = [item for item in rows if isinstance(item, dict)]
    rows.sort(key=lambda item: _num(item.get("observed_at_ts"), 0) or 0)
    if rows:
        latest = rows[-1]
        earliest = rows[0]
        evidence.setdefault("requested_quantity", latest.get("requested_quantity") or earliest.get("requested_quantity"))
        evidence.setdefault("original_limit_price", latest.get("original_limit_price") or earliest.get("original_limit_price"))
        evidence.setdefault("current_limit_price", latest.get("current_limit_price") or latest.get("limit_price"))
        evidence.setdefault("direction", latest.get("direction") or earliest.get("direction"))
        evidence.setdefault("limit_generation", latest.get("limit_generation"))
    return evidence, rows


def limit_sequence(evidence, observations):
    original = _num(evidence.get("original_limit_price") or (observations[0].get("original_limit_price") if observations else None))
    current = _num(evidence.get("current_limit_price") or (observations[-1].get("current_limit_price") if observations else None))
    sequence = []
    seen = set()
    if original is not None:
        sequence.append({"generation": 0, "limit": original, "kind": "original"})
        seen.add((0, round(original, 2)))
    for observation in observations:
        generation = int(_num(observation.get("limit_generation"), 0) or 0)
        limit = _num(observation.get("limit_price") or observation.get("current_limit_price"))
        if limit is None:
            continue
        key = (generation, round(limit, 2))
        if key in seen:
            continue
        seen.add(key)
        if generation == 0 and original is not None and abs(limit - original) < 0.005:
            continue
        sequence.append({
            "generation": generation,
            "limit": limit,
            "kind": "chase" if generation > 0 else "original",
            "first_seen_at": observation.get("observed_at"),
        })
    if current is not None and all(abs(item["limit"] - current) >= 0.005 for item in sequence):
        sequence.append({"generation": int(_num(evidence.get("limit_generation"), 0) or 0), "limit": current, "kind": "final"})
    return sequence, original, current


def opportunity_path(observations, limit, direction):
    """Market path versus a resting limit. This is not a trade PnL."""
    if not observations or limit is None or not direction:
        return {"mfe_pct": None, "mae_pct": None, "status": UNKNOWN, "not_a_trade": True}
    mfe = None
    mae = None
    for observation in observations:
        quote = _num(observation.get("side_correct_executable_quote") or observation.get("market_last"))
        if quote is None or quote <= 0:
            continue
        move = ((quote - limit) / limit) * (100.0 if direction == "LONG" else -100.0)
        mfe = move if mfe is None else max(mfe, move)
        mae = move if mae is None else min(mae, move)
    return {
        "mfe_pct": None if mfe is None else round(mfe, 4),
        "mae_pct": None if mae is None else round(mae, 4),
        "status": "OPPORTUNITY_PATH" if mfe is not None else UNKNOWN,
        "not_a_trade": True,
    }


def ttl_span_sec(observations, ttl_sec=None):
    if not observations:
        return None
    start = _num(observations[0].get("observed_at_ts"))
    end = _num(observations[-1].get("observed_at_ts"))
    if start is None or end is None:
        return None
    return end - start


def reconstruct_fill_origin(row, ttl_sec=900):
    evidence, observations = observations_from(row)
    direction = _direction(row, observations)
    requested_qty = _num(evidence.get("requested_quantity") or (observations[0].get("requested_quantity") if observations else None))
    sequence, original, current = limit_sequence(evidence, observations)
    block_reason = str(row.get("block_reason") or "").upper()
    cluster_blocked = "CLUSTER" in block_reason or "DUPLICATE" in block_reason or "CORRELATED" in block_reason
    expired = "TTL" in block_reason or str(row.get("exit_reason") or "").upper() in {"NO_FILL", "TTL_EXPIRED"}
    if not observations:
        return {
            "schema": "shadow_fill_origin_v1",
            "classification": UNKNOWN,
            "certainty": UNKNOWN,
            "avoided_exposure": None,
            "not_a_trade": True,
            "net_pnl_usd": None,
            "reason": "NO_MARKET_OBSERVATIONS",
            "direction": direction,
            "limit_sequence": sequence,
        }

    certain_full = []
    certain_partial = []
    estimated = []
    original_certain = []
    chase_certain = []
    for item in sequence:
        certainty, status, details = first_match(
            observations, item["limit"], requested_qty, direction,
            {"FULLY_EXECUTABLE", PARTIALLY_EXECUTABLE},
        )
        if not status:
            certainty, status, details = first_match(
                observations, item["limit"], requested_qty, direction,
                {"BOOK_EXECUTABLE_NO_AGGRESSOR"},
            )
        if status == "FULLY_EXECUTABLE":
            certain_full.append((item, details, certainty))
            (original_certain if item["kind"] == "original" else chase_certain).append(details)
        elif status == PARTIALLY_EXECUTABLE:
            certain_partial.append((item, details, certainty))
        elif status == "BOOK_EXECUTABLE_NO_AGGRESSOR":
            estimated.append((item, details, certainty))

    def _earliest(rows):
        return min(rows, key=lambda item: _num(item[1].get("observed_at_ts"), 0) or 0)

    first_certain = _earliest(certain_full + certain_partial) if (certain_full or certain_partial) else None
    first_estimated = _earliest(estimated) if estimated else None
    first_ts = _num(observations[0].get("observed_at_ts"))
    last_ts = _num(observations[-1].get("observed_at_ts"))
    covered_sec = ttl_span_sec(observations)
    ttl_complete = bool(covered_sec is not None and covered_sec >= ttl_sec - 1)

    if first_certain:
        item, details, certainty = first_certain
        fill_ts = _num(details.get("observed_at_ts"))
        after_expiry = bool(expired and first_ts is not None and fill_ts is not None and fill_ts - first_ts >= ttl_sec)
        if cluster_blocked:
            classification = EXECUTABLE_BUT_BLOCKED
        elif after_expiry:
            classification = EXECUTABLE_AFTER_EXPIRY
        elif details.get("partial"):
            classification = PARTIALLY_EXECUTABLE
        elif item["kind"] == "original":
            classification = EXECUTABLE_AT_ORIGINAL_LIMIT
        elif original_certain:
            classification = EXECUTABLE_AT_ORIGINAL_LIMIT
        else:
            classification = EXECUTABLE_ONLY_AFTER_CHASE
        origin = {
            "schema": "shadow_fill_origin_v1",
            "classification": classification,
            "market_fact": certainty,
            "certainty": EXECUTABLE_COUNTERFACTUAL,
            "label": EXECUTABLE_COUNTERFACTUAL,
            "avoided_exposure": False,
            "not_a_trade": False,
            "fill_timestamp": details.get("observed_at"),
            "fill_timestamp_ts": fill_ts,
            "limit_generation": details.get("limit_generation"),
            "price": details.get("limit_price"),
            "available_quantity": details.get("available_quantity"),
            "requested_quantity": requested_qty,
            "partial": bool(details.get("partial")),
            "full": not bool(details.get("partial")),
            "evidence_ids": [details.get("evidence_id")],
            "direction": direction,
            "limit_sequence": sequence,
            "original_limit": original,
            "final_limit": current,
            "cluster_blocked": cluster_blocked,
            "net_pnl_usd": None,
        }
        return origin

    opportunity = opportunity_path(observations, current or original, direction)
    if first_estimated:
        item, details, certainty = first_estimated
        classification = EXECUTABLE_BUT_BLOCKED if cluster_blocked else (
            EXECUTABLE_ONLY_AFTER_CHASE if item["kind"] != "original" else EXECUTABLE_AT_ORIGINAL_LIMIT
        )
        return {
            "schema": "shadow_fill_origin_v1",
            "classification": classification,
            "certainty": ESTIMATED_FILL_PROBABILITY,
            "label": ESTIMATED_FILL_PROBABILITY,
            "avoided_exposure": cluster_blocked,
            "not_a_trade": True,
            "reason": "BOOK_AND_DEPTH_WITHOUT_AGGRESSOR",
            "first_book_executable": details,
            "direction": direction,
            "limit_sequence": sequence,
            "original_limit": original,
            "final_limit": current,
            "cluster_blocked": cluster_blocked,
            "missed_opportunity_mfe": opportunity.get("mfe_pct"),
            "missed_opportunity_mae": opportunity.get("mae_pct"),
            "net_pnl_usd": None,
            "ttl_complete": ttl_complete,
            "observation_span_sec": covered_sec,
        }

    return {
        "schema": "shadow_fill_origin_v1",
        "classification": NEVER_EXECUTABLE,
        "certainty": FACT if observations else UNKNOWN,
        "label": NEVER_EXECUTABLE,
        "avoided_exposure": True,
        "not_a_trade": True,
        "reason": "NO_PRODUCTION_EQUIVALENT_FILL",
        "direction": direction,
        "limit_sequence": sequence,
        "original_limit": original,
        "final_limit": current,
        "cluster_blocked": cluster_blocked,
        "missed_opportunity_mfe": opportunity.get("mfe_pct"),
        "missed_opportunity_mae": opportunity.get("mae_pct"),
        "net_pnl_usd": None,
        "ttl_complete": ttl_complete,
        "observation_span_sec": covered_sec,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def closer_limit(original, direction, bps):
    original = _num(original)
    if original is None:
        return None
    if direction == "LONG":
        return original * (1.0 + bps / 10_000.0)
    return original * (1.0 - bps / 10_000.0)


def alternative_entry_grid(row, ttl_sec=900):
    evidence, observations = observations_from(row)
    direction = _direction(row, observations)
    requested_qty = _num(evidence.get("requested_quantity") or (observations[0].get("requested_quantity") if observations else None))
    sequence, original, current = limit_sequence(evidence, observations)
    results = []
    if not observations or original is None or not direction:
        return {
            "schema": "alternative_entry_grid_v1",
            "status": UNKNOWN,
            "reason": "INSUFFICIENT_LIMIT_OR_MARKET_EVIDENCE",
            "rows": [],
        }
    tested = []
    tested.append({"name": "original_limit", "limit": original, "kind": "observed"})
    tested.append({"name": "no_chase", "limit": original, "kind": "observed", "max_generation": 0})
    for item in sequence:
        if item["kind"] == "chase":
            tested.append({
                "name": f"chase_generation_{item['generation']}",
                "limit": item["limit"],
                "kind": "observed",
                "generation": item["generation"],
            })
    for bps in BPS_OFFSETS:
        if bps == 0:
            continue
        tested.append({
            "name": f"{bps}_bps_closer",
            "limit": closer_limit(original, direction, bps),
            "kind": "synthetic_bps",
            "bps": bps,
        })
    for spec in tested:
        subset = observations
        if spec.get("max_generation") == 0:
            subset = [row for row in observations if int(_num(row.get("limit_generation"), 0) or 0) == 0] or observations
        certainty, status, details = first_match(
            subset, spec["limit"], requested_qty, direction,
            {"FULLY_EXECUTABLE", PARTIALLY_EXECUTABLE},
        )
        if not status:
            certainty, status, details = first_match(
                subset, spec["limit"], requested_qty, direction,
                {"BOOK_EXECUTABLE_NO_AGGRESSOR"},
            )
        if not status:
            certainty, status, details = first_match(
                subset, spec["limit"], requested_qty, direction,
                {"QUOTE_TOUCH_ONLY"},
            )
        executable = status in {"FULLY_EXECUTABLE", PARTIALLY_EXECUTABLE, "BOOK_EXECUTABLE_NO_AGGRESSOR", "QUOTE_TOUCH_ONLY"}
        results.append({
            "name": spec["name"],
            "limit": spec["limit"],
            "kind": spec["kind"],
            "certainty": certainty or UNKNOWN,
            "status": status or "NO_FILL",
            "executable": executable,
            "first_executable": details,
            "partial": bool((details or {}).get("partial")) if details else None,
            "label": (
                FACT if certainty == FACT else
                EXECUTABLE_COUNTERFACTUAL if status in {"FULLY_EXECUTABLE", PARTIALLY_EXECUTABLE} else
                ESTIMATED_FILL_PROBABILITY if executable else
                "NO_FILL"
            ),
            "live_recommendation": False,
        })
    certain_full = [row for row in results if row["status"] == "FULLY_EXECUTABLE"]
    least_aggressive = None
    if certain_full and original is not None:
        if direction == "LONG":
            least_aggressive = min(certain_full, key=lambda row: row["limit"] or 0)
        else:
            least_aggressive = max(certain_full, key=lambda row: row["limit"] or 0)
    return {
        "schema": "alternative_entry_grid_v1",
        "status": "COMPUTED",
        "original_limit": original,
        "direction": direction,
        "rows": results,
        "first_observed_executable": next((row for row in results if row["status"] in {"FULLY_EXECUTABLE", PARTIALLY_EXECUTABLE}), None)
            or next((row for row in results if row["executable"]), None),
        "least_aggressive_tested_executable": least_aggressive,
        "descriptive_best_tested_point": (least_aggressive or {}).get("name") if least_aggressive else None,
        "live_recommendation": "not qualified",
        "note": "Synthetic bps offsets are quote-touch estimates unless they coincide with an observed limit generation. They are not certain fills.",
    }


def unreal_pct(price, entry, direction, leverage):
    price = _num(price)
    entry = _num(entry)
    if price is None or entry is None or entry <= 0 or price <= 0:
        return None
    sign = 1.0 if str(direction).upper() == "LONG" else -1.0
    return ((price - entry) / entry) * sign * float(leverage or 100) * 100.0


def tick_price(tick):
    return _num(tick.get("best_bid") if str(tick.get("direction") or "").upper() == "LONG" else tick.get("best_ask")) \
        or _num(tick.get("price"))


def replay_from_origin(ticks, origin, direction, leverage=100, margin_usd=20.0, ladder=CURRENT_LADDER, thesis_cut=CURRENT_THESIS_CUT, hard_stop=HARD_STOP_MARGIN_PCT, thesis_min_age_sec=300, mfe_protect_pct=5.0):
    if not origin or origin.get("not_a_trade") or origin.get("price") is None:
        return {
            "schema": "counterfactual_exit_replay_v1",
            "not_a_trade": True,
            "net_pnl_usd": None,
            "reason": origin.get("reason") if origin else "NO_ORIGIN",
        }
    entry = _num(origin.get("price"))
    fill_t = _num(origin.get("fill_timestamp_ts"))
    if ticks:
        t0 = _num((ticks[0] or {}).get("t"), 0) or 0
        fill_rel = None
        if fill_t is not None and ticks and _num(ticks[0].get("observed_ts") or ticks[0].get("ts")):
            start_abs = _num(ticks[0].get("observed_ts") or ticks[0].get("ts"))
            fill_rel = fill_t - start_abs if start_abs is not None else None
        if fill_rel is None:
            fill_rel = 0.0
    else:
        fill_rel = 0.0
        t0 = 0
    ordered = sorted(ticks or [], key=lambda tick: _num(tick.get("t"), 0) or 0)
    peak = 0.0
    mae = 0.0
    mfe_t = None
    mae_t = None
    exit_reason = "HORIZON_END"
    exit_unreal = None
    exit_t = None
    exit_price = None
    for tick in ordered:
        t = _num(tick.get("t"), 0) or 0
        if t < fill_rel:
            continue
        price = tick_price(tick) or _num(tick.get("price"))
        unreal = unreal_pct(price, entry, direction, leverage)
        if unreal is None:
            continue
        if unreal > peak:
            peak = unreal
            mfe_t = t
        if unreal < mae:
            mae = unreal
            mae_t = t
        age = t - fill_rel
        if unreal <= -abs(hard_stop):
            exit_reason, exit_unreal, exit_t, exit_price = "HARD_STOP", unreal, t, price
            break
        if age >= thesis_min_age_sec and unreal <= thesis_cut and peak < mfe_protect_pct:
            exit_reason, exit_unreal, exit_t, exit_price = "THESIS_FAST_CUT", unreal, t, price
            break
        lock = None
        for trigger, floor in ladder:
            if peak >= trigger:
                lock = floor
        if lock is not None and unreal <= lock:
            exit_reason, exit_unreal, exit_t, exit_price = "SCENARIO_C_LADDER", unreal, t, price
            break
        exit_unreal, exit_t, exit_price = unreal, t, price
    net = None if exit_unreal is None else round((exit_unreal / 100.0) * float(margin_usd), 4)
    horizons = {}
    last_t = _num(ordered[-1].get("t"), 0) if ordered else None
    for label, seconds in HORIZONS_SEC.items():
        target = fill_rel + seconds
        hit = None
        for tick in ordered:
            t = _num(tick.get("t"), 0) or 0
            if t >= target:
                price = tick_price(tick) or _num(tick.get("price"))
                hit = {
                    "observed": True,
                    "tick_t_rel": t,
                    "price": price,
                    "unreal_pct": unreal_pct(price, entry, direction, leverage),
                }
                break
        if hit is None:
            hit = {"observed": False, "tick_t_rel": None, "price": None, "unreal_pct": None}
        horizons[label] = hit
    mature = bool(last_t is not None and last_t >= fill_rel + HORIZONS_SEC["120m"] - 1)
    return {
        "schema": "counterfactual_exit_replay_v1",
        "not_a_trade": False,
        "certainty": origin.get("certainty"),
        "label": origin.get("label"),
        "entry_price": entry,
        "fill_t_rel": fill_rel,
        "exit_reason": exit_reason,
        "exit_unreal_pct": None if exit_unreal is None else round(exit_unreal, 4),
        "exit_price": exit_price,
        "net_pnl_usd": net,
        "mfe_pct": round(peak, 4),
        "mae_pct": round(mae, 4),
        "time_to_mfe_sec": None if mfe_t is None else round(mfe_t - fill_rel, 3),
        "time_to_mae_sec": None if mae_t is None else round(mae_t - fill_rel, 3),
        "horizons": horizons,
        "required_horizons_complete": mature and all(row["observed"] for row in horizons.values()),
        "hard_stop_hit": exit_reason == "HARD_STOP",
        "thesis_fast_cut_hit": exit_reason == "THESIS_FAST_CUT",
        "scenario_c_hit": exit_reason == "SCENARIO_C_LADDER",
    }


def replay_complete_for(origin, replay_result, ttl_sec=900, post_ttl_horizon_sec=7200):
    if not origin:
        return False, "NO_ORIGIN"
    if origin.get("classification") == NEVER_EXECUTABLE:
        span = _num(origin.get("observation_span_sec"))
        if origin.get("ttl_complete") and span is not None and span >= ttl_sec + post_ttl_horizon_sec:
            return True, "NEVER_EXECUTABLE_TTL_AND_POST_TTL_COMPLETE"
        return False, "NEVER_EXECUTABLE_HORIZON_INCOMPLETE"
    if origin.get("not_a_trade"):
        return False, "NO_CERTAIN_FILL_ORIGIN"
    if not replay_result or replay_result.get("required_horizons_complete") is not True:
        return False, "FILL_ORIGIN_HORIZONS_INCOMPLETE"
    return True, "ORIGIN_AND_HORIZONS_COMPLETE"


SOURCE_DISPOSITIONS = (
    "NEVER_APPROVED",
    "APPROVED_BUT_NEVER_SUBMITTED",
    "LIMIT_SUBMITTED",
    "CHASED_REPRICED",
    "NEVER_EXECUTABLE",
    "EXECUTABLE_BUT_NOT_FILLED_BY_STRICT_SIM",
    "PARTIALLY_FILLED",
    "FULLY_FILLED",
    "EXPIRED",
    "CLOSED",
)
COPY_DISPOSITIONS = (
    "NEVER_RECEIVED",
    "TRANSPORT_SCHEMA_REJECTED",
    "PAUSED_OR_RESEARCH_ONLY",
    "DUPLICATE_CLUSTER_BLOCKED",
    "ORDER_SUBMITTED",
    "REPRICED",
    "NEVER_FILLED",
    "PARTIALLY_FILLED",
    "FULLY_FILLED",
    "PROTECTED",
    "CLOSED",
    "FINAL_POSITION_ORDER_RECONCILIATION",
)


def classify_source_disposition(row, origin, funnel_stages=None, paper_trade=None):
    stages = {str(item).upper() for item in (funnel_stages or [])}
    paper = paper_trade if isinstance(paper_trade, dict) else {}
    origin = origin or {}
    source_status = str(row.get("source_fill_status") or "").upper()
    paper_exit = paper.get("exit_reason") or paper.get("close_reason")
    if paper_exit or str(paper.get("status") or "").upper() == "CLOSED":
        return {
            "disposition": "CLOSED",
            "exit_reason": paper_exit,
            "pnl": paper.get("pnl") if paper.get("pnl") is not None else paper.get("net_pnl_usd"),
            "actual_vs_counterfactual": "ACTUAL",
        }
    if source_status == "FILLED" or (row.get("executed") is True and row.get("filled") is True):
        return {"disposition": "FULLY_FILLED", "actual_vs_counterfactual": "ACTUAL"}
    if source_status == "PARTIAL":
        return {"disposition": "PARTIALLY_FILLED", "actual_vs_counterfactual": "ACTUAL"}
    classification = origin.get("classification")
    if classification == NEVER_EXECUTABLE:
        return {"disposition": "NEVER_EXECUTABLE", "actual_vs_counterfactual": "COUNTERFACTUAL"}
    if classification in {
        EXECUTABLE_AT_ORIGINAL_LIMIT,
        EXECUTABLE_ONLY_AFTER_CHASE,
        PARTIALLY_EXECUTABLE,
        EXECUTABLE_AFTER_EXPIRY,
        EXECUTABLE_BUT_BLOCKED,
    } and origin.get("not_a_trade") is False:
        return {
            "disposition": "EXECUTABLE_BUT_NOT_FILLED_BY_STRICT_SIM",
            "actual_vs_counterfactual": "COUNTERFACTUAL",
            "label": origin.get("label"),
        }
    if classification == EXECUTABLE_BUT_BLOCKED:
        return {"disposition": "EXECUTABLE_BUT_NOT_FILLED_BY_STRICT_SIM", "actual_vs_counterfactual": "COUNTERFACTUAL"}
    if any(int(item.get("generation") or 0) > 0 for item in origin.get("limit_sequence") or []):
        return {"disposition": "CHASED_REPRICED", "actual_vs_counterfactual": "ACTUAL"}
    if "ORDER_SUBMITTED" in stages or "FILLED" in stages:
        if "TTL" in str(row.get("block_reason") or "").upper() or str(row.get("exit_reason") or "").upper() in {"NO_FILL", "TTL_EXPIRED"}:
            return {"disposition": "EXPIRED", "actual_vs_counterfactual": "ACTUAL"}
        return {"disposition": "LIMIT_SUBMITTED", "actual_vs_counterfactual": "ACTUAL"}
    if "APPROVE" in stages:
        return {"disposition": "APPROVED_BUT_NEVER_SUBMITTED", "actual_vs_counterfactual": "ACTUAL"}
    if not stages and origin.get("classification") in {None, UNKNOWN} and not paper:
        return {
            "disposition": UNKNOWN,
            "actual_vs_counterfactual": UNKNOWN,
            "reason": "NO_APPROVAL_OR_REJECTION_EVIDENCE",
        }
    return {"disposition": "NEVER_APPROVED", "actual_vs_counterfactual": "ACTUAL"}


def classify_copy_disposition(row):
    evidence = row.get("bitfinex_evidence") if isinstance(row.get("bitfinex_evidence"), dict) else {}
    copy_status = str(row.get("copy_fill_status") or evidence.get("copy_fill_status") or "").upper()
    block = str(row.get("block_reason") or "").upper()
    terminal = str(row.get("terminal_class") or evidence.get("terminal_class") or "").upper()
    if evidence.get("copy_terminal_fence_complete") or "RECONCIL" in terminal:
        return {"disposition": "FINAL_POSITION_ORDER_RECONCILIATION", "actual_vs_counterfactual": "ACTUAL"}
    if copy_status == "FILLED":
        return {"disposition": "FULLY_FILLED", "actual_vs_counterfactual": "ACTUAL"}
    if copy_status == "PARTIAL":
        return {"disposition": "PARTIALLY_FILLED", "actual_vs_counterfactual": "ACTUAL"}
    if "PROTECTED" in terminal or evidence.get("protection_active"):
        return {"disposition": "PROTECTED", "actual_vs_counterfactual": "ACTUAL"}
    if "CLUSTER" in block or "DUPLICATE" in block or "CORRELATED" in block:
        return {"disposition": "DUPLICATE_CLUSTER_BLOCKED", "actual_vs_counterfactual": "COUNTERFACTUAL"}
    if "PAUSED" in block or "RESEARCH" in block:
        return {"disposition": "PAUSED_OR_RESEARCH_ONLY", "actual_vs_counterfactual": "COUNTERFACTUAL"}
    if "TRANSPORT" in block or "SCHEMA" in block or evidence.get("transport_rejected"):
        return {"disposition": "TRANSPORT_SCHEMA_REJECTED", "actual_vs_counterfactual": "ACTUAL"}
    if evidence.get("bitfinex_order_ids") or evidence.get("order_submitted"):
        if any(int(item.get("generation") or 0) > 0 for item in (row.get("fill_origin") or {}).get("limit_sequence") or []):
            return {"disposition": "REPRICED", "actual_vs_counterfactual": "ACTUAL"}
        if copy_status in {"UNFILLED", "UNKNOWN"}:
            return {"disposition": "NEVER_FILLED", "actual_vs_counterfactual": "ACTUAL"}
        return {"disposition": "ORDER_SUBMITTED", "actual_vs_counterfactual": "ACTUAL"}
    if not evidence.get("participant_id") and not evidence.get("bitfinex_order_ids"):
        return {"disposition": "NEVER_RECEIVED", "actual_vs_counterfactual": "ACTUAL"}
    return {"disposition": "NEVER_RECEIVED", "actual_vs_counterfactual": "ACTUAL"}


def alternative_exit_grid(ticks, origin, direction, leverage=100, margin_usd=20.0):
    if not origin or origin.get("not_a_trade"):
        return {
            "schema": "alternative_exit_grid_v1",
            "status": "NOT_APPLICABLE",
            "reason": "NO_CERTAIN_FILL_ORIGIN",
            "rows": [],
        }
    rows = []
    for name, ladder, thesis in (
        ("current_policy", CURRENT_LADDER, CURRENT_THESIS_CUT),
        ("current_hard_stop_only", (), CURRENT_THESIS_CUT),
        ("legacy_scenario_c_ladder", ALT_LADDER, CURRENT_THESIS_CUT),
    ):
        replay = replay_from_origin(
            ticks or [], origin, direction, leverage=leverage, margin_usd=margin_usd,
            ladder=ladder if name != "current_hard_stop_only" else CURRENT_LADDER,
            thesis_cut=thesis,
        )
        rows.append({"name": name, "hindsight_only": True, "live_recommendation": False, **replay})
    for thesis in THESIS_CUTS:
        replay = replay_from_origin(
            ticks or [], origin, direction, leverage=leverage, margin_usd=margin_usd,
            ladder=CURRENT_LADDER, thesis_cut=thesis,
        )
        rows.append({
            "name": f"thesis_cut_{thesis}",
            "hindsight_only": True,
            "live_recommendation": False,
            **replay,
        })
    hold = replay_from_origin(
        ticks or [], origin, direction, leverage=leverage, margin_usd=margin_usd,
        ladder=((10_000, 10_000),), thesis_cut=-10_000, hard_stop=10_000,
    )
    hold["exit_reason"] = "HOLD_TO_HORIZON"
    rows.append({"name": "hold_to_horizon", "hindsight_only": True, "live_recommendation": False, **hold})
    return {
        "schema": "alternative_exit_grid_v1",
        "status": "COMPUTED",
        "note": "Alternative exits are hindsight replays. They do not authorize live parameter changes.",
        "live_recommendation": "not qualified",
        "rows": rows,
    }


def cluster_portfolio_row(row, origin, duplicate_audit=None, peer_origin=None):
    audit = duplicate_audit if isinstance(duplicate_audit, dict) else {}
    nearest = _num(audit.get("nearest_limit_price"))
    limit = _num(audit.get("limit_price") or origin.get("final_limit") or origin.get("original_limit"))
    distance_pct = None
    if nearest and limit:
        distance_pct = abs(limit - nearest) / ((limit + nearest) / 2.0) * 100.0
    blocked = "CLUSTER" in str(row.get("block_reason") or "").upper() or str(audit.get("decision") or "").upper() in {"BLOCK_CLUSTER", "BLOCK_DUPLICATE"}
    would_fill = bool(origin and origin.get("not_a_trade") is False)
    peer_filled = bool(peer_origin and peer_origin.get("not_a_trade") is False)
    incremental = origin.get("net_pnl_usd") if would_fill else None
    return {
        "schema": "cluster_portfolio_v1",
        "trade_id": row.get("trade_id"),
        "nearest_trade_id": audit.get("nearest_trade_id"),
        "distance_pct": None if distance_pct is None else round(distance_pct, 6),
        "current_boundary_pct": CURRENT_CLUSTER_BOUNDARY_PCT,
        "inside_current_boundary": None if distance_pct is None else distance_pct <= CURRENT_CLUSTER_BOUNDARY_PCT,
        "blocked": blocked,
        "reference_filled": peer_filled,
        "duplicate_would_fill": would_fill,
        "duplicate_fill_label": (origin or {}).get("label"),
        "incremental_pnl_usd": incremental,
        "combined_exposure_if_both_allowed": bool(would_fill and peer_filled),
        "winner_skipped": bool(blocked and would_fill and incremental is not None and incremental > 0),
        "loss_avoided": bool(blocked and would_fill and incremental is not None and incremental < 0),
        "live_recommendation": "not qualified",
        "note": "Portfolio view. 0.09% is not changed from this reconstruction.",
    }


def reconstruct_row(row, ticks=None, ttl_sec=900, funnel_stages=None, paper_trade=None, duplicate_audit=None, peer_origin=None):
    origin = reconstruct_fill_origin(row, ttl_sec=ttl_sec)
    direction = origin.get("direction") or _direction(row)
    alts = alternative_entry_grid(row, ttl_sec=ttl_sec)
    replay = None
    exits = None
    if not origin.get("not_a_trade"):
        replay = replay_from_origin(ticks or [], origin, direction)
        origin["net_pnl_usd"] = replay.get("net_pnl_usd")
        exits = alternative_exit_grid(ticks or [], origin, direction)
    complete, reason = replay_complete_for(origin, replay, ttl_sec=ttl_sec)
    source = classify_source_disposition(row, origin, funnel_stages=funnel_stages, paper_trade=paper_trade)
    copy = classify_copy_disposition({**row, "fill_origin": origin})
    return {
        "schema": "complete_research_record_v1",
        "trade_id": row.get("trade_id") or row.get("canonical_trade_id"),
        "actual_vs_counterfactual": "ACTUAL" if row.get("executed") is True else "COUNTERFACTUAL",
        "source_disposition": source,
        "copy_disposition": copy,
        "fill_origin": origin,
        "alternative_entries": alts,
        "alternative_exits": exits,
        "cluster_portfolio": cluster_portfolio_row(row, origin, duplicate_audit=duplicate_audit, peer_origin=peer_origin),
        "exit_replay": replay,
        "replay_complete": complete,
        "replay_complete_reason": reason,
        "evidence_quality": origin.get("certainty") or UNKNOWN,
        "missing_evidence_reason": origin.get("reason"),
        "net_pnl_usd": origin.get("net_pnl_usd"),
        "not_a_trade": origin.get("not_a_trade"),
        "avoided_exposure": origin.get("avoided_exposure"),
        "eligibility_status": "QUALIFIED" if complete else "NOT_QUALIFIED",
        "live_policy_change_allowed": False,
        "bps_ladder_text": format_bps_ladder(alts),
    }


def attach_market_evidence(row, evidence_index):
    trade_id = str(row.get("trade_id") or row.get("canonical_trade_id") or "")
    grouped = (evidence_index or {}).get(trade_id)
    if not grouped:
        return row
    attached = dict(row)
    attached["source_order_market_evidence"] = grouped
    attached.setdefault("direction", grouped.get("direction") or (grouped.get("latest_observation") or {}).get("direction"))
    return attached



def wilson(k, n, z=1.96):
    if n is None or int(n) <= 0 or k is None:
        return {"k": k, "n": n, "p": None, "lo": None, "hi": None, "status": UNKNOWN}
    n = int(n)
    k = min(max(int(k), 0), n)
    p = k / n
    z2 = z * z
    den = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / den
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n) / den
    return {
        "k": k,
        "n": n,
        "p": round(p, 6),
        "lo": round(max(0.0, centre - margin), 6),
        "hi": round(min(1.0, centre + margin), 6),
        "status": "EMPIRICAL_ESTIMATE",
    }


def format_bps_ladder(grid):
    if not grid or not grid.get("rows"):
        return ["insufficient evidence"]
    lines = []
    for row in grid["rows"]:
        if row["name"] in {"original_limit", "no_chase"} or row["name"].endswith("_bps_closer") or row["name"].startswith("chase_"):
            label = row["name"].replace("_", " ")
            if row["status"] == "FULLY_EXECUTABLE":
                outcome = "full executable fill"
            elif row["status"] == PARTIALLY_EXECUTABLE:
                outcome = "partial fill"
            elif row["status"] == "BOOK_EXECUTABLE_NO_AGGRESSOR":
                outcome = "estimated book-executable (no aggressor)"
            elif row["status"] == "QUOTE_TOUCH_ONLY":
                outcome = "quote-touch estimate only"
            else:
                outcome = "no fill"
            lines.append(f"{label}: {outcome}")
    lines.append(f"Descriptive best tested point: {grid.get('descriptive_best_tested_point') or 'none'}")
    lines.append("Live recommendation: not qualified")
    return lines
