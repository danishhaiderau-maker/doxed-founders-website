"""Paper-only orig-offset touch grid. Does not place extra live orders.

Live orig stays 0.1% (DETERMINISTIC_ENTRY_OFFSET_PCT=0.001). This module logs
whether price *touched* hypothetical limits at 0.01%…0.30% using the same
rule as bot._pending_limit_touched:

  SHORT: bid >= limit OR max_high >= limit OR last >= limit
  LONG:  ask <= limit OR min_low <= limit OR last <= limit
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Optional, Sequence


TOUCH_GRID_SCHEMA = "chase_offset_touch_grid_v1"
TOUCH_GRID_FILE = "chase_offset_touch_grid.jsonl"
LIVE_ORIG_OFFSET_PCT = 0.10
OFFSET_PCT_GRID = tuple(round(i * 0.01, 2) for i in range(1, 31))
TTL_SEC_DEFAULT = 1800
CHASE_WINDOW_SEC = 300
NEAR_FILL_PCT = 0.001
NEAR_FILL_USD = 10.0
LIVE_LEVERAGE = 100.0
DEFAULT_MARGIN_USDT = 0.25
LIVE_THESIS_CUT = -12.0
LADDER_4_2 = (4.0, 2.0)

# Signed, shadow-only schedule.  These constants must never be consumed by the
# executable paper-order or relay chase paths.
COMPRESSED_SHADOW_SCHEMA = "compressed_chase_shadow_v1"
COMPRESSED_SHADOW_POLICY_ID = "SHADOW_COMPRESSED_CHASE_0_1_2_4_7_10_EXP13_V1"
COMPRESSED_SHADOW_STAGE_SECONDS = (0, 60, 120, 240, 420, 600)
COMPRESSED_SHADOW_EXPIRY_SEC = 780
COMPRESSED_SHADOW_STEP_PCT = 0.25


def _compressed_policy_signature() -> str:
    material = json.dumps({
        "policy_id": COMPRESSED_SHADOW_POLICY_ID,
        "stage_seconds": COMPRESSED_SHADOW_STAGE_SECONDS,
        "expiry_sec": COMPRESSED_SHADOW_EXPIRY_SEC,
        "step_pct": COMPRESSED_SHADOW_STEP_PCT,
        "execution_class": "SHADOW_ONLY",
    }, sort_keys=True, separators=(",", ":"))
    return "policy-sha256-" + hashlib.sha256(material.encode("utf-8")).hexdigest()


COMPRESSED_SHADOW_POLICY_SIGNATURE = _compressed_policy_signature()


def arm_compressed_shadow_chase(
    *, trade_id: str, direction: str, signal_price: float, signal_ts: float,
    initial_limit_price: float, shared_ai_call_id: str, opportunity_id: str,
    episode_id: str, epoch_id: str, bid: Optional[float] = None,
    ask: Optional[float] = None, last: Optional[float] = None,
    bbo_fresh: bool = False,
) -> tuple[dict, dict]:
    """Create an auditable shadow schedule and its stage-0 receipt.

    The returned state has no order quantity, relay identity, or executable
    flag by design.  It is a counterfactual market-observation state only.
    """
    identities = {
        "shared_ai_call_id": str(shared_ai_call_id or ""),
        "opportunity_id": str(opportunity_id or ""),
        "episode_id": str(episode_id or ""),
        "policy_id": COMPRESSED_SHADOW_POLICY_ID,
        "policy_signature": COMPRESSED_SHADOW_POLICY_SIGNATURE,
        "epoch_id": str(epoch_id or ""),
    }
    missing = [key for key in (
        "shared_ai_call_id", "opportunity_id", "episode_id", "epoch_id",
    ) if not identities[key]]
    generation_material = (
        f"{COMPRESSED_SHADOW_POLICY_SIGNATURE}|{trade_id}|{float(signal_ts):.6f}|"
        f"{str(direction or '').upper()}"
    )
    schedule_generation_id = "shadow-generation-" + hashlib.sha256(
        generation_material.encode("utf-8")
    ).hexdigest()
    state = {
        "schema": COMPRESSED_SHADOW_SCHEMA,
        "execution_class": "SHADOW_ONLY",
        "places_order": False,
        "relay_eligible": False,
        "trade_id": str(trade_id),
        "direction": str(direction or "").upper(),
        "signal_price": float(signal_price),
        "signal_ts": float(signal_ts),
        "expires_ts": float(signal_ts) + COMPRESSED_SHADOW_EXPIRY_SEC,
        "virtual_limit_price": float(initial_limit_price),
        "next_stage_index": 1,
        "terminal_emitted": False,
        "seen_stage_indexes": {0},
        "identity_complete": not missing,
        "missing_identity_fields": missing,
        "schedule_generation_id": schedule_generation_id,
        "tape_evidence_path": "market_microstructure_1s.jsonl",
        **identities,
    }
    return state, _compressed_shadow_receipt(
        state, event="STAGE", stage_index=0, observed_ts=float(signal_ts),
        reference_price=float(signal_price), bid=bid, ask=ask,
        last=float(last if last not in (None, 0) else signal_price),
        bbo_fresh=bbo_fresh, direction_revalidation_result="VALID_AT_SIGNAL",
        direction_revalidation_reason="SIGNED_AI_DIRECTION_AT_SIGNAL",
    )


def _compressed_shadow_receipt(
    state: Mapping[str, Any], *, event: str, stage_index: Optional[int],
    observed_ts: float, reference_price: Optional[float], bid: Optional[float],
    ask: Optional[float], last: Optional[float], bbo_fresh: bool,
    direction_revalidation_result: str, direction_revalidation_reason: str,
    coverage_status: str = "OBSERVED",
) -> dict:
    scheduled_due_ts = (
        None if stage_index is None else
        float(state["signal_ts"]) + COMPRESSED_SHADOW_STAGE_SECONDS[stage_index]
    )
    bbo_valid = bool(
        bid not in (None, 0) and ask not in (None, 0)
        and float(ask) >= float(bid)
    )
    direction_valid = direction_revalidation_result in ("VALID", "VALID_AT_SIGNAL")
    eligible = bool(
        event == "STAGE" and state.get("identity_complete") and coverage_status == "OBSERVED"
        and bbo_fresh and bbo_valid and direction_valid
    )
    return {
        "schema": COMPRESSED_SHADOW_SCHEMA,
        "event": event,
        "execution_class": "SHADOW_ONLY",
        "places_order": False,
        "relay_eligible": False,
        "trade_id": state["trade_id"],
        "direction": state["direction"],
        "shared_ai_call_id": state["shared_ai_call_id"],
        "opportunity_id": state["opportunity_id"],
        "episode_id": state["episode_id"],
        "epoch_id": state["epoch_id"],
        "policy_id": state["policy_id"],
        "policy_signature": state["policy_signature"],
        "schedule_generation_id": state["schedule_generation_id"],
        "identity_complete": bool(state.get("identity_complete")),
        "missing_identity_fields": list(state.get("missing_identity_fields") or []),
        "signal_price": float(state["signal_price"]),
        "signal_ts": float(state["signal_ts"]),
        "expires_ts": float(state["expires_ts"]),
        "tape_evidence_path": state.get("tape_evidence_path"),
        "tape_window_start_ts": float(state["signal_ts"]),
        "tape_window_end_ts": float(state["expires_ts"]),
        "stage_index": stage_index,
        "stage_due_sec": None if stage_index is None else COMPRESSED_SHADOW_STAGE_SECONDS[stage_index],
        "observed_ts": float(observed_ts),
        "scheduled_due_ts": scheduled_due_ts,
        "observed_delay_sec": None if scheduled_due_ts is None else max(0.0, float(observed_ts) - scheduled_due_ts),
        "virtual_limit_price": float(state["virtual_limit_price"]),
        "reference_price": reference_price,
        "bbo": {"bid": bid, "ask": ask, "last": last},
        "bbo_fresh": bool(bbo_fresh),
        "bbo_valid": bbo_valid,
        "coverage_status": coverage_status,
        "direction_revalidation_result": direction_revalidation_result,
        "direction_revalidation_reason": direction_revalidation_reason,
        "eligible_at_stage": eligible,
        "schedule_seconds": list(COMPRESSED_SHADOW_STAGE_SECONDS),
        "terminal_expiry_sec": COMPRESSED_SHADOW_EXPIRY_SEC,
    }


def poll_compressed_shadow_chase(
    state: dict, *, now_ts: float, last: Optional[float],
    bid: Optional[float] = None, ask: Optional[float] = None,
    bbo_fresh: bool = False,
    direction_revalidation_result: str = "NOT_REVALIDATED",
    direction_revalidation_reason: str = "NO_FILL_TIME_DIRECTION_RECEIPT",
    max_observation_delay_sec: float = 15.0,
) -> list[dict]:
    """Advance due virtual stages using observed BBO; never submit an order."""
    if not state or state.get("terminal_emitted"):
        return []
    now = float(now_ts)
    market = last
    if market in (None, 0):
        market = ask if state.get("direction") == "LONG" else bid
    out: list[dict] = []
    seen = set(state.get("seen_stage_indexes") or {0})
    next_idx = 1
    while next_idx < len(COMPRESSED_SHADOW_STAGE_SECONDS):
        if next_idx in seen:
            next_idx += 1
            continue
        due_ts = float(state["signal_ts"]) + COMPRESSED_SHADOW_STAGE_SECONDS[next_idx]
        if now < due_ts:
            break
        delay = now - due_ts
        observed = delay <= float(max_observation_delay_sec)
        if observed and market not in (None, 0):
            state["virtual_limit_price"] = _chase_target(
                state["direction"], float(state["virtual_limit_price"]),
                float(market), COMPRESSED_SHADOW_STEP_PCT,
            )
        out.append(_compressed_shadow_receipt(
            state, event="STAGE", stage_index=next_idx, observed_ts=now,
            reference_price=None if not observed or market in (None, 0) else float(market),
            bid=bid if observed else None, ask=ask if observed else None,
            last=last if observed else None, bbo_fresh=bbo_fresh if observed else False,
            direction_revalidation_result=direction_revalidation_result,
            direction_revalidation_reason=direction_revalidation_reason,
            coverage_status="OBSERVED" if observed else "COVERAGE_GAP_OVERDUE",
        ))
        seen.add(next_idx)
        next_idx += 1
    state["seen_stage_indexes"] = seen
    state["next_stage_index"] = next_idx
    if now >= float(state["expires_ts"]):
        state["terminal_emitted"] = True
        out.append(_compressed_shadow_receipt(
            state, event="EXPIRED", stage_index=None, observed_ts=now,
            reference_price=None if market in (None, 0) else float(market),
            bid=bid, ask=ask, last=last, bbo_fresh=bbo_fresh,
            direction_revalidation_result=direction_revalidation_result,
            direction_revalidation_reason=direction_revalidation_reason,
        ))
    return out


def recover_compressed_shadow_states(
    receipts: Iterable[Mapping[str, Any]], *, now_ts: float,
) -> dict[str, dict]:
    """Recover non-terminal signed shadow states without replaying receipts."""
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in receipts or []:
        if row.get("schema") != COMPRESSED_SHADOW_SCHEMA:
            continue
        if row.get("policy_signature") != COMPRESSED_SHADOW_POLICY_SIGNATURE:
            continue
        grouped.setdefault(str(row.get("trade_id") or ""), []).append(row)
    recovered: dict[str, dict] = {}
    for trade_id, rows in grouped.items():
        if not trade_id or any(row.get("event") == "EXPIRED" for row in rows):
            continue
        rows = sorted(rows, key=lambda row: float(row.get("observed_ts") or 0))
        latest = rows[-1]
        if float(latest.get("expires_ts") or 0) <= float(now_ts):
            # The runtime will emit exactly one terminal receipt on its next poll.
            pass
        seen = {int(row["stage_index"]) for row in rows if row.get("stage_index") is not None}
        missing = list(latest.get("missing_identity_fields") or [])
        recovered[trade_id] = {
            "schema": COMPRESSED_SHADOW_SCHEMA,
            "execution_class": "SHADOW_ONLY", "places_order": False,
            "relay_eligible": False, "trade_id": trade_id,
            "direction": latest.get("direction"),
            "signal_price": float(latest.get("signal_price") or 0),
            "signal_ts": float(latest.get("signal_ts") or 0),
            "expires_ts": float(latest.get("expires_ts") or 0),
            "virtual_limit_price": float(latest.get("virtual_limit_price") or 0),
            "seen_stage_indexes": seen, "next_stage_index": 1,
            "terminal_emitted": False,
            "identity_complete": not missing,
            "missing_identity_fields": missing,
            "schedule_generation_id": latest.get("schedule_generation_id") or "",
            "tape_evidence_path": latest.get("tape_evidence_path") or "",
            **{key: latest.get(key) or "" for key in (
                "shared_ai_call_id", "opportunity_id", "episode_id", "epoch_id",
                "policy_id", "policy_signature",
            )},
        }
    return recovered


def offset_pct_to_frac(offset_pct: float) -> float:
    return float(offset_pct) / 100.0


def orig_limit_price(signal_price: float, direction: str, offset_pct: float) -> float:
    price = float(signal_price)
    frac = offset_pct_to_frac(offset_pct)
    if str(direction or "").upper() == "LONG":
        return price * (1.0 - frac)
    return price * (1.0 + frac)


def pending_limit_touched(
    *,
    side: str,
    limit_price: float,
    last: Optional[float] = None,
    high: Optional[float] = None,
    low: Optional[float] = None,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
) -> bool:
    """Mirror of bot._pending_limit_touched without grabbing state_lock."""
    limit = float(limit_price)
    side_u = str(side or "").lower()
    if side_u in ("buy", "long"):
        if ask is not None and float(ask) > 0 and float(ask) <= limit:
            return True
        if low is not None and float(low) <= limit:
            return True
        if last is not None and float(last) <= limit:
            return True
        return False
    if side_u in ("sell", "short"):
        if bid is not None and float(bid) > 0 and float(bid) >= limit:
            return True
        if high is not None and float(high) >= limit:
            return True
        if last is not None and float(last) >= limit:
            return True
        return False
    return False


def candle_ts_sec(row: Sequence[Any]) -> Optional[float]:
    if not row:
        return None
    try:
        raw = float(row[0])
    except (TypeError, ValueError):
        return None
    return raw / 1000.0 if raw > 1e12 else raw


def candles_in_window(
    candles_1m: Sequence[Sequence[Any]],
    start_ts: float,
    end_ts: float,
) -> list:
    out = []
    for row in candles_1m or []:
        ts = candle_ts_sec(row)
        if ts is None:
            continue
        if start_ts - 1e-9 <= ts <= end_ts + 1e-9:
            out.append(row)
    return out


def _gap(direction: str, limit_price: float, market: float) -> float:
    if str(direction or "").upper() == "LONG":
        return float(market) - float(limit_price)
    return float(limit_price) - float(market)


def _chase_target(direction: str, current_limit: float, market: float, step_pct: float) -> float:
    gap = _gap(direction, current_limit, market)
    if gap <= 0:
        return current_limit
    if gap <= NEAR_FILL_USD or (gap / max(market, 1.0)) <= NEAR_FILL_PCT:
        return current_limit
    step = float(step_pct) * gap
    if str(direction or "").upper() == "LONG":
        new_limit = min(current_limit + step, market)
        return max(new_limit, current_limit)
    new_limit = max(current_limit - step, market)
    return min(new_limit, current_limit)


def chase_window_index(age_sec: float) -> int:
    idx = int(max(0.0, float(age_sec)) // CHASE_WINDOW_SEC)
    return 5 if idx >= 5 else idx


def simulate_touch_fill(
    candles_1m: Sequence[Sequence[Any]],
    *,
    signal_ts: float,
    signal_price: float,
    direction: str,
    offset_pct: float,
    ttl_sec: float = TTL_SEC_DEFAULT,
    chase: Optional[Mapping[str, Any]] = None,
    ticks_1s: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict:
    """Walk 1m high/low (and 1s last if present). Fill only on actual touch."""
    direction_u = str(direction or "SHORT").upper()
    side = "sell" if direction_u == "SHORT" else "buy"
    limit = orig_limit_price(signal_price, direction_u, offset_pct)
    original = limit
    end_ts = float(signal_ts) + float(ttl_sec)
    last_chase_ts = float(signal_ts)
    chase = dict(chase or {})
    no_chase = bool(chase.get("no_chase"))
    step_pct = float(chase.get("step_pct") or 0.25)
    interval_sec = float(chase.get("interval_sec") or 180)
    windows = chase.get("windows")
    if windows is not None:
        windows = {int(w) for w in windows}

    # This is the authoritative history of the limits actually used below.
    # Consumers must persist this result instead of reconstructing a schedule
    # from policy metadata after the simulation has finished.
    chase_schedule = [{
        "chase_step_index": 0,
        "active_from_ts": float(signal_ts),
        "active_until_ts": None,
        "reference_price": float(signal_price),
        "limit_price": float(limit),
        "offset_pct": float(offset_pct),
        "reason": "INITIAL_LIMIT",
    }]

    def close_schedule(at_ts: float) -> None:
        if chase_schedule[-1]["active_until_ts"] is None:
            chase_schedule[-1]["active_until_ts"] = float(at_ts)

    def maybe_chase(now_ts: float, market: float) -> None:
        nonlocal limit, last_chase_ts
        if no_chase or not windows:
            return
        age = now_ts - float(signal_ts)
        if chase_window_index(age) not in windows:
            return
        if now_ts - last_chase_ts < interval_sec:
            return
        new_limit = _chase_target(direction_u, limit, market, step_pct)
        if abs(new_limit - limit) >= 0.01:
            close_schedule(now_ts)
            limit = new_limit
            last_chase_ts = now_ts
            chase_schedule.append({
                "chase_step_index": len(chase_schedule),
                "active_from_ts": float(now_ts),
                "active_until_ts": None,
                "reference_price": float(market),
                "limit_price": float(limit),
                "offset_pct": float(offset_pct),
                "reason": "CHASE_INTERVAL",
            })

    latest_tick_ts = None
    if ticks_1s:
        for tick in sorted(ticks_1s, key=lambda row: float(row.get("t") or 0)):
            t = float(tick.get("t") or 0)
            if t < signal_ts or t > end_ts:
                continue
            latest_tick_ts = t if latest_tick_ts is None else max(latest_tick_ts, t)
            last = tick.get("price")
            bid = tick.get("best_bid") or tick.get("bid")
            ask = tick.get("best_ask") or tick.get("ask")
            high = tick.get("high")
            low = tick.get("low")
            if pending_limit_touched(
                side=side,
                limit_price=limit,
                last=None if last is None else float(last),
                high=None if high is None else float(high),
                low=None if low is None else float(low),
                bid=None if bid in (None, 0) else float(bid),
                ask=None if ask in (None, 0) else float(ask),
            ):
                close_schedule(t)
                return {
                    "touched": True,
                    "touch_ts": t,
                    "fill_price": float(limit),
                    "offset_pct": float(offset_pct),
                    "source": "1s",
                    "chase_schedule": chase_schedule,
                }
            if last is not None:
                maybe_chase(t, float(last))

    window = candles_in_window(candles_1m, signal_ts - 60, end_ts)
    for row in window:
        t = candle_ts_sec(row)
        if t is None or t + 60 < signal_ts:
            continue
        if t > end_ts:
            continue
        # A 1m OHLC bar overlapping a consumed 1s observation would otherwise
        # rewind the simulation and could invent a touch before a chase already
        # recorded from the higher-resolution stream.
        if latest_tick_ts is not None and t <= latest_tick_ts:
            continue
        high = float(row[2])
        low = float(row[3])
        close = float(row[4])
        if pending_limit_touched(
            side=side, limit_price=limit, last=close, high=high, low=low,
        ):
            fill_ts = max(t, float(signal_ts))
            close_schedule(fill_ts)
            return {
                "touched": True,
                "touch_ts": fill_ts,
                "fill_price": float(limit),
                "offset_pct": float(offset_pct),
                "source": "1m",
                "chase_schedule": chase_schedule,
            }
        maybe_chase(t + 60.0, close)
    close_schedule(end_ts)
    return {
        "touched": False,
        "touch_ts": None,
        "fill_price": None,
        "offset_pct": float(offset_pct),
        "source": "1m",
        "resting_limit": float(limit),
        "original_limit": float(original),
        "chase_schedule": chase_schedule,
    }


def unrealized_margin_pct(direction: str, entry: float, mark: float, leverage: float = LIVE_LEVERAGE) -> float:
    side = 1.0 if str(direction or "").upper() == "LONG" else -1.0
    return ((float(mark) - float(entry)) / float(entry)) * side * float(leverage) * 100.0


def simulate_exits_on_1m(
    candles_1m: Sequence[Sequence[Any]],
    *,
    direction: str,
    entry_price: float,
    fill_ts: float,
    atr14_pct: Optional[float] = None,
    hold_sec: float = 7200.0,
    margin_usdt: float = DEFAULT_MARGIN_USDT,
) -> dict:
    """Pessimistic intra-bar: SHORT sees high before low. Thesis −12 vs ladder 4→2 vs ATR k=1."""
    direction_u = str(direction or "SHORT").upper()
    end_ts = float(fill_ts) + float(hold_sec)
    thesis = LIVE_THESIS_CUT
    lock_trigger, lock_level = LADDER_4_2
    atr_target = None if atr14_pct is None else float(atr14_pct) * float(LIVE_LEVERAGE)

    def walk(exit_mode: str) -> dict:
        peak = 0.0
        mae = 0.0
        locked = False
        exit_reason = "PATH_END"
        exit_t = end_ts
        exit_unreal = None
        exit_price = None
        for row in candles_in_window(candles_1m, fill_ts, end_ts):
            t = candle_ts_sec(row)
            if t is None or t < fill_ts:
                continue
            high, low, close = float(row[2]), float(row[3]), float(row[4])
            if direction_u == "SHORT":
                marks = (high, low, close)
            else:
                marks = (low, high, close)
            for mark in marks:
                unreal = unrealized_margin_pct(direction_u, entry_price, mark)
                if unreal > peak:
                    peak = unreal
                if unreal < mae:
                    mae = unreal
                hit = None
                if unreal <= thesis:
                    hit = "THESIS_FAST_CUT"
                elif exit_mode == "ladder":
                    if peak >= lock_trigger:
                        locked = True
                    if locked and unreal <= lock_level:
                        hit = "PROFIT_LOCK_LADDER"
                elif exit_mode == "atr_k1" and atr_target is not None and unreal >= atr_target:
                    hit = "ATR_TAKE_PROFIT"
                if hit:
                    exit_reason = hit
                    exit_t = t
                    exit_unreal = unreal
                    exit_price = mark
                    return _exit_row(
                        exit_reason, exit_t, exit_unreal, exit_price, peak, mae, margin_usdt,
                    )
            exit_unreal = unrealized_margin_pct(direction_u, entry_price, close)
            exit_price = close
            exit_t = t
        if exit_unreal is None:
            exit_unreal = 0.0
            exit_price = entry_price
        return _exit_row(exit_reason, exit_t, exit_unreal, exit_price, peak, mae, margin_usdt)

    return {
        "thesis_or_ladder_4_2": walk("ladder"),
        "atr_k1": walk("atr_k1") if atr_target is not None else {
            "exit_reason": "NO_ATR",
            "net_pnl_usd": None,
        },
    }


def _exit_row(reason, t, unreal, price, peak, mae, margin_usdt) -> dict:
    pnl = round(float(margin_usdt) * float(unreal) / 100.0, 4)
    return {
        "exit_reason": reason,
        "exit_ts": t,
        "exit_price": price,
        "exit_unreal_pct": round(float(unreal), 4),
        "peak_mfe_pct": round(float(peak), 4),
        "trough_mae_pct": round(float(mae), 4),
        "net_pnl_usd": pnl,
        "green": pnl > 0,
    }


def arm_touch_grid_rows(
    *,
    trade_id: str,
    direction: str,
    signal_price: float,
    signal_ts: float,
    ttl_sec: float = TTL_SEC_DEFAULT,
    live_offset_pct: float = LIVE_ORIG_OFFSET_PCT,
    invert_on: bool = False,
) -> list:
    """One JSONL row per offset at APPROVE/TAKEN. touched starts false."""
    rows = []
    for offset_pct in OFFSET_PCT_GRID:
        rows.append({
            "schema": TOUCH_GRID_SCHEMA,
            "event": "ARMED",
            "trade_id": trade_id,
            "direction": str(direction or "").upper(),
            "signal_price": float(signal_price),
            "signal_ts": float(signal_ts),
            "expires_ts": float(signal_ts) + float(ttl_sec),
            "offset_pct": offset_pct,
            "limit_price": orig_limit_price(signal_price, direction, offset_pct),
            "touched": False,
            "touch_ts": None,
            "would_fill_price": None,
            "live_orig_offset_pct": float(live_offset_pct),
            "places_live_order": offset_pct == float(live_offset_pct),
            "invert_on": bool(invert_on),
            "fill_model": "IDEAL_TOUCH",
            "note": "path-touch simulation only; one live 0.1% order",
        })
    return rows


def new_grid_state(rows: Sequence[Mapping[str, Any]]) -> dict:
    if not rows:
        return {}
    first = rows[0]
    offsets = {}
    for row in rows:
        offsets[float(row["offset_pct"])] = {
            "limit_price": float(row["limit_price"]),
            "touched": False,
            "touch_ts": None,
        }
    return {
        "trade_id": first["trade_id"],
        "direction": first["direction"],
        "signal_price": first["signal_price"],
        "signal_ts": first["signal_ts"],
        "expires_ts": first["expires_ts"],
        "invert_on": bool(first.get("invert_on", False)),
        "offsets": offsets,
    }


def poll_grid_state(
    state: dict,
    *,
    now_ts: float,
    last: Optional[float],
    high: Optional[float] = None,
    low: Optional[float] = None,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
) -> list:
    """Return newly touched overlay rows. Never places orders."""
    if not state or now_ts > float(state.get("expires_ts") or 0):
        return []
    direction = state.get("direction") or "SHORT"
    side = "sell" if str(direction).upper() == "SHORT" else "buy"
    out = []
    for offset_pct, slot in state["offsets"].items():
        if slot["touched"]:
            continue
        if pending_limit_touched(
            side=side,
            limit_price=slot["limit_price"],
            last=last,
            high=high,
            low=low,
            bid=bid,
            ask=ask,
        ):
            slot["touched"] = True
            slot["touch_ts"] = now_ts
            out.append({
                "schema": TOUCH_GRID_SCHEMA,
                "event": "TOUCHED",
                "trade_id": state["trade_id"],
                "direction": direction,
                "offset_pct": offset_pct,
                "limit_price": slot["limit_price"],
                "touched": True,
                "touch_ts": now_ts,
                "would_fill_price": slot["limit_price"],
                "invert_on": bool(state.get("invert_on", False)),
                "last": last,
                "high": high,
                "low": low,
                "bid": bid,
                "ask": ask,
            })
    return out


CHASE_POLICIES = (
    {"id": "no_chase", "no_chase": True, "windows": set(), "step_pct": 0.0, "interval_sec": 180},
    {"id": "w234_s25_i180", "windows": {2, 3, 4}, "step_pct": 0.25, "interval_sec": 180},
    {"id": "w234_s10_i180", "windows": {2, 3, 4}, "step_pct": 0.10, "interval_sec": 180},
    {"id": "w234_s50_i180", "windows": {2, 3, 4}, "step_pct": 0.50, "interval_sec": 180},
    {"id": "w234_s25_i60", "windows": {2, 3, 4}, "step_pct": 0.25, "interval_sec": 60},
    {"id": "w01_on_s25_i180", "windows": {0, 1, 2, 3, 4}, "step_pct": 0.25, "interval_sec": 180},
    {"id": "w5plus_on_s25_i180", "windows": {2, 3, 4, 5}, "step_pct": 0.25, "interval_sec": 180},
    {"id": "all_on_s25_i180", "windows": {0, 1, 2, 3, 4, 5}, "step_pct": 0.25, "interval_sec": 180},
    {"id": "all_on_s10_i60", "windows": {0, 1, 2, 3, 4, 5}, "step_pct": 0.10, "interval_sec": 60},
    {"id": "all_on_s50_i60", "windows": {0, 1, 2, 3, 4, 5}, "step_pct": 0.50, "interval_sec": 60},
)
