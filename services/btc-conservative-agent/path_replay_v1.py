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
COLLECTOR_VERSION = "collector_v2.1"
FEATURE_SCHEMA_VERSION = "derived_features_v1"
PATH_SCHEMA_VERSION = "raw_path_v1"
REPLAY_VERSION = "path_replay_v1"
FILL_MODEL_IDEAL_TOUCH = "IDEAL_TOUCH"
FILL_MODEL_CONSERVATIVE_TOUCH = "CONSERVATIVE_TOUCH"
FILL_MODEL_ACTUAL_EXECUTION = "ACTUAL_EXECUTION"
FILL_MODELS = (
    "IDEAL_TOUCH",
    "CONSERVATIVE_TOUCH",
    "ACTUAL_EXECUTION",
    "REALISTIC_TOUCH",
    "ACTUAL_LIVE",
)
FEE_MODEL_BITFINEX_ZERO = "BITFINEX_ZERO"
FIRST_EXIT_CODES = (
    "THESIS",
    "HARD_STOP",
    "LADDER",
    "ATR_TP",
    "ATR_SL",
    "CHANDELIER",
    "STRUCTURE",
    "TIME_STOP",
    "PATH_END",
)
_FIRST_EXIT_MAP = {
    "THESIS_FAST_CUT": "THESIS",
    "HARD_STOP": "HARD_STOP",
    "PROFIT_LOCK_LADDER": "LADDER",
    "ATR_TAKE_PROFIT": "ATR_TP",
    "ATR_STOP": "ATR_SL",
    "CHANDELIER_TRAIL": "CHANDELIER",
    "STRUCTURE_TP": "STRUCTURE",
    "STRUCTURE_STOP": "STRUCTURE",
    "TIME_STOP": "TIME_STOP",
    "PATH_END": "PATH_END",
}
CONTROL_CELL = {
    "tag": "CONTROL",
    "LIVE_CELL": True,
    "orig_offset_pct": 0.10,
    "thesis_cut": -12.0,
    "ladder": "4->2",
    "invert_on": False,
}
# 1pp of margin at 100x is 0.01% of price.
MARGIN_PP_AS_PRICE_PCT = 0.01
# Documented live policy — replay grouping must not mutate these on Fly.
LIVE_THESIS_CUT = -12.0
LIVE_HARD_STOP_PCT = 13.0
LIVE_HARD_STOP_DOES_NOT_CLOSE_PAPER = True
try:
    from scenario_c_config import TRAIL_LADDER_SCENARIO_C as LIVE_SCENARIO_C_LADDER
except Exception:  # pragma: no cover - keep replay importable in isolation
    LIVE_SCENARIO_C_LADDER = ((4, 2), (5, 3), (8, 5), (12, 10), (19, 17), (40, 28), (60, 45), (80, 60), (100, 75), (150, 120))
LIVE_EARLY_LADDER_4_2 = ((4.0, 2.0),)
ATR_K_GRID = (1.0, 1.5, 2.0, 3.0)
CHANDELIER_K_GRID = (2.0, 3.0)


def first_exit_code(reason: Optional[str]) -> str:
    """Map simulator reasons onto the FIRST_EXIT contract set."""
    raw = str(reason or "PATH_END")
    if raw in FIRST_EXIT_CODES:
        return raw
    return _FIRST_EXIT_MAP.get(raw, "PATH_END")


def zero_fill_costs(*, fee_model: str = FEE_MODEL_BITFINEX_ZERO) -> dict:
    return {
        "fee_usd": 0.0,
        "spread_usd": 0.0,
        "slippage_usd": 0.0,
        "chase_cost_usd": 0.0,
        "fee_model": str(fee_model or FEE_MODEL_BITFINEX_ZERO),
    }


def _minutes_from_fill(event_t: Optional[float], fill_t: float) -> Optional[float]:
    if event_t is None:
        return None
    return round((float(event_t) - float(fill_t)) / 60.0, 4)


def _candle_ts_sec(row: Sequence[Any]) -> Optional[float]:
    if not row:
        return None
    try:
        raw = float(row[0])
    except (TypeError, ValueError):
        return None
    return raw / 1000.0 if raw > 1e12 else raw


def raw_1m_to_ticks(
    candles_1m: Sequence[Sequence[Any]],
    *,
    direction: str,
    start_ts: float,
    end_ts: float,
    as_of_ts: Optional[float] = None,
) -> list:
    """1m high-then-low-then-close (SHORT). Never includes bars after ``as_of_ts``."""
    direction_u = str(direction or "SHORT").upper()
    look = float(end_ts)
    as_of = None if as_of_ts is None else float(as_of_ts)
    ticks = []
    for row in candles_1m or []:
        t = _candle_ts_sec(row)
        if t is None or t + 60.0 < float(start_ts):
            continue
        if t > look + 1e-9:
            continue
        if as_of is not None and t > as_of + 1e-9:
            continue
        high, low, close = float(row[2]), float(row[3]), float(row[4])
        if direction_u == "SHORT":
            marks = ((t + 1.0, high), (t + 30.0, low), (t + 59.0, close))
        else:
            marks = ((t + 1.0, low), (t + 30.0, high), (t + 59.0, close))
        for ts, px in marks:
            if as_of is not None and ts > as_of + 1e-9:
                continue
            ticks.append({"t": ts, "price": px, "best_bid": px, "best_ask": px})
    ticks.sort(key=lambda tick: (float(tick.get("t") or 0), int(tick.get("seq") or 0)))
    return ticks


def path_exit_annotations(
    recovery: Optional[Mapping[str, Any]],
    first_exit_reason: Optional[str] = None,
) -> dict:
    """Cheap second-best / recovery flags from stored path stats. No extra indicator library."""
    stats = dict(recovery or {})
    first = first_exit_code(first_exit_reason)
    mfe = float(stats.get("MFE") if stats.get("MFE") is not None else stats.get("mfe_pct") or 0.0)
    recovered = bool(
        stats.get("would_have_recovered")
        or stats.get("recovered_after_12")
        or stats.get("ever_green")
    )
    hit_profit_later = bool(
        stats.get("would_have_hit_profit_later")
        or (stats.get("hit_thesis_12") and stats.get("ever_green"))
        or (mfe > 0 and first in ("THESIS", "HARD_STOP"))
    )
    second = None
    if first == "THESIS" and hit_profit_later:
        second = "LADDER" if mfe >= 4.0 else "PATH_END"
    elif first == "LADDER" and stats.get("hit_thesis_12"):
        second = "THESIS"
    elif first == "HARD_STOP" and recovered:
        second = "PATH_END"
    return {
        "FIRST_EXIT": first,
        "second_best_exit": second,
        "would_have_recovered": recovered,
        "would_have_hit_profit_later": hit_profit_later,
    }


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


def corner_adverse_levels(limit: int = 100) -> tuple:
    """12..30 every 1pp, then 40/50/60/80/100. Answers 12 vs 13 vs 14 and 50/100.

    Full integer 0–100 remains available via ``integer_levels_0_100``. This is
    the default COMPLETE grid so one 120m tape is scored without 89×89×N blowup.
    """
    cap = int(limit)
    out = []
    for n in list(range(12, 31)) + [40, 50, 60, 80, 100]:
        if n <= cap and n not in out:
            out.append(n)
    return tuple(out)


# Stage-1 query grid (replay layer, NOT collector). Coarse enough to search;
# integer 0–100 remains available for Stage-2 zoom. Do not explode this in
# order_multiverse.jsonl.
STAGE1_ENTRY_OFFSET_PCT = (0.01, 0.03, 0.05, 0.07, 0.10, 0.15, 0.20, 0.25, 0.30)
STAGE1_THESIS = (-12, -15, -20, -25, -30, -40, -50, -75, -100)
STAGE1_STOP = (10, 13, 15, 18, 20)
STAGE1_ATR_K = (1.0, 1.5, 2.0, 2.5)
STAGE1_CHASE_COUNTS = (0, 1, 2, 3, 4, 5)
STAGE1_TIME_STOP_SEC = (300, 1800, 3600, 7200)
MFE_MAE_TRAJECTORY_MIN = (1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120)
# Live thesis −12 / stop 13 sit inside Stage 1.
THESIS_CORNER_GRID = STAGE1_THESIS
STOP_CORNER_GRID = STAGE1_STOP
TIME_STOP_SEC_GRID = STAGE1_TIME_STOP_SEC
LADDER_TABLES = {
    "none": (),
    "live_4_2": LIVE_EARLY_LADDER_4_2,
    "live_c": LIVE_SCENARIO_C_LADDER,
    "early_tight": ((2, 0.5), (3, 1), (4, 2), (5, 3), (8, 6), (12, 10)),
    "early_loose": ((4, 1), (6, 2), (10, 5), (15, 9), (25, 17)),
    "high_capture": ((4, 3), (6, 5), (10, 8), (15, 12), (25, 20), (40, 32)),
    "runner_friendly": ((5, 1), (8, 3), (12, 6), (20, 12), (40, 25), (80, 55)),
}
STAGE1_LADDERS = ("none", "live_4_2", "live_c")


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
    invert_on: bool = False,
) -> dict:
    return {
        "schema": PATH_REPLAY_SCHEMA,
        "policy_tag": PATH_REPLAY_POLICY_TAG,
        "units": PATH_UNITS,
        "invert_on": bool(invert_on),
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
    time_stop_sec: Optional[float] = None,
    as_of_t: Optional[float] = None,
    reconstruct_from_price: bool = False,
) -> dict:
    """Walk a dense unreal path. First hit among hard stop, thesis, ladder lock, time.

    ``thesis_cut`` / ``hard_stop`` are unrealized margin % (e.g. -21.0), not price %.
    ``hard_stop=None`` means do not close on a hard stop (Showcase research default).
    Chronological only: ticks after ``as_of_t`` are never read.
    """
    ordered = sorted(
        ticks,
        key=lambda tick: (float(tick.get("t") or 0), int(tick.get("seq") or 0)),
    )
    if as_of_t is not None:
        cap = float(as_of_t)
        ordered = [tick for tick in ordered if float(tick.get("t") or 0) <= cap + 1e-9]
    peak = 0.0
    mae = 0.0
    t_mfe = None
    t_mae = None
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
        stored = None if reconstruct_from_price else tick.get("unreal_pct")
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
            t_mfe = t
        if unreal < mae:
            mae = unreal
            t_mae = t
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
        elif (
            time_stop_sec is not None
            and float(time_stop_sec) > 0
            and age >= float(time_stop_sec)
        ):
            hit = "TIME_STOP"
        if hit:
            exit_reason, exit_t, exit_unreal, exit_price = hit, t, unreal, mark
            break
        exit_t, exit_unreal, exit_price = t, unreal, mark
    pnl_usd = None if exit_unreal is None else round((exit_unreal / 100.0) * float(margin_usdt), 4)
    first_exit = first_exit_code(exit_reason)
    return {
        "schema": "path_policy_sim_v1",
        "policy_tag": PATH_REPLAY_POLICY_TAG,
        "replay_version": REPLAY_VERSION,
        "units": PATH_UNITS,
        "live_recommendation": False,
        "direction": str(direction).upper(),
        "entry_price": entry_price,
        "thesis_cut": thesis,
        "hard_stop": stop,
        "ladder": [list(rung) for rung in ladder],
        "time_stop_sec": None if time_stop_sec is None else float(time_stop_sec),
        "exit_reason": exit_reason,
        "FIRST_EXIT": first_exit,
        "exit_t": exit_t,
        "exit_price": exit_price,
        "exit_unreal_pct": None if exit_unreal is None else round(float(exit_unreal), 4),
        "net_pnl_usd": pnl_usd,
        "mfe_pct": round(peak, 4),
        "mae_pct": round(mae, 4),
        "MFE": round(peak, 4),
        "MFE_time": _minutes_from_fill(t_mfe, fill_t),
        "MAE": round(mae, 4),
        "MAE_time": _minutes_from_fill(t_mae, fill_t),
        "green": bool(pnl_usd is not None and pnl_usd > 0),
        "control_cell": dict(CONTROL_CELL),
    }


def replay_from_raw_1m(
    candles_1m: Sequence[Sequence[Any]],
    *,
    direction: str,
    entry_price: float,
    fill_ts: float,
    thesis_cut: float = -12.0,
    hard_stop: Optional[float] = None,
    ladder: Sequence[Sequence[float]] = LIVE_EARLY_LADDER_4_2,
    hold_sec: float = 7200.0,
    as_of_ts: Optional[float] = None,
    leverage: float = LIVE_LEVERAGE,
    margin_usdt: float = 20.0,
    time_stop_sec: Optional[float] = None,
) -> dict:
    """Reconstruct entry → path → first exit → PnL from raw 1m tape. No lookahead."""
    end_ts = float(fill_ts) + float(hold_sec)
    ticks = raw_1m_to_ticks(
        candles_1m,
        direction=direction,
        start_ts=fill_ts,
        end_ts=end_ts,
        as_of_ts=as_of_ts,
    )
    sim = simulate_policy_on_path(
        ticks,
        direction=direction,
        entry_price=entry_price,
        leverage=leverage,
        margin_usdt=margin_usdt,
        thesis_cut=thesis_cut,
        hard_stop=hard_stop,
        ladder=ladder,
        fill_t=fill_ts,
        time_stop_sec=time_stop_sec,
        as_of_t=as_of_ts,
        reconstruct_from_price=True,
    )
    recovery = path_recovery_stats(
        ticks, direction=direction, entry_price=entry_price, leverage=leverage, fill_t=fill_ts,
    )
    sim.update(path_exit_annotations(recovery, sim.get("exit_reason")))
    sim["fill_model"] = FILL_MODEL_IDEAL_TOUCH
    sim["fill_costs"] = zero_fill_costs()
    sim["raw_tick_n"] = len(ticks)
    sim["replay_from"] = "raw_1m_tape"
    return sim


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


def atr_pct_to_margin_pct(atr14_pct: Optional[float], leverage: float = LIVE_LEVERAGE) -> Optional[float]:
    """Convert ATR% of price to unrealized margin-% at ``leverage``.

    ``atr14_pct`` is (ATR/close)*100, e.g. 0.12 = 0.12% of BTC. At 100x that
    is 12 percentage points of margin, comparable to thesis −12 / ladder 4→2.
    """
    if atr14_pct is None:
        return None
    try:
        value = float(atr14_pct)
    except (TypeError, ValueError):
        return None
    if value != value or value < 0:
        return None
    return round(value * float(leverage), 6)


def _left_on_table(mfe_pct: Optional[float], exit_unreal: Optional[float]) -> Optional[float]:
    if mfe_pct is None or exit_unreal is None:
        return None
    return round(float(mfe_pct) - float(exit_unreal), 4)


def _beat_row(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> bool:
    cand = candidate.get("net_pnl_usd")
    base = baseline.get("net_pnl_usd")
    if cand is None or base is None:
        return False
    return float(cand) > float(base) + 1e-9


def simulate_atr_take_profit(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    atr14_pct: float,
    k: float = 2.0,
    leverage: float = LIVE_LEVERAGE,
    margin_usdt: float = 20.0,
    fill_t: float = 0.0,
    thesis_cut: Optional[float] = None,
) -> dict:
    """Close when favorable unreal reaches k × fill-time 3m ATR (margin %). Offline only."""
    target = atr_pct_to_margin_pct(atr14_pct, leverage)
    threshold = None if target is None else float(k) * float(target)
    thesis = None if thesis_cut is None else -abs(float(thesis_cut))
    ordered = sorted(ticks, key=lambda tick: float(tick.get("t") or 0))
    peak = 0.0
    mae = 0.0
    exit_reason = "PATH_END"
    exit_t = exit_unreal = exit_price = None
    for tick in ordered:
        t = float(tick.get("t") or 0)
        if t < fill_t:
            continue
        mark = executable_mark(tick, direction)
        stored = tick.get("unreal_pct")
        try:
            unreal = float(stored) if stored is not None else None
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
        hit = None
        if thesis is not None and unreal <= thesis:
            hit = "THESIS_FAST_CUT"
        elif threshold is not None and unreal >= threshold:
            hit = "ATR_TAKE_PROFIT"
        if hit:
            exit_reason, exit_t, exit_unreal, exit_price = hit, t, unreal, mark
            break
        exit_t, exit_unreal, exit_price = t, unreal, mark
    pnl_usd = None if exit_unreal is None else round((exit_unreal / 100.0) * float(margin_usdt), 4)
    return {
        "schema": "path_alt_tp_v1",
        "policy_tag": PATH_REPLAY_POLICY_TAG,
        "live_recommendation": False,
        "strategy": "ATR_TP",
        "k": float(k),
        "atr14_pct": float(atr14_pct) if atr14_pct is not None else None,
        "atr_margin_pct": target,
        "threshold_margin_pct": None if threshold is None else round(threshold, 4),
        "exit_reason": exit_reason,
        "exit_t": exit_t,
        "exit_price": exit_price,
        "exit_unreal_pct": None if exit_unreal is None else round(float(exit_unreal), 4),
        "net_pnl_usd": pnl_usd,
        "mfe_pct": round(peak, 4),
        "mae_pct": round(mae, 4),
        "left_vs_mfe_pct": _left_on_table(peak, exit_unreal),
        "green": bool(pnl_usd is not None and pnl_usd > 0),
    }


def simulate_chandelier_trail(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    atr14_pct: float,
    k: float = 3.0,
    leverage: float = LIVE_LEVERAGE,
    margin_usdt: float = 20.0,
    fill_t: float = 0.0,
    thesis_cut: Optional[float] = None,
) -> dict:
    """After MFE, trail at peak − k×ATR (unreal %). SHORT uses the same signed peak.

    Fill-time ATR is frozen so the trail does not chase expanding ATR.
    """
    atr_m = atr_pct_to_margin_pct(atr14_pct, leverage)
    trail_width = None if atr_m is None else float(k) * float(atr_m)
    thesis = None if thesis_cut is None else -abs(float(thesis_cut))
    ordered = sorted(ticks, key=lambda tick: float(tick.get("t") or 0))
    peak = 0.0
    mae = 0.0
    exit_reason = "PATH_END"
    exit_t = exit_unreal = exit_price = None
    for tick in ordered:
        t = float(tick.get("t") or 0)
        if t < fill_t:
            continue
        mark = executable_mark(tick, direction)
        stored = tick.get("unreal_pct")
        try:
            unreal = float(stored) if stored is not None else None
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
        floor = None if trail_width is None else peak - trail_width
        hit = None
        if thesis is not None and unreal <= thesis:
            hit = "THESIS_FAST_CUT"
        elif floor is not None and peak > 0 and unreal <= floor:
            hit = "CHANDELIER_TRAIL"
        if hit:
            exit_reason, exit_t, exit_unreal, exit_price = hit, t, unreal, mark
            break
        exit_t, exit_unreal, exit_price = t, unreal, mark
    pnl_usd = None if exit_unreal is None else round((exit_unreal / 100.0) * float(margin_usdt), 4)
    return {
        "schema": "path_alt_tp_v1",
        "policy_tag": PATH_REPLAY_POLICY_TAG,
        "live_recommendation": False,
        "strategy": "CHANDELIER",
        "k": float(k),
        "atr14_pct": float(atr14_pct) if atr14_pct is not None else None,
        "atr_margin_pct": atr_m,
        "trail_width_margin_pct": None if trail_width is None else round(trail_width, 4),
        "exit_reason": exit_reason,
        "exit_t": exit_t,
        "exit_price": exit_price,
        "exit_unreal_pct": None if exit_unreal is None else round(float(exit_unreal), 4),
        "net_pnl_usd": pnl_usd,
        "mfe_pct": round(peak, 4),
        "mae_pct": round(mae, 4),
        "left_vs_mfe_pct": _left_on_table(peak, exit_unreal),
        "green": bool(pnl_usd is not None and pnl_usd > 0),
    }


def simulate_structure_take_profit(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    donchian_low: Optional[float] = None,
    donchian_high: Optional[float] = None,
    support_price: Optional[float] = None,
    resistance_price: Optional[float] = None,
    leverage: float = LIVE_LEVERAGE,
    margin_usdt: float = 20.0,
    fill_t: float = 0.0,
    thesis_cut: Optional[float] = None,
) -> dict:
    """Opposite-structure TP: SHORT banks at fill-time Donchian low or support."""
    side = str(direction or "LONG").upper()
    thesis = None if thesis_cut is None else -abs(float(thesis_cut))
    if side == "SHORT":
        levels = [level for level in (donchian_low, support_price) if level]
        target = min(levels) if levels else None
    else:
        levels = [level for level in (donchian_high, resistance_price) if level]
        target = max(levels) if levels else None
    ordered = sorted(ticks, key=lambda tick: float(tick.get("t") or 0))
    peak = 0.0
    mae = 0.0
    exit_reason = "PATH_END"
    exit_t = exit_unreal = exit_price = None
    for tick in ordered:
        t = float(tick.get("t") or 0)
        if t < fill_t:
            continue
        mark = executable_mark(tick, direction)
        stored = tick.get("unreal_pct")
        try:
            unreal = float(stored) if stored is not None else None
        except (TypeError, ValueError):
            unreal = None
        if mark is None:
            try:
                mark = float(tick.get("price") or 0) or None
            except (TypeError, ValueError):
                mark = None
        if unreal is None:
            if mark is None:
                continue
            unreal = unrealized_margin_pct(
                direction=direction, entry_price=entry_price, mark=mark, leverage=leverage,
            )
        if unreal > peak:
            peak = unreal
        if unreal < mae:
            mae = unreal
        hit = None
        if thesis is not None and unreal <= thesis:
            hit = "THESIS_FAST_CUT"
        elif target is not None and mark is not None:
            if side == "SHORT" and mark <= float(target):
                hit = "STRUCTURE_TP"
            elif side == "LONG" and mark >= float(target):
                hit = "STRUCTURE_TP"
        if hit:
            exit_reason, exit_t, exit_unreal, exit_price = hit, t, unreal, mark
            break
        exit_t, exit_unreal, exit_price = t, unreal, mark
    pnl_usd = None if exit_unreal is None else round((exit_unreal / 100.0) * float(margin_usdt), 4)
    return {
        "schema": "path_alt_tp_v1",
        "policy_tag": PATH_REPLAY_POLICY_TAG,
        "live_recommendation": False,
        "strategy": "STRUCTURE_TP",
        "target_price": target,
        "exit_reason": exit_reason,
        "exit_t": exit_t,
        "exit_price": exit_price,
        "exit_unreal_pct": None if exit_unreal is None else round(float(exit_unreal), 4),
        "net_pnl_usd": pnl_usd,
        "mfe_pct": round(peak, 4),
        "mae_pct": round(mae, 4),
        "left_vs_mfe_pct": _left_on_table(peak, exit_unreal),
        "green": bool(pnl_usd is not None and pnl_usd > 0),
    }


def simulate_atr_stop(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    atr14_pct: float,
    k: float = 2.0,
    leverage: float = LIVE_LEVERAGE,
    margin_usdt: float = 20.0,
    fill_t: float = 0.0,
    ladder: Sequence[Sequence[float]] = (),
) -> dict:
    """Stop when adverse unreal reaches −k × fill-time 3m ATR. Offline only."""
    atr_m = atr_pct_to_margin_pct(atr14_pct, leverage)
    threshold = None if atr_m is None else -float(k) * float(atr_m)
    ordered = sorted(ticks, key=lambda tick: float(tick.get("t") or 0))
    peak = 0.0
    mae = 0.0
    lock = None
    exit_reason = "PATH_END"
    exit_t = exit_unreal = exit_price = None
    for tick in ordered:
        t = float(tick.get("t") or 0)
        if t < fill_t:
            continue
        mark = executable_mark(tick, direction)
        stored = tick.get("unreal_pct")
        try:
            unreal = float(stored) if stored is not None else None
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
        hit = None
        if threshold is not None and unreal <= threshold:
            hit = "ATR_STOP"
        elif lock is not None and unreal <= lock:
            hit = "PROFIT_LOCK_LADDER"
        if hit:
            exit_reason, exit_t, exit_unreal, exit_price = hit, t, unreal, mark
            break
        exit_t, exit_unreal, exit_price = t, unreal, mark
    pnl_usd = None if exit_unreal is None else round((exit_unreal / 100.0) * float(margin_usdt), 4)
    return {
        "schema": "path_alt_sl_v1",
        "policy_tag": PATH_REPLAY_POLICY_TAG,
        "live_recommendation": False,
        "strategy": "ATR_STOP",
        "k": float(k),
        "atr14_pct": float(atr14_pct) if atr14_pct is not None else None,
        "atr_margin_pct": atr_m,
        "threshold_margin_pct": None if threshold is None else round(threshold, 4),
        "exit_reason": exit_reason,
        "exit_t": exit_t,
        "exit_price": exit_price,
        "exit_unreal_pct": None if exit_unreal is None else round(float(exit_unreal), 4),
        "net_pnl_usd": pnl_usd,
        "mfe_pct": round(peak, 4),
        "mae_pct": round(mae, 4),
        "left_vs_mfe_pct": _left_on_table(peak, exit_unreal),
        "green": bool(pnl_usd is not None and pnl_usd > 0),
    }


def simulate_structure_stop(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    donchian_high: Optional[float] = None,
    donchian_low: Optional[float] = None,
    resistance_price: Optional[float] = None,
    support_price: Optional[float] = None,
    leverage: float = LIVE_LEVERAGE,
    margin_usdt: float = 20.0,
    fill_t: float = 0.0,
    ladder: Sequence[Sequence[float]] = (),
) -> dict:
    """SHORT stops if price breaks fill-time Donchian high or nearest resistance."""
    side = str(direction or "LONG").upper()
    if side == "SHORT":
        levels = [level for level in (donchian_high, resistance_price) if level]
        target = min(levels) if levels else None
    else:
        levels = [level for level in (donchian_low, support_price) if level]
        target = max(levels) if levels else None
    ordered = sorted(ticks, key=lambda tick: float(tick.get("t") or 0))
    peak = 0.0
    mae = 0.0
    lock = None
    exit_reason = "PATH_END"
    exit_t = exit_unreal = exit_price = None
    for tick in ordered:
        t = float(tick.get("t") or 0)
        if t < fill_t:
            continue
        mark = executable_mark(tick, direction)
        stored = tick.get("unreal_pct")
        try:
            unreal = float(stored) if stored is not None else None
        except (TypeError, ValueError):
            unreal = None
        if mark is None:
            try:
                mark = float(tick.get("price") or 0) or None
            except (TypeError, ValueError):
                mark = None
        if unreal is None:
            if mark is None:
                continue
            unreal = unrealized_margin_pct(
                direction=direction, entry_price=entry_price, mark=mark, leverage=leverage,
            )
        if unreal > peak:
            peak = unreal
        if unreal < mae:
            mae = unreal
        if ladder:
            for trigger, floor in ladder:
                if peak >= float(trigger):
                    lock = float(floor)
        hit = None
        if target is not None and mark is not None:
            if side == "SHORT" and mark >= float(target):
                hit = "STRUCTURE_STOP"
            elif side == "LONG" and mark <= float(target):
                hit = "STRUCTURE_STOP"
        if hit is None and lock is not None and unreal <= lock:
            hit = "PROFIT_LOCK_LADDER"
        if hit:
            exit_reason, exit_t, exit_unreal, exit_price = hit, t, unreal, mark
            break
        exit_t, exit_unreal, exit_price = t, unreal, mark
    pnl_usd = None if exit_unreal is None else round((exit_unreal / 100.0) * float(margin_usdt), 4)
    return {
        "schema": "path_alt_sl_v1",
        "policy_tag": PATH_REPLAY_POLICY_TAG,
        "live_recommendation": False,
        "strategy": "STRUCTURE_STOP",
        "target_price": target,
        "exit_reason": exit_reason,
        "exit_t": exit_t,
        "exit_price": exit_price,
        "exit_unreal_pct": None if exit_unreal is None else round(float(exit_unreal), 4),
        "net_pnl_usd": pnl_usd,
        "mfe_pct": round(peak, 4),
        "mae_pct": round(mae, 4),
        "left_vs_mfe_pct": _left_on_table(peak, exit_unreal),
        "green": bool(pnl_usd is not None and pnl_usd > 0),
    }


def first_hit_combo(tp_sim: Mapping[str, Any], sl_sim: Mapping[str, Any]) -> dict:
    """If the stop would fire before the take-profit, that combo's PnL wins."""
    tp_t = tp_sim.get("exit_t")
    sl_t = sl_sim.get("exit_t")
    tp_reason = str(tp_sim.get("exit_reason") or "PATH_END")
    sl_reason = str(sl_sim.get("exit_reason") or "PATH_END")
    tp_is_tp = tp_reason not in ("PATH_END", "THESIS_FAST_CUT")
    sl_is_sl = sl_reason not in ("PATH_END", "PROFIT_LOCK_LADDER")
    winner = "TP"
    chosen = tp_sim
    if sl_is_sl and sl_t is not None and (not tp_is_tp or tp_t is None or float(sl_t) <= float(tp_t or sl_t)):
        winner = "SL"
        chosen = sl_sim
    elif not tp_is_tp and sl_is_sl:
        winner = "SL"
        chosen = sl_sim
    return {
        "winner": winner,
        "tp_strategy": tp_sim.get("strategy"),
        "sl_strategy": sl_sim.get("strategy"),
        "tp_k": tp_sim.get("k"),
        "sl_k": sl_sim.get("k"),
        "exit_reason": chosen.get("exit_reason"),
        "FIRST_EXIT": first_exit_code(chosen.get("exit_reason")),
        "exit_t": chosen.get("exit_t"),
        "exit_unreal_pct": chosen.get("exit_unreal_pct"),
        "net_pnl_usd": chosen.get("net_pnl_usd"),
        "green": chosen.get("green"),
        "left_vs_mfe_pct": chosen.get("left_vs_mfe_pct"),
        "sl_fired_first": winner == "SL",
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
    atr14_pct: Optional[float] = None,
    donchian_high: Optional[float] = None,
    donchian_low: Optional[float] = None,
    support_price: Optional[float] = None,
    resistance_price: Optional[float] = None,
) -> dict:
    """Best 4pp thesis × stop × ladder-rung combo on one recorded path.

    Offline grouping only. Live Fly policy stays thesis −12 / Scenario C /
    hard-stop-does-not-close-paper. Integer 0–100 sweep remains available via
    ``integer_thesis_sweep_0_100`` / ``integer_stop_sweep_0_100``. Alt ATR /
    chandelier / structure TP+SL are replay-only and never mutate live knobs.
    """
    ticks, direction, entry = _coerce_report_path(
        path, direction=direction, entry_price=entry_price,
    )
    if isinstance(path, Mapping):
        atr14_pct = atr14_pct if atr14_pct is not None else path.get("atr14_pct") or path.get("atr14_pct_3m")
        donchian_high = donchian_high if donchian_high is not None else path.get("donchian_high") or path.get("donchian_high_3m")
        donchian_low = donchian_low if donchian_low is not None else path.get("donchian_low") or path.get("donchian_low_3m")
        support_price = support_price if support_price is not None else path.get("support_price")
        resistance_price = resistance_price if resistance_price is not None else path.get("resistance_price")
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

    live = simulate_policy_on_path(
        ticks,
        direction=direction,
        entry_price=entry,
        leverage=leverage,
        margin_usdt=margin_usdt,
        thesis_cut=LIVE_THESIS_CUT,
        hard_stop=None,
        ladder=LIVE_SCENARIO_C_LADDER,
        fill_t=fill_t,
        mfe_protect_pct=mfe_protect_pct,
    )
    live["left_vs_mfe_pct"] = _left_on_table(live.get("mfe_pct"), live.get("exit_unreal_pct"))
    early_4_2 = simulate_policy_on_path(
        ticks,
        direction=direction,
        entry_price=entry,
        leverage=leverage,
        margin_usdt=margin_usdt,
        thesis_cut=LIVE_THESIS_CUT,
        hard_stop=None,
        ladder=LIVE_EARLY_LADDER_4_2,
        fill_t=fill_t,
        mfe_protect_pct=mfe_protect_pct,
    )
    early_4_2["left_vs_mfe_pct"] = _left_on_table(early_4_2.get("mfe_pct"), early_4_2.get("exit_unreal_pct"))

    atr_tp = []
    chandelier = []
    atr_stop = []
    first_hits = []
    structure_tp = None
    structure_stop = None
    if atr14_pct is not None:
        for k in ATR_K_GRID:
            tp = simulate_atr_take_profit(
                ticks, direction=direction, entry_price=entry, atr14_pct=atr14_pct,
                k=k, leverage=leverage, margin_usdt=margin_usdt, fill_t=fill_t,
            )
            tp["beat_4_2"] = _beat_row(tp, early_4_2)
            tp["beat_live"] = _beat_row(tp, live)
            atr_tp.append(tp)
            sl = simulate_atr_stop(
                ticks, direction=direction, entry_price=entry, atr14_pct=atr14_pct,
                k=k, leverage=leverage, margin_usdt=margin_usdt, fill_t=fill_t,
            )
            sl["beat_4_2"] = _beat_row(sl, early_4_2)
            sl["beat_live"] = _beat_row(sl, live)
            atr_stop.append(sl)
            hit = first_hit_combo(tp, sl)
            hit["beat_4_2"] = _beat_row(hit, early_4_2)
            hit["beat_live"] = _beat_row(hit, live)
            first_hits.append(hit)
        for k in CHANDELIER_K_GRID:
            ch = simulate_chandelier_trail(
                ticks, direction=direction, entry_price=entry, atr14_pct=atr14_pct,
                k=k, leverage=leverage, margin_usdt=margin_usdt, fill_t=fill_t,
            )
            ch["beat_4_2"] = _beat_row(ch, early_4_2)
            ch["beat_live"] = _beat_row(ch, live)
            chandelier.append(ch)
    if any(level is not None for level in (donchian_high, donchian_low, support_price, resistance_price)):
        structure_tp = simulate_structure_take_profit(
            ticks, direction=direction, entry_price=entry,
            donchian_low=donchian_low, donchian_high=donchian_high,
            support_price=support_price, resistance_price=resistance_price,
            leverage=leverage, margin_usdt=margin_usdt, fill_t=fill_t,
        )
        structure_tp["beat_4_2"] = _beat_row(structure_tp, early_4_2)
        structure_tp["beat_live"] = _beat_row(structure_tp, live)
        structure_stop = simulate_structure_stop(
            ticks, direction=direction, entry_price=entry,
            donchian_high=donchian_high, donchian_low=donchian_low,
            resistance_price=resistance_price, support_price=support_price,
            leverage=leverage, margin_usdt=margin_usdt, fill_t=fill_t,
        )
        structure_stop["beat_4_2"] = _beat_row(structure_stop, early_4_2)
        structure_stop["beat_live"] = _beat_row(structure_stop, live)
        if atr_tp:
            for tp in atr_tp:
                hit = first_hit_combo(tp, structure_stop)
                hit["beat_4_2"] = _beat_row(hit, early_4_2)
                hit["beat_live"] = _beat_row(hit, live)
                first_hits.append(hit)
        if chandelier:
            for ch in chandelier:
                hit = first_hit_combo(ch, structure_stop)
                hit["beat_4_2"] = _beat_row(hit, early_4_2)
                hit["beat_live"] = _beat_row(hit, live)
                first_hits.append(hit)

    return {
        "schema": "path_group_report_v1",
        "policy_tag": PATH_REPLAY_POLICY_TAG,
        "units": PATH_UNITS,
        "live_recommendation": False,
        "live_policy_untouched": {
            "thesis_cut": LIVE_THESIS_CUT,
            "hard_stop_does_not_close_paper": LIVE_HARD_STOP_DOES_NOT_CLOSE_PAPER,
            "ladder": [list(rung) for rung in LIVE_SCENARIO_C_LADDER],
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
        "live": live,
        "early_4_2": early_4_2,
        "alt_tp": {
            "atr_k": atr_tp,
            "chandelier_k": chandelier,
            "structure": structure_tp,
            "k_grid": list(ATR_K_GRID),
            "chandelier_k_grid": list(CHANDELIER_K_GRID),
        },
        "alt_sl": {
            "atr_k": atr_stop,
            "structure": structure_stop,
            "k_grid": list(ATR_K_GRID),
        },
        "first_hit": first_hits,
        "fill_stamps": {
            "atr14_pct": atr14_pct,
            "atr_margin_pct": atr_pct_to_margin_pct(atr14_pct, leverage),
            "donchian_high": donchian_high,
            "donchian_low": donchian_low,
            "support_price": support_price,
            "resistance_price": resistance_price,
        },
        "rows": rows if include_all else [],
    }


def path_recovery_stats(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    leverage: float = LIVE_LEVERAGE,
    fill_t: float = 0.0,
) -> dict:
    """MFE/MAE / time-to-green / whether a wider stop would have let price recover.

    One walk of the stored tape. Answers: ever green? would 50% have saved a −12 cut?
    """
    series = unreal_series_from_path(
        ticks, direction=direction, entry_price=entry_price, leverage=leverage, fill_t=fill_t,
    )
    peak = 0.0
    mae = 0.0
    t_green = None
    t_mae = None
    t_mfe = None
    first_hit = {}
    green_after = {}
    for t, unreal in series:
        if unreal > peak:
            peak = unreal
            t_mfe = t
        if unreal < mae:
            mae = unreal
            t_mae = t
        if t_green is None and unreal > 0:
            t_green = t
        for level in (12, 13, 15, 20, 50, 100):
            key = str(level)
            if key not in first_hit and unreal <= -float(level):
                first_hit[key] = t
            if key in first_hit and key not in green_after and unreal > 0:
                green_after[key] = t
    ever_green = t_green is not None
    hit_12 = "12" in first_hit
    return {
        "schema": "path_recovery_v1",
        "mfe_pct": round(peak, 4),
        "mae_pct": round(mae, 4),
        "MFE": round(peak, 4),
        "MFE_time": _minutes_from_fill(t_mfe, fill_t),
        "MAE": round(mae, 4),
        "MAE_time": _minutes_from_fill(t_mae, fill_t),
        "ever_green": ever_green,
        "time_to_green_sec": None if t_green is None else round(float(t_green) - float(fill_t), 3),
        "time_to_mfe_sec": None if t_mfe is None else round(float(t_mfe) - float(fill_t), 3),
        "time_to_mae_sec": None if t_mae is None else round(float(t_mae) - float(fill_t), 3),
        "hit_thesis_12": hit_12,
        "would_stop_13_hit": mae <= -LIVE_HARD_STOP_PCT,
        "recovered_after_12": "12" in green_after,
        "recovered_after_13": "13" in green_after,
        "would_50_save": bool(hit_12 and mae > -50.0 and "12" in green_after),
        "would_100_save": bool(hit_12 and mae > -100.0 and "12" in green_after),
        "would_have_recovered": "12" in green_after or (hit_12 and ever_green),
        "would_have_hit_profit_later": bool(hit_12 and ever_green and peak > 0),
        "first_hit_ts": first_hit,
        "green_after_hit_ts": green_after,
        "path_tick_n": len(series),
    }


def mfe_mae_trajectory(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    fill_t: float = 0.0,
    leverage: float = LIVE_LEVERAGE,
) -> dict:
    """Running MFE/MAE at 1,2,3,5,10,15,20,30,45,60,90,120 minutes. Missing = None."""
    series = unreal_series_from_path(
        ticks, direction=direction, entry_price=entry_price, leverage=leverage, fill_t=fill_t,
    )
    peak = 0.0
    mae = 0.0
    t_mfe = None
    t_mae = None
    points = {int(m): None for m in MFE_MAE_TRAJECTORY_MIN}
    for t, unreal in series:
        if unreal > peak:
            peak = unreal
            t_mfe = t
        if unreal < mae:
            mae = unreal
            t_mae = t
        age_min = (float(t) - float(fill_t)) / 60.0
        for minute in MFE_MAE_TRAJECTORY_MIN:
            if points[int(minute)] is None and age_min + 1e-9 >= float(minute):
                points[int(minute)] = {
                    "mfe_pct": round(peak, 4),
                    "mae_pct": round(mae, 4),
                    "unreal_pct": round(unreal, 4),
                }
    return {
        "schema": "mfe_mae_trajectory_v1",
        "minutes": list(MFE_MAE_TRAJECTORY_MIN),
        "points": points,
        "final_mfe_pct": round(peak, 4),
        "final_mae_pct": round(mae, 4),
        "MFE": round(peak, 4),
        "MFE_time": _minutes_from_fill(t_mfe, fill_t),
        "MAE": round(mae, 4),
        "MAE_time": _minutes_from_fill(t_mae, fill_t),
        "path_complete_120m": points[120] is not None,
        "path_tick_n": len(series),
    }


def _corner_sim(
    ticks,
    *,
    direction,
    entry_price,
    fill_t,
    leverage,
    margin_usdt,
    thesis_cut,
    hard_stop,
    ladder,
    time_stop_sec=None,
):
    return simulate_policy_on_path(
        ticks,
        direction=direction,
        entry_price=entry_price,
        leverage=leverage,
        margin_usdt=margin_usdt,
        thesis_cut=thesis_cut,
        hard_stop=hard_stop,
        ladder=ladder,
        fill_t=fill_t,
        time_stop_sec=time_stop_sec,
    )


def score_all_corners(
    ticks: Sequence[Mapping[str, Any]],
    *,
    direction: str,
    entry_price: float,
    fill_t: float = 0.0,
    leverage: float = LIVE_LEVERAGE,
    margin_usdt: float = 20.0,
    atr14_pct: Optional[float] = None,
    donchian_high: Optional[float] = None,
    donchian_low: Optional[float] = None,
    support_price: Optional[float] = None,
    resistance_price: Optional[float] = None,
    invert_on: bool = False,
    orig: float = 0.10,
    chase_id: str = "no_chase",
    include_atr: bool = True,
    include_combo: bool = False,
) -> dict:
    """Score one stored 120m path across entry-adjacent exits: thesis, stop, ladder, ATR, time.

    Independent axis sweeps so 12 vs 13 vs 14 and 50/100 are answered. Bounded first-hit
    combos for live-adjacent + recovery. Never a live Fly grid. Invert is a tag, not a mutation.
    """
    recovery = path_recovery_stats(
        ticks, direction=direction, entry_price=entry_price, leverage=leverage, fill_t=fill_t,
    )
    scores = []
    common = {
        "orig": round(float(orig), 2),
        "chase": str(chase_id),
        "invert_on": bool(invert_on),
        "mfe_pct": recovery["mfe_pct"],
        "mae_pct": recovery["mae_pct"],
        "ever_green": recovery["ever_green"],
        "time_to_green_sec": recovery["time_to_green_sec"],
        "would_50_save": recovery["would_50_save"],
        "would_100_save": recovery["would_100_save"],
        "path_tick_n": recovery["path_tick_n"],
        "live_recommendation": False,
    }

    def add(exit_id: str, sim: Mapping[str, Any], *, first_hit: Optional[str] = None):
        pnl = sim.get("net_pnl_usd")
        green = sim.get("green")
        if green is None and pnl is not None:
            green = float(pnl) > 0
        row = dict(common)
        row.update({
            "exit": exit_id,
            "pnl": None if pnl is None else round(float(pnl), 4),
            "first_hit": first_hit or sim.get("exit_reason"),
            "green": bool(green),
            "exit_unreal_pct": sim.get("exit_unreal_pct"),
            "exit_t": sim.get("exit_t"),
            "sim_mfe_pct": sim.get("mfe_pct"),
            "sim_mae_pct": sim.get("mae_pct"),
        })
        scores.append(row)

    live_42 = _corner_sim(
        ticks, direction=direction, entry_price=entry_price, fill_t=fill_t,
        leverage=leverage, margin_usdt=margin_usdt, thesis_cut=LIVE_THESIS_CUT,
        hard_stop=None, ladder=LIVE_EARLY_LADDER_4_2,
    )
    add("live_4_2_t12", live_42)
    live_c = _corner_sim(
        ticks, direction=direction, entry_price=entry_price, fill_t=fill_t,
        leverage=leverage, margin_usdt=margin_usdt, thesis_cut=LIVE_THESIS_CUT,
        hard_stop=None, ladder=LIVE_SCENARIO_C_LADDER,
    )
    add("live_c_t12", live_c)
    live_stop13 = _corner_sim(
        ticks, direction=direction, entry_price=entry_price, fill_t=fill_t,
        leverage=leverage, margin_usdt=margin_usdt, thesis_cut=LIVE_THESIS_CUT,
        hard_stop=LIVE_HARD_STOP_PCT, ladder=LIVE_EARLY_LADDER_4_2,
    )
    add("live_4_2_t12_s13", live_stop13)

    for thesis in THESIS_CORNER_GRID:
        sim = _corner_sim(
            ticks, direction=direction, entry_price=entry_price, fill_t=fill_t,
            leverage=leverage, margin_usdt=margin_usdt, thesis_cut=thesis,
            hard_stop=None, ladder=LIVE_EARLY_LADDER_4_2,
        )
        add(f"thesis_m{abs(int(thesis))}", sim)
    for stop in STOP_CORNER_GRID:
        sim = _corner_sim(
            ticks, direction=direction, entry_price=entry_price, fill_t=fill_t,
            leverage=leverage, margin_usdt=margin_usdt, thesis_cut=LIVE_THESIS_CUT,
            hard_stop=stop, ladder=LIVE_EARLY_LADDER_4_2,
        )
        add(f"stop_m{int(stop)}", sim)
    add(
        "stop_none",
        _corner_sim(
            ticks, direction=direction, entry_price=entry_price, fill_t=fill_t,
            leverage=leverage, margin_usdt=margin_usdt, thesis_cut=LIVE_THESIS_CUT,
            hard_stop=None, ladder=LIVE_EARLY_LADDER_4_2,
        ),
    )
    for ladder_id, ladder in LADDER_TABLES.items():
        if ladder_id in ("live_4_2", "none"):
            continue
        sim = _corner_sim(
            ticks, direction=direction, entry_price=entry_price, fill_t=fill_t,
            leverage=leverage, margin_usdt=margin_usdt, thesis_cut=LIVE_THESIS_CUT,
            hard_stop=None, ladder=ladder,
        )
        add(f"ladder_{ladder_id}", sim)
    add(
        "ladder_none",
        _corner_sim(
            ticks, direction=direction, entry_price=entry_price, fill_t=fill_t,
            leverage=leverage, margin_usdt=margin_usdt, thesis_cut=LIVE_THESIS_CUT,
            hard_stop=None, ladder=(),
        ),
    )
    for sec in TIME_STOP_SEC_GRID:
        sim = _corner_sim(
            ticks, direction=direction, entry_price=entry_price, fill_t=fill_t,
            leverage=leverage, margin_usdt=margin_usdt, thesis_cut=LIVE_THESIS_CUT,
            hard_stop=None, ladder=LIVE_EARLY_LADDER_4_2, time_stop_sec=sec,
        )
        add(f"time_s{int(sec)}", sim)

    if include_atr and atr14_pct is not None:
        for k in STAGE1_ATR_K:
            tp = simulate_atr_take_profit(
                ticks, direction=direction, entry_price=entry_price, atr14_pct=atr14_pct,
                k=k, leverage=leverage, margin_usdt=margin_usdt, fill_t=fill_t,
            )
            add(f"atr_tp_k{k:g}", tp)
            sl = simulate_atr_stop(
                ticks, direction=direction, entry_price=entry_price, atr14_pct=atr14_pct,
                k=k, leverage=leverage, margin_usdt=margin_usdt, fill_t=fill_t,
            )
            add(f"atr_sl_k{k:g}", sl)
            hit = first_hit_combo(tp, sl)
            add(f"fh_atr_tp_k{k:g}_atr_sl_k{k:g}", hit, first_hit=hit.get("exit_reason"))
        for k in CHANDELIER_K_GRID:
            ch = simulate_chandelier_trail(
                ticks, direction=direction, entry_price=entry_price, atr14_pct=atr14_pct,
                k=k, leverage=leverage, margin_usdt=margin_usdt, fill_t=fill_t,
            )
            add(f"chand_k{k:g}", ch)
    struct_sl = None
    if any(level is not None for level in (donchian_high, donchian_low, support_price, resistance_price)):
        struct_tp = simulate_structure_take_profit(
            ticks, direction=direction, entry_price=entry_price,
            donchian_low=donchian_low, donchian_high=donchian_high,
            support_price=support_price, resistance_price=resistance_price,
            leverage=leverage, margin_usdt=margin_usdt, fill_t=fill_t,
        )
        add("struct_tp", struct_tp)
        struct_sl = simulate_structure_stop(
            ticks, direction=direction, entry_price=entry_price,
            donchian_high=donchian_high, donchian_low=donchian_low,
            resistance_price=resistance_price, support_price=support_price,
            leverage=leverage, margin_usdt=margin_usdt, fill_t=fill_t,
        )
        add("struct_sl", struct_sl)
        if include_atr and atr14_pct is not None:
            for k in STAGE1_ATR_K:
                tp = simulate_atr_take_profit(
                    ticks, direction=direction, entry_price=entry_price, atr14_pct=atr14_pct,
                    k=k, leverage=leverage, margin_usdt=margin_usdt, fill_t=fill_t,
                )
                hit = first_hit_combo(tp, struct_sl)
                add(f"fh_atr_tp_k{k:g}_struct_sl", hit, first_hit=hit.get("exit_reason"))
            for k in CHANDELIER_K_GRID:
                ch = simulate_chandelier_trail(
                    ticks, direction=direction, entry_price=entry_price, atr14_pct=atr14_pct,
                    k=k, leverage=leverage, margin_usdt=margin_usdt, fill_t=fill_t,
                )
                hit = first_hit_combo(ch, struct_sl)
                add(f"fh_chand_k{k:g}_struct_sl", hit, first_hit=hit.get("exit_reason"))

    # Stage 1 is independent axes. Cartesian zoom is Stage 2 on promising regions.
    if include_combo:
        for thesis in (-12, -50, -100):
            for stop in (13, None):
                sim = _corner_sim(
                    ticks, direction=direction, entry_price=entry_price, fill_t=fill_t,
                    leverage=leverage, margin_usdt=margin_usdt, thesis_cut=thesis,
                    hard_stop=stop, ladder=LIVE_EARLY_LADDER_4_2,
                )
                stop_tag = "none" if stop is None else str(int(stop))
                add(f"combo_t{abs(int(thesis))}_s{stop_tag}_live_4_2", sim)

    traj = mfe_mae_trajectory(
        ticks, direction=direction, entry_price=entry_price, leverage=leverage, fill_t=fill_t,
    )
    greens = [row for row in scores if row.get("green") and row.get("pnl") is not None]
    killer = None
    live_row = next((row for row in scores if row.get("exit") == "live_4_2_t12"), None)
    if live_row and live_row.get("green") is False:
        killer = live_row.get("first_hit")
    best = None
    for row in scores:
        pnl = row.get("pnl")
        if pnl is None:
            continue
        if best is None or float(pnl) > float(best["pnl"]):
            best = row
    return {
        "schema": "stage1_replay_v1",
        "live_recommendation": False,
        "invert_on": bool(invert_on),
        "units": PATH_UNITS,
        "cohort": "filled",
        "live_policy_untouched": {
            "orig_offset_pct": 0.10,
            "thesis_cut": LIVE_THESIS_CUT,
            "hard_stop_pct": LIVE_HARD_STOP_PCT,
            "hard_stop_does_not_close_paper": LIVE_HARD_STOP_DOES_NOT_CLOSE_PAPER,
            "ladder": "4->2 then Scenario C",
        },
        "recovery": recovery,
        "mfe_mae_trajectory": traj,
        "n": len(scores),
        "n_green": len(greens),
        "what_kicked_live": killer,
        "best_descriptive_not_policy": None if best is None else {
            "exit": best.get("exit"),
            "pnl": best.get("pnl"),
            "first_hit": best.get("first_hit"),
            "green": best.get("green"),
            "note": "descriptive on this tape; not a live recommendation; not win-rate selection",
        },
        "chase_exit_scores": scores,
        "integer_sweep_available": True,
        "note": "Stage-1 query on stored tape. TTL-unfilled must not enter this cohort.",
    }


def stage1_replay(*args, **kwargs):
    """Query engine: Stage-1 coarse grid on one stored path. Collector must not call this per tick."""
    return score_all_corners(*args, **kwargs)


def expectancies_from_stage1(rows: Sequence[Mapping[str, Any]], *, cohort: str) -> dict:
    """Split P(fill) vs P(profit|fill) vs E[PnL|fill]. TTL-unfilled is fill-only."""
    if cohort != "filled":
        filled = [row for row in rows if row.get("touched") or row.get("filled")]
        n = len(rows)
        n_fill = len(filled)
        return {
            "schema": "stage1_expectancy_v1",
            "cohort": cohort,
            "p_fill": None if n == 0 else round(n_fill / n, 4),
            "p_profit_given_fill": None,
            "e_pnl_given_fill": None,
            "n": n,
            "n_fill": n_fill,
            "note": "TTL-unfilled contributes only to P(fill|signal,entry)",
        }
    pnls = [float(row["pnl"]) for row in rows if row.get("pnl") is not None]
    greens = [row for row in rows if row.get("green") and row.get("pnl") is not None]
    return {
        "schema": "stage1_expectancy_v1",
        "cohort": "filled",
        "p_fill": 1.0,
        "p_profit_given_fill": None if not pnls else round(len(greens) / len(pnls), 4),
        "e_pnl_given_fill": None if not pnls else round(sum(pnls) / len(pnls), 4),
        "n": len(pnls),
        "note": "Do not rank live policy by win rate alone. Robustness/OOS later.",
    }


FEATURE_FAMILY_INVENTORY = {
    "schema": "feature_family_inventory_v1",
    "note": "Recording / schema hooks. Not live trading rules.",
    "already_stored": {
        "signal_ts_price_side_score": True,
        "invert_on": True,
        "1s_path_replay_jsonl": True,
        "120m_post_exit": True,
        "1m_ohlcv_mtf": True,
        "atr14_pct_3m_at_fill": True,
        "donchian_3m_at_fill": True,
        "support_resistance_at_fill": True,
        "chase_offset_touch_0_01_to_0_30": True,
        "order_multiverse_path_1m": True,
        "hypothetical_touch_fills": True,
        "mfe_mae_trajectory_1_to_120m": True,
        "cycle_3m_rsi_stoch_adx_log_only": True,
        "rsi_5m_log_only": True,
        "funding_snapshot": True,
        "session_features_policy_engine": True,
        "maker_taker_fee_profile": True,
    },
    "needs_new_api_or_series": {
        "raw_order_book_depth": "not a full L2 tape; BBO only on ticks",
        "open_interest": "not recorded",
        "liquidations": "not recorded",
        "basis_perp_spot": "not recorded",
        "atr_percentile_regime": "ATR level stored; percentile rank not stored",
        "idealized_vs_realistic_fill_modes": "1m high-touch only so far",
        "slippage_realized_vs_quote": "partial fill_sim on live fill only",
    },
    "lower_priority_oscillators": ("macd", "additional_rsi_gates"),
}


def ticks_from_path_replay_rows(rows: Iterable[Mapping[str, Any]], trade_id: str) -> list:
    """Rebuild a walkable tick list from path_replay.jsonl / signal_replay ticks."""
    out = []
    tid = str(trade_id)
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("trade_id") or "") != tid:
            continue
        if row.get("ticks"):
            out.extend(list(row.get("ticks") or []))
            continue
        if row.get("t") is None and row.get("price") is None:
            continue
        out.append(row)
    return out

