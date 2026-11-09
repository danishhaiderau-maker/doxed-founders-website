"""EXT2 end-to-end search path (A19): bars -> features -> regimes -> entries ->
lattice -> folds -> switching -> gates -> fixtures -> outputs. Paper only.

Every function takes a `Calendar`; a real run only accepts `FROZEN` (runner).
Nothing here touches the network, keys, or Live.
"""
from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timezone
from typing import Any

import numpy as np

from research import ext2_rules as rules
from research.ext2_calendar import FROZEN, Calendar, day_ms
from research.ext2_costs import (
    FEE_BP,
    FUNDING,
    Ext2Stop,
    fit_roll_half_spread_bp,
    friction_bp_per_side,
    stress_friction_bp_per_side,
)
from research.ext2_features import (
    ALL_Q,
    FEATURES,
    PERCENTILES,
    entry_print_index,
    feature_matrix,
    regime_labels,
    thesis_next_bar,
    trailing_percentiles,
    trigger_directions,
)
from research.ext2_lattice import EXIT_GRID_N, EXIT_TABLE, PrintPath, compute_base, resolve
from research.ext2_rules import ENTRY_DELAY_MS, ENTRY_LATENCY_CAP_MS

MODES = ("follow", "fade")
REGIMES = ("NONE", "BULL", "BEAR", "SIDE")
N_SINGLE = len(FEATURES)
MEMBERS: tuple[tuple[int, ...], ...] = tuple((i,) for i in range(N_SINGLE)) + tuple(
    (i, j) for i in range(N_SINGLE) for j in range(i + 1, N_SINGLE)
)
N_MEMBERS = len(MEMBERS)  # 21
N_L = 4
N_P = len(PERCENTILES)
N_M = len(MODES)
N_G = len(REGIMES)
N_KEYS = N_MEMBERS * N_L * N_P * N_M * N_G  # 3360
N_GRID_CELLS = N_KEYS * EXIT_GRID_N  # 181440
S1_CELL = N_GRID_CELLS
S2_CELL = N_GRID_CELLS + 1
N_CELLS = N_GRID_CELLS + 2  # 181442
SWITCH_REGIMES = (1, 2, 3)
# RK D2 (10 Nov): which feature's thesis cut a PAIR uses is undefined in card v3 / A10.
# Hook only; Strategist pre-registers one choice in v4. Default = the shipped rule.
#   lowest_id: the pair's lower feature id (shipped behaviour)
#   either:    cut at the first print where EITHER feature's thesis has crossed back
#   both:      cut at the first print where BOTH have crossed back (the later of the two)
PAIR_THESIS_RULES = ("lowest_id", "either", "both")
PAIR_THESIS_RULE = "lowest_id"


def key_id(member: int, li: int, pi: int, mi: int, gi: int) -> int:
    return (((member * N_L + li) * N_P + pi) * N_M + mi) * N_G + gi


def key_parts(key: int) -> tuple[int, int, int, int, int]:
    gi = key % N_G
    rest = key // N_G
    mi = rest % N_M
    rest //= N_M
    pi = rest % N_P
    rest //= N_P
    li = rest % N_L
    member = rest // N_L
    return member, li, pi, mi, gi


def enumerate_cells() -> list[tuple]:
    """N9: enumerate actual cell identities (not a loop product) and assert uniqueness."""
    out = []
    for member in range(N_MEMBERS):
        for li in range(N_L):
            for pi in range(N_P):
                for mi in range(N_M):
                    for gi in range(N_G):
                        for x in range(EXIT_GRID_N):
                            out.append((MEMBERS[member], li, pi, mi, gi, int(EXIT_TABLE["tp"][x]),
                                        int(EXIT_TABLE["ts"][x]), int(EXIT_TABLE["tc"][x]), int(EXIT_TABLE["hs"][x])))
    out.append(("S1",))
    out.append(("S2",))
    if len(set(out)) != len(out):
        raise Ext2Stop("STOP_COUNT")
    return out


def _bars_for_days(calendar: Calendar, days: Sequence[date]) -> np.ndarray:
    origin = calendar.origin_ms()
    out = []
    for day in days:
        first = (day_ms(day) - origin) // 60_000
        out.append(np.arange(first, first + 1440, dtype=np.int64))
    return np.concatenate(out) if out else np.zeros(0, dtype=np.int64)


def _utc_day(mts: int) -> date:
    return datetime.fromtimestamp(mts / 1000.0, timezone.utc).date()


def prepare(grid: Mapping[str, Any], trades: Mapping[str, np.ndarray], calendar: Calendar = FROZEN,
            timer: Callable[[str, float], None] | None = None) -> dict[str, Any]:
    """Features, trailing percentiles (95% rule), triggers, regimes, thesis lookups."""
    t0 = time.monotonic()
    feats = feature_matrix(grid, trades, calendar)
    t1 = time.monotonic()
    close = feats["close"]
    n = feats["n"]
    regimes = regime_labels(close, calendar)
    pct: dict[tuple[int, int], np.ndarray] = {}
    trig: dict[tuple[int, int], np.ndarray] = {}
    eligible: dict[tuple[int, int], np.ndarray] = {}
    share: dict[tuple[int, int], np.ndarray] = {}
    origin = int(grid["origin_mts"])
    mts = np.asarray(trades["mts"], dtype=np.int64)
    th_idx = np.full((N_SINGLE, N_L, 2, n), mts.shape[0], dtype=np.int64)
    close_mts = feats["close_mts"]
    window = calendar.percentile_bars
    for fi, name in enumerate(FEATURES):
        for li, lookback in enumerate(calendar.lookbacks):
            values = feats["features"][name][lookback]
            p = trailing_percentiles(values, window, ALL_Q)
            pct[(fi, li)] = p
            trig[(fi, li)] = trigger_directions(values, p)
            eligible[(fi, li)] = np.isfinite(values) & np.isfinite(p[:, 0])
            finite = np.isfinite(values).astype(np.float64)
            csum = np.concatenate(([0.0], np.cumsum(finite)))
            sh = np.full(n, np.nan)
            if n > window:
                idx = np.arange(window, n)
                sh[idx] = (csum[idx] - csum[idx - window]) / window
            share[(fi, li)] = sh
            nxt = thesis_next_bar(values, p[:, 10])
            for di, d in enumerate((1, -1)):
                j = nxt[d]
                has = j < n
                when = np.where(has, close_mts[np.minimum(j, n - 1)], np.iinfo(np.int64).max)
                th_idx[fi, li, di] = np.where(has, np.searchsorted(mts, when, side="left"), mts.shape[0])
    t2 = time.monotonic()
    if timer is not None:
        timer("features", t1 - t0)
        timer("rolling_quantiles", t2 - t1)
    return {
        "calendar": calendar, "grid": grid, "trades": trades, "features": feats, "regimes": regimes,
        "pct": pct, "trig": trig, "eligible": eligible, "window_share": share, "thesis_idx": th_idx,
        "origin_mts": origin, "n": n,
    }


def member_directions(ctx: Mapping[str, Any], member: int, li: int, pi: int, bars: np.ndarray) -> np.ndarray:
    """A7: singles use their trigger; a pair fires only when both features fire the
    same side on the same bar at the same L and p (disagree/undefined -> none)."""
    feats = MEMBERS[member]
    d = ctx["trig"][(feats[0], li)][bars, pi].astype(np.int8)
    for other in feats[1:]:
        e = ctx["trig"][(other, li)][bars, pi]
        d = np.where(d == e, d, 0).astype(np.int8)
    return d


def pair_thesis_rule_check(rule: str) -> str:
    if rule not in PAIR_THESIS_RULES:
        raise Ext2Stop("STOP_PAIR_THESIS_RULE")
    return rule


def thesis_print_index(th_all: np.ndarray, member: np.ndarray, li: np.ndarray, di: np.ndarray, bar: np.ndarray,
                       rule: str | None = None) -> np.ndarray:
    """Thesis-cut print index per chain (singles: their own feature; pairs: RK D2 hook)."""
    chosen = pair_thesis_rule_check(PAIR_THESIS_RULE if rule is None else rule)
    first = th_all[_FIRST_FEATURE[member], li, di, bar]
    if chosen == "lowest_id":
        return first
    last = th_all[_LAST_FEATURE[member], li, di, bar]
    if chosen == "either":
        return np.minimum(first, last)
    return np.maximum(first, last)


def build_segments(ctx: Mapping[str, Any], calendar: Calendar = FROZEN) -> list[dict[str, Any]]:
    trades = ctx["trades"]
    mts = np.asarray(trades["mts"], dtype=np.int64)
    price = np.asarray(trades["price"], dtype=np.float64)
    test_day_list = list(calendar.test_days())
    segments = []
    for fold_index, fold in enumerate(calendar.folds):
        for kind in ("test", "val"):
            days = fold.test_days() if kind == "test" else fold.val_days()
            fit_days = fold.train_days() if kind == "test" else fold.fit_days()
            boundary = fold.test_censor_ms() if kind == "test" else fold.val_censor_ms()
            prices_by_day = {}
            for day in fit_days:
                lo = np.searchsorted(mts, day_ms(day), side="left")
                hi = np.searchsorted(mts, day_ms(day) + 86_400_000, side="left")
                prices_by_day[day] = price[lo:hi]
            roll = fit_roll_half_spread_bp(prices_by_day, fit_days=list(fit_days), test_day=days[0],
                                           test_days=list(days), fold_train_days=list(fit_days))
            fric = friction_bp_per_side(roll)
            bars = _bars_for_days(calendar, days)
            day_index = np.full(bars.shape[0], -1, dtype=np.int64)
            if kind == "test":
                for k, day in enumerate(days):
                    day_index[k * 1440:(k + 1) * 1440] = test_day_list.index(day)
            segments.append({
                "name": f"{fold.name}-{kind}", "fold": fold_index, "kind": kind, "bars": bars,
                "boundary_mts": boundary, "roll_half_spread_bp": roll, "friction_bp_per_side": fric,
                "stress_friction_bp_per_side": stress_friction_bp_per_side(roll),
                "day_index": day_index, "days": [d.isoformat() for d in days],
            })
    return segments


def segment_entries(ctx: Mapping[str, Any], seg: Mapping[str, Any]) -> dict[str, np.ndarray]:
    """A9 entry for every bar of a segment: first print >= close + 1000 ms, no later
    than +60 s (stale -> no trade), strictly before the active censor boundary."""
    trades = ctx["trades"]
    mts = np.asarray(trades["mts"], dtype=np.int64)
    price = np.asarray(trades["price"], dtype=np.float64)
    close_mts = ctx["features"]["close_mts"][seg["bars"]]
    idx = entry_print_index(mts, close_mts)
    has = idx < mts.shape[0]
    safe = np.minimum(idx, mts.shape[0] - 1)
    entry_mts = np.where(has, mts[safe], -1)
    ready = close_mts + ENTRY_DELAY_MS
    latency = np.where(has, entry_mts - ready, np.iinfo(np.int64).max)
    fresh = has & (latency <= ENTRY_LATENCY_CAP_MS)
    before = has & (entry_mts < int(seg["boundary_mts"]))
    valid = fresh & before
    if np.any(valid & ~(entry_mts >= close_mts + ENTRY_DELAY_MS)):
        raise Ext2Stop("STOP_C3C")
    return {"entry_idx": idx, "entry_mts": entry_mts, "entry_price": np.where(has, price[safe], np.nan),
            "valid": valid, "stale": has & ~fresh, "latency_ms": latency, "close_mts": close_mts}


def compute_lattice(ctx: Mapping[str, Any], segments: list[dict[str, Any]], backend,
                    on_chunk: Callable[[int], None] | None = None) -> dict[str, Any]:
    trades = ctx["trades"]
    path = PrintPath(trades["mts"], trades["price"], backend)
    em, ep, sd, bd = [], [], [], []
    offset = 0
    for seg in segments:
        ent = segment_entries(ctx, seg)
        seg["entries"] = ent
        seg["event_offset"] = offset
        m = seg["bars"].shape[0]
        for s in (1.0, -1.0):
            em.append(np.where(ent["valid"], ent["entry_mts"], 0))
            ep.append(np.where(ent["valid"], ent["entry_price"], 1.0))
            sd.append(np.full(m, s))
            bd.append(np.full(m, int(seg["boundary_mts"]), dtype=np.int64))
        offset += 2 * m
    t0 = time.monotonic()
    base = compute_base(path, np.concatenate(em), np.concatenate(ep), np.concatenate(sd),
                        np.concatenate(bd), on_chunk=on_chunk)
    base["seconds"] = time.monotonic() - t0
    return base


class Accumulator:
    """Per-cell aggregates over out-of-fold (test) and inner-val trades."""

    def __init__(self, n_test_days: int, n_folds: int):
        self.n_test_days = n_test_days
        self.n_folds = n_folds
        c = N_GRID_CELLS
        self.n = np.zeros(c, dtype=np.int64)
        self.gross = np.zeros(c)
        self.net = np.zeros(c)
        self.stress = np.zeros(c)
        self.tick = np.zeros(c)
        self.rt = np.zeros(c)
        self.fold_net = np.zeros((c, n_folds))
        self.fold_n = np.zeros((c, n_folds), dtype=np.int64)
        self.daily = np.zeros((c, n_test_days))
        self.daily_n = np.zeros((c, n_test_days), dtype=np.int32)
        self.val_net = np.zeros((c, n_folds))
        self.val_n = np.zeros((c, n_folds), dtype=np.int64)
        self.path_end = np.zeros(c, dtype=np.int64)  # per-cell PATH_END (code 8), report-only
        self.counts = {"liquidation_count": 0, "censored_close_count": 0, "path_end_count": 0,
                       "dropped_count": 0, "trades_oof": 0, "trades_val": 0}

    def add(self, seg: Mapping[str, Any], cell: np.ndarray, res: Mapping[str, np.ndarray], gross: np.ndarray,
            net: np.ndarray, stress: np.ndarray, tick: np.ndarray, day: np.ndarray) -> None:
        c = N_GRID_CELLS
        fold = int(seg["fold"])
        if seg["kind"] == "test":
            self.n += np.bincount(cell, minlength=c)
            self.gross += np.bincount(cell, weights=gross, minlength=c)
            self.net += np.bincount(cell, weights=net, minlength=c)
            self.stress += np.bincount(cell, weights=stress, minlength=c)
            self.tick += np.bincount(cell, weights=tick, minlength=c)
            self.rt += np.bincount(cell, minlength=c) * 2.0 * float(seg["friction_bp_per_side"])
            self.fold_net[:, fold] += np.bincount(cell, weights=net, minlength=c)
            self.fold_n[:, fold] += np.bincount(cell, minlength=c)
            flat = cell * self.n_test_days + day
            size = c * self.n_test_days
            self.daily += np.bincount(flat, weights=net, minlength=size).reshape(c, self.n_test_days)
            self.daily_n += np.bincount(flat, minlength=size).reshape(c, self.n_test_days).astype(np.int32)
            code = res["code"]
            self.counts["liquidation_count"] += int(np.sum(code == 5))
            self.counts["censored_close_count"] += int(np.sum(code == 6))
            self.counts["path_end_count"] += int(np.sum(code == 8))
            self.path_end += np.bincount(cell[code == 8], minlength=c)
            self.counts["trades_oof"] += int(cell.size)
        else:
            self.val_net[:, fold] += np.bincount(cell, weights=net, minlength=c)
            self.val_n[:, fold] += np.bincount(cell, minlength=c)
            self.counts["trades_val"] += int(cell.size)


def _segment_candidates(ctx: Mapping[str, Any], seg: Mapping[str, Any],
                        keys: Sequence[int] | None = None, shift: int = 0) -> dict[str, np.ndarray]:
    """Candidate signal bars per key (sorted), concatenated with offsets."""
    bars = seg["bars"]
    m = bars.shape[0]
    ent = seg["entries"]
    regime = ctx["regimes"]["code"][bars]
    wanted = range(N_KEYS) if keys is None else sorted(set(int(k) for k in keys))
    us, sides, keys_out, offsets = [], [], [], []
    count = 0
    cache: dict[tuple[int, int, int], np.ndarray] = {}
    key_list = []
    for key in wanted:
        member, li, pi, mi, gi = key_parts(key)
        sig = cache.get((member, li, pi))
        if sig is None:
            sig = member_directions(ctx, member, li, pi, bars)
            if shift:
                shifted = np.zeros_like(sig)
                shifted[shift:] = sig[:-shift]
                sig = shifted
            cache[(member, li, pi)] = sig
        mask = (sig != 0) & ent["valid"]
        if gi > 0:
            mask &= regime == gi
        u = np.flatnonzero(mask)
        side = sig[u].astype(np.float64) * (1.0 if mi == 0 else -1.0)
        key_list.append(key)
        offsets.append(count)
        us.append(u)
        sides.append(side)
        keys_out.append(np.full(u.shape[0], key, dtype=np.int64))
        count += u.shape[0]
    offsets.append(count)
    return {
        "u": np.concatenate(us) if us else np.zeros(0, dtype=np.int64),
        "side": np.concatenate(sides) if sides else np.zeros(0),
        "key": np.concatenate(keys_out) if keys_out else np.zeros(0, dtype=np.int64),
        "keys": np.asarray(key_list, dtype=np.int64),
        "offsets": np.asarray(offsets, dtype=np.int64),
        "m": m,
    }


def run_chains(ctx: Mapping[str, Any], seg: Mapping[str, Any], base: Mapping[str, np.ndarray],
               chains: Sequence[tuple[int, int]] | None = None, *, record: bool = False,
               acc: Accumulator | None = None, shift: int = 0,
               flush_every: int = 4_000_000) -> dict[str, Any]:
    """A9 per-cell paths: one position per cell, next signal bar >= exit bar + 1.

    chains: (key, x) pairs; None = all 3360 keys x 54 exits. Vectorised: every
    active chain advances one trade per step."""
    trades = ctx["trades"]
    mts = np.asarray(trades["mts"], dtype=np.int64)
    price = np.asarray(trades["price"], dtype=np.float64)
    origin = int(ctx["origin_mts"])
    keys = None if chains is None else [k for k, _ in chains]
    cand = _segment_candidates(ctx, seg, keys, shift=shift)
    m = cand["m"]
    key_pos = {int(k): i for i, k in enumerate(cand["keys"].tolist())}
    if chains is None:
        ck = np.repeat(np.arange(cand["keys"].shape[0]), EXIT_GRID_N)
        cx = np.tile(np.arange(EXIT_GRID_N), cand["keys"].shape[0])
    else:
        ck = np.asarray([key_pos[int(k)] for k, _ in chains], dtype=np.int64)
        cx = np.asarray([int(x) for _, x in chains], dtype=np.int64)
    off = cand["offsets"]
    pos = off[ck].copy()
    end = off[ck + 1]
    active = pos < end
    ck, cx, pos, end = ck[active], cx[active], pos[active], end[active]
    encoded = np.repeat(np.arange(cand["keys"].shape[0], dtype=np.int64), np.diff(off)) * (m + 1) + cand["u"]
    bars = seg["bars"]
    ent = seg["entries"]
    fric = float(seg["friction_bp_per_side"])
    th_all = ctx["thesis_idx"]
    thesis_rule = ctx.get("pair_thesis_rule", PAIR_THESIS_RULE)
    records: list[dict[str, np.ndarray]] = []
    buffered: list[dict[str, np.ndarray]] = []
    buffered_n = 0
    steps = 0
    while ck.size:
        steps += 1
        u = cand["u"][pos]
        side = cand["side"][pos]
        key = cand["keys"][ck]
        member = key // (N_L * N_P * N_M * N_G)
        li = (key // (N_P * N_M * N_G)) % N_L
        mi = (key // N_G) % N_M
        trigger_dir = side * np.where(mi == 0, 1.0, -1.0)
        di = np.where(trigger_dir > 0, 0, 1)
        bar = bars[u]
        th = thesis_print_index(th_all, member, li, di, bar, thesis_rule)
        ev = int(seg["event_offset"]) + np.where(side > 0, 0, m) + u
        e_mts = ent["entry_mts"][u]
        e_px = ent["entry_price"][u]
        res = resolve(base, ev, cx, th, mts, price, e_mts, e_px, side)
        kept = res["code"] != 7
        cell = key * EXIT_GRID_N + cx
        if np.any(kept):
            gross = res["gross_bp"][kept]
            net = gross - 2.0 * fric
            stress = gross - 2.0 * float(seg["stress_friction_bp_per_side"])
            tick = net - 10000.0 / e_px[kept] - 10000.0 / res["exit_price"][kept]
            chunk = {"cell": cell[kept], "u": u[kept], "side": side[kept], "gross": gross, "net": net,
                     "stress": stress, "tick": tick, "day": seg["day_index"][u[kept]],
                     "code": res["code"][kept], "entry_mts": e_mts[kept], "exit_mts": res["exit_mts"][kept],
                     "hold_ms": res["hold_ms"][kept], "exit_price": res["exit_price"][kept],
                     "entry_price": e_px[kept]}
            if record:
                records.append(chunk)
            if acc is not None:
                buffered.append(chunk)
                buffered_n += chunk["cell"].size
                if buffered_n >= flush_every:
                    _flush(acc, seg, buffered)
                    buffered, buffered_n = [], 0
        exit_bar = np.where(kept, (res["exit_mts"] - origin) // 60_000, -1)
        exit_u = exit_bar - int(bars[0])
        nxt_target = np.where(kept, ck * (m + 1) + np.maximum(exit_u + 1, 0), -1)
        nxt = np.where(kept, np.searchsorted(encoded, nxt_target, side="left"), pos + 1)
        nxt = np.maximum(nxt, pos + 1)
        alive = nxt < end
        ck, cx, pos, end = ck[alive], cx[alive], nxt[alive], end[alive]
    if acc is not None and buffered:
        _flush(acc, seg, buffered)
    out: dict[str, Any] = {"steps": steps, "candidates": int(cand["u"].shape[0])}
    if record:
        keys_r = ("cell", "u", "side", "gross", "net", "stress", "tick", "day", "code", "entry_mts",
                  "exit_mts", "hold_ms", "exit_price", "entry_price")
        out["trades"] = {k: (np.concatenate([r[k] for r in records]) if records else np.zeros(0)) for k in keys_r}
    return out


_FIRST_FEATURE = np.asarray([members[0] for members in MEMBERS], dtype=np.int64)
_LAST_FEATURE = np.asarray([members[-1] for members in MEMBERS], dtype=np.int64)


def _flush(acc: Accumulator, seg: Mapping[str, Any], buffered: list[dict[str, np.ndarray]]) -> None:
    cat = {k: np.concatenate([b[k] for b in buffered]) for k in buffered[0]}
    acc.add(seg, cat["cell"].astype(np.int64), {"code": cat["code"]}, cat["gross"], cat["net"], cat["stress"],
            cat["tick"], cat["day"].astype(np.int64))


def test_day_weeks(calendar: Calendar) -> np.ndarray:
    return np.asarray([d.isocalendar()[0] * 100 + d.isocalendar()[1] for d in calendar.test_days()], dtype=np.int64)


def cell_daily_series(acc: Accumulator, cell: int) -> np.ndarray:
    present = acc.daily_n[cell] > 0
    return acc.daily[cell][present]


def replay_cells(ctx, segments, base, cells: Sequence[int], *, shift: int = 0,
                 kinds: Sequence[str] = ("test",), folds: Sequence[int] | None = None) -> dict[int, dict[str, np.ndarray]]:
    """Trade-level replay of selected grid cells (for n_eff, CI, median hold, switching, +1 bar)."""
    by_cell: dict[int, list[dict[str, np.ndarray]]] = {int(c): [] for c in cells}
    chains = [(int(c) // EXIT_GRID_N, int(c) % EXIT_GRID_N) for c in cells]
    if not chains:
        return {}
    for seg in segments:
        if seg["kind"] not in kinds or (folds is not None and seg["fold"] not in folds):
            continue
        out = run_chains(ctx, seg, base, chains, record=True, shift=shift)
        tr = out["trades"]
        if tr["cell"].size == 0:
            continue
        for c in by_cell:
            sel = tr["cell"] == c
            if np.any(sel):
                piece = {k: v[sel] for k, v in tr.items()}
                piece["fold"] = np.full(int(sel.sum()), seg["fold"], dtype=np.int64)
                by_cell[c].append(piece)
    merged = {}
    for c, parts in by_cell.items():
        if parts:
            merged[c] = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
        else:
            merged[c] = None
    return merged


def trade_level_metrics(trades: Mapping[str, np.ndarray] | None, cell_id: int, calendar: Calendar) -> dict[str, Any]:
    if trades is None or trades["net"].size == 0:
        return {"n_eff": 0, "ci_low": None, "ci_high": None, "median_hold_s": None}
    clusters = trades["entry_mts"] // 3_600_000
    boot = rules.cluster_bootstrap(trades["net"], clusters, seed=rules.WHITE_RC_SEED + int(cell_id))
    boot["median_hold_s"] = float(np.median(trades["hold_ms"] / 1000.0))
    return boot


def package_metrics(trades: Mapping[str, np.ndarray] | None, calendar: Calendar, n_folds: int) -> dict[str, Any]:
    days = len(calendar.test_days())
    if trades is None or trades["net"].size == 0:
        return {"n_trades": 0, "daily": np.zeros(days), "daily_n": np.zeros(days, dtype=np.int64)}
    daily = np.bincount(trades["day"].astype(np.int64), weights=trades["net"], minlength=days)
    daily_n = np.bincount(trades["day"].astype(np.int64), minlength=days)
    fold_net = np.bincount(trades["fold"], weights=trades["net"], minlength=n_folds)
    return {"n_trades": int(trades["net"].size), "gross_mean": float(trades["gross"].mean()),
            "net_sum": float(trades["net"].sum()), "stress_sum": float(trades["stress"].sum()),
            "tick_sum": float(trades["tick"].sum()), "fold_net": fold_net, "daily": daily, "daily_n": daily_n,
            "rt_mean": float(np.mean(trades["gross"] - trades["net"]))}


def switching(ctx, segments, base, acc: Accumulator) -> dict[str, Any]:
    """A16 S1/S2 from inner-val only. Legs are G=R cells with >= 20 inner-val trades;
    best inner-val net (tie: smaller cell id). S2 drops legs with inner-val net <= 0."""
    calendar: Calendar = ctx["calendar"]
    gi_of_cell = (np.arange(N_GRID_CELLS) // EXIT_GRID_N) % N_G
    picks: dict[str, dict[str, Any]] = {"S1": {}, "S2": {}}
    for fold_index, fold in enumerate(calendar.folds):
        for regime in SWITCH_REGIMES:
            ok = (gi_of_cell == regime) & (acc.val_n[:, fold_index] >= rules.SWITCH_MIN_INNER_TRADES)
            cand = np.flatnonzero(ok)
            label = REGIMES[regime]
            if cand.size == 0:
                picks["S1"][f"{fold.name}:{label}"] = None
                picks["S2"][f"{fold.name}:{label}"] = None
                continue
            vals = acc.val_net[cand, fold_index]
            best = int(cand[np.lexsort((cand, -vals))[0]])
            picks["S1"][f"{fold.name}:{label}"] = best
            picks["S2"][f"{fold.name}:{label}"] = best if acc.val_net[best, fold_index] > 0 else None
    packages = {}
    for name in ("S1", "S2"):
        parts = []
        legs = sorted({c for c in picks[name].values() if c is not None})
        replay = replay_cells(ctx, segments, base, legs) if legs else {}
        for fold_index, fold in enumerate(calendar.folds):
            for regime in SWITCH_REGIMES:
                cell = picks[name][f"{fold.name}:{REGIMES[regime]}"]
                if cell is None or replay.get(cell) is None:
                    continue
                tr = replay[cell]
                sel = tr["fold"] == fold_index
                if np.any(sel):
                    parts.append({k: v[sel] for k, v in tr.items()})
        merged = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]} if parts else None
        leg_ok = all(int(acc.n[c]) >= rules.SWITCH_MIN_LEG_OOF_TRADES for c in legs) if legs else False
        packages[name] = {"legs": picks[name], "trades": merged, "legs_min_oof_ok": leg_ok,
                          "not_tested_regimes": [k for k, v in picks[name].items() if v is None]}
    return packages


def evaluate_gates(ctx, segments, base, acc: Accumulator, packages: Mapping[str, Any],
                   backend=None, timer: Callable[[str, float], None] | None = None) -> dict[str, Any]:
    """A17 gates 1-12 over all N = 181,442 cells (untested cells p = 1)."""
    calendar: Calendar = ctx["calendar"]
    n_folds = len(calendar.folds)
    weeks = test_day_weeks(calendar)
    pkg = {name: package_metrics(packages[name]["trades"], calendar, n_folds) for name in ("S1", "S2")}
    n_trades = np.concatenate([acc.n, [pkg["S1"]["n_trades"], pkg["S2"]["n_trades"]]])
    with np.errstate(divide="ignore", invalid="ignore"):
        gross_mean = np.where(acc.n > 0, acc.gross / np.maximum(acc.n, 1), np.nan)
        rt_mean = np.where(acc.n > 0, acc.rt / np.maximum(acc.n, 1), np.nan)
    gross_mean = np.concatenate([gross_mean, [pkg[s].get("gross_mean", np.nan) for s in ("S1", "S2")]])
    rt_mean = np.concatenate([rt_mean, [pkg[s].get("rt_mean", np.nan) for s in ("S1", "S2")]])
    net_sum = np.concatenate([acc.net, [pkg[s].get("net_sum", 0.0) for s in ("S1", "S2")]])
    stress_sum = np.concatenate([acc.stress, [pkg[s].get("stress_sum", 0.0) for s in ("S1", "S2")]])
    tick_sum = np.concatenate([acc.tick, [pkg[s].get("tick_sum", 0.0) for s in ("S1", "S2")]])
    fold_net = np.vstack([acc.fold_net] + [np.asarray(pkg[s].get("fold_net", np.zeros(n_folds)))[None, :]
                                           for s in ("S1", "S2")])
    daily = np.vstack([acc.daily, pkg["S1"]["daily"][None, :], pkg["S2"]["daily"][None, :]])
    daily_n = np.vstack([acc.daily_n, pkg["S1"]["daily_n"][None, :], pkg["S2"]["daily_n"][None, :]])
    folds_pos = (fold_net > 0).sum(axis=1)
    g1 = gross_mean >= 2.0 * rt_mean
    g2 = net_sum > 0
    g3 = stress_sum > 0
    g4 = folds_pos >= rules.MIN_FOLDS_POS
    g5 = n_trades >= rules.MIN_OOF_TRADES
    t0 = time.monotonic()
    p_hac = np.ones(N_CELLS)
    t_count = (daily_n > 0).sum(axis=1)
    tested = np.zeros(N_CELLS, dtype=bool)
    sr = np.full(N_CELLS, np.nan)
    gate6_method = ctx.get("gate6_method", rules.GATE6_METHOD)
    for cell in np.flatnonzero(t_count >= rules.T_MIN_TEST_DAYS):
        series = daily[cell][daily_n[cell] > 0]
        stat = rules.hac_statistic(series)
        if stat["testable"]:
            tested[cell] = True
            p_hac[cell] = rules.primary_p_from_stat(stat, gate6_method)
            sst = rules.sharpe_stats(series)
            if sst is not None:
                sr[cell] = sst["sr"]
    bh = rules.bh_reject(p_hac)
    t1 = time.monotonic()
    rc = rules.white_rc_pvalues(daily, tested, backend=backend)
    t2 = time.monotonic()
    sr_var = float(np.nanvar(sr[tested], ddof=1)) if np.sum(tested & np.isfinite(sr)) > 1 else 0.0
    sr0 = rules.expected_max_sharpe(sr_var)
    dsr = np.full(N_CELLS, np.nan)
    conc_day = np.ones(N_CELLS)
    conc_week = np.ones(N_CELLS)
    for cell in np.flatnonzero(tested):
        series = daily[cell][daily_n[cell] > 0]
        dsr[cell] = rules.deflated_sharpe(series, sr0)
        conc = rules.concentration(daily[cell], weeks)
        conc_day[cell] = conc["max_day_share"]
        conc_week[cell] = conc["max_week_share"]
    g6 = bh
    g7 = rules.white_rc_gate(rc["p"])
    g8 = np.nan_to_num(dsr, nan=0.0) >= rules.DSR_MIN
    g10 = (conc_day <= rules.MAX_DAY_SHARE) & (conc_week <= rules.MAX_WEEK_SHARE)
    pre = g1 & g2 & g3 & g4 & g5
    p_boot = np.full(N_CELLS, np.nan)
    for cell in np.flatnonzero(pre & tested):
        series = daily[cell][daily_n[cell] > 0]
        p_boot[cell] = rules.bootstrap_mean_pvalue(series, seed=rules.WHITE_RC_SEED + int(cell))
    daily_ok = pre & g6 & g7 & g8 & g10
    n_eff = np.full(N_CELLS, np.nan)
    ci_low = np.full(N_CELLS, np.nan)
    ci_high = np.full(N_CELLS, np.nan)
    median_hold = np.full(N_CELLS, np.nan)
    grid_cand = [int(c) for c in np.flatnonzero(daily_ok[:N_GRID_CELLS])]
    replay = replay_cells(ctx, segments, base, grid_cand) if grid_cand else {}
    for cell in grid_cand:
        mtr = trade_level_metrics(replay.get(cell), cell, calendar)
        n_eff[cell], median_hold[cell] = mtr["n_eff"], (mtr["median_hold_s"] or np.nan)
        ci_low[cell], ci_high[cell] = (mtr["ci_low"] or np.nan), (mtr["ci_high"] or np.nan)
    for offset, name in ((0, "S1"), (1, "S2")):
        cell = N_GRID_CELLS + offset
        mtr = trade_level_metrics(packages[name]["trades"], cell, calendar)
        n_eff[cell], median_hold[cell] = mtr["n_eff"], (mtr["median_hold_s"] or np.nan)
        ci_low[cell], ci_high[cell] = (mtr["ci_low"] or np.nan), (mtr["ci_high"] or np.nan)
    g9 = np.nan_to_num(n_eff, nan=0.0) >= rules.NEFF_LEAD_FLOOR
    g11 = np.nan_to_num(median_hold, nan=0.0) >= rules.MIN_MEDIAN_HOLD_S
    legs_ok = np.ones(N_CELLS, dtype=bool)
    legs_ok[S1_CELL] = packages["S1"]["legs_min_oof_ok"]
    legs_ok[S2_CELL] = packages["S2"]["legs_min_oof_ok"]
    all_gates = daily_ok & g9 & g11 & legs_ok
    lead_ids = np.flatnonzero(all_gates)
    if lead_ids.size > rules.MAX_LEADS:
        order = np.lexsort((lead_ids, -net_sum[lead_ids]))
        lead_ids = lead_ids[order][: rules.MAX_LEADS]
    is_lead = np.zeros(N_CELLS, dtype=bool)
    is_lead[lead_ids] = True
    if timer is not None:
        timer("hac_bh", t1 - t0)
        timer("white_rc_panel", t2 - t1)
    return {
        "n_trades": n_trades, "gross_mean": gross_mean, "net_sum": net_sum, "stress_sum": stress_sum,
        "tick_sum": tick_sum, "folds_pos": folds_pos, "p_hac": p_hac, "tested": tested, "bh": bh,
        "p_boot": p_boot, "white_rc_p": rc["p"], "rc": {k: v for k, v in rc.items() if k != "p"},
        "dsr": dsr, "sr0": sr0, "sr_var": sr_var, "n_eff": n_eff, "ci_low": ci_low, "ci_high": ci_high,
        "max_day_share": conc_day, "max_week_share": conc_week, "median_hold_s": median_hold,
        "is_lead": is_lead, "lead_ids": [int(c) for c in lead_ids], "t_days": t_count,
        "gate_pass_counts": {f"g{k}": int(np.sum(v)) for k, v in
                             ((1, g1), (2, g2), (3, g3), (4, g4), (5, g5), (6, g6), (7, g7), (8, g8),
                              (9, g9), (10, g10), (11, g11))},
        "daily": daily, "daily_n": daily_n,
        "path_end_count": np.concatenate([acc.path_end, [_package_path_end(packages[name]["trades"])
                                                          for name in ("S1", "S2")]]),
    }


def _package_path_end(trades: Mapping[str, np.ndarray] | None) -> int:
    return 0 if trades is None or "code" not in trades else int(np.sum(np.asarray(trades["code"]) == 8))


def day_basis_receipt(gates: Mapping[str, Any], calendar: Calendar) -> dict[str, Any]:
    """GS v4 CF13: per-gate day basis and T, recorded (behaviour unchanged in push 4)."""
    tested = np.asarray(gates["tested"], dtype=bool)
    t_days = np.asarray(gates["t_days"])[tested]
    t_stats = None if t_days.size == 0 else {"min": int(t_days.min()), "median": float(np.median(t_days)),
                                              "max": int(t_days.max()), "tested_cells": int(t_days.size)}
    all_days = len(calendar.test_days())
    return {
        "g6_hac_t": {"basis": "trade_days (test days with >= 1 trade)", "T": "per cell = t_days (cells.npz)",
                     "T_tested": t_stats, "hac_lag_basis": "trade_days", "t_min_test_days": rules.T_MIN_TEST_DAYS},
        "g8_dsr": {"basis": "trade_days", "T": "per cell = t_days (cells.npz)", "T_tested": t_stats},
        "g7_white_rc": {"basis": "all_test_days_zero_filled", "T": all_days},
        "g10_concentration": {"basis": "all_test_days_zero_filled", "T": all_days},
        "p_boot": {"basis": "trade_days", "role": "reported_only"},
        "note": "unchanged in push 4 (HM: both bases correct; not reconciled); disclosed per GS v4 CF13",
    }


CELL_DTYPE = np.dtype([
    ("cell_id", np.int64), ("kind", "U6"), ("feature_ids", "U8"), ("L", np.int64), ("p", np.float64),
    ("M", "U6"), ("G", "U4"), ("TP", np.float64), ("TS", np.int64), ("TC", np.int64), ("HS", np.float64),
    ("n_trades", np.int64), ("gross_mean_bp", np.float64), ("net_sum_bp", np.float64), ("folds_pos", np.int64),
    ("t_days", np.int64), ("p_hac", np.float64), ("p_boot", np.float64), ("bh_pass", np.bool_),
    ("white_rc_p", np.float64), ("dsr", np.float64), ("n_eff", np.float64), ("ci_low_bp", np.float64),
    ("ci_high_bp", np.float64), ("max_day_share", np.float64), ("max_week_share", np.float64),
    ("median_hold_s", np.float64), ("is_lead", np.bool_), ("net_stress_1_5_sum_bp", np.float64),
    ("net_stress_tick_sum_bp", np.float64), ("path_end_count", np.int64),
])


def cells_table(gates: Mapping[str, Any], calendar: Calendar) -> np.ndarray:
    from research.ext2_lattice import HARD_STOP_BP, TAKE_PROFIT_BP, TIME_STOP_MIN
    table = np.zeros(N_CELLS, dtype=CELL_DTYPE)
    ids = np.arange(N_GRID_CELLS)
    key = ids // EXIT_GRID_N
    x = ids % EXIT_GRID_N
    gi = key % N_G
    mi = (key // N_G) % N_M
    pi = (key // (N_G * N_M)) % N_P
    li = (key // (N_G * N_M * N_P)) % N_L
    member = key // (N_G * N_M * N_P * N_L)
    table["cell_id"] = np.arange(N_CELLS)
    table["kind"][:N_GRID_CELLS] = np.where(member < N_SINGLE, "single", "pair")
    table["kind"][N_GRID_CELLS:] = "switch"
    names = np.asarray(["-".join(str(f + 1) for f in m) for m in MEMBERS])
    table["feature_ids"][:N_GRID_CELLS] = names[member]
    table["L"][:N_GRID_CELLS] = np.asarray(calendar.lookbacks)[li]
    table["p"][:N_GRID_CELLS] = np.asarray(PERCENTILES)[pi]
    table["M"][:N_GRID_CELLS] = np.asarray(MODES)[mi]
    table["G"][:N_GRID_CELLS] = np.asarray(REGIMES)[gi]
    table["TP"][:N_GRID_CELLS] = np.asarray(TAKE_PROFIT_BP)[EXIT_TABLE["tp"][x]]
    table["TS"][:N_GRID_CELLS] = np.asarray(TIME_STOP_MIN)[EXIT_TABLE["ts"][x]]
    table["TC"][:N_GRID_CELLS] = EXIT_TABLE["tc"][x]
    table["HS"][:N_GRID_CELLS] = np.asarray(HARD_STOP_BP)[EXIT_TABLE["hs"][x]]
    for field, key_name in (("n_trades", "n_trades"), ("gross_mean_bp", "gross_mean"), ("net_sum_bp", "net_sum"),
                            ("folds_pos", "folds_pos"), ("t_days", "t_days"), ("p_hac", "p_hac"),
                            ("p_boot", "p_boot"), ("bh_pass", "bh"), ("white_rc_p", "white_rc_p"), ("dsr", "dsr"),
                            ("n_eff", "n_eff"), ("ci_low_bp", "ci_low"), ("ci_high_bp", "ci_high"),
                            ("max_day_share", "max_day_share"), ("max_week_share", "max_week_share"),
                            ("median_hold_s", "median_hold_s"), ("is_lead", "is_lead"),
                            ("net_stress_1_5_sum_bp", "stress_sum"), ("net_stress_tick_sum_bp", "tick_sum"),
                            ("path_end_count", "path_end_count")):
        table[field] = gates[key_name]
    return table


def dry_check(ctx: Mapping[str, Any], calendar: Calendar = FROZEN) -> dict[str, Any]:
    """Coverage-only dry-check (no P&L): per feature, per pair, per L, per fold."""
    eligible = {FEATURES[fi]: {lb: ctx["eligible"][(fi, li)] for li, lb in enumerate(calendar.lookbacks)}
                for fi in range(N_SINGLE)}
    share = {FEATURES[fi]: {lb: ctx["window_share"][(fi, li)] for li, lb in enumerate(calendar.lookbacks)}
             for fi in range(N_SINGLE)}
    n = ctx["n"]
    fold_masks = {}
    for fold in calendar.folds:
        mask = np.zeros(n, dtype=bool)
        mask[_bars_for_days(calendar, fold.test_days())] = True
        fold_masks[fold.name] = mask
    members = [tuple(FEATURES[f] for f in m) for m in MEMBERS]
    report = rules.dry_check_coverage(eligible, fold_masks, share, members)
    grid = ctx["grid"]
    regime = ctx["regimes"]["code"]
    scored = np.zeros(n, dtype=bool)
    for mask in fold_masks.values():
        scored |= mask
    any_undefined = ~ctx["regimes"]["code"].astype(bool)
    per_feature_undefined = {}
    for (fi, li), mask in ctx["eligible"].items():
        per_feature_undefined[f"{FEATURES[fi]}@{calendar.lookbacks[li]}"] = int(np.sum(scored & ~mask))
        any_undefined = any_undefined | ~mask
    sigma = ctx["regimes"]["sigma"]
    first_sigma = np.flatnonzero(np.isfinite(sigma))
    report.update({
        "empty_bar_count": int(np.sum(grid["empty"])),
        "trade_minute_share": float(np.mean(~np.asarray(grid["empty"]))),
        "regime_undefined_scored_bars": int(np.sum(scored & (regime == 0))),
        "regime_share_by_fold": {
            name: {lab: float(np.mean(regime[mask] == code)) for lab, code in
                   (("UNDEFINED", 0), ("BULL", 1), ("BEAR", 2), ("SIDE", 3))}
            for name, mask in fold_masks.items()
        },
        "first_sigma_bar": None if first_sigma.size == 0 else int(first_sigma[0]),
        "first_sigma_utc": None if first_sigma.size == 0 else datetime.fromtimestamp(
            int(grid["open_mts"][first_sigma[0]]) / 1000.0, timezone.utc).isoformat(),
        "undefined_stat_bars": int(np.sum(scored & any_undefined)),
        "undefined_stat_bars_by_feature": per_feature_undefined,
        "scored_bars": int(scored.sum()),
    })
    return report


def fixture_c3b(grid_fn: Callable[[Mapping[str, np.ndarray]], Mapping[str, Any]], trades: Mapping[str, np.ndarray],
                ctx: Mapping[str, Any], tau_mts: int, calendar: Calendar) -> dict[str, Any]:
    """C3b: perturb every trade with mts >= tau (price x1.01, amount sign flipped);
    every feature, percentile, trigger, regime and thesis lookup on bars closing
    at or before tau, and every lattice first-passage before tau, must be
    byte-identical. FAIL = STOP."""
    mts = np.asarray(trades["mts"], dtype=np.int64)
    late = mts >= int(tau_mts)
    perturbed = {k: np.array(v, copy=True) for k, v in trades.items()}
    perturbed["price"][late] = perturbed["price"][late] * 1.01
    perturbed["amount"][late] = -perturbed["amount"][late]
    grid2 = grid_fn(perturbed)
    ctx2 = prepare(grid2, perturbed, calendar)
    close_mts = ctx["features"]["close_mts"]
    early = close_mts <= int(tau_mts)
    mismatches = []

    def same(a: np.ndarray, b: np.ndarray) -> bool:
        a = np.asarray(a)[early]
        b = np.asarray(b)[early]
        return a.tobytes() == b.tobytes()
    for name in FEATURES:
        for lb in calendar.lookbacks:
            if not same(ctx["features"]["features"][name][lb], ctx2["features"]["features"][name][lb]):
                mismatches.append(f"feature:{name}@{lb}")
    for k in ctx["pct"]:
        if not same(ctx["pct"][k], ctx2["pct"][k]):
            mismatches.append(f"percentile:{k}")
        if not same(ctx["trig"][k], ctx2["trig"][k]):
            mismatches.append(f"trigger:{k}")
    if not same(ctx["regimes"]["code"], ctx2["regimes"]["code"]):
        mismatches.append("regime")
    n_late = int(np.searchsorted(mts, int(tau_mts), side="left"))
    th1 = ctx["thesis_idx"][..., early]
    th2 = ctx2["thesis_idx"][..., early]
    before = th1 < n_late
    if not np.array_equal(th1[before], th2[before]) or np.any(th2[~before] < n_late):
        mismatches.append("thesis")
    return {"fixture": "C3b", "tau_mts": int(tau_mts), "bars_checked": int(early.sum()),
            "mismatches": mismatches, "pass": not mismatches}


def fixture_c3b_lattice(path_mts: np.ndarray, path_price: np.ndarray, base_fn, events: Mapping[str, np.ndarray],
                        tau_mts: int) -> dict[str, Any]:
    """C3b on the lattice: first passages before tau unchanged after perturbing prints >= tau."""
    mts = np.asarray(path_mts, dtype=np.int64)
    price = np.asarray(path_price, dtype=np.float64)
    n_late = int(np.searchsorted(mts, int(tau_mts), side="left"))
    price2 = price.copy()
    price2[n_late:] *= 1.01
    a = base_fn(mts, price, events)
    b = base_fn(mts, price2, events)
    bad = []
    for key in ("liq", "hs", "tp", "ts", "start"):
        x, y = a[key], b[key]
        early = x < n_late
        if not np.array_equal(x[early], y[early]) or np.any(y[~early] < n_late):
            bad.append(key)
    return {"fixture": "C3b-lattice", "mismatches": bad, "pass": not bad}


def fixture_c3b_lattice_on_run(ctx: Mapping[str, Any], segments: Sequence[Mapping[str, Any]], backend,
                               tau_mts: int, max_events: int = 4096) -> dict[str, Any]:
    """Runs fixture_c3b_lattice on the run's own print path with up to `max_events`
    valid entries of the first segment (both sides). FAIL = STOP (runner)."""
    trades = ctx["trades"]
    ent = segments[0]["entries"]
    pick = np.flatnonzero(ent["valid"] & (ent["entry_mts"] < int(tau_mts)))[: max_events // 2]
    em = np.concatenate([ent["entry_mts"][pick]] * 2)
    events = {"mts": em, "price": np.concatenate([ent["entry_price"][pick]] * 2),
              "side": np.concatenate([np.ones(pick.size), -np.ones(pick.size)]),
              "boundary": np.full(em.shape[0], int(segments[0]["boundary_mts"]), dtype=np.int64)}

    def base_fn(m, pr, ev):
        return compute_base(PrintPath(m, pr, backend), ev["mts"], ev["price"], ev["side"], ev["boundary"])
    out = fixture_c3b_lattice(trades["mts"], trades["price"], base_fn, events, tau_mts)
    out["events_checked"] = int(em.shape[0])
    return out


def f6_eligibility_disclosure(dry: Mapping[str, Any]) -> dict[str, Any]:
    """RK D3: f6 (large_print_L) eligibility per L and fold (structural ~5% in F1 from the
    double 30-day window). Ineligible cells stay in N as untested at p = 1; the 95%
    threshold is frozen and the span is never shortened."""
    member = dry.get("per_member", {}).get("large_print_L", {})
    shares = {lb: {fold: row["eligible_share"] for fold, row in entry["by_fold"].items()}
              for lb, entry in member.items()}
    return {"feature": "f6 large_print_L", "eligible_share_by_L_and_fold": shares,
            "rule": "ineligible cells stay in N (181,442) as untested, p = 1; 95% threshold frozen; "
                    "30-day span never shortened (RK D3 / O2)"}


def fixture_c3c(segments: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """C3c: every valid entry print is strictly after bar close + latency (1 s)."""
    violations = 0
    checked = 0
    for seg in segments:
        ent = seg["entries"]
        valid = ent["valid"]
        checked += int(valid.sum())
        violations += int(np.sum(valid & ~(ent["entry_mts"] >= ent["close_mts"] + ENTRY_DELAY_MS)))
        violations += int(np.sum(valid & ~(ent["entry_mts"] > ent["close_mts"])))
    return {"fixture": "C3c", "entries_checked": checked, "violations": violations, "pass": violations == 0}


def canary(gates: Mapping[str, Any], seed: int = rules.WHITE_RC_SEED, backend=None,
           gate6_method: str | None = None) -> dict[str, Any]:
    """Shuffled-label canary on a sign-randomised daily panel. Each (cell, day) net
    sign is flipped INDEPENDENTLY at random. This destroys the label/edge link AND
    destroys cross-cell dependence, within-cell serial dependence and skew (sign
    flipping symmetrises each cell's series). It is a SANITY CHECK, not a calibration
    test: it cannot detect gate-6 failure modes from serial dependence or skew
    (GS v4 G5 / HM). It calls the same gate-6 function (rules.primary_p_from_stat with
    the method in force) as the main path. The daily-panel LEAD gates are re-run on it:
    gate 2 (net > 0), gate 6 (BH over N on the HAC p), gate 7 (White RC) and
    gate 8 (DSR vs SR0 with N trials). Any canary LEAD (> 0) = FAIL = STOP.
    BH-only rejections are reported separately (diagnostic, not the pass rule)."""
    rng = np.random.Generator(np.random.PCG64(int(seed) + 7))
    daily = gates["daily"]
    daily_n = gates["daily_n"]
    signs = rng.choice(np.asarray([-1.0, 1.0]), size=daily.shape)
    flipped = daily * signs
    p = np.ones(daily.shape[0])
    srs = np.full(daily.shape[0], np.nan)
    tested = np.zeros(daily.shape[0], dtype=bool)
    t_info: dict[int, Any] = {}
    for cell in np.flatnonzero(gates["tested"]):
        series = flipped[cell][daily_n[cell] > 0]
        stat = rules.hac_statistic(series)
        p[cell] = rules.primary_p_from_stat(stat, gate6_method)
        if stat["testable"]:
            tested[cell] = True
            t_info[int(cell)] = (stat["T"], stat["t"])
            sst = rules.sharpe_stats(series)
            if sst is not None:
                srs[cell] = sst["sr"]
    bh = rules.bh_reject(p)
    positive = flipped.sum(axis=1) > 0
    bh_only = np.flatnonzero(bh & positive)
    rc = rules.white_rc_pvalues(flipped, tested, backend=backend)
    g7 = rules.white_rc_gate(rc["p"])
    sr_var = float(np.nanvar(srs[tested], ddof=1)) if np.sum(tested & np.isfinite(srs)) > 1 else 0.0
    sr0 = rules.expected_max_sharpe(sr_var)
    lead_cells = []
    for cell in bh_only:
        series = flipped[cell][daily_n[cell] > 0]
        if g7[cell] and rules.deflated_sharpe(series, sr0) >= rules.DSR_MIN:
            lead_cells.append(int(cell))
    return {"fixture": "canary_sign_randomised", "canary_leads": len(lead_cells), "canary_lead_cells": lead_cells,
            "bh_only_rejections": int(bh_only.size),
            "bh_only_detail": [{"cell": int(c), "T": t_info.get(int(c), (None, None))[0],
                                "t": t_info.get(int(c), (None, None))[1], "p": float(p[c]),
                                "white_rc_p": float(rc["p"][c])} for c in bh_only[:20]],
            "pass": not lead_cells, "gates_applied": ["g2", "g6", "g7", "g8"],
            "note": "sign-randomised daily panel (independent per cell-day; destroys cross-cell and serial "
                    "dependence and skew); sanity check, not a calibration test; BH over N, White RC and DSR "
                    "re-run on the canary panel; bh_only_rejections are diagnostic, never evidence",
            "gate6_disclosure": rules.GATE6_DISCLOSURE}


def plus_one_bar(ctx, segments, base, gates: Mapping[str, Any]) -> dict[str, Any]:
    """Diagnostic only: a LEAD that improves under a +1-bar signal shift is flagged leak-suspect."""
    out = {}
    grid_leads = [c for c in gates["lead_ids"] if c < N_GRID_CELLS]
    if not grid_leads:
        return {"diagnostic": "+1-bar", "leads": {}, "note": "no grid LEADs"}
    shifted = replay_cells(ctx, segments, base, grid_leads, shift=1)
    for cell in grid_leads:
        tr = shifted.get(cell)
        net = 0.0 if tr is None else float(tr["net"].sum())
        out[str(cell)] = {"net_sum_bp": float(gates["net_sum"][cell]), "shifted_net_sum_bp": net,
                          "leak_suspect": net > float(gates["net_sum"][cell])}
    return {"diagnostic": "+1-bar", "leads": out}


PHASES_AFTER_LATTICE = ("cell_chains", "switching", "gates")


def run_search(ctx: Mapping[str, Any], backend, *, on_chunk=None,
               on_phase: Callable[[str], None] | None = None,
               timer: Callable[[str, float], None] | None = None,
               gate6_method: str | None = None, pair_thesis_rule: str | None = None) -> dict[str, Any]:
    """Full A19 path from prepared features to gate results. `on_phase` announces the
    CPU phases after the lattice (cell_chains, switching, gates) as their own units, so
    they run under the 600 s CPU cap, not the last GPU lattice chunk's 120 s cap."""
    calendar: Calendar = ctx["calendar"]
    if gate6_method is not None:
        ctx["gate6_method"] = rules.gate6_method_check(gate6_method)
    if pair_thesis_rule is not None:
        ctx["pair_thesis_rule"] = pair_thesis_rule_check(pair_thesis_rule)
    phase = on_phase or (lambda name: None)
    segments = build_segments(ctx, calendar)
    base = compute_lattice(ctx, segments, backend, on_chunk=on_chunk)
    if timer is not None:
        timer("exit_lattice", base["seconds"])
    acc = Accumulator(len(calendar.test_days()), len(calendar.folds))
    phase("cell_chains")
    t0 = time.monotonic()
    chain_info = {}
    for seg in segments:
        info = run_chains(ctx, seg, base, None, acc=acc)
        chain_info[seg["name"]] = info
    if timer is not None:
        timer("cell_chains", time.monotonic() - t0)
    phase("switching")
    packages = switching(ctx, segments, base, acc)
    phase("gates")
    gates = evaluate_gates(ctx, segments, base, acc, packages, backend=backend, timer=timer)
    stale = sum(int(seg["entries"]["stale"].sum()) for seg in segments)
    lat = np.concatenate([seg["entries"]["latency_ms"][seg["entries"]["valid"]] for seg in segments])
    return {"segments": segments, "base": base, "acc": acc, "packages": packages, "gates": gates,
            "chain_info": chain_info, "stale_entry_bars": stale,
            "latency": rules.latency_distribution([int(v) for v in lat[:200_000]]) if lat.size else None}


def leads_json(gates: Mapping[str, Any], table: np.ndarray) -> list[dict[str, Any]]:
    out = []
    for cell in gates["lead_ids"]:
        row = {name: (table[name][cell].item() if hasattr(table[name][cell], "item") else table[name][cell])
               for name in table.dtype.names}
        row["label"] = rules.R3_LEAD_LABEL
        out.append(row)
    return out


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, np.ndarray):
        return jsonable(value.tolist())
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return f if math.isfinite(f) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def dumps(value: Any) -> str:
    return json.dumps(jsonable(value), sort_keys=True)


__all__ = [
    "N_CELLS", "N_GRID_CELLS", "S1_CELL", "S2_CELL", "MEMBERS", "prepare", "build_segments", "run_search",
    "dry_check", "fixture_c3b", "fixture_c3c", "canary", "plus_one_bar", "cells_table", "leads_json",
    "enumerate_cells", "key_id", "key_parts", "FEE_BP", "FUNDING",
]
