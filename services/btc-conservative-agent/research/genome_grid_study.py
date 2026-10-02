"""Full-history policy genome grid over collected tape (research only, never execution).

The analyzer's Safe Policy Genome replays only the most recent 150 events
(``ANALYZER_PROTECTION_REPLAY_MAX_EVENTS``) and only for entry children the
collector already recorded, so its public Top-100 collapses whenever recent
episodes are few or unsupported. This study evaluates the same genome axes over
every collected signal episode instead:

* entry: offset from signal price, chase windows / remaining-gap step / reprice
  interval, entry TTL, follow-vs-fade of the signalled side;
* exit: the engine's own ``protection_screen()`` (ATR stop/target, Scenario-C
  ladder x ATR stop sweep, thesis fast cut, time stops, break-even locks, MFE
  giveback, ATR trail, chandelier, hybrid runner partials, hard % stop);
* outcome: N, fills, win rate, EV, net, R-multiple, MFE/MAE, drawdown and a
  fixed chronological 70/30 holdout, per fill world.

Every row is SIMULATED on public 1 s Bitfinex tape (two fill worlds: BBO
marketable and ideal touch). Live paper outcomes are reported separately as
LIVE_PAPER per lane. Nothing here can place or change an order; retired tiles
are evaluated only as parameter sets over market data, never as runtime code.
"""
from __future__ import annotations

import argparse
import bisect
import glob
import gzip
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

AGENT_DIR = Path(__file__).resolve().parents[1]
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

SCHEMA = "genome_grid_report_v1"
ROWS_SCHEMA = "genome_grid_policy_row_v1"
DEFAULT_MIRROR = r"C:\DoxxedCrypto\fly-mirror-segments\tree"
DEFAULT_TIER_A = r"C:\DoxxedCrypto\bot-data-compact\tierA"
DEFAULT_OUT = r"C:\DoxxedCrypto\analyzer-exports\genome-grid"
DEFAULT_CYCLE_STATUS = r"C:\DoxxedCrypto\laptop-chain\segment-analyzer-cycle.status.json"

LEVERAGE = 100.0
MARGIN_USD = 0.25
PATH_END_SEC = 7200
CHASE_BUCKET_SEC = 300
MIN_PATH_COVERAGE = 0.9
TAPE_STALE_SEC = 3.5
HOLDOUT_TRAIN_FRACTION = 0.7
MIN_OOS_FILLS_FOR_RANK = 10
MIN_TRAIN_FILLS_FOR_RANK = 30
DUPLICATE_SIGNAL_SEC = 90
WORLDS = ("BBO_MARKETABLE", "IDEAL_TOUCH")
DIRECTION_RULES = ("FOLLOW", "FADE")

# Chase identities follow research_v3_search: windows are 5-minute buckets, the
# step is the share of the remaining gap to the passive touch, the interval is
# the reprice cadence. ``w234`` waits 10 min, then chases from 600 s to 1500 s.
CHASES = {
    "no_chase": ((), 0.0, 0),
    "w234_s50_i180": ((2, 3, 4), 0.5, 180),
    "w234_s25_i180": ((2, 3, 4), 0.25, 180),
    "w234_s10_i180": ((2, 3, 4), 0.10, 180),
    "w234_s25_i60": ((2, 3, 4), 0.25, 60),
    "w01_on_s25_i180": ((0, 1), 0.25, 180),
    "all_on_s50_i60": ((0, 1, 2, 3, 4, 5), 0.5, 60),
}
# Entry parameters of the analyzer-hypothesis experiment (0.27 / 0.30 % offset,
# w234 chase, 50 % remaining-gap moves, 180 s reprice, 30 min TTL). Those tiles
# are retired; here they are only parameter sets replayed over market data.
HYPOTHESIS_OFFSETS = (0.27, 0.30)
HYPOTHESIS_CHASE = "w234_s50_i180"
HYPOTHESIS_TTL = 1800
SWEEP_OFFSETS = (0.05, 0.10, 0.15, 0.20, 0.25, 0.27, 0.30, 0.35, 0.40, 0.50)
SWEEP_TTLS = (900, 1800, 3600)
# One representative exit per family for the entry sweep; the protection sweep
# crosses all engine protections with the taker and registry entries.
SWEEP_PROTECTIONS = (
    "ATR_TP_2.5_SCENARIO_C",
    "ATR_TP_2.5_THESIS_12_HARD_30",
    "ATR_TRAIL_SL_1.5_ARM_0.75_TRAIL_1",
    "CHANDELIER_1.5",
    "ATR_TP_2.5_GIVEBACK_20PCT",
    "HYBRID_secure_25_25_runner_TRAIL_1",
)


# ------------------------------------------------------------------ inputs

def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def _rotations(base: Path) -> list[Path]:
    out = [base] if base.is_file() else []
    for p in glob.glob(str(base) + ".*"):
        if p.rsplit(".", 1)[-1].isdigit():
            out.append(Path(p))
    return out


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ts(value: Any) -> float | None:
    number = _num(value)
    if number is not None or not isinstance(value, str):
        return number
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _file_receipt(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {"path": str(path), "bytes": st.st_size, "mtime": round(st.st_mtime, 3)}


@dataclass
class Tape:
    t0: int
    bid: np.ndarray
    ask: np.ndarray
    last: np.ndarray
    low: np.ndarray
    high: np.ndarray
    fresh: np.ndarray
    receipts: list[dict[str, Any]]

    @property
    def end(self) -> int:
        return self.t0 + len(self.bid)

    def window(self, start: int, end: int) -> dict[str, np.ndarray]:
        a, b = max(0, start - self.t0), max(0, min(len(self.bid), end - self.t0))
        return {k: getattr(self, k)[a:b] for k in ("bid", "ask", "last", "low", "high", "fresh")}


def _tape_sources(mirror: Path, tier_a: Path) -> list[tuple[str, Path]]:
    sources = [("parquet", Path(p)) for p in sorted(glob.glob(str(tier_a / "bitfinex_l1_tape_1s" / "v1" / "date=*" / "*.parquet")))]
    sources += [("jsonl", p) for p in sorted(_rotations(mirror / "market_microstructure_1s.jsonl"))]
    return sources


def _tape_rows(sources: list[tuple[str, Path]]) -> Iterable[dict[str, Any]]:
    for kind, path in sources:
        if kind == "parquet":
            import pyarrow.parquet as pq  # noqa: PLC0415
            for batch in pq.ParquetFile(str(path)).iter_batches(columns=["row"], batch_size=20000):
                for raw in batch.column(0).to_pylist():
                    try:
                        yield json.loads(raw)
                    except (TypeError, ValueError):
                        continue
        else:
            yield from _read_jsonl(path)


def load_tape(mirror: Path, tier_a: Path) -> Tape:
    """Dense 1 s arrays (NaN where a second is missing) from Tier A Parquet + mirror rotations."""
    sources = _tape_sources(mirror, tier_a)
    receipts = [_file_receipt(p) | {"source": "TIER_A_PARQUET" if k == "parquet" else "MIRROR_JSONL"} for k, p in sources]
    by_ts: dict[int, tuple[float, ...]] = {}
    for row in _tape_rows(sources):
        ts = _num(row.get("bucket_ts"))
        bid, ask = _num(row.get("bid")), _num(row.get("ask"))
        if ts is None or not bid or not ask or ask < bid:
            continue
        last = _num(row.get("last")) or (bid + ask) / 2
        low = _num(row.get("trade_low")) or last
        high = _num(row.get("trade_high")) or last
        age = _num(row.get("source_age_sec"))
        fresh = row.get("valid_bbo", True) is not False and row.get("fresh", True) is not False and (age is None or age <= TAPE_STALE_SEC)
        by_ts[int(ts)] = (bid, ask, last, min(low, last), max(high, last), 1.0 if fresh else 0.0)
    if not by_ts:
        raise SystemExit("GENOME_GRID_NO_TAPE")
    t0, t1 = min(by_ts), max(by_ts) + 1
    arr = np.full((t1 - t0, 6), np.nan)
    for ts, vals in by_ts.items():
        arr[ts - t0] = vals
    return Tape(t0, arr[:, 0].copy(), arr[:, 1].copy(), arr[:, 2].copy(), arr[:, 3].copy(), arr[:, 4].copy(),
                np.nan_to_num(arr[:, 5]), receipts)


def tape_atr14_pct(tape: Tape, ts: float, bar_sec: int = 180, bars: int = 14) -> float | None:
    """Simple-mean ATR14 on 3-minute bars of the last price, as % of price (fallback when the feature is dead)."""
    end = int(ts) - tape.t0
    start = end - bar_sec * (bars + 1)
    if start < 0 or end > len(tape.last):
        return None
    seg = tape.last[start:end].reshape(bars + 1, bar_sec)
    if np.isnan(seg).mean() > 0.2 or np.isnan(seg).all(axis=1).any():
        return None
    hi, lo = np.nanmax(seg, axis=1), np.nanmin(seg, axis=1)
    close = np.array([s[~np.isnan(s)][-1] for s in seg])
    tr = np.maximum(hi[1:] - lo[1:], np.maximum(abs(hi[1:] - close[:-1]), abs(lo[1:] - close[:-1])))
    return float(np.mean(tr) / close[-1] * 100.0) if close[-1] else None


def load_episodes(mirror: Path, tape: Tape) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One episode per collected signal: every v3 opportunity plus every signal_replay start."""
    led = mirror / "v3" / "ledgers"
    import combo_pathway_config as registry  # noqa: PLC0415
    lanes_by_episode: dict[str, Counter] = defaultdict(Counter)
    scores: dict[str, tuple[float, float]] = {}
    outcomes: dict[str, Counter] = defaultdict(Counter)
    for row in _read_jsonl(led / "decision.jsonl"):
        ep = str(row.get("episode_id") or "")
        if not ep:
            continue
        lanes_by_episode[ep][str(row.get("research_lane") or "UNKNOWN")] += 1
        ls, ss = _num(row.get("long_score")), _num(row.get("short_score"))
        if ls is not None and ss is not None:
            scores[ep] = (ls, ss)
        outcomes[ep][str(row.get("outcome_state") or "UNKNOWN")] += 1
    episodes: list[dict[str, Any]] = []
    stats: Counter = Counter()
    seen: set[str] = set()
    for row in _read_jsonl(led / "opportunity.jsonl"):
        ep = str(row.get("episode_id") or "")
        ts, price = _num(row.get("signal_ts")), _num(row.get("signal_price"))
        if not ep or ep in seen or ts is None:
            stats["opportunity_skipped_identity"] += 1
            continue
        seen.add(ep)
        lanes = [lane for lane in lanes_by_episode[ep] if lane != "UNKNOWN"]
        if lanes and all(registry.is_cross_venue_clock_lane(lane) for lane in lanes):
            # Per-second cross-venue signals with 60 s exits are a different clock, not an AI decision episode.
            stats["opportunity_cross_venue_clock_excluded"] += 1
            continue
        price_source = "SIGNAL_PRICE"
        if not price:
            i = int(ts) - tape.t0
            mid = (tape.bid[i] + tape.ask[i]) / 2 if 0 <= i < len(tape.bid) else float("nan")
            if mid != mid:
                stats["opportunity_no_signal_price"] += 1
                continue
            price, price_source = float(mid), "TAPE_MID_AT_SIGNAL"
            stats["opportunity_price_from_tape_mid"] += 1
        raw = str(row.get("raw_direction") or "")
        direction, side_basis = raw, "AI_RAW_DIRECTION"
        if raw not in ("LONG", "SHORT"):
            ls, ss = scores.get(ep, (None, None))
            if ls is None or ls == ss:
                stats["opportunity_no_side"] += 1
                continue
            direction, side_basis = ("LONG" if ls > ss else "SHORT"), "SCORE_LED_SIDE"
        feat = row.get("feature_snapshot_at_signal") or {}
        atr, atr_source = _num(feat.get("atr14_pct_3m")), "FEATURE_ATR14_PCT_3M"
        if not atr or atr <= 0:
            atr, atr_source = tape_atr14_pct(tape, ts), "TAPE_ATR14_3M"
        regime = feat.get("regime_label") or feat.get("regime")
        if isinstance(regime, Mapping):
            regime = regime.get("value")
        episodes.append({
            "episode_id": ep, "signal_ts": ts, "signal_price": price, "direction": direction,
            "side_basis": side_basis, "source": "V3_OPPORTUNITY", "atr14_pct": atr, "atr_source": atr_source,
            "price_source": price_source,
            "regime": str(regime or "UNKNOWN"), "lanes": sorted(lanes_by_episode[ep]),
            "decision_outcomes": dict(outcomes[ep]),
        })
        stats[f"side_{side_basis}"] += 1
        stats[f"atr_{atr_source if atr else 'MISSING'}"] += 1
    v3_ts = {d: sorted(e["signal_ts"] for e in episodes if e["direction"] == d) for d in ("LONG", "SHORT")}
    seen_sr: set[str] = set()
    for path in _rotations(mirror / "signal_replay.jsonl"):
        for row in _read_jsonl(path):
            tid = str(row.get("trade_id") or "")
            if not tid or tid in seen_sr:
                continue
            seen_sr.add(tid)
            ts, price = _ts(row.get("start_ts")), _num(row.get("start_price"))
            if ts is None or not price or row.get("direction") not in ("LONG", "SHORT"):
                stats["signal_replay_skipped"] += 1
                continue
            lane = str(row.get("lane") or "unknown")
            near = v3_ts[row["direction"]]
            k = bisect.bisect_left(near, ts - DUPLICATE_SIGNAL_SEC)
            if k < len(near) and near[k] <= ts + DUPLICATE_SIGNAL_SEC:
                # The same AI call already appears as a v3 opportunity; count it once.
                stats["signal_replay_duplicate_of_v3"] += 1
                continue
            episodes.append({
                "episode_id": f"signal_replay:{tid}", "signal_ts": ts, "signal_price": price,
                "direction": row["direction"], "side_basis": "SIGNAL_REPLAY_DIRECTION",
                "source": f"SIGNAL_REPLAY_{lane.upper()}", "atr14_pct": tape_atr14_pct(tape, ts),
                "atr_source": "TAPE_ATR14_3M", "regime": "UNKNOWN", "lanes": [lane], "decision_outcomes": {},
            })
            stats[f"signal_replay_{lane}"] += 1
    episodes.sort(key=lambda e: (e["signal_ts"], e["episode_id"]))
    return episodes, dict(stats)


# ----------------------------------------------------------- policy grid

def registry_protections() -> dict[str, dict[str, Any]]:
    """Exits of the active AI-clock tiles, read from the canonical registry (no second tile list).

    A time exit with a catastrophic stop is the canonical evaluator's ATR_TARGET mode without a target.
    """
    import combo_pathway_config as registry  # noqa: PLC0415
    out: dict[str, dict[str, Any]] = {}
    for lane in registry.ACTIVE_TILE_ORDER:
        spec = registry.ACTIVE_TILE_REGISTRY[lane]
        exit_policy = spec.get("exit_policy") or {}
        if registry.is_cross_venue_clock_lane(lane) or exit_policy.get("family") != "TIME_EXIT_WITH_CATASTROPHIC_STOP":
            continue
        pid = f"REGISTRY_{spec['exit_profile_id']}"
        prot = out.setdefault(pid, {
            "protection_id": pid, "policy_family": "REGISTRY_TIME_EXIT", "registry_lanes": [],
            "loss_protection": {"hard_stop_margin_pct": float(exit_policy["hard_stop_margin_pct"]),
                                "time_stop_min": float(exit_policy["max_duration_sec"]) / 60.0},
            "profit_protection": {"mode": "ATR_TARGET", "atr_tp_k": None},
        })
        prot["registry_lanes"].append(lane)
    return out


def protection_specs() -> dict[str, dict[str, Any]]:
    from research_v3_candidates import protection_screen  # noqa: PLC0415
    return {p["protection_id"]: p for p in protection_screen()} | registry_protections()


def sweep_protections(protections: Mapping[str, Any]) -> list[str]:
    return [p for p in SWEEP_PROTECTIONS if p in protections] + [p for p, v in protections.items() if v.get("registry_lanes")]


def entry_specs() -> list[dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}

    def add(offset: float, chase: str, ttl: int, stage: str) -> None:
        key = "TAKER_AT_SIGNAL" if offset == 0.0 else f"OFFSET_{offset:.2f}_CHASE_{chase}_TTL{ttl}"
        stages = set((entries.get(key) or {}).get("stages") or ())
        stages.add(stage)
        entries[key] = {"entry_id": key, "offset_pct": offset, "chase_id": "no_chase" if offset == 0.0 else chase,
                        "ttl_sec": 0 if offset == 0.0 else ttl, "stages": sorted(stages)}

    add(0.0, "no_chase", 0, "PROTECTION_SWEEP")
    add(0.0, "no_chase", 0, "ENTRY_SWEEP")
    for off in HYPOTHESIS_OFFSETS:
        add(off, HYPOTHESIS_CHASE, HYPOTHESIS_TTL, "PROTECTION_SWEEP")
    for off in SWEEP_OFFSETS:
        for chase in CHASES:
            for ttl in SWEEP_TTLS:
                add(off, chase, ttl, "ENTRY_SWEEP")
    return list(entries.values())


def limit_schedule(entry: Mapping[str, Any], direction: str, signal_price: float,
                   bid: np.ndarray, ask: np.ndarray) -> np.ndarray:
    """Per-second resting limit; a chase moves toward the passive touch, never away from it."""
    ttl = int(entry["ttl_sec"])
    sign = 1.0 if direction == "LONG" else -1.0
    limit = np.full(ttl, signal_price * (1.0 - sign * entry["offset_pct"] / 100.0))
    windows, step, interval = CHASES[entry["chase_id"]]
    if not windows or step <= 0:
        return limit
    t, end = min(windows) * CHASE_BUCKET_SEC, min(ttl, (max(windows) + 1) * CHASE_BUCKET_SEC)
    current = limit[0]
    while t < end:
        touch = bid[t] if direction == "LONG" else ask[t]
        if touch == touch and (touch - current) * sign > 0:
            current = current + step * (touch - current)
            limit[t:] = current
        t += interval
    return limit


def simulate_fill(entry: Mapping[str, Any], direction: str, signal_price: float,
                  w: Mapping[str, np.ndarray]) -> dict[str, tuple[int, float] | None]:
    """First fill second and price per world, or None (no fill / insufficient tape)."""
    bid, ask, last, low, high, fresh = (w[k] for k in ("bid", "ask", "last", "low", "high", "fresh"))
    if entry["offset_pct"] == 0.0:
        for i in range(min(5, len(bid))):
            if fresh[i] and bid[i] == bid[i]:
                return {"BBO_MARKETABLE": (i, float(ask[i] if direction == "LONG" else bid[i])),
                        "IDEAL_TOUCH": (i, float(last[i]))}
        return {"BBO_MARKETABLE": None, "IDEAL_TOUCH": None}
    ttl = int(entry["ttl_sec"])
    if len(bid) < ttl:
        return {"BBO_MARKETABLE": None, "IDEAL_TOUCH": None}
    limit = limit_schedule(entry, direction, signal_price, bid[:ttl], ask[:ttl])
    with np.errstate(invalid="ignore"):
        if direction == "LONG":
            bbo = (fresh[:ttl] > 0) & (ask[:ttl] <= limit)
            touch = low[:ttl] <= limit
        else:
            bbo = (fresh[:ttl] > 0) & (bid[:ttl] >= limit)
            touch = high[:ttl] >= limit
    out: dict[str, tuple[int, float] | None] = {}
    for world, hit in (("BBO_MARKETABLE", bbo), ("IDEAL_TOUCH", touch)):
        out[world] = (int(np.argmax(hit)), float(limit[int(np.argmax(hit))])) if hit.any() else None
    return out


def prepare_path(direction: str, entry_price: float, fill_idx: int, w: Mapping[str, np.ndarray]) -> dict[str, np.ndarray] | None:
    """Executable-side marks (bid for LONG exits, ask for SHORT) from the fill second for PATH_END_SEC."""
    mark = w["bid"] if direction == "LONG" else w["ask"]
    seg = mark[fill_idx: fill_idx + PATH_END_SEC]
    if len(seg) < PATH_END_SEC * MIN_PATH_COVERAGE:
        return None
    ok = ~np.isnan(seg)
    if ok.mean() < MIN_PATH_COVERAGE:
        return None
    age = np.nonzero(ok)[0].astype(float)
    price = seg[ok]
    raw = (price - entry_price) / entry_price * 100.0
    cur = raw * LEVERAGE if direction == "LONG" else -raw * LEVERAGE
    return {"age": age, "price": price, "cur": cur, "mfe": np.maximum.accumulate(cur), "mae": np.minimum.accumulate(cur)}


def fast_replay(path: Mapping[str, np.ndarray], spec: Mapping[str, Any], atr_pct: float,
                margin_usd: float = MARGIN_USD) -> dict[str, Any]:
    """Vectorised twin of research_v3_policy_replay.replay_protected_policy (same precedence and floors)."""
    loss, profit = spec["loss_protection"], spec["profit_protection"]
    cur, mfe, age = path["cur"], path["mfe"], path["age"]
    n = len(cur)
    atr_m = float(atr_pct) * LEVERAGE
    mode = str(profit.get("mode") or "ATR_TARGET")
    floor = np.full(n, -np.inf)
    any_floor = np.zeros(n, dtype=bool)

    def add_floor(cond: np.ndarray, value: Any) -> None:
        nonlocal floor, any_floor
        floor = np.maximum(floor, np.where(cond, value, -np.inf))
        any_floor = any_floor | cond

    be_floor = float(profit.get("break_even_floor_pct") or 0)
    if profit.get("break_even_arm_mfe_pct") is not None:
        add_floor(mfe >= float(profit["break_even_arm_mfe_pct"]), be_floor)
    if profit.get("break_even_arm_atr_k") is not None:
        add_floor(mfe >= float(profit["break_even_arm_atr_k"]) * atr_m, be_floor)
    if profit.get("mfe_giveback_abs_pct") is not None:
        add_floor(mfe > 0, mfe - float(profit["mfe_giveback_abs_pct"]))
    if profit.get("mfe_giveback_fraction") is not None:
        add_floor(mfe > 0, mfe * (1.0 - float(profit["mfe_giveback_fraction"])))
    act = float(profit.get("trail_activation_atr_k") or 0) * atr_m
    if mode in {"ATR_TRAIL", "HYBRID_RUNNER"} and profit.get("atr_trail_k") is not None:
        add_floor(mfe >= act, mfe - float(profit["atr_trail_k"]) * atr_m)
    if mode == "CHANDELIER" and profit.get("chandelier_atr_k") is not None:
        add_floor(mfe >= act, mfe - float(profit["chandelier_atr_k"]) * atr_m)
    for trigger, value in profit.get("ladder") or []:
        add_floor(mfe >= float(trigger), float(value))
    active_any = np.maximum.accumulate(any_floor)
    active = np.maximum.accumulate(floor)

    conds: list[tuple[str, np.ndarray]] = [("PHYSICAL_HARD_STOP", cur <= -float(loss["hard_stop_margin_pct"]))]
    if loss.get("atr_stop_k") is not None:
        conds.append(("ATR_STOP", cur <= -float(loss["atr_stop_k"]) * atr_m))
    if loss.get("thesis_cut_margin_pct") is not None:
        conds.append(("THESIS_FAST_CUT", (age <= float(loss.get("thesis_window_sec") or 0)) & (cur <= float(loss["thesis_cut_margin_pct"]))))
    conds.append(("PROFIT_PROTECTION_FLOOR", active_any & (cur <= active)))
    if loss.get("time_stop_min") is not None:
        conds.append(("TIME_STOP", age >= float(loss["time_stop_min"]) * 60.0))
    if mode in {"ATR_TARGET", "HYBRID_RUNNER"} and profit.get("atr_tp_k") is not None:
        conds.append(("ATR_TAKE_PROFIT", cur >= float(profit["atr_tp_k"]) * atr_m))
    best, reason = n, "PATH_END"
    for name, c in conds:
        if c.any():
            i = int(np.argmax(c))
            if i < best:
                best, reason = i, name
    exit_idx = best if best < n else n - 1
    exit_margin = float(cur[exit_idx])
    remaining, realized = 1.0, 0.0
    for trigger_k, fraction in profit.get("partial_take_profits") or []:
        if remaining <= 0:
            continue
        hit = cur[: exit_idx + 1] >= float(trigger_k) * atr_m
        if hit.any():
            take = min(float(fraction), remaining)
            realized += take * float(cur[int(np.argmax(hit))])
            remaining -= take
    realized += remaining * exit_margin
    return {
        "net_pnl_usd": margin_usd * realized / 100.0, "portfolio_margin_return_pct": realized,
        "exit_reason": reason, "exit_age_sec": float(age[exit_idx]),
        "mfe_pct": float(mfe[exit_idx]), "mae_pct": float(path["mae"][exit_idx]),
    }


def initial_risk_margin_pct(spec: Mapping[str, Any], atr_pct: float) -> float:
    """1R = the tightest initial loss bound (ATR stop, thesis cut, hard stop) in margin %."""
    loss = spec["loss_protection"]
    bounds = [float(loss["hard_stop_margin_pct"])]
    if loss.get("atr_stop_k") is not None:
        bounds.append(float(loss["atr_stop_k"]) * float(atr_pct) * LEVERAGE)
    if loss.get("thesis_cut_margin_pct") is not None:
        bounds.append(abs(float(loss["thesis_cut_margin_pct"])))
    return max(1e-9, min(bounds))


# ----------------------------------------------------------- evaluation

EXIT_REASONS = ("PHYSICAL_HARD_STOP", "ATR_STOP", "THESIS_FAST_CUT", "PROFIT_PROTECTION_FLOOR", "TIME_STOP",
                "ATR_TAKE_PROFIT", "PATH_END")
NO_FILL, CENSORED_PATH = -1, -2

_TAPE: Tape | None = None
_ENTRIES: list[dict[str, Any]] = []
_PROTECTIONS: dict[str, dict[str, Any]] = {}


def _below_normal() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes  # noqa: PLC0415
        k = ctypes.windll.kernel32
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)
    except Exception:  # noqa: BLE001
        pass


def _init_worker(tape: Tape, entries: list[dict[str, Any]], protections: dict[str, dict[str, Any]]) -> None:
    global _TAPE, _ENTRIES, _PROTECTIONS
    _TAPE, _ENTRIES, _PROTECTIONS = tape, entries, protections
    _below_normal()


def _spec(protection: Mapping[str, Any]) -> dict[str, Any]:
    return {"loss_protection": protection["loss_protection"], "profit_protection": protection["profit_protection"]}


def row_keys(entries: list[dict[str, Any]], protections: Mapping[str, Any]) -> list[tuple[int, str, str, str]]:
    """The (entry, protection, rule, world) order every evaluated episode emits its outcome arrays in."""
    sweep = sweep_protections(protections)
    return [(e_i, pid, rule, world) for rule in DIRECTION_RULES for e_i, entry in enumerate(entries)
            for world in WORLDS for pid in (list(protections) if "PROTECTION_SWEEP" in entry["stages"] else sweep)]


def evaluate_episode(job: tuple[int, dict[str, Any]]) -> dict[str, Any]:
    """All (entry x protection x direction rule x world) outcomes for one episode, aligned to ``row_keys``."""
    idx, ep = job
    tape = _TAPE
    assert tape is not None
    start = int(math.floor(ep["signal_ts"])) + 1
    need = max(int(e["ttl_sec"]) for e in _ENTRIES) + PATH_END_SEC + 5
    if start < tape.t0 or start + need > tape.end:
        return {"idx": idx, "status": "CENSORED_TAPE_WINDOW", "rows": []}
    if not ep.get("atr14_pct"):
        return {"idx": idx, "status": "UNSUPPORTED_ATR_MISSING", "rows": []}
    w = tape.window(start, start + need)
    if np.isnan(w["bid"][:PATH_END_SEC]).mean() > 1 - MIN_PATH_COVERAGE:
        return {"idx": idx, "status": "CENSORED_TAPE_GAP", "rows": []}
    rows: list[tuple] = []
    nan = float("nan")
    sweep = sweep_protections(_PROTECTIONS)
    for rule in DIRECTION_RULES:
        direction = ep["direction"] if rule == "FOLLOW" else ("SHORT" if ep["direction"] == "LONG" else "LONG")
        paths: dict[tuple[int, float], dict | None] = {}
        replays: dict[tuple[int, float, str], dict] = {}
        for e_i, entry in enumerate(_ENTRIES):
            fills = simulate_fill(entry, direction, ep["signal_price"], w)
            prot_ids = list(_PROTECTIONS) if "PROTECTION_SWEEP" in entry["stages"] else sweep
            for world in WORLDS:
                fill = fills.get(world)
                if fill is None:
                    rows.extend((nan, nan, nan, nan, NO_FILL) for _ in prot_ids)
                    continue
                if fill not in paths:
                    paths[fill] = prepare_path(direction, fill[1], fill[0], w)
                path = paths[fill]
                for pid in prot_ids:
                    if path is None:
                        rows.append((nan, nan, nan, nan, CENSORED_PATH))
                        continue
                    key = (fill[0], fill[1], pid)
                    rep = replays.get(key)
                    if rep is None:
                        spec = _spec(_PROTECTIONS[pid])
                        rep = fast_replay(path, spec, ep["atr14_pct"])
                        rep["r_multiple"] = rep["portfolio_margin_return_pct"] / initial_risk_margin_pct(spec, ep["atr14_pct"])
                        replays[key] = rep
                    rows.append((rep["net_pnl_usd"], rep["r_multiple"], rep["mfe_pct"], rep["mae_pct"],
                                 EXIT_REASONS.index(rep["exit_reason"])))
    arr = np.array(rows, dtype=np.float64)
    return {"idx": idx, "status": "EVALUATED", "values": arr[:, :4],
            "code": arr[:, 4].astype(np.int8)}


def canonical_parity_sample(episodes: list[dict[str, Any]], tape: Tape, protections: dict[str, dict[str, Any]],
                            sample: int = 40) -> dict[str, Any]:
    """Replay a deterministic sample through the engine's canonical evaluator and compare exactly."""
    from research_v3_policy_replay import replay_protected_policy  # noqa: PLC0415
    checked = mismatches = 0
    examples: list[dict[str, Any]] = []
    pids = list(protections)
    stride = max(1, len(episodes) // max(1, sample))
    for n, ep in enumerate(episodes[::stride][:sample]):
        start = int(math.floor(ep["signal_ts"])) + 1
        if not ep.get("atr14_pct") or start < tape.t0 or start + PATH_END_SEC + 10 > tape.end:
            continue
        w = tape.window(start, start + PATH_END_SEC + 10)
        entry_price = float(w["ask"][0] if ep["direction"] == "LONG" else w["bid"][0])
        path = prepare_path(ep["direction"], entry_price, 0, w) if entry_price == entry_price else None
        if path is None:
            continue
        prices = [{"ts": float(start + a), "price": float(p)} for a, p in zip(path["age"], path["price"])]
        for pid in pids[n % 7::7]:
            spec = {"entry": {"entry_policy_id": "TAKER_AT_SIGNAL", "offset_pct": 0.0, "chase_id": "no_chase"},
                    "fill": {"execution_world": "CONSERVATIVE_BBO_DEPTH_V1", "source_fill_model": "genome-grid"},
                    **_spec(protections[pid]),
                    "portfolio": {"concurrency_cap": 1, "size_scale": 1.0, "daily_loss_kill_pct": 3}}
            canon = replay_protected_policy(prices, direction=ep["direction"], entry_price=entry_price,
                                            fill_ts=float(start), atr_pct_at_fill=float(ep["atr14_pct"]),
                                            leverage=LEVERAGE, margin_usd=MARGIN_USD, policy_spec=spec,
                                            collect_trace=False)
            if canon.get("status") != "COMPLETE":
                continue
            fast = fast_replay(path, spec, ep["atr14_pct"])
            checked += 1
            if abs(float(canon["net_pnl_usd"]) - fast["net_pnl_usd"]) > 1e-6 or canon["exit_reason"] != fast["exit_reason"]:
                mismatches += 1
                if len(examples) < 5:
                    examples.append({"episode_id": ep["episode_id"], "protection_id": pid,
                                     "canonical": [canon["net_pnl_usd"], canon["exit_reason"]],
                                     "genome_grid": [round(fast["net_pnl_usd"], 8), fast["exit_reason"]]})
    return {"schema": "genome_grid_canonical_parity_v1",
            "evaluator": "research_v3_policy_replay.replay_protected_policy", "checked": checked,
            "mismatches": mismatches, "examples": examples,
            "status": "MATCH" if checked and not mismatches else ("MISMATCH" if mismatches else "NOT_CHECKED")}


# ------------------------------------------------------------ aggregation

def _wilson(wins: int, n: int) -> list[float | None]:
    if n <= 0:
        return [None, None]
    z, p = 1.96, wins / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(100 * (mid - half), 2), round(100 * (mid + half), 2)]


def _stats(pnl: list[float], signals: int, r: list[float] | None = None,
           mfe: list[float] | None = None, mae: list[float] | None = None) -> dict[str, Any]:
    n = len(pnl)
    out: dict[str, Any] = {"signals": signals, "fills": n, "fill_rate": round(n / signals, 4) if signals else None}
    if n == 0:
        return out | {"wins": 0, "losses": 0, "win_rate_pct": None, "net_pnl_usd": 0.0, "ev_per_fill_usd": None,
                      "ev_per_signal_usd": 0.0 if signals else None}
    arr = np.array(pnl)
    wins = int((arr > 0).sum())
    equity = np.cumsum(arr)
    out |= {
        "wins": wins, "losses": int((arr < 0).sum()), "win_rate_pct": round(100 * wins / n, 2),
        "win_rate_ci95_pct": _wilson(wins, n), "net_pnl_usd": round(float(arr.sum()), 6),
        "ev_per_fill_usd": round(float(arr.mean()), 6),
        "ev_per_signal_usd": round(float(arr.sum()) / signals, 6) if signals else None,
        "ev_se_usd": round(float(arr.std(ddof=1) / math.sqrt(n)), 6) if n > 1 else None,
        "max_drawdown_usd": round(float(np.min(equity - np.maximum.accumulate(np.maximum(equity, 0.0)))), 6),
    }
    if r:
        out["avg_r"] = round(float(np.mean(r)), 4)
    if mfe:
        out["avg_mfe_margin_pct"] = round(float(np.mean(mfe)), 3)
    if mae:
        out["avg_mae_margin_pct"] = round(float(np.mean(mae)), 3)
    return out


def aggregate(results: list[dict[str, Any]], episodes: list[dict[str, Any]], entries: list[dict[str, Any]],
              protections: dict[str, dict[str, Any]], cut_ts: float) -> list[dict[str, Any]]:
    done = sorted((r for r in results if r["status"] == "EVALUATED"), key=lambda r: episodes[r["idx"]]["signal_ts"])
    keys = row_keys(entries, protections)
    if not done:
        return []
    values = np.stack([r["values"] for r in done])  # episodes x keys x (pnl, r, mfe, mae)
    codes = np.stack([r["code"] for r in done])
    assert values.shape[1] == len(keys), (values.shape, len(keys))
    is_oos = np.array([episodes[r["idx"]]["signal_ts"] >= cut_ts for r in done])
    n_oos = int(is_oos.sum())
    n_train = len(done) - n_oos
    rows = []
    for k, (e_i, pid, rule, world) in enumerate(keys):
        filled = codes[:, k] >= 0
        v = values[filled, k].astype(np.float64)
        oos_f = is_oos[filled]
        a = {"pnl": v[:, 0].tolist(), "r": v[:, 1].tolist(), "mfe": v[:, 2].tolist(), "mae": v[:, 3].tolist(),
             "exit": [EXIT_REASONS[c] for c in codes[filled, k]], "train": v[~oos_f, 0].tolist(),
             "oos": v[oos_f, 0].tolist(), "oos_r": v[oos_f, 1].tolist(), "sig_train": n_train, "sig_oos": n_oos}
        entry, prot = entries[e_i], protections[pid]
        lp, pp = prot["loss_protection"], prot["profit_protection"]
        windows, step, interval = CHASES[entry["chase_id"]]
        rows.append({
            "schema": ROWS_SCHEMA, "policy_id": f"{rule}|{entry['entry_id']}|{pid}",
            "evidence_label": "SIMULATED_COUNTERFACTUAL", "fill_world": world, "direction_rule": rule,
            "policy_family": prot["policy_family"], "stages": entry["stages"],
            "entry": {"entry_id": entry["entry_id"], "offset_pct": entry["offset_pct"], "chase_id": entry["chase_id"],
                      "chase_windows_5m": list(windows), "chase_remaining_gap_step": step, "reprice_sec": interval,
                      "ttl_sec": entry["ttl_sec"]},
            "exit": {"protection_id": pid, "mode": pp.get("mode"), "atr_tp_k": pp.get("atr_tp_k"),
                     "atr_stop_k": lp.get("atr_stop_k"), "hard_stop_margin_pct": lp.get("hard_stop_margin_pct"),
                     "thesis_cut_margin_pct": lp.get("thesis_cut_margin_pct"), "thesis_window_sec": lp.get("thesis_window_sec"),
                     "time_stop_min": lp.get("time_stop_min"), "ladder": "scenario_c" if pp.get("ladder") else "none",
                     "break_even_arm_mfe_pct": pp.get("break_even_arm_mfe_pct"),
                     "break_even_arm_atr_k": pp.get("break_even_arm_atr_k"),
                     "break_even_floor_pct": pp.get("break_even_floor_pct"),
                     "mfe_giveback_abs_pct": pp.get("mfe_giveback_abs_pct"),
                     "mfe_giveback_fraction": pp.get("mfe_giveback_fraction"), "atr_trail_k": pp.get("atr_trail_k"),
                     "chandelier_atr_k": pp.get("chandelier_atr_k"),
                     "trail_activation_atr_k": pp.get("trail_activation_atr_k"),
                     "partial_take_profits": pp.get("partial_take_profits") or []},
            "all": _stats(a["pnl"], a["sig_train"] + a["sig_oos"], a["r"], a["mfe"], a["mae"]),
            "train": _stats(a["train"], a["sig_train"]),
            "oos": _stats(a["oos"], a["sig_oos"], a["oos_r"]),
            "exit_reasons": dict(Counter(a["exit"]).most_common(8)),
        })
        rows[-1]["holdout_verdict"] = holdout_verdict(rows[-1])
    return rows


def holdout_verdict(row: Mapping[str, Any]) -> str:
    """Selection uses train only; the chronological holdout confirms or rejects it."""
    tr, oos = row["train"], row["oos"]
    if tr["fills"] < MIN_TRAIN_FILLS_FOR_RANK or oos["fills"] < MIN_OOS_FILLS_FOR_RANK:
        return "INSUFFICIENT"
    if (tr["ev_per_fill_usd"] or 0) <= 0:
        return "NEGATIVE_TRAIN"
    return "CONFIRMED" if (oos["ev_per_fill_usd"] or 0) > 0 else "FAILED_HOLDOUT"


AXES = {
    "direction_rule": lambda r: r["direction_rule"],
    "fill_world": lambda r: r["fill_world"],
    "entry_offset_pct": lambda r: r["entry"]["offset_pct"],
    "chase_id": lambda r: r["entry"]["chase_id"],
    "entry_ttl_sec": lambda r: r["entry"]["ttl_sec"],
    "exit_family": lambda r: r["policy_family"],
    "exit_mode": lambda r: r["exit"]["mode"],
    "atr_stop_k": lambda r: r["exit"]["atr_stop_k"],
    "atr_tp_k": lambda r: r["exit"]["atr_tp_k"],
    "hard_stop_margin_pct": lambda r: r["exit"]["hard_stop_margin_pct"],
    "profit_ladder": lambda r: r["exit"]["ladder"],
    "thesis_cut_margin_pct": lambda r: r["exit"]["thesis_cut_margin_pct"],
    "time_stop_min": lambda r: r["exit"]["time_stop_min"],
    "break_even_arm_mfe_pct": lambda r: r["exit"]["break_even_arm_mfe_pct"],
    "mfe_giveback": lambda r: r["exit"]["mfe_giveback_fraction"] if r["exit"]["mfe_giveback_fraction"] is not None
    else r["exit"]["mfe_giveback_abs_pct"],
    "atr_trail_k": lambda r: r["exit"]["atr_trail_k"],
    "chandelier_atr_k": lambda r: r["exit"]["chandelier_atr_k"],
    "partial_plan": lambda r: json.dumps(r["exit"]["partial_take_profits"]) if r["exit"]["partial_take_profits"] else "none",
}


def dimension_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per genome axis: each value's best train-selected policy with its holdout result, so a collapsed axis is visible."""
    out = {}
    for axis, key in AXES.items():
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            groups[str(key(r))].append(r)
        values = []
        for value, items in groups.items():
            ranked = [r for r in items if r["holdout_verdict"] != "INSUFFICIENT"]
            best = max(ranked, key=lambda r: r["train"]["ev_per_fill_usd"]) if ranked else None
            values.append({
                "value": value, "policies": len(items), "pooled_fills": sum(r["all"]["fills"] for r in items),
                "confirmed_policies": sum(1 for r in items if r["holdout_verdict"] == "CONFIRMED"),
                "best_policy_id": best["policy_id"] if best else None,
                "best_train_ev_per_fill_usd": best["train"]["ev_per_fill_usd"] if best else None,
                "best_holdout_verdict": best["holdout_verdict"] if best else None,
                "best_oos_ev_per_fill_usd": best["oos"]["ev_per_fill_usd"] if best else None,
                "best_oos_win_rate_pct": best["oos"]["win_rate_pct"] if best else None,
                "best_oos_fills": best["oos"]["fills"] if best else None,
            })
        values.sort(key=lambda v: (v["best_train_ev_per_fill_usd"] is None, -(v["best_train_ev_per_fill_usd"] or 0)))
        out[axis] = {"distinct_values": len(values), "values": values}
    return out


def live_paper_by_lane(mirror: Path) -> list[dict[str, Any]]:
    """Terminal paper lifecycles per lane (LIVE_PAPER evidence, never pooled with simulated rows)."""
    try:
        import combo_pathway_config as reg  # noqa: PLC0415
        active, retired = set(reg.ACTIVE_TILE_ORDER), set(reg.RETIRED_TILE_LANES)
    except Exception:  # noqa: BLE001
        active, retired = set(), set()
    by_lane: dict[str, list[float]] = defaultdict(list)
    for row in _read_jsonl(mirror / "v3" / "ledgers" / "lifecycle.jsonl"):
        pnl = _num(row.get("net_pnl_usd"))
        if row.get("terminal") is True and pnl is not None:
            by_lane[str(row.get("research_lane") or "UNKNOWN")].append(pnl)
    out = []
    for lane, vals in sorted(by_lane.items()):
        arr = np.array(vals)
        status = "ACTIVE_REGISTRY" if lane in active else "RETIRED_QUARANTINED" if lane in retired else "NON_TILE_OR_CONTROL"
        out.append({"lane": lane, "evidence_label": "LIVE_PAPER", "registry_status": status,
                    "terminal_closes": len(vals), "wins": int((arr > 0).sum()),
                    "win_rate_pct": round(100 * float((arr > 0).mean()), 2),
                    "net_pnl_usd": round(float(arr.sum()), 6), "ev_per_close_usd": round(float(arr.mean()), 6),
                    "comparable_with_current_roster": status == "ACTIVE_REGISTRY"})
    return out


# ------------------------------------------------------------------ run

def analyzer_cycle_busy(status_path: Path) -> bool:
    """True while the segment analyzer is in its CPU-heavy ANALYZER phase.

    Cycles run back to back (promotion / migration / analyzer / retention), so
    the grid starts in the I/O-bound phases at below-normal priority; after the
    first pass only new episodes are computed (seconds).
    """
    try:
        st = json.loads(status_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return not st.get("finishedAt") and str(st.get("phase") or "").upper() == "ANALYZER"


def grid_signature(entries: list[dict[str, Any]], protections: Mapping[str, Any]) -> str:
    """Identity of every input that shapes one episode's outcome arrays (grid, constants, this code)."""
    blob = json.dumps({"entries": entries, "protections": protections, "chases": CHASES, "worlds": WORLDS,
                       "rules": DIRECTION_RULES, "lev": LEVERAGE, "margin": MARGIN_USD, "path_end": PATH_END_SEC,
                       "coverage": MIN_PATH_COVERAGE, "stale": TAPE_STALE_SEC,
                       "code": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _episode_key(ep: Mapping[str, Any]) -> str:
    ident = json.dumps([ep["episode_id"], ep["signal_ts"], ep["signal_price"], ep["direction"], ep.get("atr14_pct")])
    return hashlib.sha1(ident.encode("utf-8")).hexdigest()


def _cache_load(cache: Path, ep: Mapping[str, Any], idx: int) -> dict[str, Any] | None:
    path = cache / f"{_episode_key(ep)}.npz"
    try:
        with np.load(path) as z:
            return {"idx": idx, "status": "EVALUATED", "values": z["values"], "code": z["code"]}
    except (OSError, ValueError, KeyError):
        return None


def _cache_store(cache: Path, ep: Mapping[str, Any], res: Mapping[str, Any]) -> None:
    # Only evaluated windows are final; censored ones may complete as tape arrives.
    if res["status"] != "EVALUATED":
        return
    tmp = cache / f"{_episode_key(ep)}.tmp.npz"
    np.savez_compressed(tmp, values=res["values"], code=res["code"])
    os.replace(tmp, cache / f"{_episode_key(ep)}.npz")


def _revision() -> str:
    try:
        out = subprocess.run(["git", "-C", str(AGENT_DIR), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _iso(ts: float | None) -> str | None:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else None


def run(mirror: Path, tier_a: Path, out_dir: Path, *, workers: int = 2, max_episodes: int | None = None) -> dict[str, Any]:
    for p in (mirror, tier_a, out_dir):
        if "onedrive" in str(p).lower():
            raise SystemExit(f"REFUSED_ONEDRIVE: {p}")
    started = time.time()
    tape = load_tape(mirror, tier_a)
    episodes, episode_stats = load_episodes(mirror, tape)
    protections = protection_specs()
    entries = entry_specs()
    if max_episodes:
        need = max(int(e['ttl_sec']) for e in entries) + PATH_END_SEC + 5
        episodes = [ep for ep in episodes if int(ep['signal_ts']) + 1 + need <= tape.end][-max_episodes:]
    signature = grid_signature(entries, protections)
    cache = out_dir / "episode-cache" / signature
    cache.mkdir(parents=True, exist_ok=True)
    for stale in (out_dir / "episode-cache").iterdir():
        if stale.is_dir() and stale.name != signature:
            shutil.rmtree(stale, ignore_errors=True)
    results, jobs = [], []
    for idx, ep in enumerate(episodes):
        hit = _cache_load(cache, ep, idx)
        if hit is not None:
            results.append(hit)
        else:
            jobs.append((idx, ep))
    cached = len(results)
    if workers > 1 and len(jobs) > 16:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker,
                                 initargs=(tape, entries, protections)) as pool:
            fresh = list(pool.map(evaluate_episode, jobs, chunksize=8))
    else:
        _init_worker(tape, entries, protections)
        fresh = [evaluate_episode(j) for j in jobs]
    for res in fresh:
        _cache_store(cache, episodes[res["idx"]], res)
    results += fresh
    evaluated = [r["idx"] for r in results if r["status"] == "EVALUATED"]
    ts_sorted = sorted(episodes[i]["signal_ts"] for i in evaluated)
    cut_ts = ts_sorted[int(len(ts_sorted) * HOLDOUT_TRAIN_FRACTION)] if ts_sorted else 0.0
    rows = aggregate(results, episodes, entries, protections, cut_ts)
    parity = canonical_parity_sample([episodes[i] for i in evaluated], tape, protections)
    ranked = sorted((r for r in rows if r["holdout_verdict"] != "INSUFFICIENT"),
                    key=lambda r: r["train"]["ev_per_fill_usd"], reverse=True)
    verdicts = Counter(r["holdout_verdict"] for r in rows)
    report = {
        "schema": SCHEMA,
        "generated_at": _iso(time.time()),
        "code_revision": _revision(),
        "evidence_label": "SIMULATED_COUNTERFACTUAL",
        "note": ("Simulated on collected 1 s Bitfinex tape for every collected signal episode (executed, shadow, "
                 "blocked, and score-led side of no-trade calls). Not execution evidence and not qualification; live "
                 "paper outcomes are listed separately as LIVE_PAPER. Retired tiles appear only as parameter sets "
                 "evaluated over market data."),
        "fill_worlds": {
            "BBO_MARKETABLE": "fills at the resting limit only when a fresh valid opposite BBO is at/through it "
                              "(no queue position, no depth-quantity check)",
            "IDEAL_TOUCH": "fills at the limit when the traded price touches it (optimistic diagnostic)",
        },
        "cost_model": "zero trading fees and no funding (as the canonical replay); taker entries pay the spread",
        "path_model": (f"executable-side 1 s marks (bid for LONG exits, ask for SHORT) for {PATH_END_SEC}s after fill; "
                       f"windows with <{int(MIN_PATH_COVERAGE * 100)}% tape coverage are CENSORED"),
        "holdout": {"rule": "CHRONOLOGICAL_70_30_BY_SIGNAL_TS", "cut_ts": cut_ts, "cut_utc": _iso(cut_ts),
                    "sealed": False, "selection": "RANKED_BY_TRAIN_EV_PER_FILL; holdout only confirms",
                    "min_train_fills_for_rank": MIN_TRAIN_FILLS_FOR_RANK, "min_oos_fills_for_rank": MIN_OOS_FILLS_FOR_RANK,
                    "verdicts": dict(verdicts),
                    "independence_note": "episodes minutes apart share overlapping price paths; win-rate CIs assume "
                                         "independence and are optimistic"},
        "coverage": {
            "episodes_collected": len(episodes),
            "episode_status": dict(Counter(r["status"] for r in results)),
            "episodes_evaluated": len(evaluated),
            "episodes_from_cache": cached, "grid_signature": signature,
            "evaluated_by_source": dict(Counter(episodes[i]["source"] for i in evaluated)),
            "evaluated_by_atr_source": dict(Counter(episodes[i]["atr_source"] for i in evaluated)),
            "episode_inputs": episode_stats,
            "first_signal_utc": _iso(min(ts_sorted)) if ts_sorted else None,
            "last_signal_utc": _iso(max(ts_sorted)) if ts_sorted else None,
            "tape_first_utc": _iso(tape.t0), "tape_last_utc": _iso(tape.end - 1),
            "tape_seconds": int(len(tape.bid)), "tape_seconds_present": int((~np.isnan(tape.bid)).sum()),
            "engine_replay_window_note": "the Safe Policy Genome replays only the most recent 150 events "
                                         "(ANALYZER_PROTECTION_REPLAY_MAX_EVENTS)",
        },
        "grid": {
            "entries": len(entries), "protections": len(protections), "direction_rules": list(DIRECTION_RULES),
            "worlds": list(WORLDS), "policies_evaluated": len(rows), "policies_ranked": len(ranked),
            "stages": {
                "PROTECTION_SWEEP": f"taker + analyzer-hypothesis entries ({list(HYPOTHESIS_OFFSETS)} {HYPOTHESIS_CHASE} "
                                    f"TTL {HYPOTHESIS_TTL}s) x all {len(protections)} protections (engine protection_screen "
                                    f"+ active-registry exits)",
                "ENTRY_SWEEP": f"offsets {list(SWEEP_OFFSETS)} x chases {list(CHASES)} x TTL {list(SWEEP_TTLS)} "
                               f"x {len(sweep_protections(protections))} family exits",
            },
            "protection_source": "research_v3_candidates.protection_screen() + combo_pathway_config active-registry exits",
            "registry_protections": {pid: p["registry_lanes"] for pid, p in protections.items() if p.get("registry_lanes")},
        },
        "canonical_parity": parity,
        "dimension_summary": dimension_summary(rows),
        "top_100_by_world": {world: [r for r in ranked if r["fill_world"] == world][:100] for world in WORLDS},
        "confirmed_by_world": {world: [r for r in ranked if r["fill_world"] == world
                                       and r["holdout_verdict"] == "CONFIRMED"][:100] for world in WORLDS},
        "live_paper_by_lane": live_paper_by_lane(mirror),
        "inputs": {"tape_files": tape.receipts, "mirror": str(mirror), "tier_a": str(tier_a)},
        "runtime_sec": round(time.time() - started, 1),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    blob = gzip.compress("\n".join(json.dumps(r, separators=(",", ":"), default=str) for r in rows).encode("utf-8"))
    report["rows_artifact"] = {"file": "genome_grid_rows.jsonl.gz", "rows": len(rows), "sha256": hashlib.sha256(blob).hexdigest()}
    _atomic_write(out_dir / "genome_grid_rows.jsonl.gz", blob)
    _atomic_write(out_dir / "genome_grid_report.json", json.dumps(report, indent=1, default=str).encode("utf-8"))
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Full-history policy genome grid over collected tape (research only)")
    ap.add_argument("--mirror", default=os.environ.get("GENOME_GRID_MIRROR", DEFAULT_MIRROR))
    ap.add_argument("--tier-a", default=os.environ.get("GENOME_GRID_TIER_A", DEFAULT_TIER_A))
    ap.add_argument("--out-dir", default=os.environ.get("GENOME_GRID_OUT", DEFAULT_OUT))
    ap.add_argument("--workers", type=int, default=int(os.environ.get("GENOME_GRID_WORKERS", "2")))
    ap.add_argument("--max-episodes", type=int, default=None)
    ap.add_argument("--cycle-status", default=DEFAULT_CYCLE_STATUS)
    ap.add_argument("--ignore-cycle", action="store_true", help="run even while an analyzer cycle is active")
    args = ap.parse_args(argv)
    _below_normal()
    if not args.ignore_cycle and analyzer_cycle_busy(Path(args.cycle_status)):
        print(json.dumps({"status": "SKIPPED_ANALYZER_CYCLE_ACTIVE", "cycle_status": args.cycle_status}))
        return 0
    rep = run(Path(args.mirror), Path(args.tier_a), Path(args.out_dir), workers=args.workers,
              max_episodes=args.max_episodes)
    print(json.dumps({"status": "OK", "generated_at": rep["generated_at"], "coverage": rep["coverage"]["episode_status"],
                      "policies": rep["grid"]["policies_evaluated"], "parity": rep["canonical_parity"]["status"],
                      "runtime_sec": rep["runtime_sec"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())