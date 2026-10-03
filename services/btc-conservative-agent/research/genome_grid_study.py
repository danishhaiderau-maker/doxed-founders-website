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

Every row is SIMULATED on public 1 s Bitfinex tape. The headline fill world is
the shared ``REALISTIC_V1`` model (research/fill_model.py: measured latency,
opposite-BBO taker fills with size walk, maker fills only on trade-through or
queue consumption, latency on marketable exits, targets booked at the level);
``OPTIMISTIC_TOUCH_SHADOW`` is a labelled comparison shadow, never the
headline. Live paper outcomes are reported separately as
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

from research import fill_model as fm  # noqa: E402

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
TWIN_SIGNAL_SEC = 5.0
GENOME_COHORT = "AI_DECISION"
AI_EPISODE_CLASSES = ("AI_COMMITTED", "AI_COMMITTED_SCORE_CONFLICT", "AI_NO_TRADE_SCORE_LED", "AI_SIGNAL_REPLAY")
XVENUE_EPISODE_CLASSES = ("XVENUE_LEAD", "XVENUE_PREMIUM")
CLUSTER_SEC = 3600
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261003
WALK_FORWARD_MIN_CLASS_EPISODES = 60
HEADLINE_WORLD = fm.FILL_MODEL_VERSION
SHADOW_WORLD = "OPTIMISTIC_TOUCH_SHADOW"
WORLDS = (HEADLINE_WORLD, SHADOW_WORLD)
TRADE_FIELDS = ("bid_qty", "ask_qty", "tlow", "thigh", "buy_qty", "sell_qty", "buy_vwap", "sell_vwap")
VALUE_COLUMNS = ("net_pnl_usd", "r_multiple", "mfe_pct", "mae_pct", "fill_fraction", "markout_60s_bp", "maker")
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
    bid_qty: np.ndarray | None = None
    ask_qty: np.ndarray | None = None
    tlow: np.ndarray | None = None
    thigh: np.ndarray | None = None
    buy_qty: np.ndarray | None = None
    sell_qty: np.ndarray | None = None
    buy_vwap: np.ndarray | None = None
    sell_vwap: np.ndarray | None = None

    @property
    def end(self) -> int:
        return self.t0 + len(self.bid)

    def window(self, start: int, end: int) -> dict[str, np.ndarray]:
        a, b = max(0, start - self.t0), max(0, min(len(self.bid), end - self.t0))
        out = {k: getattr(self, k)[a:b] for k in ("bid", "ask", "last", "low", "high", "fresh")}
        for k in TRADE_FIELDS:
            arr = getattr(self, k)
            out[k] = arr[a:b] if arr is not None else np.full(max(0, b - a), np.nan)
        return out


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
        nan = float("nan")
        trade = tuple(nan if v is None else v for v in (
            _num(row.get("bid_qty")), _num(row.get("ask_qty")), _num(row.get("trade_low")), _num(row.get("trade_high")),
            _num(row.get("buy_qty")), _num(row.get("sell_qty")), _num(row.get("buy_vwap")), _num(row.get("sell_vwap"))))
        by_ts[int(ts)] = (bid, ask, last, min(low, last), max(high, last), 1.0 if fresh else 0.0) + trade
    if not by_ts:
        raise SystemExit("GENOME_GRID_NO_TAPE")
    t0, t1 = min(by_ts), max(by_ts) + 1
    arr = np.full((t1 - t0, 6 + len(TRADE_FIELDS)), np.nan)
    for ts, vals in by_ts.items():
        arr[ts - t0] = vals
    extra = {k: arr[:, 6 + j].copy() for j, k in enumerate(TRADE_FIELDS)}
    return Tape(t0, arr[:, 0].copy(), arr[:, 1].copy(), arr[:, 2].copy(), arr[:, 3].copy(), arr[:, 4].copy(),
                np.nan_to_num(arr[:, 5]), receipts, **extra)


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


def decision_identity(raw: Any) -> tuple[str, bool]:
    """Canonical decision id of a ledger/replay id and whether it is a reversal-study derivative.

    ``rev-scan-x`` (reversal study, side inverted) and ``lane-decision:<lane>:scan-x`` belong to the AI call
    ``scan-x``; ids of the per-second cross-venue evaluator are ``xvp-*`` / ``xvl-*``.
    """
    s = str(raw or "").strip()
    if s.startswith("lane-decision:"):
        s = s.rsplit(":", 1)[-1]
    rev = s.startswith("rev-")
    return (s[4:] if rev else s), rev


def xvenue_class(decision_id: str, lanes: Iterable[str] = ()) -> str | None:
    import combo_pathway_config as registry  # noqa: PLC0415
    if decision_id.startswith("xvp-"):
        return "XVENUE_PREMIUM"
    if decision_id.startswith("xvl-"):
        return "XVENUE_LEAD"
    for lane in lanes:
        name = str(lane or "").upper()
        if registry.is_cross_venue_clock_lane(name) or "XVENUE" in name:
            return "XVENUE_PREMIUM" if "PREMIUM" in name else "XVENUE_LEAD"
    return None


def _near(sorted_ts: list[float], ts: float, window: float) -> bool:
    k = bisect.bisect_left(sorted_ts, ts - window)
    return k < len(sorted_ts) and sorted_ts[k] <= ts + window


def load_episodes(mirror: Path, tape: Tape) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One episode per unique AI decision, classified; cross-venue triggers and duplicate rows never enter.

    v3 opportunities are keyed by ``shared_ai_call_id``. Triggers of the per-second cross-venue evaluator
    (``xvp-*`` / ``xvl-*``, Tiles 3/4) form a separate cohort even when the row also carries CONTROL_V1, and
    identity-less CONTROL_V1 rows (reversal-study ``rev-scan-*`` twins of an AI call, often side-inverted) are
    duplicates, not decisions. ``signal_replay`` starts add AI calls the v3 ledger lacks; reversal-study starts
    are derivatives of their call. Every dropped row is counted under ``excluded_*_by_reason``.
    """
    led = mirror / "v3" / "ledgers"
    lanes_by_episode: dict[str, Counter] = defaultdict(Counter)
    event_ids: dict[str, set[str]] = defaultdict(set)
    scores: dict[str, tuple[float, float]] = {}
    outcomes: dict[str, Counter] = defaultdict(Counter)
    for row in _read_jsonl(led / "decision.jsonl"):
        ep = str(row.get("episode_id") or "")
        if not ep:
            continue
        lanes_by_episode[ep][str(row.get("research_lane") or "UNKNOWN")] += 1
        if row.get("event_id"):
            event_ids[ep].add(str(row["event_id"]))
        ls, ss = _num(row.get("long_score")), _num(row.get("short_score"))
        if ls is not None and ss is not None:
            scores[ep] = (ls, ss)
        outcomes[ep][str(row.get("outcome_state") or "UNKNOWN")] += 1
    opportunities = list(_read_jsonl(led / "opportunity.jsonl"))

    def call_of(row: Mapping[str, Any]) -> str:
        call = str(row.get("shared_ai_call_id") or "")
        return "" if call.upper() in ("", "UNKNOWN", "NONE") else decision_identity(call)[0]

    v3_ai_ids = {c for c in map(call_of, opportunities) if c and not xvenue_class(c)}
    episodes: list[dict[str, Any]] = []
    stats: Counter = Counter()
    excluded_v3: Counter = Counter()
    excluded_replay: Counter = Counter()
    xvenue_ids: dict[str, set[str]] = defaultdict(set)
    kept_ids: set[str] = set()
    seen: set[str] = set()
    for row in opportunities:
        ep = str(row.get("episode_id") or "")
        ts, price = _num(row.get("signal_ts")), _num(row.get("signal_price"))
        if not ep or ep in seen or ts is None:
            stats["opportunity_skipped_identity"] += 1
            continue
        seen.add(ep)
        lanes = [lane for lane in lanes_by_episode[ep] if lane != "UNKNOWN"]
        call = call_of(row)
        ids = {decision_identity(e) for e in event_ids[ep]}
        xv = xvenue_class(call, lanes) or next((c for c in (xvenue_class(i) for i, _ in ids) if c), None)
        if xv:
            # Per-second cross-venue triggers are a different clock and signal source, never an AI decision.
            xvenue_ids[xv].add(call or ep)
            excluded_v3[xv] += 1
            continue
        if not call:
            bases = {i for i, _ in ids}
            if bases & v3_ai_ids:
                excluded_v3["DUPLICATE_OF_AI_DECISION"] += 1
            elif ids and all(rev for _, rev in ids):
                excluded_v3["REVERSAL_STUDY_ORPHAN"] += 1
            else:
                excluded_v3["IDENTITY_INCOMPLETE_NO_SHARED_AI_CALL_ID"] += 1
            continue
        if call in kept_ids:
            excluded_v3["DUPLICATE_OF_AI_DECISION"] += 1
            continue
        price_source = "SIGNAL_PRICE"
        if not price:
            i = int(ts) - tape.t0
            mid = (tape.bid[i] + tape.ask[i]) / 2 if 0 <= i < len(tape.bid) else float("nan")
            if mid != mid:
                excluded_v3["NO_SIGNAL_PRICE"] += 1
                continue
            price, price_source = float(mid), "TAPE_MID_AT_SIGNAL"
            stats["opportunity_price_from_tape_mid"] += 1
        raw = str(row.get("raw_direction") or "")
        ls, ss = scores.get(ep, (None, None))
        score_side = None if ls is None or ls == ss else ("LONG" if ls > ss else "SHORT")
        if raw in ("LONG", "SHORT"):
            direction, side_basis = raw, "AI_RAW_DIRECTION"
            klass = "AI_COMMITTED_SCORE_CONFLICT" if score_side and score_side != raw else "AI_COMMITTED"
        elif score_side:
            direction, side_basis, klass = score_side, "SCORE_LED_SIDE", "AI_NO_TRADE_SCORE_LED"
        else:
            excluded_v3["NO_SIDE"] += 1
            continue
        feat = row.get("feature_snapshot_at_signal") or {}
        atr, atr_source = _num(feat.get("atr14_pct_3m")), "FEATURE_ATR14_PCT_3M"
        if not atr or atr <= 0:
            atr, atr_source = tape_atr14_pct(tape, ts), "TAPE_ATR14_3M"
        regime = feat.get("regime_label") or feat.get("regime")
        if isinstance(regime, Mapping):
            regime = regime.get("value")
        kept_ids.add(call)
        episodes.append({
            "episode_id": ep, "decision_id": call, "episode_class": klass, "cohort": GENOME_COHORT,
            "signal_ts": ts, "signal_price": price, "direction": direction,
            "side_basis": side_basis, "source": "V3_OPPORTUNITY", "atr14_pct": atr, "atr_source": atr_source,
            "price_source": price_source,
            "regime": str(regime or "UNKNOWN"), "lanes": sorted(lanes_by_episode[ep]),
            "decision_outcomes": dict(outcomes[ep]),
        })
        stats[f"side_{side_basis}"] += 1
        stats[f"atr_{atr_source if atr else 'MISSING'}"] += 1
    # Two v3 rows of one call less than TWIN_SIGNAL_SEC apart cannot be separate 3-minute AI decisions.
    episodes.sort(key=lambda e: (e["signal_ts"], e["episode_id"]))
    deduped: list[dict[str, Any]] = []
    for e in episodes:
        if deduped and e["signal_ts"] - deduped[-1]["signal_ts"] < TWIN_SIGNAL_SEC:
            excluded_v3["DUPLICATE_TWIN_SIGNAL"] += 1
            continue
        deduped.append(e)
    episodes = deduped
    ai_ts = sorted(e["signal_ts"] for e in episodes)
    starts: list[tuple[bool, float, str, float, str, str]] = []
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
            did, rev = decision_identity(tid)
            starts.append((rev, ts, did, price, row["direction"], str(row.get("lane") or "unknown")))
    starts.sort()  # originals before reversal-study derivatives, then chronological
    for rev, ts, did, price, direction, lane in starts:
        xv = xvenue_class(did)
        if xv:
            xvenue_ids[xv].add(did)
            excluded_replay[xv] += 1
            continue
        if did in kept_ids or did in v3_ai_ids or _near(ai_ts, ts, DUPLICATE_SIGNAL_SEC):
            # The same AI decision is already an episode (any side: reversal starts invert it); count it once.
            excluded_replay["DUPLICATE_OF_AI_DECISION"] += 1
            continue
        if rev:
            excluded_replay["REVERSAL_STUDY_ORPHAN"] += 1
            continue
        kept_ids.add(did)
        bisect.insort(ai_ts, ts)
        episodes.append({
            "episode_id": f"signal_replay:{did}", "decision_id": did, "episode_class": "AI_SIGNAL_REPLAY",
            "cohort": GENOME_COHORT, "signal_ts": ts, "signal_price": price,
            "direction": direction, "side_basis": "SIGNAL_REPLAY_DIRECTION",
            "source": f"SIGNAL_REPLAY_{lane.upper()}", "atr14_pct": tape_atr14_pct(tape, ts),
            "atr_source": "TAPE_ATR14_3M", "regime": "UNKNOWN", "lanes": [lane], "decision_outcomes": {},
        })
        stats[f"signal_replay_{lane}"] += 1
    episodes.sort(key=lambda e: (e["signal_ts"], e["episode_id"]))
    out: dict[str, Any] = dict(stats)
    out["admitted_by_class"] = dict(Counter(e["episode_class"] for e in episodes))
    out["excluded_v3_by_reason"] = dict(excluded_v3)
    out["excluded_signal_replay_by_reason"] = dict(excluded_replay)
    out["v3_opportunity_rows"] = len(opportunities)
    out["v3_opportunities_admitted"] = sum(1 for e in episodes if e["source"] == "V3_OPPORTUNITY")
    out["xvenue_unique_triggers"] = {k: len(v) for k, v in sorted(xvenue_ids.items())}
    return episodes, out


def episode_integrity(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """The AI genome cohort must hold exactly one episode per AI decision and only AI episode classes."""
    ids = Counter(e.get("decision_id") for e in episodes)
    eps = Counter(e.get("episode_id") for e in episodes)
    classes = Counter(e.get("episode_class") for e in episodes)
    cohorts = Counter(e.get("cohort") for e in episodes)
    ts = sorted(float(e["signal_ts"]) for e in episodes)
    dup_ids = sorted(str(i) for i, n in ids.items() if i and n > 1)
    missing = ids.get(None, 0) + ids.get("", 0)
    foreign = {str(c): n for c, n in classes.items() if c not in AI_EPISODE_CLASSES}
    twins = sum(1 for a, b in zip(ts, ts[1:]) if b - a < TWIN_SIGNAL_SEC)
    violations = []
    if dup_ids:
        violations.append(f"DUPLICATE_DECISION_ID x{len(dup_ids)}: {dup_ids[:5]}")
    if missing:
        violations.append(f"MISSING_DECISION_ID x{missing}")
    if any(n > 1 for n in eps.values()):
        violations.append("DUPLICATE_EPISODE_ID")
    if foreign:
        violations.append(f"MIXED_EPISODE_CLASSES {foreign} in the {GENOME_COHORT} cohort")
    if set(cohorts) - {GENOME_COHORT}:
        violations.append(f"MIXED_COHORTS {dict(cohorts)}")
    if twins:
        violations.append(f"TWIN_SIGNALS x{twins} within {TWIN_SIGNAL_SEC:g}s")
    return {"status": "FAIL" if violations else "PASS", "cohort": GENOME_COHORT, "episodes": len(episodes),
            "unique_decision_ids": len([i for i in ids if i]), "duplicate_decision_ids": len(dup_ids),
            "missing_decision_ids": missing, "twin_signals": twins, "twin_window_sec": TWIN_SIGNAL_SEC,
            "classes": dict(classes), "allowed_classes": list(AI_EPISODE_CLASSES), "foreign_classes": foreign,
            "violations": violations}


# ----------------------------------------------------------- policy grid

class EpisodeIntegrityError(RuntimeError):
    """The AI-decision cohort holds duplicate decisions or non-AI classes; the study refuses to publish."""


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


Fill = tuple  # (fill second, entry price, filled fraction, maker 0/1)


def _w(w: Mapping[str, np.ndarray], key: str, n: int) -> np.ndarray:
    arr = w.get(key)
    return arr if arr is not None else np.full(n, np.nan)


def _qty(price: float) -> float:
    return MARGIN_USD * LEVERAGE / price


def realistic_taker_fill(direction: str, w: Mapping[str, np.ndarray], start: int = 0) -> Fill | None:
    """REALISTIC_V1 taker: opposite BBO of the first fresh quote at/after arrival, with size walk."""
    bid, ask, fresh = w["bid"], w["ask"], w["fresh"]
    long = direction == "LONG"
    for i in range(max(0, start), min(start + fm.TAKER_MAX_WAIT_SEC + 1, len(bid))):
        if fresh[i] and bid[i] == bid[i] and ask[i] >= bid[i]:
            top = float(ask[i] if long else bid[i])
            top_qty = _w(w, "ask_qty" if long else "bid_qty", len(bid))[i]
            walk = fm.size_walk(top, top_qty, float(ask[i] - bid[i]), _qty(top), direction)
            return (i, float(walk["vwap"]), 1.0, 0)
    return None


def realistic_maker_fill(entry: Mapping[str, Any], direction: str, signal_price: float,
                         w: Mapping[str, np.ndarray], start: int = 0) -> Fill | None:
    """REALISTIC_V1 resting limit (row twin: fill_model.maker_fill_rows): trade-through or queue consumption."""
    ttl = int(entry["ttl_sec"])
    n_all = len(w["bid"])
    if n_all < start + ttl:
        return None
    sl = slice(start, start + ttl)
    long = direction == "LONG"
    sign = 1.0 if long else -1.0
    bid, ask, fresh = w["bid"][sl], w["ask"][sl], w["fresh"][sl]
    limit = fm.round_limit_passive(limit_schedule(entry, direction, signal_price, bid, ask), direction)
    touch, opp = (bid, ask) if long else (ask, bid)
    touch_qty = _w(w, "bid_qty" if long else "ask_qty", n_all)[sl]
    opp_qty = _w(w, "ask_qty" if long else "bid_qty", n_all)[sl]
    agg_qty = _w(w, "sell_qty" if long else "buy_qty", n_all)[sl]
    agg_vwap = _w(w, "sell_vwap" if long else "buy_vwap", n_all)[sl]
    eps = fm.price_tol(limit)
    with np.errstate(invalid="ignore"):
        printed = np.nan_to_num(agg_qty) > 0
        through = printed & (sign * (limit - agg_vwap) > eps)
        at_limit = printed & (np.abs(agg_vwap - limit) <= eps)
        at_qty = np.where(at_limit, np.nan_to_num(agg_qty), 0.0)
        quoted = (fresh > 0) & ~np.isnan(touch) & ~np.isnan(opp)
        reached = quoted & (sign * (limit - touch) >= -eps)
        marketable = quoted & (sign * (limit - opp) >= -eps)
    qty = _qty(float(limit[0]))
    starts = np.flatnonzero(np.r_[True, limit[1:] != limit[:-1]])
    ends = np.r_[starts[1:], ttl]
    filled, cost, first = 0.0, 0.0, None
    for s, e in zip(starts.tolist(), ends.tolist()):
        lim = float(limit[s])
        tol = float(eps[s])
        if filled == 0.0 and marketable[s]:
            walk = fm.size_walk(float(opp[s]), opp_qty[s], float(ask[s] - bid[s]), qty, direction)
            return (start + s, float(walk["vwap"]), 1.0, 0)
        seg_through = through[s:e]
        t_rel = int(np.argmax(seg_through)) if seg_through.any() else None
        prints = at_qty[s:e]
        f_rel, part = None, 0.0
        if prints.any():
            r = reached[s:e]
            r_rel = int(np.argmax(r)) if r.any() else None
            p_rel = int(np.argmax(prints > 0))
            q_rel = r_rel if r_rel is not None and r_rel <= p_rel else p_rel
            q_qty = touch_qty[s + q_rel]
            if q_rel == r_rel and abs(touch[s + q_rel] - lim) > tol:
                queue = 0.0  # the limit improved the touch: nobody ahead
            else:
                queue = float(q_qty) if q_qty == q_qty else math.inf
            avail = np.cumsum(prints) - queue
            full = avail >= (qty - filled) - 1e-15
            f_rel = int(np.argmax(full)) if full.any() else None
        hits = [x for x in (t_rel, f_rel) if x is not None]
        if hits:
            k = min(hits)
            cost += (qty - filled) * lim
            return (first if first is not None else start + s + k, cost / qty, 1.0, 1)
        if prints.any():
            part = float(min(qty - filled, max(0.0, avail[-1])))
            if part > 0:
                first = first if first is not None else start + s + int(np.argmax(avail > 0))
                filled += part
                cost += part * lim
    if filled > 0 and first is not None:
        return (first, cost / filled, filled / qty, 1)
    return None


def optimistic_touch_fill(entry: Mapping[str, Any], direction: str, signal_price: float,
                          w: Mapping[str, np.ndarray]) -> Fill | None:
    """OPTIMISTIC_TOUCH shadow (the former IDEAL_TOUCH world): taker at last, maker on any traded touch."""
    bid, ask, last, low, high, fresh = (w[k] for k in ("bid", "ask", "last", "low", "high", "fresh"))
    if entry["offset_pct"] == 0.0:
        for i in range(min(5, len(bid))):
            if fresh[i] and bid[i] == bid[i]:
                return (i, float(last[i]), 1.0, 0)
        return None
    ttl = int(entry["ttl_sec"])
    if len(bid) < ttl:
        return None
    limit = limit_schedule(entry, direction, signal_price, bid[:ttl], ask[:ttl])
    with np.errstate(invalid="ignore"):
        touch = low[:ttl] <= limit if direction == "LONG" else high[:ttl] >= limit
    if not touch.any():
        return None
    i = int(np.argmax(touch))
    return (i, float(limit[i]), 1.0, 1)


def simulate_fill(entry: Mapping[str, Any], direction: str, signal_price: float,
                  w: Mapping[str, np.ndarray], latency_steps: int = 0) -> dict[str, Fill | None]:
    """Fill per world: (second, price, filled fraction, maker) or None (no fill / insufficient tape)."""
    if entry["offset_pct"] == 0.0:
        real = realistic_taker_fill(direction, w, latency_steps)
    else:
        real = realistic_maker_fill(entry, direction, signal_price, w, latency_steps)
    return {HEADLINE_WORLD: real, SHADOW_WORLD: optimistic_touch_fill(entry, direction, signal_price, w)}


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
    # Exit-side aggressor VWAP (buy prints above a LONG's sell target, sell prints below a SHORT's buy target).
    thr = w.get("buy_vwap" if direction == "LONG" else "sell_vwap")
    thr_qty = w.get("buy_qty" if direction == "LONG" else "sell_qty")
    if thr is not None and thr_qty is not None:
        thr = np.where(np.nan_to_num(thr_qty) > 0, thr, np.nan)
    thr_seg = thr[fill_idx: fill_idx + PATH_END_SEC][ok] if thr is not None else np.full(len(price), np.nan)
    thr_raw = (thr_seg - entry_price) / entry_price * 100.0 * LEVERAGE
    return {"age": age, "price": price, "cur": cur, "mfe": np.maximum.accumulate(cur), "mae": np.minimum.accumulate(cur),
            "thr_cur": thr_raw if direction == "LONG" else -thr_raw}


def fast_replay(path: Mapping[str, np.ndarray], spec: Mapping[str, Any], atr_pct: float,
                margin_usd: float = MARGIN_USD, *, realistic: bool = False,
                exit_latency_sec: float = 0.0) -> dict[str, Any]:
    """Vectorised twin of research_v3_policy_replay.replay_protected_policy (same precedence and floors).

    ``realistic=True`` applies the REALISTIC_V1 exit rules: targets / partial take-profits fill only when a trade
    prints through the level and are booked at the level; every other exit books the executable-side mark at
    trigger + ``exit_latency_sec``. ``realistic=False`` is the canonical trigger-mark booking (parity mode).
    """
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
    tp_m = float(profit["atr_tp_k"]) * atr_m if profit.get("atr_tp_k") is not None else None
    thr_cur = path.get("thr_cur")
    if thr_cur is None:
        thr_cur = np.full(n, np.nan)
    if mode in {"ATR_TARGET", "HYBRID_RUNNER"} and tp_m is not None:
        with np.errstate(invalid="ignore"):
            conds.append(("ATR_TAKE_PROFIT", thr_cur > tp_m if realistic else cur >= tp_m))
    best, reason = n, "PATH_END"
    for name, c in conds:
        if c.any():
            i = int(np.argmax(c))
            if i < best:
                best, reason = i, name
    exit_idx = best if best < n else n - 1
    if realistic:
        exit_margin, _ = fm.realistic_exit_margin(cur, age, exit_idx, reason, latency_sec=exit_latency_sec,
                                                  target_margin=tp_m)
    else:
        exit_margin = float(cur[exit_idx])
    remaining, realized = 1.0, 0.0
    for trigger_k, fraction in profit.get("partial_take_profits") or []:
        if remaining <= 0:
            continue
        level = float(trigger_k) * atr_m
        with np.errstate(invalid="ignore"):
            hit = thr_cur[: exit_idx + 1] > level if realistic else cur[: exit_idx + 1] >= level
        if hit.any():
            take = min(float(fraction), remaining)
            realized += take * (level if realistic else float(cur[int(np.argmax(hit))]))
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
_LATENCY_SEC: float = 0.0


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


def _init_worker(tape: Tape, entries: list[dict[str, Any]], protections: dict[str, dict[str, Any]],
                 latency_sec: float = 0.0) -> None:
    global _TAPE, _ENTRIES, _PROTECTIONS, _LATENCY_SEC
    _TAPE, _ENTRIES, _PROTECTIONS, _LATENCY_SEC = tape, entries, protections, float(latency_sec)
    _below_normal()


def latency_steps(signal_ts: float, latency_sec: float) -> int:
    """Tape index (0 = first full second after the signal) of the first quote observed at/after arrival."""
    start = int(math.floor(signal_ts)) + 1
    return max(0, int(math.ceil(float(signal_ts) + float(latency_sec) - fm.PRICE_EPS)) - start)


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
    lat = latency_steps(ep["signal_ts"], _LATENCY_SEC)
    need = max(int(e["ttl_sec"]) for e in _ENTRIES) + PATH_END_SEC + 5 + lat + fm.TAKER_MAX_WAIT_SEC
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
    maker_rate, taker_rate = fm._cost.fee_rates()
    notional = MARGIN_USD * LEVERAGE
    mid = (w["bid"] + w["ask"]) / 2.0
    horizon = fm.ADVERSE_SELECTION_HORIZON_SEC
    for rule in DIRECTION_RULES:
        direction = ep["direction"] if rule == "FOLLOW" else ("SHORT" if ep["direction"] == "LONG" else "LONG")
        paths: dict[tuple[int, float], dict | None] = {}
        replays: dict[tuple[int, float, str, bool], dict] = {}
        for e_i, entry in enumerate(_ENTRIES):
            fills = simulate_fill(entry, direction, ep["signal_price"], w, lat)
            prot_ids = list(_PROTECTIONS) if "PROTECTION_SWEEP" in entry["stages"] else sweep
            for world in WORLDS:
                fill = fills.get(world)
                if fill is None:
                    rows.extend((nan, nan, nan, nan, nan, nan, nan, NO_FILL) for _ in prot_ids)
                    continue
                f_idx, f_px, frac, maker = fill
                realistic = world == HEADLINE_WORLD
                pkey = (f_idx, f_px)
                if pkey not in paths:
                    paths[pkey] = prepare_path(direction, f_px, f_idx, w)
                path = paths[pkey]
                markout = nan
                if realistic and maker and f_idx + horizon < len(mid):
                    markout = fm.adverse_selection_bp(float(mid[f_idx + horizon]), f_px, direction)
                    markout = nan if markout is None else markout
                for pid in prot_ids:
                    if path is None:
                        rows.append((nan, nan, nan, nan, frac, markout, maker, CENSORED_PATH))
                        continue
                    key = (f_idx, f_px, pid, realistic)
                    rep = replays.get(key)
                    if rep is None:
                        spec = _spec(_PROTECTIONS[pid])
                        rep = fast_replay(path, spec, ep["atr14_pct"], realistic=realistic,
                                          exit_latency_sec=fm.EXIT_LATENCY_SEC if realistic else 0.0)
                        rep["r_multiple"] = rep["portfolio_margin_return_pct"] / initial_risk_margin_pct(spec, ep["atr14_pct"])
                        if realistic:
                            exit_rate = maker_rate if rep["exit_reason"] == "ATR_TAKE_PROFIT" else taker_rate
                            rep["fees_usd"] = notional * ((maker_rate if maker else taker_rate) + exit_rate)
                        replays[key] = rep
                    pnl = (rep["net_pnl_usd"] - rep.get("fees_usd", 0.0)) * frac
                    rows.append((pnl, rep["r_multiple"], rep["mfe_pct"], rep["mae_pct"], frac, markout, maker,
                                 EXIT_REASONS.index(rep["exit_reason"])))
    arr = np.array(rows, dtype=np.float64)
    return {"idx": idx, "status": "EVALUATED", "values": arr[:, :len(VALUE_COLUMNS)],
            "code": arr[:, len(VALUE_COLUMNS)].astype(np.int8)}


def canonical_parity_sample(episodes: list[dict[str, Any]], tape: Tape, protections: dict[str, dict[str, Any]],
                            sample: int = 40) -> dict[str, Any]:
    """Replay a deterministic sample through the engine's canonical evaluator and compare exactly."""
    from research_v3_policy_replay import replay_protected_policy  # noqa: PLC0415
    checked = mismatches = 0
    r_checked = r_mismatches = 0
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
            r_ok, r_exp, r_got = realistic_exit_parity(canon, path, spec, float(ep["atr14_pct"]), start)
            if r_ok is None:
                continue
            r_checked += 1
            if not r_ok:
                r_mismatches += 1
                if len(examples) < 5:
                    examples.append({"episode_id": ep["episode_id"], "protection_id": pid, "world": HEADLINE_WORLD,
                                     "canonical_trigger_plus_fill_model": r_exp, "genome_grid": r_got})
    return {"schema": "genome_grid_canonical_parity_v2",
            "evaluator": "research_v3_policy_replay.replay_protected_policy", "checked": checked,
            "mismatches": mismatches, "examples": examples,
            "status": "MATCH" if checked and not mismatches else ("MISMATCH" if mismatches else "NOT_CHECKED"),
            "realistic_exit_parity": {
                "fill_model": HEADLINE_WORLD, "checked": r_checked, "mismatches": r_mismatches,
                "rule": "canonical trigger (exit_ts, reason) + fill_model.realistic_exit_margin == genome grid "
                        "REALISTIC_V1 exit (marketable exits, no partial take-profits)",
                "status": "MATCH" if r_checked and not r_mismatches else ("MISMATCH" if r_mismatches else "NOT_CHECKED")}}


def realistic_exit_parity(canon: Mapping[str, Any], path: Mapping[str, np.ndarray], spec: Mapping[str, Any],
                          atr_pct: float, start: int) -> tuple[bool | None, float | None, float | None]:
    """Apply the shared fill model to the canonical replay's trigger and compare with the grid's realistic exit."""
    profit = spec["profit_protection"]
    reason = str(canon.get("exit_reason") or "")
    if profit.get("partial_take_profits") or reason == "ATR_TAKE_PROFIT" or canon.get("exit_ts") is None:
        return None, None, None
    fast = fast_replay(path, spec, atr_pct, realistic=True, exit_latency_sec=fm.EXIT_LATENCY_SEC)
    if fast["exit_reason"] != reason:
        return None, None, None  # a trade-through target changed the trigger; not a booking comparison
    idx = int(np.searchsorted(path["age"], float(canon["exit_ts"]) - start - 1e-6))
    idx = min(idx, len(path["age"]) - 1)
    margin, _ = fm.realistic_exit_margin(path["cur"], path["age"], idx, reason, latency_sec=fm.EXIT_LATENCY_SEC)
    expected = MARGIN_USD * margin / 100.0
    return abs(expected - fast["net_pnl_usd"]) <= 1e-9, round(expected, 10), round(fast["net_pnl_usd"], 10)


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
              protections: dict[str, dict[str, Any]], cut_ts: float,
              matrix_out: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    done = sorted((r for r in results if r["status"] == "EVALUATED"), key=lambda r: episodes[r["idx"]]["signal_ts"])
    keys = row_keys(entries, protections)
    if not done:
        return []
    values = np.stack([r["values"] for r in done])  # episodes x keys x (pnl, r, mfe, mae)
    codes = np.stack([r["code"] for r in done])
    assert values.shape[1] == len(keys), (values.shape, len(keys))
    is_oos = np.array([episodes[r["idx"]]["signal_ts"] >= cut_ts for r in done])
    classes = np.array([str(episodes[r["idx"]].get("episode_class") or "UNCLASSIFIED") for r in done])
    filled_all = codes >= 0
    pnl0 = np.where(filled_all, values[:, :, 0], 0.0)
    class_totals = {c: (int((classes == c).sum()), filled_all[classes == c].sum(axis=0), pnl0[classes == c].sum(axis=0))
                    for c in sorted(set(classes.tolist()))}
    del pnl0
    if matrix_out is not None:
        matrix_out.update(values=values, codes=codes, classes=classes,
                          ts=np.array([episodes[r["idx"]]["signal_ts"] for r in done], dtype=np.float64),
                          episode_idx=np.array([r["idx"] for r in done], dtype=np.int64))
    n_oos = int(is_oos.sum())
    n_train = len(done) - n_oos
    rows = []
    for k, (e_i, pid, rule, world) in enumerate(keys):
        filled = codes[:, k] >= 0
        v = values[filled, k].astype(np.float64)
        oos_f = is_oos[filled]
        attempted = ~np.isnan(values[:, k, 4])
        fq = values[attempted, k]
        maker_fills = fq[fq[:, 6] > 0]
        markouts = maker_fills[:, 5][~np.isnan(maker_fills[:, 5])] if len(maker_fills) else np.array([])
        fill_quality = {
            "fills_incl_censored_path": int(attempted.sum()),
            "partial_fills": int((fq[:, 4] < 1.0 - 1e-12).sum()),
            "avg_fill_fraction": round(float(fq[:, 4].mean()), 4) if len(fq) else None,
            "maker_fills": int(len(maker_fills)),
            "maker_avg_markout_60s_bp": round(float(markouts.mean()), 3) if len(markouts) else None,
            "maker_adverse_share": round(float((markouts < 0).mean()), 4) if len(markouts) else None,
        }
        a = {"pnl": v[:, 0].tolist(), "r": v[:, 1].tolist(), "mfe": v[:, 2].tolist(), "mae": v[:, 3].tolist(),
             "exit": [EXIT_REASONS[c] for c in codes[filled, k]], "train": v[~oos_f, 0].tolist(),
             "oos": v[oos_f, 0].tolist(), "oos_r": v[oos_f, 1].tolist(), "sig_train": n_train, "sig_oos": n_oos}
        entry, prot = entries[e_i], protections[pid]
        lp, pp = prot["loss_protection"], prot["profit_protection"]
        windows, step, interval = CHASES[entry["chase_id"]]
        rows.append({
            "schema": ROWS_SCHEMA, "policy_id": f"{rule}|{entry['entry_id']}|{pid}",
            "evidence_label": "SIMULATED_COUNTERFACTUAL", "fill_world": world, "direction_rule": rule,
            "fill_model": world, "fill_model_role": fm.HEADLINE_ROLE if world == HEADLINE_WORLD else fm.SHADOW_ROLE,
            "fill_quality": fill_quality,
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
            "by_episode_class": {c: {"signals": s, "fills": int(f[k]), "net_pnl_usd": round(float(p[k]), 6),
                                     "ev_per_fill_usd": round(float(p[k]) / int(f[k]), 6) if f[k] else None}
                                 for c, (s, f, p) in class_totals.items()},
        })
        rows[-1]["holdout_verdict"] = holdout_verdict(rows[-1])
    return rows


def _bp(usd: float | None) -> float | None:
    return None if usd is None else round(usd / (MARGIN_USD * LEVERAGE) * 1e4, 2)


def cluster_bootstrap(pnl: Iterable[float], ts: Iterable[float], *, cluster_sec: int = CLUSTER_SEC,
                      resamples: int = BOOTSTRAP_RESAMPLES, seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    """EV-per-fill 95% CI from a block bootstrap over UTC-hour clusters, and the implied effective independent n.

    Fills within the same hour share overlapping price paths; resampling whole hours keeps that dependence.
    ``n_eff = n * var_iid(mean) / var_cluster_bootstrap(mean)``, clipped to [1, n].
    """
    p = np.asarray(list(pnl), dtype=np.float64)
    t = np.asarray(list(ts), dtype=np.float64)
    n = int(len(p))
    out: dict[str, Any] = {"method": "1H_CLUSTER_BLOCK_BOOTSTRAP_95", "cluster_sec": cluster_sec, "fills": n,
                           "resamples": resamples}
    if n < 2:
        return out | {"clusters": n, "ev_ci95_usd": [None, None], "ev_ci95_bp": [None, None], "n_eff": float(n),
                      "ci_excludes_zero": False}
    _, inv = np.unique(np.floor(t / cluster_sec).astype(np.int64), return_inverse=True)
    sums, counts = np.bincount(inv, weights=p), np.bincount(inv).astype(np.float64)
    c = len(sums)
    draw = np.random.default_rng(seed).integers(0, c, size=(resamples, c))
    means = sums[draw].sum(axis=1) / counts[draw].sum(axis=1)
    lo, hi = (float(x) for x in np.percentile(means, [2.5, 97.5]))
    var_boot, var_iid = float(means.var(ddof=1)), float(p.var(ddof=1)) / n
    n_eff = float(n) if var_boot <= 0 else min(float(n), max(1.0, n * var_iid / var_boot))
    return out | {"clusters": c, "ev_ci95_usd": [round(lo, 6), round(hi, 6)], "ev_ci95_bp": [_bp(lo), _bp(hi)],
                  "n_eff": round(n_eff, 1), "ci_excludes_zero": bool(lo > 0 or hi < 0)}


def row_cluster_stats(matrix: Mapping[str, Any], k: int, cut_ts: float) -> dict[str, Any]:
    filled = matrix["codes"][:, k] >= 0
    pnl, ts = matrix["values"][filled, k, 0], matrix["ts"][filled]
    oos = ts >= cut_ts
    return {"all": cluster_bootstrap(pnl, ts), "oos": cluster_bootstrap(pnl[oos], ts[oos])}


def walk_forward_by_day(matrix: Mapping[str, Any], policy_ids: list[str], key_mask: np.ndarray, *,
                        episode_mask: np.ndarray | None = None,
                        min_train_fills: int = MIN_TRAIN_FILLS_FOR_RANK) -> dict[str, Any]:
    """Walk-forward by UTC day: pick the best train-EV policy on all prior days, score it on the next day only."""
    ts = matrix["ts"]
    em = np.ones(len(ts), dtype=bool) if episode_mask is None else np.asarray(episode_mask, dtype=bool)
    cols = np.flatnonzero(key_mask)
    days = np.floor(ts / 86400).astype(np.int64)
    filled = matrix["codes"][:, cols] >= 0
    pnl = np.where(filled, matrix["values"][:, cols, 0], 0.0)
    folds: list[dict[str, Any]] = []
    oos_pnl: list[float] = []
    oos_ts: list[float] = []
    for d in sorted(set(days[em].tolist()))[1:]:
        tr, te = em & (days < d), em & (days == d)
        f, s = filled[tr].sum(axis=0), pnl[tr].sum(axis=0)
        fold: dict[str, Any] = {"test_day_utc": time.strftime("%Y-%m-%d", time.gmtime(d * 86400)),
                                "train_episodes": int(tr.sum()), "test_episodes": int(te.sum())}
        ok = f >= min_train_fills
        if not ok.any():
            folds.append(fold | {"status": "NO_ELIGIBLE_POLICY"})
            continue
        ev = np.where(ok, s / np.maximum(f, 1), -np.inf)
        j = int(np.argmax(ev))
        hit = filled[te, j]
        tp = pnl[te, j][hit]
        oos_pnl.extend(tp.tolist())
        oos_ts.extend(ts[te][hit].tolist())
        folds.append(fold | {"status": "SCORED", "selected_policy_id": policy_ids[cols[j]],
                             "train_fills": int(f[j]), "train_ev_per_fill_usd": round(float(ev[j]), 6),
                             "test_fills": int(len(tp)), "test_net_pnl_usd": round(float(tp.sum()), 6),
                             "test_ev_per_fill_usd": round(float(tp.mean()), 6) if len(tp) else None,
                             "test_ev_per_fill_bp": _bp(float(tp.mean())) if len(tp) else None})
    pooled = _stats(oos_pnl, sum(f["test_episodes"] for f in folds if f.get("status") == "SCORED"))
    pooled["ev_per_fill_bp"] = _bp(pooled.get("ev_per_fill_usd"))
    pooled["cluster_1h"] = cluster_bootstrap(oos_pnl, oos_ts)
    return {"rule": "WALK_FORWARD_BY_UTC_DAY", "fill_world": HEADLINE_WORLD, "min_train_fills": min_train_fills,
            "selection": "argmax train EV/fill over all prior UTC days among REALISTIC_V1 policies; next day scored once",
            "episodes": int(em.sum()), "folds": folds, "pooled_oos": pooled}


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


def _best_ranked(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    ranked = [r for r in items if r["holdout_verdict"] != "INSUFFICIENT"]
    return max(ranked, key=lambda r: r["train"]["ev_per_fill_usd"]) if ranked else None


def dimension_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per genome axis and value: the best train-selected REALISTIC_V1 policy with its chronological holdout.

    Each value row is selected only among that value's headline-world policies, so rows differ by construction;
    the optimistic touch world appears only in the labelled ``shadow_*`` columns and can never be a winner.
    """
    out = {}
    for axis, key in AXES.items():
        groups: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for r in rows:
            groups[str(key(r))][r["fill_world"]].append(r)
        values = []
        for value, by_world in groups.items():
            items = by_world[HEADLINE_WORLD]
            best, shadow = _best_ranked(items), _best_ranked(by_world[SHADOW_WORLD])
            values.append({
                "value": value, "fill_world": HEADLINE_WORLD, "policies": len(items),
                "pooled_fills": sum(r["all"]["fills"] for r in items),
                "confirmed_policies": sum(1 for r in items if r["holdout_verdict"] == "CONFIRMED"),
                "best_policy_id": best["policy_id"] if best else None,
                "best_fill_world": best["fill_world"] if best else None,
                "best_train_ev_per_fill_usd": best["train"]["ev_per_fill_usd"] if best else None,
                "best_holdout_verdict": best["holdout_verdict"] if best else None,
                "best_oos_ev_per_fill_usd": best["oos"]["ev_per_fill_usd"] if best else None,
                "best_oos_win_rate_pct": best["oos"]["win_rate_pct"] if best else None,
                "best_oos_fills": best["oos"]["fills"] if best else None,
                "best_cluster_1h": (best.get("cluster_1h") or {}).get("all") if best else None,
                "shadow_label": f"{SHADOW_WORLD} - comparison shadow, not headline",
                "shadow_best_policy_id": shadow["policy_id"] if shadow else None,
                "shadow_best_train_ev_per_fill_usd": shadow["train"]["ev_per_fill_usd"] if shadow else None,
                "shadow_best_oos_ev_per_fill_usd": shadow["oos"]["ev_per_fill_usd"] if shadow else None,
            })
        values.sort(key=lambda v: (v["best_train_ev_per_fill_usd"] is None, -(v["best_train_ev_per_fill_usd"] or 0)))
        ranked = [v for v in values if v["best_policy_id"]]
        out[axis] = {"distinct_values": len(values), "ranked_values": len(ranked),
                     "distinct_best_policies": len({v["best_policy_id"] for v in ranked}),
                     "headline_fill_world": HEADLINE_WORLD, "values": values}
    return out


def live_paper_by_lane(mirror: Path) -> list[dict[str, Any]]:
    """Terminal paper lifecycles per lane (LIVE_PAPER evidence, never pooled with simulated rows)."""
    try:
        import combo_pathway_config as reg  # noqa: PLC0415
        active, retired = set(reg.ACTIVE_TILE_ORDER), set(reg.RETIRED_TILE_LANES)
    except Exception:  # noqa: BLE001
        active, retired = set(), set()
    by_lane: dict[str, list[float]] = defaultdict(list)
    seen_ts: dict[str, list[float]] = defaultdict(list)
    for row in _read_jsonl(mirror / "v3" / "ledgers" / "lifecycle.jsonl"):
        pnl = _num(row.get("net_pnl_usd"))
        if row.get("terminal") is True and pnl is not None:
            lane = str(row.get("research_lane") or "UNKNOWN")
            by_lane[lane].append(pnl)
            marks = [t for t in (_num(row.get("mae_ts")), _num(row.get("mfe_ts"))) if t]
            if marks:
                seen_ts[lane].append(max(marks))
    out = []
    for lane, vals in sorted(by_lane.items()):
        arr = np.array(vals)
        curve = np.cumsum(arr)
        span = (max(seen_ts[lane]) - min(seen_ts[lane])) / 86400.0 if len(seen_ts[lane]) > 1 else 0.0
        status = "ACTIVE_REGISTRY" if lane in active else "RETIRED_QUARANTINED" if lane in retired else "NON_TILE_OR_CONTROL"
        out.append({"lane": lane, "evidence_label": "LIVE_PAPER", "registry_status": status,
                    "fill_model": "FLY_PAPER_PRE_REALISTIC_V1",
                    "fill_model_note": "Fly paper fills predate REALISTIC_V1 (post-freeze clean epoch); not comparable "
                                       "with the REALISTIC_V1 headline rows",
                    "terminal_closes": len(vals), "wins": int((arr > 0).sum()), "losses": int((arr < 0).sum()),
                    "win_rate_pct": round(100 * float((arr > 0).mean()), 2),
                    "net_pnl_usd": round(float(arr.sum()), 6), "ev_per_close_usd": round(float(arr.mean()), 6),
                    "avg_pnl_bp": _bp(float(arr.mean())),
                    "max_drawdown_usd": round(float(np.min(curve - np.maximum.accumulate(np.maximum(curve, 0.0)))), 6),
                    "max_drawdown_basis": "ledger append order (close order); peak includes the zero start",
                    "trades_per_day": round(len(vals) / span, 2) if span >= 1 / 24 else None,
                    "comparable_with_current_roster": status == "ACTIVE_REGISTRY"})
    out.sort(key=lambda x: (-x["net_pnl_usd"], x["lane"]))
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


def grid_signature(entries: list[dict[str, Any]], protections: Mapping[str, Any], latency_sec: float = 0.0) -> str:
    """Identity of every input that shapes one episode's outcome arrays (grid, constants, fill model, this code)."""
    blob = json.dumps({"entries": entries, "protections": protections, "chases": CHASES, "worlds": WORLDS,
                       "fill_model": fm.fill_model_fingerprint(), "latency_sec": latency_sec,
                       "fill_model_code": hashlib.sha256(Path(fm.__file__).read_bytes()).hexdigest(),
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
    integrity = episode_integrity(episodes)
    if integrity["status"] != "PASS":
        raise EpisodeIntegrityError(json.dumps(integrity["violations"])[:600])
    protections = protection_specs()
    entries = entry_specs()
    latency = measure_latency(mirror)
    lat_sec = float(latency["latency_sec"])
    if max_episodes:
        need = max(int(e['ttl_sec']) for e in entries) + PATH_END_SEC + 5 + int(math.ceil(lat_sec)) + fm.TAKER_MAX_WAIT_SEC + 1
        episodes = [ep for ep in episodes if int(ep['signal_ts']) + 1 + need <= tape.end][-max_episodes:]
    signature = grid_signature(entries, protections, lat_sec)
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
                                 initargs=(tape, entries, protections, lat_sec)) as pool:
            fresh = list(pool.map(evaluate_episode, jobs, chunksize=8))
    else:
        _init_worker(tape, entries, protections, lat_sec)
        fresh = [evaluate_episode(j) for j in jobs]
    for res in fresh:
        _cache_store(cache, episodes[res["idx"]], res)
    results += fresh
    evaluated = [r["idx"] for r in results if r["status"] == "EVALUATED"]
    ts_sorted = sorted(episodes[i]["signal_ts"] for i in evaluated)
    cut_ts = ts_sorted[int(len(ts_sorted) * HOLDOUT_TRAIN_FRACTION)] if ts_sorted else 0.0
    matrix: dict[str, Any] = {}
    rows = aggregate(results, episodes, entries, protections, cut_ts, matrix_out=matrix)
    parity = canonical_parity_sample([episodes[i] for i in evaluated], tape, protections)
    ranked = sorted((r for r in rows if r["holdout_verdict"] != "INSUFFICIENT"),
                    key=lambda r: r["train"]["ev_per_fill_usd"], reverse=True)
    verdicts = Counter(r["holdout_verdict"] for r in rows)
    top_by_world = {world: [r for r in ranked if r["fill_world"] == world][:100] for world in WORLDS}
    confirmed = {world: [r for r in ranked if r["fill_world"] == world and r["holdout_verdict"] == "CONFIRMED"][:100]
                 for world in WORLDS}
    walk_forward: dict[str, Any] = {}
    class_summary: dict[str, Any] = {}
    layer: dict[str, Any] | None = None
    if matrix:
        index = {id(r): k for k, r in enumerate(rows)}
        for r in {id(r): r for lst in (*top_by_world.values(), *confirmed.values()) for r in lst}.values():
            r["cluster_1h"] = row_cluster_stats(matrix, index[id(r)], cut_ts)
        headline_rows = [r for r in rows if r["fill_world"] == HEADLINE_WORLD]
        for key in AXES.values():
            groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for r in headline_rows:
                groups[str(key(r))].append(r)
            for items in groups.values():
                best = _best_ranked(items)
                if best is not None and "cluster_1h" not in best:
                    best["cluster_1h"] = row_cluster_stats(matrix, index[id(best)], cut_ts)
        policy_ids = [r["policy_id"] for r in rows]
        headline_keys = np.array([r["fill_world"] == HEADLINE_WORLD for r in rows])
        walk_forward[GENOME_COHORT] = walk_forward_by_day(matrix, policy_ids, headline_keys)
        for klass in AI_EPISODE_CLASSES:
            mask = matrix["classes"] == klass
            if int(mask.sum()) >= WALK_FORWARD_MIN_CLASS_EPISODES:
                walk_forward[klass] = walk_forward_by_day(matrix, policy_ids, headline_keys, episode_mask=mask)
        head = top_by_world[HEADLINE_WORLD][:1]
        class_summary = {
            "evaluated_by_class": dict(Counter(matrix["classes"].tolist())),
            "headline_top_policy": ({"policy_id": head[0]["policy_id"], "by_episode_class": head[0]["by_episode_class"]}
                                    if head else None),
            "best_headline_policy_by_class": {
                klass: next(({"policy_id": r["policy_id"], **r["by_episode_class"][klass],
                              "ev_per_fill_bp": _bp(r["by_episode_class"][klass]["ev_per_fill_usd"])}
                             for r in sorted((r for r in rows if r["fill_world"] == HEADLINE_WORLD
                                              and (r["by_episode_class"].get(klass) or {}).get("fills", 0)
                                              >= MIN_TRAIN_FILLS_FOR_RANK),
                                             key=lambda r: -(r["by_episode_class"][klass]["ev_per_fill_usd"] or 0))),
                            None)
                for klass in sorted(set(matrix["classes"].tolist()))},
            "note": "best_headline_policy_by_class is in-sample (all fills, no holdout) - descriptive only",
        }
        from research import genome_research_layer as research_layer  # noqa: PLC0415

        layer = research_layer.compute(sys.modules[__name__], mirror, tape, episodes, matrix, row_keys(entries, protections),
                                       entries, protections, rows, out_dir, generation=signature,
                                       code_revision=_revision())
        del matrix
    report = {
        "schema": SCHEMA,
        "generated_at": _iso(time.time()),
        "code_revision": _revision(),
        "evidence_label": "SIMULATED_COUNTERFACTUAL",
        "fill_model": fm.fill_model_declaration(decision_latency=latency, exit_latency_sec=fm.EXIT_LATENCY_SEC),
        "headline_fill_world": HEADLINE_WORLD,
        "shadow_fill_worlds": [SHADOW_WORLD],
        "headline_vs_shadow": headline_vs_shadow(rows),
        "note": ("Simulated on collected 1 s Bitfinex tape for one episode per unique AI decision (committed calls, "
                 "score-led side of no-trade calls, and signal-replay AI calls the v3 ledger lacks). Cross-venue "
                 "evaluator triggers and duplicate/reversal-study rows are excluded and counted. Not execution "
                 "evidence and not qualification; live paper outcomes are listed separately as LIVE_PAPER. Retired "
                 "tiles appear only as parameter sets evaluated over market data."),
        "episode_integrity": integrity,
        "episode_cohorts": {
            GENOME_COHORT: {"status": "EVALUATED", "classes": list(AI_EPISODE_CLASSES),
                            "admitted_by_class": episode_stats.get("admitted_by_class")},
            "XVENUE_EVALUATOR": {"status": "EXCLUDED_FROM_AI_GENOME", "classes": list(XVENUE_EPISODE_CLASSES),
                                 "unique_triggers": episode_stats.get("xvenue_unique_triggers"),
                                 "note": "per-second cross-venue evaluator triggers (Tiles 3/4) run on a 60 s clock; "
                                         "they are not AI decisions and are studied separately (XVENUE-INVERT-STUDY)"},
            "EXCLUDED_DUPLICATES": {"v3": episode_stats.get("excluded_v3_by_reason"),
                                    "signal_replay": episode_stats.get("excluded_signal_replay_by_reason")},
        },
        "episode_class_summary": class_summary,
        "walk_forward_by_utc_day": walk_forward,
        "fill_worlds": {
            HEADLINE_WORLD: "HEADLINE. Shared research/fill_model.py REALISTIC_V1: taker at the opposite BBO of the "
                            f"first fresh quote at signal + measured latency ({lat_sec:g}s, {latency['source']}) with "
                            "size walk; resting limits fill only on a trade print through the limit or at-limit "
                            "aggressor volume beyond the top-of-book queue estimate (partials allowed, reprice loses "
                            "queue); stops/floors/time exits at the executable-side BBO after "
                            f"{fm.EXIT_LATENCY_SEC:g}s; targets fill only on a trade through and book at the level",
            SHADOW_WORLD: "COMPARISON SHADOW, NOT HEADLINE (former IDEAL_TOUCH): taker at last price with no "
                          "latency, limit fills on any traded touch, exits booked at the triggering mark",
        },
        "cost_model": (f"fees from bitfinex_cost_profile ({fm._cost.FEE_PROFILE_ID}: maker "
                       f"{fm._cost.MAKER_FEE_RATE:g}, taker {fm._cost.TAKER_FEE_RATE:g}) applied per leg in "
                       "REALISTIC_V1; no funding; spread, latency and size walk are in the fill prices"),
        "path_model": (f"executable-side 1 s marks (bid for LONG exits, ask for SHORT) for {PATH_END_SEC}s after fill; "
                       f"windows with <{int(MIN_PATH_COVERAGE * 100)}% tape coverage are CENSORED"),
        "holdout": {"rule": "CHRONOLOGICAL_70_30_BY_SIGNAL_TS", "cut_ts": cut_ts, "cut_utc": _iso(cut_ts),
                    "sealed": False, "selection": "RANKED_BY_TRAIN_EV_PER_FILL; holdout only confirms",
                    "min_train_fills_for_rank": MIN_TRAIN_FILLS_FOR_RANK, "min_oos_fills_for_rank": MIN_OOS_FILLS_FOR_RANK,
                    "verdicts": dict(verdicts),
                    "independence_note": "episodes minutes apart share overlapping price paths; win-rate CIs assume "
                                         "independence and are optimistic - use cluster_1h (1 h block-bootstrap EV "
                                         "CI and n_eff) and walk_forward_by_utc_day for inference"},
        "coverage": {
            "episodes_collected": len(episodes),
            "episode_status": dict(Counter(r["status"] for r in results)),
            "episodes_evaluated": len(evaluated),
            "episodes_from_cache": cached, "grid_signature": signature,
            "evaluated_by_source": dict(Counter(episodes[i]["source"] for i in evaluated)),
            "evaluated_by_class": dict(Counter(episodes[i]["episode_class"] for i in evaluated)),
            "v3_opportunity_rows": episode_stats.get("v3_opportunity_rows"),
            "v3_opportunities_excluded_declared": sum((episode_stats.get("excluded_v3_by_reason") or {}).values()),
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
        "top_100_by_world": top_by_world,
        "confirmed_by_world": confirmed,
        "live_paper_by_lane": live_paper_by_lane(mirror),
        "inputs": {"tape_files": tape.receipts, "mirror": str(mirror), "tier_a": str(tier_a)},
        "runtime_sec": round(time.time() - started, 1),
    }
    if layer is not None:
        from research import genome_research_layer as research_layer  # noqa: PLC0415

        report.update(research_layer.report_block(layer))
    out_dir.mkdir(parents=True, exist_ok=True)
    blob = gzip.compress("\n".join(json.dumps(r, separators=(",", ":"), default=str) for r in rows).encode("utf-8"))
    report["rows_artifact"] = {"file": "genome_grid_rows.jsonl.gz", "rows": len(rows), "sha256": hashlib.sha256(blob).hexdigest()}
    _atomic_write(out_dir / "genome_grid_rows.jsonl.gz", blob)
    _atomic_write(out_dir / "genome_grid_report.json", json.dumps(report, indent=1, default=str).encode("utf-8"))
    if layer is not None:
        try:
            research_layer.materialize(layer, report, out_dir)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"research_api_cache": "FAILED", "error": f"{type(exc).__name__}: {exc}"[:300]}),
                  file=sys.stderr)
    return report


def measure_latency(mirror: Path) -> dict[str, Any]:
    """Measured signal -> paper order latency of AI-clock lanes (cross-venue per-second lanes excluded)."""
    try:
        import combo_pathway_config as registry  # noqa: PLC0415
        lanes = set(registry.ACTIVE_TILE_ORDER) | set(getattr(registry, "RETIRED_TILE_LANES", ()))
        exclude = {lane for lane in lanes if registry.is_cross_venue_clock_lane(lane)}
    except Exception:  # noqa: BLE001
        exclude = set()
    exclude |= {"FAMILY_XVENUE_LEAD_60S", "FAMILY_XVENUE_PREMIUM_60S"}
    return fm.measure_decision_latency(mirror / "v3" / "ledgers", exclude_lanes=exclude)


def headline_vs_shadow(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """How the headline (REALISTIC_V1) differs from the optimistic shadow over the same policies."""
    out: dict[str, Any] = {}
    for world in WORLDS:
        rs = [r for r in rows if r["fill_world"] == world]
        fills = sum(r["all"]["fills"] for r in rs)
        out[world] = {
            "role": fm.HEADLINE_ROLE if world == HEADLINE_WORLD else fm.SHADOW_ROLE,
            "policies": len(rs), "fills": fills,
            "net_pnl_usd_all_policies": round(sum(r["all"]["net_pnl_usd"] for r in rs), 4),
            "positive_ev_policies": sum(1 for r in rs if (r["all"]["ev_per_fill_usd"] or 0) > 0),
            "holdout_verdicts": dict(Counter(r["holdout_verdict"] for r in rs)),
            "partial_fills": sum((r.get("fill_quality") or {}).get("partial_fills") or 0 for r in rs),
        }
        taker = [r for r in rs if r["entry"]["entry_id"] == "TAKER_AT_SIGNAL" and r["exit"]["protection_id"].startswith("REGISTRY_")]
        out[world]["registry_taker_rows"] = [
            {"policy_id": r["policy_id"], "fills": r["all"]["fills"], "win_rate_pct": r["all"]["win_rate_pct"],
             "ev_per_fill_usd": r["all"]["ev_per_fill_usd"], "net_pnl_usd": r["all"]["net_pnl_usd"]} for r in taker]
    return out


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
    try:
        rep = run(Path(args.mirror), Path(args.tier_a), Path(args.out_dir), workers=args.workers,
                  max_episodes=args.max_episodes)
    except EpisodeIntegrityError as exc:
        print(json.dumps({"status": "EPISODE_INTEGRITY_FAILED", "violations": str(exc)}))
        return 2
    print(json.dumps({"status": "OK", "generated_at": rep["generated_at"], "coverage": rep["coverage"]["episode_status"],
                      "policies": rep["grid"]["policies_evaluated"], "parity": rep["canonical_parity"]["status"],
                      "runtime_sec": rep["runtime_sec"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())