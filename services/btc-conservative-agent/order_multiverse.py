"""Discrete multiverse of ONE live paper ticket.

Live ticket stays orig 0.1% / thesis −12 / 4→2 then Scenario C. This module
does not place extra orders. Each TAKEN/ORDER is one universe-anchor. We
simulate the discrete grid of that same signal's price path:

  orig 0.01%…0.30% (0.01) → touch-fill only (1m high tagged the limit)
  chase variants on actual path touches
  first-hit exits vs live 4→2+thesis−12, ATR, chandelier, structure, thesis grid, stops

TTL (~30m) is when an unfilled paper ticket COMPLETE's on the signal→expire
path. 120m is extra tape AFTER a simulated/real fill, not a delay before
scoring. COMPLETE the paper cont-* id; lab-hunter ids are not a substitute.

Storage: one compact JSON line per TAKEN in ``order_multiverse.jsonl``.
n=1 100% green is not a policy result.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Optional, Sequence

from chase_offset_touch_grid import (
    CHASE_POLICIES,
    DEFAULT_MARGIN_USDT,
    LIVE_ORIG_OFFSET_PCT,
    OFFSET_PCT_GRID,
    TTL_SEC_DEFAULT,
    candle_ts_sec,
    simulate_touch_fill,
)
from path_replay_v1 import (
    ATR_K_GRID,
    CHANDELIER_K_GRID,
    LIVE_EARLY_LADDER_4_2,
    LIVE_SCENARIO_C_LADDER,
    LIVE_THESIS_CUT,
    THESIS_4PP_GRID,
    first_hit_combo,
    simulate_atr_stop,
    simulate_atr_take_profit,
    simulate_chandelier_trail,
    simulate_policy_on_path,
    simulate_structure_stop,
    simulate_structure_take_profit,
)


ORDER_MULTIVERSE_SCHEMA = "order_multiverse_v1"
ORDER_MULTIVERSE_FILE = "order_multiverse.jsonl"
HOLD_SEC_DEFAULT = 7200.0
SCORE_CAP = 320
LIVE_CHASE_ID = "w234_s25_i180"
LAB_OR_HUNTER_PREFIXES = ("lab-", "tbh-")


def paper_multiverse_trade_id(*candidates: Any) -> str:
    """Paper cont-* wins. Lab-hunter ids never substitute for the paper ticket."""
    ids = [str(raw or "").strip() for raw in candidates]
    ids = [tid for tid in ids if tid]
    for tid in ids:
        if tid.startswith("cont-"):
            return tid
    for tid in ids:
        if any(tid.startswith(prefix) for prefix in LAB_OR_HUNTER_PREFIXES):
            continue
        return tid
    return ""


def policy_reject_n1_perfect_green(n_orders: int, all_green: bool) -> bool:
    """True when a 100% green print must not be treated as a policy result."""
    return int(n_orders) < 2 or (int(n_orders) == 1 and bool(all_green))


def _offset_key(offset_pct: float) -> str:
    return f"{float(offset_pct):.2f}"


def candles_to_path_ticks(
    candles_1m: Sequence[Sequence[Any]],
    *,
    direction: str,
    start_ts: float,
    end_ts: float,
) -> list:
    """1m high tagged: SHORT sees high then low then close (touch, not blind shadow)."""
    direction_u = str(direction or "SHORT").upper()
    ticks = []
    for row in candles_1m or []:
        t = candle_ts_sec(row)
        if t is None or t + 60.0 < float(start_ts):
            continue
        if t > float(end_ts) + 1e-9:
            break
        high, low, close = float(row[2]), float(row[3]), float(row[4])
        if direction_u == "SHORT":
            marks = ((t + 1.0, high), (t + 30.0, low), (t + 59.0, close))
        else:
            marks = ((t + 1.0, low), (t + 30.0, high), (t + 59.0, close))
        for ts, px in marks:
            ticks.append({"t": ts, "price": px, "best_bid": px, "best_ask": px})
    return ticks


def _compact_score(
    *,
    orig: float,
    chase_id: str,
    exit_id: str,
    sim: Mapping[str, Any],
    first_hit: Optional[str] = None,
) -> dict:
    pnl = sim.get("net_pnl_usd")
    hit = first_hit or sim.get("exit_reason")
    green = sim.get("green")
    if green is None and pnl is not None:
        green = float(pnl) > 0
    return {
        "orig": round(float(orig), 2),
        "chase": chase_id,
        "exit": exit_id,
        "pnl": None if pnl is None else round(float(pnl), 4),
        "first_hit": hit,
        "green": bool(green),
    }


def _path_horizon_ts(candles_1m, ticks_1s, signal_ts: float) -> float:
    last = float(signal_ts)
    for row in candles_1m or []:
        t = candle_ts_sec(row)
        if t is not None:
            last = max(last, t + 60.0)
    for tick in ticks_1s or []:
        try:
            last = max(last, float(tick.get("t") or 0))
        except (TypeError, ValueError):
            continue
    return last


def _score_live_ladders(ticks, *, direction, entry, fill_t, orig, chase_id, margin_usdt) -> list:
    out = []
    live_42 = simulate_policy_on_path(
        ticks,
        direction=direction,
        entry_price=entry,
        thesis_cut=LIVE_THESIS_CUT,
        hard_stop=None,
        ladder=LIVE_EARLY_LADDER_4_2,
        fill_t=fill_t,
        margin_usdt=margin_usdt,
    )
    out.append(_compact_score(orig=orig, chase_id=chase_id, exit_id="live_4_2_t12", sim=live_42))
    live_c = simulate_policy_on_path(
        ticks,
        direction=direction,
        entry_price=entry,
        thesis_cut=LIVE_THESIS_CUT,
        hard_stop=None,
        ladder=LIVE_SCENARIO_C_LADDER,
        fill_t=fill_t,
        margin_usdt=margin_usdt,
    )
    out.append(_compact_score(orig=orig, chase_id=chase_id, exit_id="live_c_t12", sim=live_c))
    return out


def _score_full_exits(
    ticks,
    *,
    direction,
    entry,
    fill_t,
    orig,
    chase_id,
    margin_usdt,
    atr14_pct,
    donchian_high,
    donchian_low,
    support_price,
    resistance_price,
) -> list:
    scores = _score_live_ladders(
        ticks,
        direction=direction,
        entry=entry,
        fill_t=fill_t,
        orig=orig,
        chase_id=chase_id,
        margin_usdt=margin_usdt,
    )
    atr_tp = []
    atr_sl = []
    chand = []
    if atr14_pct is not None:
        for k in ATR_K_GRID:
            tp = simulate_atr_take_profit(
                ticks, direction=direction, entry_price=entry, atr14_pct=atr14_pct,
                k=k, fill_t=fill_t, margin_usdt=margin_usdt,
            )
            atr_tp.append(tp)
            scores.append(_compact_score(
                orig=orig, chase_id=chase_id, exit_id=f"atr_tp_k{k:g}", sim=tp,
            ))
            sl = simulate_atr_stop(
                ticks, direction=direction, entry_price=entry, atr14_pct=atr14_pct,
                k=k, fill_t=fill_t, margin_usdt=margin_usdt,
            )
            atr_sl.append(sl)
            scores.append(_compact_score(
                orig=orig, chase_id=chase_id, exit_id=f"atr_sl_k{k:g}", sim=sl,
            ))
            hit = first_hit_combo(tp, sl)
            scores.append(_compact_score(
                orig=orig, chase_id=chase_id,
                exit_id=f"fh_atr_tp_k{k:g}_atr_sl_k{k:g}",
                sim=hit, first_hit=hit.get("exit_reason"),
            ))
        for k in CHANDELIER_K_GRID:
            ch = simulate_chandelier_trail(
                ticks, direction=direction, entry_price=entry, atr14_pct=atr14_pct,
                k=k, fill_t=fill_t, margin_usdt=margin_usdt,
            )
            chand.append(ch)
            scores.append(_compact_score(
                orig=orig, chase_id=chase_id, exit_id=f"chand_k{k:g}", sim=ch,
            ))
    struct_tp = struct_sl = None
    if any(level is not None for level in (donchian_high, donchian_low, support_price, resistance_price)):
        struct_tp = simulate_structure_take_profit(
            ticks, direction=direction, entry_price=entry,
            donchian_low=donchian_low, donchian_high=donchian_high,
            support_price=support_price, resistance_price=resistance_price,
            fill_t=fill_t, margin_usdt=margin_usdt,
        )
        scores.append(_compact_score(orig=orig, chase_id=chase_id, exit_id="struct_tp", sim=struct_tp))
        struct_sl = simulate_structure_stop(
            ticks, direction=direction, entry_price=entry,
            donchian_high=donchian_high, donchian_low=donchian_low,
            resistance_price=resistance_price, support_price=support_price,
            fill_t=fill_t, margin_usdt=margin_usdt,
        )
        scores.append(_compact_score(orig=orig, chase_id=chase_id, exit_id="struct_sl", sim=struct_sl))
        for tp in atr_tp:
            hit = first_hit_combo(tp, struct_sl)
            scores.append(_compact_score(
                orig=orig, chase_id=chase_id,
                exit_id=f"fh_atr_tp_k{tp.get('k'):g}_struct_sl",
                sim=hit, first_hit=hit.get("exit_reason"),
            ))
        for ch in chand:
            hit = first_hit_combo(ch, struct_sl)
            scores.append(_compact_score(
                orig=orig, chase_id=chase_id,
                exit_id=f"fh_chand_k{ch.get('k'):g}_struct_sl",
                sim=hit, first_hit=hit.get("exit_reason"),
            ))
    for thesis in THESIS_4PP_GRID:
        sim = simulate_policy_on_path(
            ticks,
            direction=direction,
            entry_price=entry,
            thesis_cut=thesis,
            hard_stop=None,
            ladder=LIVE_EARLY_LADDER_4_2,
            fill_t=fill_t,
            margin_usdt=margin_usdt,
        )
        scores.append(_compact_score(
            orig=orig, chase_id=chase_id, exit_id=f"thesis_m{abs(int(thesis))}", sim=sim,
        ))
    return scores


def _score_priority(row: Mapping[str, Any], live_orig: float) -> tuple:
    orig = float(row.get("orig") or 0)
    chase = str(row.get("chase") or "")
    exit_id = str(row.get("exit") or "")
    live = 0 if abs(orig - float(live_orig)) < 1e-9 else 1
    chase_rank = 0 if chase in ("no_chase", LIVE_CHASE_ID) else 1
    always = 0 if exit_id in ("live_4_2_t12", "live_c_t12") else 1
    return (live, chase_rank, always, exit_id)


def cap_chase_exit_scores(
    scores: Sequence[Mapping[str, Any]],
    *,
    live_orig: float = LIVE_ORIG_OFFSET_PCT,
    cap: int = SCORE_CAP,
) -> list:
    """Keep live baseline + ladder comparison; then best/worst; drop the rest."""
    rows = [dict(row) for row in scores]
    if len(rows) <= int(cap):
        return rows
    must = []
    rest = []
    for row in rows:
        orig = float(row.get("orig") or 0)
        chase = str(row.get("chase") or "")
        exit_id = str(row.get("exit") or "")
        keep = (
            abs(orig - float(live_orig)) < 1e-9
            and chase in ("no_chase", LIVE_CHASE_ID)
            and (
                exit_id in ("live_4_2_t12", "live_c_t12")
                or exit_id.startswith("atr_")
                or exit_id.startswith("chand_")
                or exit_id.startswith("struct_")
                or exit_id.startswith("fh_")
                or exit_id.startswith("thesis_")
            )
        ) or (
            exit_id in ("live_4_2_t12", "live_c_t12")
            and chase in ("no_chase", LIVE_CHASE_ID)
        )
        if keep:
            must.append(row)
        else:
            rest.append(row)
    seen = set()
    out = []
    for row in must:
        key = (row.get("orig"), row.get("chase"), row.get("exit"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    rest_sorted = sorted(
        rest,
        key=lambda row: (
            abs(float(row["pnl"])) if row.get("pnl") is not None else -1.0,
            0 if row.get("green") else 1,
        ),
        reverse=True,
    )
    for row in rest_sorted:
        if len(out) >= int(cap):
            break
        key = (row.get("orig"), row.get("chase"), row.get("exit"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    out.sort(key=lambda row: _score_priority(row, live_orig))
    return out[: int(cap)]


def build_order_multiverse(
    *,
    trade_id: str,
    signal_price: float,
    signal_ts: float,
    direction: str = "SHORT",
    candles_1m: Sequence[Sequence[Any]] = (),
    ticks_1s: Optional[Sequence[Mapping[str, Any]]] = None,
    live_orig: float = LIVE_ORIG_OFFSET_PCT,
    ttl_sec: float = TTL_SEC_DEFAULT,
    hold_sec: float = HOLD_SEC_DEFAULT,
    atr14_pct: Optional[float] = None,
    donchian_high: Optional[float] = None,
    donchian_low: Optional[float] = None,
    support_price: Optional[float] = None,
    resistance_price: Optional[float] = None,
    margin_usdt: float = DEFAULT_MARGIN_USDT,
    path_complete: bool = False,
) -> dict:
    direction_u = str(direction or "SHORT").upper()
    ticks_abs = list(ticks_1s or [])
    touches = {}
    fills = []
    for offset_pct in OFFSET_PCT_GRID:
        hit = simulate_touch_fill(
            candles_1m,
            signal_ts=signal_ts,
            signal_price=signal_price,
            direction=direction_u,
            offset_pct=offset_pct,
            ttl_sec=ttl_sec,
            chase={"no_chase": True},
            ticks_1s=ticks_abs or None,
        )
        touches[_offset_key(offset_pct)] = hit.get("touch_ts")
        for policy in CHASE_POLICIES:
            chase_hit = simulate_touch_fill(
                candles_1m,
                signal_ts=signal_ts,
                signal_price=signal_price,
                direction=direction_u,
                offset_pct=offset_pct,
                ttl_sec=ttl_sec,
                chase=policy,
                ticks_1s=ticks_abs or None,
            )
            if not chase_hit.get("touched"):
                continue
            fills.append({
                "orig": float(offset_pct),
                "chase_id": policy["id"],
                "fill_ts": float(chase_hit["touch_ts"]),
                "fill_price": float(chase_hit["fill_price"]),
            })

    horizon = _path_horizon_ts(candles_1m, ticks_abs, signal_ts)
    ttl_end = float(signal_ts) + float(ttl_sec)
    # COMPLETE at TTL/cancel (or when the caller already closed the ticket).
    # Do not wait hold_sec/120m before scoring — that window is extra tape
    # after a simulated or real fill, used when the path already has it.
    pending = (not path_complete) and horizon + 1.0 < ttl_end

    scores = []
    if fills and not pending:
        for fill in fills:
            end_ts = float(fill["fill_ts"]) + float(hold_sec)
            path_ticks = [tick for tick in ticks_abs if float(tick.get("t") or 0) >= fill["fill_ts"] - 1e-9]
            if not path_ticks:
                path_ticks = candles_to_path_ticks(
                    candles_1m, direction=direction_u, start_ts=fill["fill_ts"], end_ts=end_ts,
                )
            full = (
                abs(fill["orig"] - float(live_orig)) < 1e-9
                and fill["chase_id"] in ("no_chase", LIVE_CHASE_ID)
            )
            if full:
                scores.extend(_score_full_exits(
                    path_ticks,
                    direction=direction_u,
                    entry=fill["fill_price"],
                    fill_t=fill["fill_ts"],
                    orig=fill["orig"],
                    chase_id=fill["chase_id"],
                    margin_usdt=margin_usdt,
                    atr14_pct=atr14_pct,
                    donchian_high=donchian_high,
                    donchian_low=donchian_low,
                    support_price=support_price,
                    resistance_price=resistance_price,
                ))
            else:
                scores.extend(_score_live_ladders(
                    path_ticks,
                    direction=direction_u,
                    entry=fill["fill_price"],
                    fill_t=fill["fill_ts"],
                    orig=fill["orig"],
                    chase_id=fill["chase_id"],
                    margin_usdt=margin_usdt,
                ))
        scores = cap_chase_exit_scores(scores, live_orig=live_orig)

    greens = [row for row in scores if row.get("green") and row.get("pnl") is not None]
    reds = [row for row in scores if row.get("green") is False and row.get("pnl") is not None]
    return {
        "schema": ORDER_MULTIVERSE_SCHEMA,
        "event": "PENDING" if pending else "COMPLETE",
        "pending": pending,
        "trade_id": str(trade_id),
        "signal_px": float(signal_price),
        "signal_ts": float(signal_ts),
        "direction": direction_u,
        "live_orig": float(live_orig),
        "live_ticket_unchanged": True,
        "live_thesis_cut": LIVE_THESIS_CUT,
        "live_ladder": "4->2 then Scenario C",
        "touches": touches,
        "n_touched": sum(1 for ts in touches.values() if ts is not None),
        "n_missed": sum(1 for ts in touches.values() if ts is None),
        "chase_exit_scores": scores,
        "n_green": len(greens),
        "n_red": len(reds),
        "n": len(scores),
        "policy_reject_n1_100_green": policy_reject_n1_perfect_green(1, True),
        "note": "discrete grid only; not a live order grid; n=1 100% green is not policy",
    }


def compact_json_line(record: Mapping[str, Any]) -> str:
    return json.dumps(record, separators=(",", ":"), ensure_ascii=True)


def write_order_multiverse(path: str, record: Mapping[str, Any]) -> str:
    line = compact_json_line(record)
    if line.count("\n"):
        raise ValueError("order_multiverse row must be one JSON line")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return line
