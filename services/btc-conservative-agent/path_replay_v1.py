"""Paper-collection path replay — any integer 0–100 threshold, later.

What we are doing (not a live Fly knob grid):

1. One live paper policy on Fly. Trades as today: thesis −12, Scenario C
   rungs, hard-stop-does-not-close-paper. Cheetah stays paused. No RSI veto.
2. Record a dense unreal path (in-trade + 120m after exit) in margin-% at
   100x, 1s/tick. Because the path is continuous, any integer 0–100 for
   thesis, hard stop, or ladder can be replayed later.
3. Offline report grouping (analysis only):
   - Thesis cut-fast: −4, −8, −13, −17, −21, … to −100 (near live −12, then
     4pp). Full 0–100 integer sweep remains available so 21 vs 22 still works.
   - Hard stop: same 4pp sequence 4, 8, 13, 17, … 100 (adverse threshold).
   - Ladder MFE buckets 0–5, 5–10, … 95–100. If the path shoots through 5–10
     into 10–15: did rung 1 lock then rung 2? Were 3 vs 4 rungs required?
     Which (thesis bucket × stop bucket × ladder rungs) had best total PnL?
4. 3-minute RSI/Stoch/ADX (1m resampled to 3m) stamp + log-only WOULD_BLOCK
   so later we can see which conjunction matches winning groups. Not a veto.

Live knobs stay fixed. Candidate values such as −21 vs −22 are recovered here
from 1s/tick unreal samples.

Units: unrealized % on 100x margin. 1 percentage point ≈ 0.01% price
(at BTC ~64k that is ~$6.40). This is NOT a price-percent grid.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence, Union


PATH_REPLAY_SCHEMA = "path_replay_v1"
PATH_REPLAY_POLICY_TAG = "path_replay_v1"
PATH_REPLAY_FILE = "path_replay.jsonl"
PATH_UNITS = "unrealized_pct_on_100x_margin"
LIVE_LEVERAGE = 100
# 1pp of margin at 100x is 0.01% of price.
MARGIN_PP_AS_PRICE_PCT = 0.01
# Documented live policy — replay grouping must not mutate these on Fly.
LIVE_THESIS_CUT = -12.0
LIVE_HARD_STOP_DOES_NOT_CLOSE_PAPER = True


def abs_4pp_grid(limit: int = 100) -> tuple:
    """4, 8, 13, 17, 21, … capped at ``limit``.

    Starts 4 then 8, then 13 (near live thesis −12 / stop 13) and every 4pp
    after. Analysis grouping only — not a live Fly knob grid.
    """
    cap = int(limit)
    out = [4, 8]
    n = 13
    while n <= cap:
        out.append(n)
        n += 4
    if out[-1] < cap:
        out.append(cap)
    return tuple(out)


# Thesis cut-fast grouping: −4, −8, −13, −17, −21, … −100.
THESIS_4PP_GRID = tuple(-level for level in abs_4pp_grid(100))
# Matching adverse-threshold stops: 4, 8, 13, 17, … 100.
STOP_4PP_GRID = abs_4pp_grid(100)
# MFE buckets for ladder grouping.
LADDER_BUCKETS = tuple((lo, lo + 5) for lo in range(0, 100, 5))
# Rung counts asked in the shoot-through question (3 vs 4) plus 0–2.
LADDER_RUNG_COUNTS = (0, 1, 2, 3, 4)


def integer_levels_0_100(*, adverse: bool = True) -> tuple:
    """Full 1pp 0–100 sweep. 21 vs 22 stays distinguishable. Not a live grid."""
    levels = tuple(range(0, 101))
    if adverse:
        return tuple(-level for level in levels)
    return levels


def margin_pct_to_price_pct(margin_pct: float, leverage: float = LIVE_LEVERAGE) -> float:
    return float(margin_pct) / float(leverage)


def price_for_short_unreal(entry: float, unreal_pct: float, leverage: float = LIVE_LEVERAGE) -> float:
    """Price that produces ``unreal_pct`` margin for a SHORT at ``leverage``."""
    move = float(unreal_pct) / (float(leverage) * 100.0)
    return float(entry) * (1.0 - move)


def unrealized_margin_pct(
    *,
    direction: str,
    entry_price: float,
    mark: float,
    leverage: float = LIVE_LEVERAGE,
) -> float:
    if entry_price <= 0 or mark <= 0:
        return 0.0
    side = 1.0 if str(direction or "LONG").upper() == "LONG" else -1.0
    return ((mark - entry_price) / entry_price) * side * float(leverage) * 100.0


def executable_mark(tick: Mapping[str, Any], direction: str) -> Optional[float]:
    side = str(direction or "LONG").upper()
    key = "best_bid" if side == "LONG" else "best_ask"
    for candidate in (tick.get(key), tick.get("price")):
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def build_path_sample(
    *,
    trade_id: str,
    t: float,
    price: float,
    unreal_pct: Optional[float],
    phase: str,
    direction: str = "SHORT",
    leverage: float = LIVE_LEVERAGE,
    best_bid: Optional[float] = None,
    best_ask: Optional[float] = None,
    mark_source: str = "last_trade",
    peak_mfe_pct: Optional[float] = None,
    trough_mae_pct: Optional[float] = None,
    observed_ts: Optional[float] = None,
    seq: Optional[int] = None,
) -> dict:
    return {
        "schema": PATH_REPLAY_SCHEMA,
        "policy_tag": PATH_REPLAY_POLICY_TAG,
        "units": PATH_UNITS,
        "trade_id": trade_id,
        "t": round(float(t), 3),
        "price": float(price),
        "unreal_pct": None if unreal_pct is None else round(float(unreal_pct), 4),
        "phase": phase,
        "direction": str(direction or "").upper(),
        "leverage": float(leverage),
        "best_bid": None if best_bid in (None, 0) else float(best_bid),
        "best_ask": None if best_ask in (None, 0) else float(best_ask),
        "mark_source": mark_source,
        "peak_mfe_pct": None if peak_mfe_pct is None else round(float(peak_mfe_pct), 4),
        "trough_mae_pct": None if trough_mae_pct is None else round(float(trough_mae_pct), 4),
        "observed_ts": observed_ts,
        "seq": seq,
        "price_pct_per_margin_pp": MARGIN_PP_AS_PRICE_PCT,
    }


def simulate_policy_on_path(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    leverage: float = LIVE_LEVERAGE,
    margin_usdt: float = 20.0,
    thesis_cut: float = -12.0,
    hard_stop: Optional[float] = None,
    ladder: Sequence[Sequence[float]] = (),
    thesis_min_age_sec: float = 0.0,
    fill_t: float = 0.0,
    mfe_protect_pct: float = 5.0,
) -> dict:
    """Walk a dense unreal path. First hit among hard stop, thesis, ladder lock.

    ``thesis_cut`` / ``hard_stop`` are unrealized margin % (e.g. -21.0), not price %.
    ``hard_stop=None`` means do not close on a hard stop (Showcase research default).
    """
    ordered = sorted(ticks, key=lambda tick: float(tick.get("t") or 0))
    peak = 0.0
    mae = 0.0
    lock = None
    exit_reason = "PATH_END"
    exit_t = None
    exit_unreal = None
    exit_price = None
    thesis = -abs(float(thesis_cut))
    stop = None if hard_stop is None else -abs(float(hard_stop))
    for tick in ordered:
        t = float(tick.get("t") or 0)
        if t < fill_t:
            continue
        mark = executable_mark(tick, direction)
        stored = tick.get("unreal_pct")
        unreal = None
        if stored is not None:
            try:
                unreal = float(stored)
            except (TypeError, ValueError):
                unreal = None
        if unreal is None:
            if mark is None:
                continue
            unreal = unrealized_margin_pct(
                direction=direction, entry_price=entry_price, mark=mark, leverage=leverage,
            )
        elif mark is None:
            try:
                mark = float(tick.get("price") or 0) or None
            except (TypeError, ValueError):
                mark = None
        if unreal > peak:
            peak = unreal
        if unreal < mae:
            mae = unreal
        if ladder:
            for trigger, floor in ladder:
                if peak >= float(trigger):
                    lock = float(floor)
        age = t - fill_t
        hit = None
        if stop is not None and unreal <= stop:
            hit = "HARD_STOP"
        elif (
            age >= thesis_min_age_sec
            and unreal <= thesis
            and peak < float(mfe_protect_pct)
        ):
            hit = "THESIS_FAST_CUT"
        elif lock is not None and unreal <= lock:
            hit = "PROFIT_LOCK_LADDER"
        if hit:
            exit_reason, exit_t, exit_unreal, exit_price = hit, t, unreal, mark
            break
        exit_t, exit_unreal, exit_price = t, unreal, mark
    pnl_usd = None if exit_unreal is None else round((exit_unreal / 100.0) * float(margin_usdt), 4)
    return {
        "schema": "path_policy_sim_v1",
        "policy_tag": PATH_REPLAY_POLICY_TAG,
        "units": PATH_UNITS,
        "live_recommendation": False,
        "direction": str(direction).upper(),
        "entry_price": entry_price,
        "thesis_cut": thesis,
        "hard_stop": stop,
        "ladder": [list(rung) for rung in ladder],
        "exit_reason": exit_reason,
        "exit_t": exit_t,
        "exit_price": exit_price,
        "exit_unreal_pct": None if exit_unreal is None else round(float(exit_unreal), 4),
        "net_pnl_usd": pnl_usd,
        "mfe_pct": round(peak, 4),
        "mae_pct": round(mae, 4),
        "green": bool(pnl_usd is not None and pnl_usd > 0),
    }


def one_point_thesis_sweep(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    thesis_levels: Iterable[float],
    hard_stop: Optional[float] = None,
    ladder: Sequence[Sequence[float]] = (),
    leverage: float = LIVE_LEVERAGE,
    margin_usdt: float = 20.0,
    thesis_min_age_sec: float = 0.0,
    fill_t: float = 0.0,
) -> list:
    """Offline 1pp thesis grid. Never a live Fly grid."""
    rows = []
    for level in thesis_levels:
        rows.append(
            simulate_policy_on_path(
                ticks,
                direction=direction,
                entry_price=entry_price,
                leverage=leverage,
                margin_usdt=margin_usdt,
                thesis_cut=level,
                hard_stop=hard_stop,
                ladder=ladder,
                thesis_min_age_sec=thesis_min_age_sec,
                fill_t=fill_t,
            )
        )
    return rows


def one_point_stop_sweep(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    stop_levels: Iterable[float],
    thesis_cut: float = -12.0,
    ladder: Sequence[Sequence[float]] = (),
    leverage: float = LIVE_LEVERAGE,
    margin_usdt: float = 20.0,
    thesis_min_age_sec: float = 0.0,
    fill_t: float = 0.0,
) -> list:
    """Offline 1pp hard-stop grid. Adverse threshold; never a live Fly grid."""
    rows = []
    for level in stop_levels:
        rows.append(
            simulate_policy_on_path(
                ticks,
                direction=direction,
                entry_price=entry_price,
                leverage=leverage,
                margin_usdt=margin_usdt,
                thesis_cut=thesis_cut,
                hard_stop=level,
                ladder=ladder,
                thesis_min_age_sec=thesis_min_age_sec,
                fill_t=fill_t,
            )
        )
    return rows


def integer_thesis_sweep_0_100(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    hard_stop: Optional[float] = None,
    ladder: Sequence[Sequence[float]] = (),
    **kwargs: Any,
) -> list:
    """Full integer 0–100 thesis sweep so 21 vs 22 remains possible."""
    return one_point_thesis_sweep(
        ticks,
        direction=direction,
        entry_price=entry_price,
        thesis_levels=integer_levels_0_100(adverse=True),
        hard_stop=hard_stop,
        ladder=ladder,
        **kwargs,
    )


def integer_stop_sweep_0_100(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    thesis_cut: float = -12.0,
    ladder: Sequence[Sequence[float]] = (),
    **kwargs: Any,
) -> list:
    """Full integer 0–100 stop sweep (adverse thresholds)."""
    return one_point_stop_sweep(
        ticks,
        direction=direction,
        entry_price=entry_price,
        stop_levels=integer_levels_0_100(adverse=False),
        thesis_cut=thesis_cut,
        ladder=ladder,
        **kwargs,
    )


def ladder_for_bucket(bucket: Sequence[float]) -> list:
    """Single-rung ladder for one MFE bucket.

    Bucket (0, 5) does not arm a lock (trigger 0 is not a profit rung).
    Bucket (5, 10) arms at +5% and locks at breakeven (0). Higher buckets
    arm at ``lo`` and lock at the previous 5pp boundary.
    """
    lo = float(bucket[0])
    if lo <= 0:
        return []
    return [(lo, max(0.0, lo - 5.0))]


def ladder_for_rung_count(n_rungs: int) -> list:
    """5pp rungs: 1→(5,0), 2→(10,5), 3→(15,10), 4→(20,15)."""
    count = int(n_rungs)
    if count <= 0:
        return []
    return [(5.0 * i, 5.0 * (i - 1)) for i in range(1, count + 1)]


def mfe_bucket(mfe_pct: float) -> Optional[tuple]:
    """Half-open buckets [0,5), [5,10), … [95,100]; 100 sits in 95–100."""
    mfe = float(mfe_pct)
    if mfe < 0:
        return None
    for lo, hi in LADDER_BUCKETS:
        if lo <= mfe < hi:
            return (int(lo), int(hi))
        if hi == 100 and mfe >= lo:
            return (int(lo), int(hi))
    return (95, 100)


def unreal_series_from_path(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    leverage: float = LIVE_LEVERAGE,
    fill_t: float = 0.0,
) -> list:
    series = []
    ordered = sorted(ticks, key=lambda tick: float(tick.get("t") or 0))
    for tick in ordered:
        t = float(tick.get("t") or 0)
        if t < fill_t:
            continue
        stored = tick.get("unreal_pct")
        if stored is not None:
            try:
                series.append((t, float(stored)))
                continue
            except (TypeError, ValueError):
                pass
        mark = executable_mark(tick, direction)
        if mark is None:
            continue
        series.append((
            t,
            unrealized_margin_pct(
                direction=direction, entry_price=entry_price, mark=mark, leverage=leverage,
            ),
        ))
    return series


def analyze_shoot_through(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    leverage: float = LIVE_LEVERAGE,
    fill_t: float = 0.0,
    n_rungs: int = 4,
) -> dict:
    """Did the path shoot through 5–10 into 10–15 before rung 1 locked?"""
    series = unreal_series_from_path(
        ticks, direction=direction, entry_price=entry_price, leverage=leverage, fill_t=fill_t,
    )
    rungs = ladder_for_rung_count(n_rungs)
    peak = 0.0
    mae = 0.0
    state = [
        {"rung": i + 1, "trigger": trig, "floor": floor, "armed_t": None, "locked_t": None}
        for i, (trig, floor) in enumerate(rungs)
    ]
    for t, unreal in series:
        if unreal > peak:
            peak = unreal
        if unreal < mae:
            mae = unreal
        for i, rung in enumerate(state):
            if rung["armed_t"] is None and peak >= rung["trigger"]:
                rung["armed_t"] = t
            next_armed = i + 1 < len(state) and state[i + 1]["armed_t"] is not None
            if (
                rung["armed_t"] is not None
                and rung["locked_t"] is None
                and unreal <= rung["floor"]
                and not next_armed
            ):
                rung["locked_t"] = t
            # If the next trigger is already armed, this rung was skipped.
            if next_armed and rung["locked_t"] is None:
                pass
    events = []
    for i, rung in enumerate(state):
        next_armed_t = state[i + 1]["armed_t"] if i + 1 < len(state) else None
        locked_before_next = (
            rung["locked_t"] is not None
            and (next_armed_t is None or rung["locked_t"] <= next_armed_t)
        )
        shot = bool(
            rung["armed_t"] is not None
            and next_armed_t is not None
            and not locked_before_next
        )
        events.append({
            "rung": rung["rung"],
            "trigger": rung["trigger"],
            "floor": rung["floor"],
            "armed": rung["armed_t"] is not None,
            "armed_t": rung["armed_t"],
            "locked_t": rung["locked_t"],
            "locked_before_next": locked_before_next,
            "shot_through": shot,
        })
    armed = [ev for ev in events if ev["armed"]]
    entered = []
    for lo, hi in LADDER_BUCKETS:
        if peak < lo:
            break
        entered.append([int(lo), int(hi)])
    rungs_required = len(armed)
    return {
        "mfe_pct": round(peak, 4),
        "mae_pct": round(mae, 4),
        "mfe_bucket": list(mfe_bucket(peak) or ()),
        "buckets_entered": entered,
        "shot_through": any(ev["shot_through"] for ev in events),
        "rung_events": events,
        "rungs_required": rungs_required,
        "rung_3_vs_4": {
            "rung_3_armed": rungs_required >= 3,
            "rung_4_armed": rungs_required >= 4,
            "note": "3 rungs cover MFE 15–20; 4 rungs cover 20–25. Extra rungs do not fire if MFE never reached them.",
        },
    }


def _coerce_report_path(
    path: Union[Sequence[Mapping[str, Any]], Mapping[str, Any]],
    *,
    direction: Optional[str],
    entry_price: Optional[float],
) -> tuple:
    if isinstance(path, Mapping) and path.get("ticks") is not None:
        ticks = list(path.get("ticks") or [])
        direction = direction or path.get("direction") or "SHORT"
        raw_entry = entry_price if entry_price is not None else path.get("entry_price", path.get("entry"))
    else:
        ticks = list(path)
        direction = direction or "SHORT"
        raw_entry = entry_price
    direction = str(direction or "SHORT").upper()
    try:
        entry = float(raw_entry) if raw_entry not in (None, "") else 0.0
    except (TypeError, ValueError):
        entry = 0.0
    if entry <= 0 and ticks:
        mark = executable_mark(ticks[0], direction)
        entry = float(mark or 0)
    return ticks, direction, entry


def replay_group_report(
    path: Union[Sequence[Mapping[str, Any]], Mapping[str, Any]],
    *,
    direction: Optional[str] = None,
    entry_price: Optional[float] = None,
    leverage: float = LIVE_LEVERAGE,
    margin_usdt: float = 20.0,
    fill_t: float = 0.0,
    mfe_protect_pct: float = 5.0,
    include_all: bool = False,
) -> dict:
    """Best 4pp thesis × stop × ladder-rung combo on one recorded path.

    Offline grouping only. Live Fly policy stays thesis −12 / Scenario C /
    hard-stop-does-not-close-paper. Integer 0–100 sweep remains available via
    ``integer_thesis_sweep_0_100`` / ``integer_stop_sweep_0_100``.
    """
    ticks, direction, entry = _coerce_report_path(
        path, direction=direction, entry_price=entry_price,
    )
    shoot = analyze_shoot_through(
        ticks,
        direction=direction,
        entry_price=entry,
        leverage=leverage,
        fill_t=fill_t,
        n_rungs=4,
    )
    bucket_ladders = []
    for bucket in LADDER_BUCKETS:
        ladder = ladder_for_bucket(bucket)
        sim = simulate_policy_on_path(
            ticks,
            direction=direction,
            entry_price=entry,
            leverage=leverage,
            margin_usdt=margin_usdt,
            thesis_cut=-100.0,
            hard_stop=None,
            ladder=ladder,
            fill_t=fill_t,
            mfe_protect_pct=mfe_protect_pct,
        )
        bucket_ladders.append({
            "bucket": [int(bucket[0]), int(bucket[1])],
            "ladder": [list(rung) for rung in ladder],
            "locked": sim["exit_reason"] == "PROFIT_LOCK_LADDER",
            "exit_reason": sim["exit_reason"],
            "exit_t": sim["exit_t"],
            "net_pnl_usd": sim["net_pnl_usd"],
        })

    best = None
    rows = []
    for thesis in THESIS_4PP_GRID:
        for stop in STOP_4PP_GRID:
            for n_rungs in LADDER_RUNG_COUNTS:
                sim = simulate_policy_on_path(
                    ticks,
                    direction=direction,
                    entry_price=entry,
                    leverage=leverage,
                    margin_usdt=margin_usdt,
                    thesis_cut=thesis,
                    hard_stop=stop,
                    ladder=ladder_for_rung_count(n_rungs),
                    fill_t=fill_t,
                    mfe_protect_pct=mfe_protect_pct,
                )
                row = {
                    "thesis_cut": thesis,
                    "hard_stop": stop,
                    "ladder_rungs": n_rungs,
                    "ladder": sim["ladder"],
                    "exit_reason": sim["exit_reason"],
                    "exit_t": sim["exit_t"],
                    "exit_unreal_pct": sim["exit_unreal_pct"],
                    "net_pnl_usd": sim["net_pnl_usd"],
                    "green": sim["green"],
                    "mfe_bucket": shoot["mfe_bucket"],
                }
                if include_all:
                    rows.append(row)
                pnl = float("-inf") if sim["net_pnl_usd"] is None else float(sim["net_pnl_usd"])
                if best is None or pnl > best["_pnl"]:
                    tagged = dict(row)
                    tagged["_pnl"] = pnl
                    best = tagged

    if best is not None:
        best.pop("_pnl", None)

    return {
        "schema": "path_group_report_v1",
        "policy_tag": PATH_REPLAY_POLICY_TAG,
        "units": PATH_UNITS,
        "live_recommendation": False,
        "live_policy_untouched": {
            "thesis_cut": LIVE_THESIS_CUT,
            "hard_stop_does_not_close_paper": LIVE_HARD_STOP_DOES_NOT_CLOSE_PAPER,
            "note": "4pp / 5pp numbers are analysis grouping, not a live knob grid.",
        },
        "mfe_pct": shoot["mfe_pct"],
        "mae_pct": shoot["mae_pct"],
        "mfe_bucket": shoot["mfe_bucket"],
        "shoot_through": shoot,
        "bucket_ladders": bucket_ladders,
        "best": best,
        "combo_count": len(THESIS_4PP_GRID) * len(STOP_4PP_GRID) * len(LADDER_RUNG_COUNTS),
        "thesis_grid": list(THESIS_4PP_GRID),
        "stop_grid": list(STOP_4PP_GRID),
        "ladder_buckets": [list(bucket) for bucket in LADDER_BUCKETS],
        "integer_sweep_available": True,
        "rows": rows if include_all else [],
    }
