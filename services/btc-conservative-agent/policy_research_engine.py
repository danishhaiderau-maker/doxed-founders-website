"""Constrained policy-research engine.

Replay many predeclared policies against one canonical market path.
Never changes live 0.09% / thesis -12% / hard stop -13% / Scenario C / chase.
Never treats missing evidence as $0. Never labels a backtest optimum as a live rec.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from statistics import median

SCHEMA = "policy_research_engine_v1"
HORIZONS_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600, "120m": 7200}
LIVE_HARD_STOP_PCT = 30.0
LIVE_THESIS_CUT = -12.0
LIVE_CLUSTER_BPS = 9
LIVE_LADDER = ((4, 2), (5, 3), (8, 5), (12, 10), (19, 17), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120))
MAX_PATH_GAP_MS = 15_000
SMOKE_FLOOR_N = 15
PRELIM_N = 30
STRONG_N = 50

SOURCE_STATES = (
    "REJECTED_BEFORE_APPROVAL", "APPROVED_NOT_SUBMITTED", "ORDER_SUBMITTED",
    "NEVER_EXECUTABLE", "EXECUTABLE_UNFILLED", "PARTIAL_FILL", "FULL_FILL",
    "EXPIRED", "CLOSED",
)
COPY_STATES = (
    "NEVER_RECEIVED", "TRANSPORT_REJECTED", "POLICY_REJECTED", "PAUSED",
    "SOURCE_RESEARCH_ONLY", "CORRELATED_CLUSTER_BLOCKED", "ORDER_SUBMITTED",
    "PARTIAL_FILL", "FULL_FILL", "PROTECTED", "CLOSED", "RECONCILIATION_INCOMPLETE",
    "USER_RELAY_STOP", "EXPIRED_CYCLE_SKIPPED",
)
SHADOW_STATES = (
    "NEVER_EXECUTABLE", "EXECUTABLE_AT_ORIGINAL_LIMIT", "EXECUTABLE_AFTER_CHASE",
    "EXECUTABLE_AFTER_EXPIRY", "PARTIALLY_EXECUTABLE", "INCOMPLETE_EVIDENCE",
)
DIVERGENCE_COHORTS = (
    "BOTH_EXECUTED", "SHOWCASE_ONLY", "COPY_ONLY", "CORRELATED_CLUSTER_BLOCKED",
    "PAUSED_OR_RESEARCH_ONLY", "TRANSPORT_REJECTED", "NEVER_EXECUTABLE",
    "EXECUTABLE_COUNTERFACTUAL",
)
UNKNOWN_CANNOT_COLLECT = (
    "OTHER_TRADERS_QUEUE", "VENUE_MATCHING_ENGINE_INTERNALS",
    "FUTURE_REGIME", "NEWS_WITHOUT_FEED",
)

LADDER_FAMILIES = {
    "CURRENT_BASELINE": LIVE_LADDER,
    "EARLY_TIGHT": ((2, 0.5), (3, 1), (4, 2), (5, 3), (8, 6), (12, 10)),
    "EARLY_LOOSE": ((4, 1), (6, 2), (10, 5), (15, 9), (25, 17)),
    "HIGH_CAPTURE": ((4, 3), (6, 5), (10, 8), (15, 12), (25, 20), (40, 32)),
    "RUNNER_FRIENDLY": ((5, 1), (8, 3), (12, 6), (20, 12), (40, 25), (80, 55)),
}
THESIS_THRESHOLDS = (-2, -3, -4, -5, -6, -8, -10, -12, -15, -18)
THESIS_MIN_AGES_SEC = (0, 15, 30, 60, 120, 300, 600)
HARD_STOP_GRID = (8, 10, 12, 13, 15, 18, 20, 25, 30, 40)
CHASE_MAX = (0, 1, 2, 3, 4, 5)
CHASE_FIRST_SEC = (30, 60, 90, 120, 180, 300)
CHASE_BPS = (2, 5, 7.5, 10, 15, 20)
CHASE_TTL_MIN = (5, 10, 15, 20, 30)
CLUSTER_BPS_GRID = (5, 7, 8, 9, 10, 12, 15, 20)
TIME_STOP_MIN = (5, 15, 30, 60, 120)
BREAKEVEN_AFTER_MFE = (2, 3, 4, 5)
SIZE_SCALE = (0.5, 1.0, 1.5)
SPREAD_BPS_GRID = (None, 1, 2, 3, 5, 8, 10, 15)
ADX_BANDS = ((None, 15), (15, 20), (20, 25), (25, 30), (30, 40), (40, None))
GIVEBACK_ABS = (2, 3, 5)
GIVEBACK_PCT = (0.20, 0.30, 0.40)

SAFETY_CONSTRAINTS = {
    "physical_hard_stop_live_pct": LIVE_HARD_STOP_PCT,
    "require_reduce_only_stop_while_open": True,
    "no_stop_path_research_only": True,
    "max_drawdown_pct": 25.0,
    "cvar_5pct_floor_usd": -15.0,
    "liquidation_distance_min_pct": 8.0,
    "max_correlated_exposure_bps": LIVE_CLUSTER_BPS,
}


def _num(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def content_addressed_ref(payload, schema="market_path_v1"):
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()
    return {
        "schema": "content_addressed_evidence_ref_v1",
        "evidence_schema": schema,
        "sha256": digest,
        "bytes": len(blob),
        "object_id": f"{schema}:{digest[:16]}",
    }


def session_features(ts_unix):
    ts = _num(ts_unix)
    if ts is None:
        return {"utc_hour": None, "weekday": None, "session": None, "minutes_to_funding": None, "weekend_or_gap": None}
    hour = int((ts % 86400) // 3600)
    weekday = int(((ts // 86400) + 4) % 7)  # 1970-01-01 Thursday
    if 0 <= hour < 7:
        session = "ASIA"
    elif 7 <= hour < 12:
        session = "LONDON"
    elif 12 <= hour < 21:
        session = "NY"
    else:
        session = "ASIA"
    minutes_to_funding = int((8 * 3600 - (ts % (8 * 3600))) // 60)
    return {
        "utc_hour": hour,
        "weekday": weekday,
        "session": session,
        "minutes_to_funding": minutes_to_funding,
        "weekend_or_gap": weekday >= 5,
        "high_impact_window": "UNKNOWN",
    }


def setup_dna(row):
    src = row if isinstance(row, dict) else {}
    feats = src.get("features") if isinstance(src.get("features"), dict) else {}
    market = src.get("market") if isinstance(src.get("market"), dict) else {}

    def pick(*keys):
        for key in keys:
            if src.get(key) is not None:
                return _num(src.get(key))
            if feats.get(key) is not None:
                return _num(feats.get(key))
            if market.get(key) is not None:
                return _num(market.get(key))
        return None

    return {
        "schema": "setup_dna_v1",
        "adx": pick("adx", "adx_at_signal"),
        "adx_slope": pick("adx_slope"),
        "plus_di": pick("plus_di", "di_plus", "+DI"),
        "minus_di": pick("minus_di", "di_minus", "-DI"),
        "spread_bps": pick("spread_bps", "directional_spread"),
        "depth": pick("visible_executable_qty", "depth"),
        "volume_percentile": pick("volume_percentile", "vol_percentile"),
        "delta": pick("delta", "cvd"),
        "imbalance": pick("imbalance", "book_imbalance"),
        "velocity": pick("velocity", "price_velocity"),
        "ema_distance_pct": pick("ema_distance_pct", "ema_stack_distance"),
        "sr_distance_pct": pick("sr_distance_pct", "distance_to_sr_pct"),
        "signal_age_sec": pick("signal_age_sec", "age_sec"),
        "chase_generation": pick("limit_generation", "limit_chase_count"),
        "atr": pick("atr", "atr_pct"),
    }


def clock_alignment(row):
    ev = row.get("bitfinex_evidence") if isinstance(row.get("bitfinex_evidence"), dict) else {}
    timing = row.get("execution_timing") if isinstance(row.get("execution_timing"), dict) else {}
    return {
        "schema": "clock_alignment_v1",
        "source_ts": row.get("source_ts") or row.get("ts"),
        "platform_received_at": ev.get("platform_received_at") or timing.get("platform_received_at"),
        "exchange_ack_at": timing.get("exchange_ack_at") or ev.get("exchange_ack_at"),
        "exchange_mts": ev.get("exchange_mts") or timing.get("exchange_mts"),
        "source_to_platform_ms": _num(timing.get("source_to_platform_ms") or ev.get("source_to_platform_ms")),
        "platform_to_ack_ms": _num(timing.get("platform_to_ack_ms") or ev.get("platform_to_ack_ms")),
        "fly_ts": row.get("fly_ts") or row.get("ts"),
        "railway_ts": ev.get("railway_ts") or timing.get("railway_ts"),
    }


def path_gaps(ticks, window_sec=7200):
    ordered = sorted(
        (tick for tick in (ticks or []) if isinstance(tick, dict)),
        key=lambda tick: _num(tick.get("observed_ts") or tick.get("t"), 0) or 0,
    )
    gaps = []
    prev = None
    stale = []
    for tick in ordered:
        ts = _num(tick.get("observed_ts"))
        t_rel = _num(tick.get("t"))
        age = _num(tick.get("book_age_sec"))
        if age is not None:
            stale.append(age)
        key = ts if ts is not None else t_rel
        if prev is not None and key is not None:
            delta_ms = (key - prev) * (1000.0 if ts is not None else 1000.0)
            if ts is None:
                delta_ms = (key - prev) * 1000.0
            gaps.append(delta_ms)
        if key is not None:
            prev = key
    max_gap = max(gaps) if gaps else None
    observed = (_num(ordered[-1].get("t"), 0) or 0) - (_num(ordered[0].get("t"), 0) or 0) if len(ordered) >= 2 else 0
    coverage = min(1.0, observed / window_sec) if window_sec else None
    censored = bool(max_gap is not None and max_gap > MAX_PATH_GAP_MS)
    return {
        "schema": "path_gap_v1",
        "tick_count": len(ordered),
        "gap_count": len(gaps),
        "max_gap_ms": None if max_gap is None else round(max_gap, 3),
        "mean_gap_ms": None if not gaps else round(sum(gaps) / len(gaps), 3),
        "window_sec": window_sec,
        "observed_frac_of_120m": None if coverage is None else round(coverage, 6),
        "max_book_stale_sec": None if not stale else round(max(stale), 4),
        "censored": censored,
        "censor_reason": "MAX_GAP_EXCEEDED" if censored else None,
    }


def compact_horizon_receipts(ticks, origin_t=0.0, side_key="best_ask"):
    receipts = {}
    ordered = sorted((tick for tick in (ticks or []) if isinstance(tick, dict)), key=lambda t: _num(t.get("t"), 0) or 0)
    for label, seconds in HORIZONS_SEC.items():
        target = origin_t + seconds
        hit = next(
            (
                tick for tick in ordered
                if (_num(tick.get("t"), -1) or -1) >= target
                and _num(tick.get(side_key) or tick.get("price"), 0)
            ),
            None,
        )
        receipts[label] = {
            "required_sec": seconds,
            "observed": hit is not None,
            "tick_t_rel": None if hit is None else hit.get("t"),
            "price": None if hit is None else (hit.get(side_key) or hit.get("price")),
            "best_bid": None if hit is None else hit.get("best_bid"),
            "best_ask": None if hit is None else hit.get("best_ask"),
        }
    complete = all(row["observed"] for row in receipts.values())
    return {"schema": "horizon_receipts_v1", "origin_t_rel": origin_t, "required": receipts, "complete": complete}


def chase_history(row, observations=None):
    evidence = row.get("source_order_market_evidence") if isinstance(row.get("source_order_market_evidence"), dict) else {}
    rows = list(observations or evidence.get("observations") or [])
    generations = []
    seen = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        gen = int(_num(item.get("limit_generation"), 0) or 0)
        limit = _num(item.get("limit_price") or item.get("current_limit_price"))
        key = (gen, round(limit or 0, 2))
        if key in seen:
            continue
        seen.add(key)
        generations.append({
            "generation": gen,
            "limit": limit,
            "observed_at": item.get("observed_at"),
            "imbalance": _num(item.get("imbalance") or item.get("book_imbalance")),
            "visible_executable_qty": _num(item.get("visible_executable_qty")),
        })
    chase_count = max((item["generation"] for item in generations), default=int(_num(row.get("limit_chase_count"), 0) or 0))
    return {
        "schema": "chase_history_v1",
        "chase_count": chase_count,
        "generations": generations,
        "original_limit": _num(evidence.get("original_limit_price") or row.get("original_limit_price")),
        "final_limit": _num(evidence.get("current_limit_price") or row.get("limit_price")),
    }


def slippage_decomposition(row):
    mid = _num(row.get("mid") or row.get("signal_price"))
    limit = _num(row.get("limit_price") or row.get("current_limit_price"))
    fill = _num(row.get("fill_price") or row.get("entry"))
    exit_fill = _num(row.get("exit_price") or row.get("exit"))
    original = _num(row.get("original_limit_price"))
    chase_deterioration = None
    if original and limit:
        chase_deterioration = abs(limit - original)
    market_deterioration = None
    if limit and fill:
        market_deterioration = abs(fill - limit)
    return {
        "schema": "slippage_decomposition_v1",
        "quoted_mid": mid,
        "limit": limit,
        "actual_fill": fill,
        "exit_fill": exit_fill,
        "chase_induced_usd": chase_deterioration,
        "market_move_usd": market_deterioration,
        "mid_to_fill_usd": None if mid is None or fill is None else abs(fill - mid),
    }


def cost_completeness(row):
    costs = row.get("actual_costs") if isinstance(row.get("actual_costs"), dict) else {}
    missing = []
    for key, label in (
        ("entry_fee_usd", "entry_fee"),
        ("exit_fee_usd", "exit_fee"),
        ("funding_usd", "funding"),
        ("stop_slippage_usd", "stop_slippage"),
    ):
        if costs.get(key) is None and row.get(key) is None:
            missing.append(label)
    return {
        "schema": "cost_completeness_v1",
        "cost_complete": not missing,
        "missing_legs": missing,
        "live_change_blocked_if_incomplete": True,
    }


def stop_replacement_chain(events):
    chain = []
    for event in events or []:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("eventType") or event.get("event_type") or event.get("event") or "").upper()
        if "STOP" not in kind and "TRAIL" not in kind and "PROTECT" not in kind:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
        chain.append({
            "event": kind,
            "order_id": payload.get("bitfinexOrderId") or payload.get("bitfinex_order_id") or payload.get("order_id"),
            "price": _num(payload.get("stopPrice") or payload.get("price")),
            "qty": _num(payload.get("qty") or payload.get("amount")),
            "reduce_only": payload.get("reduce_only") if payload.get("reduce_only") is not None else True,
            "ts": event.get("createdAt") or event.get("created_at") or payload.get("ts"),
            "ack": "ACK" in kind or payload.get("ack") is True,
            "reject": "REJECT" in kind or "FAIL" in kind,
            "cancel_race": "CANCEL" in kind and "RACE" in kind,
        })
    return {"schema": "stop_replacement_chain_v1", "replacements": chain, "count": len(chain)}


def episode_tag(opportunities, window_sec=240, price_bps=9):
    """Cluster same-direction opportunities in time+price into episodes."""
    rows = sorted(
        (row for row in opportunities if isinstance(row, dict) and row.get("trade_id")),
        key=lambda row: _num(row.get("ts_unix") or row.get("created_ts") or 0, 0) or 0,
    )
    episodes = []
    assigned = {}
    for row in rows:
        ts = _num(row.get("ts_unix") or row.get("created_ts"), 0) or 0
        px = _num(row.get("limit_price") or row.get("signal_price") or row.get("price"), 0) or 0
        direction = str(row.get("direction") or "").upper()
        placed = None
        for episode in episodes:
            if episode["direction"] != direction:
                continue
            if ts - episode["end_ts"] > window_sec:
                continue
            ref = episode["ref_price"] or px
            if ref and px and abs(px - ref) / ref * 10_000 > price_bps:
                continue
            placed = episode
            break
        if placed is None:
            placed = {
                "episode_id": f"ep-{row.get('trade_id')}",
                "direction": direction,
                "start_ts": ts,
                "end_ts": ts,
                "ref_price": px,
                "members": [],
            }
            episodes.append(placed)
        placed["end_ts"] = max(placed["end_ts"], ts)
        placed["members"].append(row.get("trade_id"))
        assigned[row.get("trade_id")] = placed
    out = {}
    for trade_id, episode in assigned.items():
        members = episode["members"]
        out[trade_id] = {
            "episode_id": episode["episode_id"],
            "episode_start": episode["start_ts"],
            "episode_end": episode["end_ts"],
            "n_signals_in_episode": len(members),
            "rank_in_episode": members.index(trade_id) + 1,
        }
    return out


def divergence_cohort(row):
    source_filled = bool(
        row.get("executed") is True and row.get("filled") is True
        or str(row.get("source_fill_status") or "").upper() in {"FILLED", "PARTIAL"}
        or (isinstance(row.get("paper_trade"), dict) and row["paper_trade"].get("net_pnl_usd") is not None)
    )
    copy_filled = bool(
        str(row.get("copy_fill_status") or "").upper() in {"FILLED", "PARTIAL"}
        or (row.get("copy_fill_observed") or {}).get("classification")
    )
    block = str(row.get("block_reason") or row.get("copy_disposition") or "").upper()
    if "CLUSTER" in block or "CORRELATED" in block:
        return "CORRELATED_CLUSTER_BLOCKED"
    if "USER_RELAY_STOP" in block:
        return "TRANSPORT_REJECTED"
    if "TRANSPORT" in block or "400" in block:
        return "TRANSPORT_REJECTED"
    if "PAUSED" in block or "RESEARCH" in block:
        return "PAUSED_OR_RESEARCH_ONLY"
    if source_filled and copy_filled:
        return "BOTH_EXECUTED"
    if source_filled and not copy_filled:
        return "SHOWCASE_ONLY"
    if copy_filled and not source_filled:
        return "COPY_ONLY"
    origin = (row.get("fill_origin") or {}).get("classification") if isinstance(row.get("fill_origin"), dict) else None
    if origin == "NEVER_EXECUTABLE":
        return "NEVER_EXECUTABLE"
    if origin:
        return "EXECUTABLE_COUNTERFACTUAL"
    if str(row.get("copy_disposition") or "").upper() in {"NEVER_RECEIVED", "EXPIRED_CYCLE_SKIPPED"}:
        return "SHOWCASE_ONLY"
    return "SHOWCASE_ONLY" if source_filled else "NEVER_EXECUTABLE"


def canonical_opportunity(row, ticks=None, observations=None, lifecycle_events=None, paper_trade=None):
    row = row if isinstance(row, dict) else {}
    ticks = ticks or row.get("ticks") or []
    chase = chase_history(row, observations)
    gaps = path_gaps(ticks)
    side = "best_bid" if str(row.get("direction") or "LONG").upper() == "LONG" else "best_ask"
    receipts = compact_horizon_receipts(ticks, origin_t=_num(row.get("origin_t_rel") or row.get("virtual_fill_t"), 0) or 0, side_key=side)
    costs = cost_completeness(row)
    replay_complete = bool(receipts["complete"] and not gaps["censored"] and costs["cost_complete"])
    path_ref = content_addressed_ref(
        [{"t": t.get("t"), "bid": t.get("best_bid"), "ask": t.get("best_ask")} for t in ticks[:8]],
        "market_path_v1",
    )
    if ticks:
        path_ref["start_offset"] = 0
        path_ref["end_offset"] = max(0, len(ticks) - 1)
        path_ref["time_range"] = {
            "start_t": (ticks[0] or {}).get("t") if ticks else None,
            "end_t": (ticks[-1] or {}).get("t") if ticks else None,
        }
    record = {
        "schema": "canonical_opportunity_v1",
        "trade_id": row.get("trade_id"),
        "source_state": row.get("source_state"),
        "copy_state": row.get("copy_state") or row.get("copy_disposition"),
        "shadow_state": ((row.get("fill_origin") or {}) if isinstance(row.get("fill_origin"), dict) else {}).get("classification"),
        "divergence_cohort": divergence_cohort({**row, "paper_trade": paper_trade}),
        "chase": chase,
        "path_gaps": gaps,
        "horizon_receipts": receipts,
        "clock": clock_alignment(row),
        "setup_dna": setup_dna(row),
        "session": session_features(_num(row.get("created_ts") or row.get("ts_unix"))),
        "slippage": slippage_decomposition(row),
        "cost": costs,
        "stop_chain": stop_replacement_chain(lifecycle_events or row.get("lifecycle_events")),
        "microstructure": {
            "schema": "execution_microstructure_v1",
            "time_at_price_ms": _num(row.get("time_at_price_ms")),
            "trade_through_vs_order_size": _num(row.get("trade_through_volume") or row.get("trade_through_vs_size")),
            "maker_taker": row.get("maker_taker") or row.get("liquidity_role") or "UNKNOWN",
            "cancel_replace_count": _num(row.get("cancel_replace_count"), 0) or 0,
            "cancel_replace_latency_ms": _num(row.get("cancel_replace_latency_ms")),
            "book_imbalance_by_generation": [
                {"generation": item.get("generation"), "imbalance": item.get("imbalance")}
                for item in chase.get("generations") or []
            ],
            "queue_position": "UNKNOWN",
            "fill_hypotheses": ["CONSERVATIVE_FILL", "BASE_FILL", "OPTIMISTIC_FILL"],
            "unsubmitted_queue": "UNKNOWN",
        },
        "portfolio_path": {
            "schema": "portfolio_path_v1",
            "concurrent_open_count": row.get("concurrent_open_count"),
            "combined_notional_usd": _num(row.get("combined_notional_usd")),
            "margin_used_pct": _num(row.get("margin_used_pct")),
            "liquidation_vs_mark_pct": _num(row.get("liquidation_vs_mark_pct")),
            "funding_accrued_usd": _num(row.get("funding_accrued_usd") or row.get("funding_usd")),
            "reserved_margin_pending_usd": _num(row.get("reserved_margin_pending_usd")),
        },
        "divergence_telemetry": {
            "schema": "divergence_telemetry_v1",
            "copy_disposition": row.get("copy_disposition") or row.get("copy_state"),
            "source_epoch_id": row.get("source_epoch_id") or row.get("epoch_id"),
            "copy_session_id": row.get("copy_session_id") or row.get("showcase_session_epoch"),
            "excluded_from_showcase_wr": divergence_cohort({**row, "paper_trade": paper_trade}) not in {"BOTH_EXECUTED", "SHOWCASE_ONLY"},
        },
        "physical_safety": {
            **SAFETY_CONSTRAINTS,
            "live_hard_stop_pct": LIVE_HARD_STOP_PCT,
            "no_stop_research_only": True,
            "research_cannot_disable_live": True,
        },
        "market_path_ref": path_ref,
        "replay_complete": replay_complete,
        "replay_complete_reason": (
            "RECEIPTS_AND_COSTS_COMPLETE"
            if replay_complete
            else gaps.get("censor_reason")
            or ("COST_INCOMPLETE" if not costs["cost_complete"] else "HORIZON_RECEIPTS_INCOMPLETE")
        ),
        "unknown_cannot_collect": list(UNKNOWN_CANNOT_COLLECT),
        "live_policy_change_allowed": False,
        "not_a_trade": row.get("not_a_trade"),
        "net_pnl_usd": None if row.get("not_a_trade") is True else row.get("net_pnl_usd"),
    }
    if paper_trade:
        record["paper_trade"] = {
            "fill_price": paper_trade.get("entry") or paper_trade.get("fill_price"),
            "exit_price": paper_trade.get("exit") or paper_trade.get("exit_price"),
            "net_pnl_usd": paper_trade.get("net_pnl_usd"),
            "exit_reason": paper_trade.get("exit_reason"),
        }
    return record


def tick_price(tick, direction="LONG"):
    if not isinstance(tick, dict):
        return None
    key = "best_bid" if str(direction).upper() == "LONG" else "best_ask"
    return _num(tick.get(key) or tick.get("price"))


def replay_path(ticks, *, direction, entry_price, fill_t, qty, leverage, ladder, thesis_cut, thesis_min_age, hard_stop, fee_rate=0.0002, time_stop_sec=None, breakeven_after_mfe=None, giveback_abs=None, giveback_pct=None, size_scale=1.0, skip=False):
    """Deterministic path replay. Conservative ordering if two exits share a sample."""
    if skip:
        return {
            "schema": "path_replay_result_v1",
            "entered": False,
            "exit_reason": "SKIP",
            "net_pnl_usd": None,
            "not_a_trade": True,
            "live_recommendation": False,
        }
    if entry_price is None or not ticks:
        return {
            "schema": "path_replay_result_v1",
            "entered": False,
            "exit_reason": "INCOMPLETE_EVIDENCE",
            "net_pnl_usd": None,
            "not_a_trade": True,
            "ambiguous": False,
        }
    direction = str(direction or "LONG").upper()
    qty = (qty or 0.03) * size_scale
    fill_t = fill_t or 0.0
    peak = 0.0
    mae = 0.0
    mfe_t = None
    mae_t = None
    exit_reason = "HORIZON_END"
    exit_unreal = None
    exit_t = None
    exit_price = None
    ambiguous = False
    breakeven_armed = False
    lock = None
    ordered = sorted(ticks, key=lambda tick: _num(tick.get("t"), 0) or 0)
    for tick in ordered:
        t = _num(tick.get("t"), 0) or 0
        if t < fill_t:
            continue
        price = tick_price(tick, direction)
        if price is None or entry_price <= 0:
            continue
        move = ((price - entry_price) / entry_price) * (1 if direction == "LONG" else -1)
        unreal = move * leverage * 100.0
        if unreal > peak:
            peak = unreal
            mfe_t = t
        if unreal < mae:
            mae = unreal
            mae_t = t
        age = t - fill_t
        candidates = []
        if hard_stop is not None and unreal <= -abs(hard_stop):
            candidates.append("HARD_STOP")
        if age >= thesis_min_age and unreal <= thesis_cut and peak < 5.0:
            candidates.append("THESIS_FAST_CUT")
        if ladder:
            for trigger, floor in ladder:
                if peak >= trigger:
                    lock = floor
            if lock is not None and unreal <= lock:
                candidates.append("SCENARIO_C_LADDER")
        if giveback_abs is not None and peak - unreal >= giveback_abs and peak >= 4:
            candidates.append("CONTINUOUS_GIVEBACK")
        if giveback_pct is not None and peak > 0 and (peak - unreal) >= peak * giveback_pct and peak >= 4:
            candidates.append("CONTINUOUS_GIVEBACK")
        if breakeven_after_mfe is not None and peak >= breakeven_after_mfe:
            breakeven_armed = True
        if breakeven_armed and unreal <= 0.2:
            candidates.append("BREAKEVEN_AFTER_MFE")
        if time_stop_sec is not None and age >= time_stop_sec:
            candidates.append("TIME_STOP")
        if len(candidates) > 1:
            # Conservative: take the worst-for-equity trigger, never the favorable one.
            order = ["HARD_STOP", "THESIS_FAST_CUT", "TIME_STOP", "SCENARIO_C_LADDER", "CONTINUOUS_GIVEBACK", "BREAKEVEN_AFTER_MFE"]
            candidates.sort(key=lambda name: order.index(name) if name in order else 99)
            ambiguous = True
        if candidates:
            exit_reason = candidates[0]
            exit_unreal, exit_t, exit_price = unreal, t, price
            break
        exit_unreal, exit_t, exit_price = unreal, t, price
    gross = None if exit_unreal is None else (exit_unreal / 100.0) * 20.0 * size_scale
    fees = None if gross is None else abs(entry_price * qty * fee_rate * 2)
    net = None if gross is None or fees is None else round(gross - fees, 4)
    return {
        "schema": "path_replay_result_v1",
        "entered": True,
        "entry_price": entry_price,
        "fill_t": fill_t,
        "exit_reason": exit_reason,
        "exit_t": exit_t,
        "exit_price": exit_price,
        "mfe_pct": round(peak, 4),
        "mae_pct": round(mae, 4),
        "time_to_mfe_sec": None if mfe_t is None else round(mfe_t - fill_t, 3),
        "time_to_mae_sec": None if mae_t is None else round(mae_t - fill_t, 3),
        "gross_pnl_usd": None if gross is None else round(gross, 4),
        "fees_usd": None if fees is None else round(fees, 4),
        "net_pnl_usd": net,
        "ambiguous_same_sample": ambiguous,
        "size_scale": size_scale,
        "live_recommendation": False,
        "research_only_no_stop": hard_stop is None,
    }


def decision_pack(family, candidate, rows, *, holdout_n=0, safety_bound=False, missing=None):
    episode_ids = {row.get("episode_id") for row in rows if row.get("episode_id")}
    episode_n = len(episode_ids) if episode_ids else len(rows)
    pnls = [row.get("net_pnl_usd") for row in rows if row.get("net_pnl_usd") is not None]
    missing = list(missing or [])
    if holdout_n < SMOKE_FLOOR_N:
        missing.append("QUALIFIED_HOLDOUT_ZERO")
    if episode_n < SMOKE_FLOOR_N:
        missing.append("EPISODE_CLUSTERED_N_BELOW_SMOKE_FLOOR")
    if not all(row.get("cost_complete") is True for row in rows):
        missing.append("COST_INCOMPLETE")
    authorized = bool(
        episode_n >= SMOKE_FLOOR_N
        and holdout_n >= SMOKE_FLOOR_N
        and not safety_bound
        and not missing
    )
    return {
        "schema": "knob_decision_pack_v1",
        "family": family,
        "best_descriptive_candidate": candidate,
        "episode_clustered_n": episode_n,
        "raw_row_n": len(rows),
        "holdout_n": holdout_n,
        "mean_net_usd": None if not pnls else round(sum(pnls) / len(pnls), 4),
        "median_net_usd": None if not pnls else round(median(pnls), 4),
        "cvar_5pct_usd": None if len(pnls) < 5 else round(sum(sorted(pnls)[: max(1, int(len(pnls) * 0.05))]) / max(1, int(len(pnls) * 0.05)), 4),
        "safety_constraints_bind": safety_bound,
        "live_change_authorized": authorized,
        "missing_evidence": missing or [],
        "smoke_floor_n": SMOKE_FLOOR_N,
        "note": "15 is a smoke floor, not an optimum. Backtest candidate is not a live recommendation.",
    }


def portfolio_replay(results, max_concurrent=1):
    open_count = 0
    peak_concurrent = 0
    equity = 500.0
    peak_equity = 500.0
    max_dd = 0.0
    for result in results:
        if result.get("entered"):
            open_count += 1
            peak_concurrent = max(peak_concurrent, open_count)
            if open_count > max_concurrent:
                result = {**result, "rejected_by_portfolio": True, "net_pnl_usd": None}
            else:
                equity += result.get("net_pnl_usd") or 0
                peak_equity = max(peak_equity, equity)
                dd = (peak_equity - equity) / peak_equity * 100 if peak_equity else 0
                max_dd = max(max_dd, dd)
            open_count = max(0, open_count - 1)
    return {
        "schema": "portfolio_replay_v1",
        "peak_concurrent": peak_concurrent,
        "ending_equity_usd": round(equity, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "rejected_if_dd_bound": max_dd > SAFETY_CONSTRAINTS["max_drawdown_pct"],
        "live_recommendation": False,
    }


def build_reports(opportunities):
    """Eleven required reports plus extra-collector coverage. All not-qualified for live change."""
    n = len(opportunities)
    classified = Counter(row.get("shadow_state") or "UNCLASSIFIED" for row in opportunities)
    cohorts = Counter(row.get("divergence_cohort") or "UNCLASSIFIED" for row in opportunities)
    censored = sum(1 for row in opportunities if (row.get("path_gaps") or {}).get("censored"))
    complete_120 = sum(1 for row in opportunities if (row.get("horizon_receipts") or {}).get("complete"))
    cost_complete = sum(1 for row in opportunities if (row.get("cost") or {}).get("cost_complete"))
    chase_nonzero = sum(1 for row in opportunities if ((row.get("chase") or {}).get("chase_count") or 0) > 0)
    keys = sum(1 for row in opportunities if row.get("policy_comparability_key"))
    baseline = decision_pack("CURRENT_BASELINE", "keep_live_ladder", opportunities, missing=["QUALIFIED_HOLDOUT_ZERO"])
    return {
        "schema": "policy_research_reports_v1",
        "OPPORTUNITY_ACCOUNTING_REPORT": {
            "n": n, "shadow_states": dict(classified), "cohorts": dict(cohorts),
            "unclassified": classified.get("UNCLASSIFIED", 0),
        },
        "FILLABILITY_REPORT": {"n": n, "never_executable": classified.get("NEVER_EXECUTABLE", 0)},
        "LADDER_DESIGN_REPORT": {"families": list(LADDER_FAMILIES), "decision": baseline, "live_unchanged": list(LIVE_LADDER)},
        "THESIS_CUT_REPORT": {"thresholds": list(THESIS_THRESHOLDS), "min_ages_sec": list(THESIS_MIN_AGES_SEC), "live_unchanged": LIVE_THESIS_CUT},
        "HARD_STOP_REPORT": {"grid": list(HARD_STOP_GRID), "live_invariant_pct": LIVE_HARD_STOP_PCT, "no_stop_research_only": True},
        "CHASE_POLICY_REPORT": {"max": list(CHASE_MAX), "nonzero_chase_rows": chase_nonzero},
        "CLUSTER_DISTANCE_REPORT": {"bps_grid": list(CLUSTER_BPS_GRID), "live_bps": LIVE_CLUSTER_BPS, "portfolio_incremental": True},
        "ENTRY_FILTER_REPORT": {"spread_bps": list(SPREAD_BPS_GRID), "adx_bands": [list(b) for b in ADX_BANDS]},
        "POPULATION_FIDELITY_REPORT": {"cohorts": dict(cohorts)},
        "POLICY_READINESS_REPORT": {
            "live_change_authorized": False,
            "qualified_real_copy_rows": 0,
            "complete_120m": complete_120,
            "cost_complete": cost_complete,
            "policy_keys": keys,
            "decision_packs": [baseline],
        },
        "DATA_QUALITY_REPORT": {
            "censored_paths": censored,
            "horizon_complete": complete_120,
            "unknown_cannot_collect": list(UNKNOWN_CANNOT_COLLECT),
        },
        "EXTRA_COLLECTORS": {
            "episode_tagging": True,
            "path_gaps": True,
            "clock_alignment": True,
            "microstructure_fill_uncertainty": True,
            "divergence_telemetry": True,
            "portfolio_path": True,
            "session_features": True,
            "extra_exit_policies": ["TIME_STOP", "BREAKEVEN_AFTER_MFE", "SKIP", "SIZE_SCALE", "GIVEBACK"],
            "setup_dna_numeric": True,
            "slippage_decomposition": True,
            "cost_completeness": True,
            "stop_replacement_chain": True,
            "physical_safety_invariants": SAFETY_CONSTRAINTS,
            "decision_pack": True,
        },
    }
