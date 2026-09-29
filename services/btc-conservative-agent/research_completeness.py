"""Evidence-only research completeness fields for paper lifecycles and shadows.

Every helper here is a pure projection of already-observed runtime state.  None
of them decides, submits, reprices, fills, cancels or closes an order, and none
of them can change a paper or live policy.  Missing evidence stays ``None`` and
is paired with an explicit reason; it is never replaced by a fabricated zero.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

COMPLETENESS_SCHEMA = "paper_lifecycle_completeness_v1"
SHADOW_COMPLETENESS_SCHEMA = "shadow_row_completeness_v1"
STOP_AXIS_SCHEMA = "hard_vs_atr_stop_counterfactual_v1"

NO_FILL_TTL_OUTCOMES = (
    "EXPIRED_NO_TOUCH",
    "EXPIRED_TOUCHED_NO_FILL",
    "EXPIRED_TOUCH_UNKNOWN",
    "CANCELLED_REVALIDATION",
    "CANCELLED_OTHER",
)
LIFECYCLE_COMPLETENESS_FIELDS = (
    "session_label", "mae_ts", "mfe_ts", "fill_revalidation_count",
    "no_fill_ttl_outcome", "exit_depth",
)
# The axis needs an ATR stop even for families whose own policy has none; the
# reference multiplier is labelled so it is never mistaken for tile policy.
STOP_AXIS_REFERENCE_ATR_K = 1.5
STOP_AXIS_REFERENCE_HARD_STOP_MARGIN_PCT = 30.0

_EXPIRY_TOKENS = ("TTL", "EXPIRE", "CHASE_WINDOW", "CHASE_GATE", "STALE")


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number > 0 else None


def _epoch_seconds(value: Any) -> float | None:
    number = _finite(value)
    if number is not None:
        return number / 1000.0 if number > 1e12 else number
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def session_label_for_ts(ts: Any) -> str | None:
    """ASIA [00,08) / EU [08,16) / US [16,24) UTC; unknown time stays None."""
    seconds = _epoch_seconds(ts)
    if seconds is None or seconds <= 0:
        return None
    hour = datetime.fromtimestamp(seconds, timezone.utc).hour
    return "ASIA" if hour < 8 else "EU" if hour < 16 else "US"


def track_path_extreme_timestamps(pos: dict, unreal_pct: Any, now: Any) -> None:
    """Remember when the observed unrealized margin reached a new extreme."""
    value, ts = _finite(unreal_pct), _finite(now)
    if not isinstance(pos, dict) or value is None or ts is None:
        return
    peak = _finite(pos.get("_mfe_track_value"))
    trough = _finite(pos.get("_mae_track_value"))
    if peak is None or value > peak:
        pos["_mfe_track_value"] = value
        pos["mfe_ts"] = ts
    if trough is None or value < trough:
        pos["_mae_track_value"] = value
        pos["mae_ts"] = ts


def _extreme_ts(pos: Mapping[str, Any], *, side: str) -> tuple[float | None, str]:
    """Pair the reported (entry-floored) MFE/MAE with the time it occurred."""
    reported_key = "max_pnl_pct" if side == "mfe" else "max_drawdown"
    tracked_value = _finite(pos.get(f"_{side}_track_value"))
    tracked_ts = _finite(pos.get(f"{side}_ts"))
    reported = _finite(pos.get(reported_key))
    entry_ts = _epoch_seconds(pos.get("entry_ts"))
    if tracked_ts is None or tracked_value is None:
        return None, "PATH_EXTREME_NOT_TRACKED"
    floored = (side == "mfe" and tracked_value < 0) or (side == "mae" and tracked_value > 0)
    if floored and reported is not None and reported == 0:
        # Runtime MFE/MAE are floored at the entry mark (0%), so the extreme
        # that the reported value describes occurred at the fill itself.
        if entry_ts is None:
            return None, "ENTRY_FLOOR_WITHOUT_ENTRY_TS"
        return entry_ts, "ENTRY_FLOOR"
    return tracked_ts, "OBSERVED_EXIT_WORKER_TICK"


def classify_no_fill_ttl_outcome(
    reason: Any, *, touched: Any = None, filled: bool = False,
) -> str | None:
    """Map an unfilled terminal reason to one bounded TTL outcome label."""
    if filled:
        return None
    text = str(reason or "").strip().upper()
    if text in {"FILLED", "PARTIAL_FILL_SIM_RESIDUAL_CANCELLED"}:
        return None
    if text.startswith("FILL_REVALIDATION"):
        return "CANCELLED_REVALIDATION"
    if any(token in text for token in _EXPIRY_TOKENS):
        if touched is True:
            return "EXPIRED_TOUCHED_NO_FILL"
        if touched is False:
            return "EXPIRED_NO_TOUCH"
        return "EXPIRED_TOUCH_UNKNOWN"
    return "CANCELLED_OTHER"


def exit_depth_context(exit_sim: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Order-book liquidity observed at the exit, or ``None`` plus a reason."""
    if not isinstance(exit_sim, Mapping) or not exit_sim:
        return None, "EXIT_DEPTH_SIMULATION_ABSENT"
    if exit_sim.get("book_empty"):
        return None, "EXIT_BOOK_EMPTY_BBO_FALLBACK"
    visible = _positive(exit_sim.get("filled_qty"))
    levels = exit_sim.get("levels_consumed")
    if visible is None or not isinstance(levels, int) or isinstance(levels, bool) or levels <= 0:
        return None, "EXIT_DEPTH_NOT_WALKED"
    return {
        "basis": "EXIT_DEPTH_WALK",
        "source": exit_sim.get("source") or exit_sim.get("book_source"),
        "book_observed_ts": exit_sim.get("book_observed_ts"),
        "best_executable_price": _positive(exit_sim.get("best_price")),
        "execution_vwap": _positive(exit_sim.get("avg_price")),
        "visible_executable_qty": visible,
        "levels_consumed": levels,
        "fully_filled": exit_sim.get("fully_filled") is True,
        "slippage_usd": _finite(exit_sim.get("slippage_usd")),
    }, None


def closed_lifecycle_completeness(
    pos: Mapping[str, Any], *, exit_sim: Any = None,
) -> dict[str, Any]:
    """Completeness fields for one filled-and-closed paper lifecycle."""
    pos = pos if isinstance(pos, Mapping) else {}
    entry_ts = _epoch_seconds(pos.get("entry_ts"))
    mfe_ts, mfe_basis = _extreme_ts(pos, side="mfe")
    mae_ts, mae_basis = _extreme_ts(pos, side="mae")
    depth, depth_reason = exit_depth_context(exit_sim)
    count = pos.get("fill_revalidation_count")
    count = count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None
    return {
        "lifecycle_completeness_schema": COMPLETENESS_SCHEMA,
        "session_label": session_label_for_ts(entry_ts),
        "session_label_basis": "ENTRY_FILL_UTC_HOUR" if entry_ts else "ENTRY_TS_MISSING",
        "mfe_ts": mfe_ts,
        "mfe_ts_basis": mfe_basis,
        "mae_ts": mae_ts,
        "mae_ts_basis": mae_basis,
        "fill_revalidation_count": count,
        "fill_revalidation_count_reason": None if count is not None else "ORDER_COUNTER_ABSENT",
        "no_fill_ttl_outcome": None,
        "no_fill_ttl_outcome_reason": "ORDER_FILLED",
        "exit_depth": depth,
        "exit_depth_unavailable_reason": depth_reason,
    }


def unfilled_lifecycle_completeness(
    source: Mapping[str, Any], *, reason: Any, touched: Any = None,
    created_ts: Any = None,
) -> dict[str, Any]:
    """Completeness fields for an order that ended without a fill."""
    source = source if isinstance(source, Mapping) else {}
    ts = _epoch_seconds(created_ts) or _epoch_seconds(source.get("created_ts"))
    if touched is None:
        touch_count = source.get("limit_touch_count")
        if isinstance(touch_count, int) and not isinstance(touch_count, bool) and touch_count > 0:
            touched = True
    count = source.get("fill_revalidation_count")
    count = count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None
    outcome = classify_no_fill_ttl_outcome(reason, touched=touched)
    return {
        "lifecycle_completeness_schema": COMPLETENESS_SCHEMA,
        "session_label": session_label_for_ts(ts),
        "session_label_basis": "ORDER_CREATED_UTC_HOUR" if ts else "ORDER_CREATED_TS_MISSING",
        "mfe_ts": None, "mfe_ts_basis": "NO_POSITION_OPENED",
        "mae_ts": None, "mae_ts_basis": "NO_POSITION_OPENED",
        "fill_revalidation_count": count,
        "fill_revalidation_count_reason": None if count is not None else "ORDER_COUNTER_ABSENT",
        "no_fill_ttl_outcome": outcome,
        "no_fill_ttl_outcome_reason": str(reason or "UNSPECIFIED")[:128],
        "exit_depth": None,
        "exit_depth_unavailable_reason": "NO_POSITION_OPENED",
    }


def completeness_projection(source: Mapping[str, Any]) -> dict[str, Any]:
    """Copy already-computed completeness fields (and reasons) from a row."""
    source = source if isinstance(source, Mapping) else {}
    if source.get("lifecycle_completeness_schema") != COMPLETENESS_SCHEMA:
        return {}
    keys = (
        "lifecycle_completeness_schema", "session_label", "session_label_basis",
        "mfe_ts", "mfe_ts_basis", "mae_ts", "mae_ts_basis",
        "fill_revalidation_count", "fill_revalidation_count_reason",
        "no_fill_ttl_outcome", "no_fill_ttl_outcome_reason",
        "exit_depth", "exit_depth_unavailable_reason",
    )
    return {key: source.get(key) for key in keys}


def _stop_hit(samples, *, stop_price: float, sign: float):
    for ts, price in samples:
        if (sign > 0 and price <= stop_price) or (sign < 0 and price >= stop_price):
            return ts, price
    return None


def hard_vs_atr_stop_counterfactual(
    path_rows: Iterable[Mapping[str, Any]], *, direction: Any, entry_price: Any,
    fill_ts: Any, leverage: Any, atr_abs: Any = None, atr_pct: Any = None,
    exit_policy: Mapping[str, Any] | None = None, actual_exit_price: Any = None,
    actual_close_ts: Any = None,
) -> dict[str, Any]:
    """Shadow-only: what a fixed hard stop vs the ATR stop would have done.

    The observed path ends at the actual exit, so a stop that did not trigger
    before it is reported as ``NOT_HIT_BEFORE_ACTUAL_EXIT`` and inherits the
    actual exit; nothing beyond the recorded path is extrapolated.
    """
    exit_policy = exit_policy if isinstance(exit_policy, Mapping) else {}
    entry, lev = _positive(entry_price), _positive(leverage)
    start = _epoch_seconds(fill_ts)
    side = str(direction or "").upper()
    base = {"schema": STOP_AXIS_SCHEMA, "shadow_only": True, "live_policy_effect": "NONE"}
    if entry is None or lev is None or start is None or side not in {"LONG", "SHORT"}:
        return {**base, "status": "INPUTS_INCOMPLETE", "hard_stop": None, "atr_stop": None}
    sign = 1.0 if side == "LONG" else -1.0
    samples = []
    for row in path_rows or ():
        if not isinstance(row, Mapping):
            continue
        ts = _epoch_seconds(row.get("ts") if row.get("ts") is not None else row.get("bucket_ts"))
        price = _positive(row.get("price") if row.get("price") is not None else row.get("last"))
        if ts is not None and price is not None and ts >= start:
            samples.append((ts, price))
    samples.sort()
    hard_margin = _positive(exit_policy.get("hard_stop_margin_pct"))
    hard_source = "TILE_EXIT_POLICY" if hard_margin else "AXIS_REFERENCE"
    hard_margin = hard_margin or STOP_AXIS_REFERENCE_HARD_STOP_MARGIN_PCT
    atr = _positive(atr_abs)
    if atr is None and _positive(atr_pct) is not None:
        atr = entry * float(atr_pct) / 100.0
    atr_k = _positive(exit_policy.get("initial_stop_atr_k"))
    atr_source = "TILE_EXIT_POLICY" if atr_k else "AXIS_REFERENCE"
    atr_k = atr_k or STOP_AXIS_REFERENCE_ATR_K
    actual_exit = _positive(actual_exit_price)

    def arm(stop_price: float | None, spec: dict[str, Any], missing: str | None):
        if stop_price is None:
            return {**spec, "status": missing, "stop_price": None}
        if not samples:
            return {**spec, "status": "PATH_UNAVAILABLE", "stop_price": round(stop_price, 2)}
        hit = _stop_hit(samples, stop_price=stop_price, sign=sign)
        exit_price = stop_price if hit else actual_exit
        move = ((exit_price - entry) / entry) * 100.0 * sign if exit_price else None
        return {
            **spec,
            "status": "HIT" if hit else "NOT_HIT_BEFORE_ACTUAL_EXIT",
            "stop_price": round(stop_price, 2),
            "hit_ts": hit[0] if hit else None,
            "exit_price": round(exit_price, 2) if exit_price else None,
            "price_return_pct": round(move, 6) if move is not None else None,
            "margin_return_pct": round(move * lev, 4) if move is not None else None,
        }

    hard_price = entry * (1.0 - sign * (hard_margin / lev) / 100.0)
    atr_price = entry - sign * atr * atr_k if atr else None
    hard = arm(hard_price, {"margin_pct": hard_margin, "parameter_source": hard_source}, None)
    atr_arm = arm(atr_price, {"atr_k": atr_k, "atr_abs": atr, "parameter_source": atr_source},
                  "ATR_UNAVAILABLE")
    first = None
    if hard.get("hit_ts") is not None or atr_arm.get("hit_ts") is not None:
        first = min(
            (("HARD", hard.get("hit_ts")), ("ATR", atr_arm.get("hit_ts"))),
            key=lambda item: item[1] if item[1] is not None else float("inf"),
        )[0]
    return {
        **base,
        "status": "COMPUTED" if samples else "PATH_UNAVAILABLE",
        "path_basis": "OBSERVED_1S_PRICE_PATH_TO_ACTUAL_EXIT",
        "path_sample_count": len(samples),
        "censored_at_actual_exit_ts": _epoch_seconds(actual_close_ts),
        "hard_stop": hard,
        "atr_stop": atr_arm,
        "first_trigger": first,
    }


def _cost_assumptions(buf: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    fee = buf.get("fee_model") if isinstance(buf.get("fee_model"), Mapping) else None
    execution = buf.get("execution_profile") if isinstance(buf.get("execution_profile"), Mapping) else None
    missing = []
    if not fee:
        missing.append("FEE_MODEL_MISSING")
    if not execution:
        missing.append("EXECUTION_PROFILE_MISSING")
    if missing:
        return None, missing
    material = {"fee_model": dict(fee), "execution_profile": dict(execution)}
    return {
        **material,
        "slippage_model": str(
            execution.get("executable_marks") or execution.get("slippage_model") or "UNSPECIFIED"
        ),
        "cost_assumptions_sha256": hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest(),
    }, []


def shadow_row_completeness(buf: Mapping[str, Any]) -> dict[str, Any]:
    """Identity, cost and depth context stamped on every shadow outcome row."""
    buf = buf if isinstance(buf, Mapping) else {}
    identity_source = buf.get("policy_identity") if isinstance(buf.get("policy_identity"), Mapping) else {}
    identity = {
        "tile_lane": str(buf.get("research_lane") or "").upper() or None,
        "policy_signature": buf.get("policy_signature") or identity_source.get("policy_signature"),
        "collection_epoch_id": (
            buf.get("collection_epoch_id") or identity_source.get("collection_epoch_id")
        ),
    }
    missing = [f"{key.upper()}_MISSING" for key, value in identity.items() if not value]
    costs, cost_missing = _cost_assumptions(buf)
    ticks = [tick for tick in (buf.get("ticks") or ()) if isinstance(tick, Mapping)]
    depth_rows = [
        tick for tick in ticks
        if _positive(tick.get("depth_bid_qty")) and _positive(tick.get("depth_ask_qty"))
    ]
    bid_qty = [float(tick["depth_bid_qty"]) for tick in depth_rows]
    ask_qty = [float(tick["depth_ask_qty"]) for tick in depth_rows]
    depth_context = {
        "basis": "REPLAY_TICK_TOP_OF_BOOK_QTY",
        "tick_count": len(ticks),
        "depth_tick_count": len(depth_rows),
        "min_top_bid_qty": min(bid_qty) if bid_qty else None,
        "min_top_ask_qty": min(ask_qty) if ask_qty else None,
        "median_top_bid_qty": sorted(bid_qty)[len(bid_qty) // 2] if bid_qty else None,
        "median_top_ask_qty": sorted(ask_qty)[len(ask_qty) // 2] if ask_qty else None,
        "reason": None if depth_rows else "NO_TOP_OF_BOOK_QTY_ON_REPLAY_TICKS",
    }
    return {
        "shadow_completeness_schema": SHADOW_COMPLETENESS_SCHEMA,
        "shadow_policy_identity": identity,
        "shadow_identity_status": "COMPLETE" if not missing else "INCOMPLETE",
        "shadow_identity_missing": missing,
        "cost_assumptions": costs,
        "cost_assumptions_status": "COMPLETE" if costs else "INCOMPLETE",
        "cost_assumptions_missing": cost_missing,
        "shadow_depth_context": depth_context,
        "shadow_depth_status": "TOP_OF_BOOK_QTY" if depth_rows else "UNAVAILABLE",
    }


def replay_tick_depth(state_view: Mapping[str, Any], *, now: Any, max_age_sec: float = 5.0) -> dict[str, Any] | None:
    """Fresh top-of-book quantities for one replay tick, else ``None``."""
    if not isinstance(state_view, Mapping):
        return None
    observed = _finite(state_view.get("bbo_ts"))
    current = _finite(now)
    bid_qty, ask_qty = _positive(state_view.get("bid_qty")), _positive(state_view.get("ask_qty"))
    if observed is None or current is None or bid_qty is None or ask_qty is None:
        return None
    if not -1.0 <= current - observed <= max_age_sec:
        return None
    return {
        "depth_bid_qty": bid_qty, "depth_ask_qty": ask_qty,
        "depth_best_bid": _positive(state_view.get("bid")),
        "depth_best_ask": _positive(state_view.get("ask")),
        "depth_observed_ts": observed,
    }
