"""Dense 1 s Bitfinex L1 tape and cross-venue mids for the strategy lab.

Port of the tape section of ``diagnostics/tile2_20261001/sim2.py``:
``market_microstructure_1s.jsonl`` (+ numeric rotations) becomes dense
per-second arrays with forward-filled quotes, a ``present`` mask and a hole
run length so paths crossing a tape hole longer than ``HOLE_CENSOR_SEC`` are
censored instead of being filled with stale quotes.

Closed rotations are immutable, so each one is parsed once and cached as a
small ``.npz`` keyed by (name, size, mtime); only the active file is re-read
every cycle.

Multi-day history: the live mirror is a rolling ~55 h window, so with
``HistorySources`` the tape is the ordered, de-duplicated union of
(1) Tier A ``bitfinex_l1_tape_1s`` partitions, (2) frozen manual mirror
archives (same row schema) and (3) the current raw mirror, bounded below by
the epoch start so statistics stay epoch-pure. On a duplicate second the
newest source wins (mirror > Tier A > archive). Archive files and Tier A
partitions are immutable and cached by (path, size, mtime).
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

BFX_TAPE_FILE = "market_microstructure_1s.jsonl"
HOLE_CENSOR_SEC = 60
ATR_BAR_SEC = 180
ATR_PERIOD = 14
ATR_MIN_PRESENT = 120

TIER_A_TAPE_DATASET = "bitfinex_l1_tape_1s"
TIER_A_CROSS_VENUE_DATASET = "cross_venue_tape_1m"
DEFAULT_ARCHIVE_GLOB = r"C:\DoxxedCrypto\archive\fly-mirror-segments-*\tree"
DEFAULT_TIER_A_ROOT = r"C:\DoxxedCrypto\bot-data-compact"
SOURCE_ORDER = ("archive", "tier_a", "mirror")          # later wins on a duplicate key


@dataclass(frozen=True)
class HistorySources:
    """Where multi-day inputs come from (empty = rolling raw mirror only)."""
    archive_dirs: tuple = ()
    tier_a_root: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.archive_dirs or self.tier_a_root)

    def describe(self) -> dict:
        return {"archive_dirs": list(self.archive_dirs), "tier_a_root": self.tier_a_root,
                "precedence": "mirror > tier_a > archive on duplicate keys",
                "bound": "rows before the current epoch start are excluded"}


def laptop_defaults_enabled() -> bool:
    return os.name == "nt" and os.path.isdir(r"C:\DoxxedCrypto") and "PYTEST_CURRENT_TEST" not in os.environ


def default_history_sources() -> HistorySources:
    """``STRATEGY_LAB_ARCHIVE_DIRS`` (``os.pathsep`` list, ``none`` disables) and
    ``STRATEGY_LAB_TIER_A_ROOT`` / ``DOXXED_BOT_DATA_COMPACT_DIR``; otherwise the
    laptop defaults (never under pytest or off the laptop)."""
    env_archive = os.environ.get("STRATEGY_LAB_ARCHIVE_DIRS")
    env_tier_a = os.environ.get("STRATEGY_LAB_TIER_A_ROOT") or os.environ.get("DOXXED_BOT_DATA_COMPACT_DIR")
    laptop = laptop_defaults_enabled()
    if env_archive is not None:
        archive = () if env_archive.strip().lower() in ("", "none", "off") else tuple(
            p for p in env_archive.split(os.pathsep) if p and os.path.isdir(p))
    elif laptop:
        archive = tuple(sorted(p for p in glob.glob(DEFAULT_ARCHIVE_GLOB) if os.path.isdir(p)))
    else:
        archive = ()
    if env_tier_a is not None:
        tier_a = None if env_tier_a.strip().lower() in ("", "none", "off") else env_tier_a
    else:
        tier_a = DEFAULT_TIER_A_ROOT if laptop and os.path.isdir(DEFAULT_TIER_A_ROOT) else None
    return HistorySources(archive_dirs=archive, tier_a_root=tier_a)


def _iso(ts) -> Optional[str]:
    if ts is None or not np.isfinite(ts):
        return None
    import time as _time
    return _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(float(ts)))


def coverage_summary(*, epoch_start: Optional[float], first_ts: Optional[float], last_ts: Optional[float],
                     present_units: int, window_units: int, unit_sec: int, now: Optional[float],
                     sources: Optional[dict] = None, unique_by_source: Optional[dict] = None) -> dict:
    """Truthful coverage: present units over the whole epoch, not only the loaded window.

    ``epoch_coverage_share`` = present seconds / seconds since epoch start
    (to ``now``, so collection lag counts as missing); ``in_window_present_share``
    = present units / units between the first and last available row.
    """
    sources = sources or {}
    end = float(now) if now is not None else (float(last_ts) if last_ts is not None else None)
    out = {
        "epoch_start": _iso(epoch_start), "epoch_start_ts": epoch_start,
        "first_available_ts": first_ts, "first_available": _iso(first_ts),
        "last_ts": last_ts, "last": _iso(last_ts),
        "horizon_hours": round((float(last_ts) - float(first_ts) + unit_sec) / 3600.0, 3)
        if first_ts is not None and last_ts is not None else 0.0,
        "unit_sec": int(unit_sec), "present_units": int(present_units),
        "in_window_present_share": round(present_units / window_units, 4) if window_units else 0.0,
        "epoch_coverage_share": None, "epoch_seconds": None,
        "sources": {"tier_a_rows": int(sources.get("tier_a", 0)), "archive_rows": int(sources.get("archive", 0)),
                    "mirror_rows": int(sources.get("mirror", 0))},
    }
    if unique_by_source is not None:
        out["unique_units_by_source"] = {k: int(unique_by_source.get(k, 0)) for k in SOURCE_ORDER}
    if epoch_start is not None and end is not None:
        span = max(float(end) - float(epoch_start), float(unit_sec))
        out["epoch_seconds"] = int(span)
        out["epoch_coverage_share"] = round(min(1.0, present_units * unit_sec / span), 4)
    return out


def winners_by_source(keys: np.ndarray, source_codes: np.ndarray) -> dict:
    """Unique keys credited to the source that wins each duplicate (last in SOURCE_ORDER)."""
    if keys.size == 0:
        return {k: 0 for k in SOURCE_ORDER}
    order = np.lexsort((source_codes, keys))
    k = keys[order]
    keep = np.append(k[1:] != k[:-1], True)
    counts = np.bincount(source_codes[order][keep], minlength=len(SOURCE_ORDER))
    return {name: int(counts[i]) for i, name in enumerate(SOURCE_ORDER)}


def generations(path: str) -> list:
    """Closed numeric rotations oldest-first, then the active file."""
    rotated = []
    for candidate in glob.glob(glob.escape(path) + ".*"):
        suffix = candidate.rsplit(".", 1)[-1]
        if suffix.isdigit():
            rotated.append((int(suffix), candidate))
    return [p for _, p in sorted(rotated)] + ([path] if os.path.isfile(path) else [])


def _tape_rows_to_arrays(rows) -> dict:
    ts, bid, ask, buy, sell = [], [], [], [], []
    for row in rows:
        if not isinstance(row, dict) or row.get("valid_bbo") is not True:
            continue
        b, a = row.get("bid"), row.get("ask")
        if not isinstance(b, (int, float)) or not isinstance(a, (int, float)) or not 0 < b <= a:
            continue
        try:
            ts.append(int(row["bucket_ts"]))
        except (KeyError, TypeError, ValueError):
            continue
        bid.append(float(b))
        ask.append(float(a))
        buy.append(float(row.get("buy_qty") or 0.0))
        sell.append(float(row.get("sell_qty") or 0.0))
    return {
        "ts": np.asarray(ts, dtype=np.int64),
        "bid": np.asarray(bid, dtype=np.float64),
        "ask": np.asarray(ask, dtype=np.float64),
        "buy": np.asarray(buy, dtype=np.float64),
        "sell": np.asarray(sell, dtype=np.float64),
    }


def _json_lines(path: str):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _parse_tape_file(path: str) -> dict:
    return _tape_rows_to_arrays(_json_lines(path))


def _parse_tier_a_tape(path: str) -> dict:
    import pandas as pd

    frame = pd.read_parquet(path, columns=["row"])

    def rows():
        for text in frame["row"]:
            if isinstance(text, str) and text.startswith("{"):
                try:
                    yield json.loads(text)
                except ValueError:
                    continue
    return _tape_rows_to_arrays(rows())


def _cache_key(path: str, full_path: bool = False) -> str:
    st = os.stat(path)
    name = os.path.abspath(path) if full_path else os.path.basename(path)
    raw = f"{name}|{st.st_size}|{st.st_mtime_ns}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def _load_file(path: str, cache_dir: Optional[str], cacheable: bool, *, full_path_key: bool = False,
               parser=_parse_tape_file) -> tuple:
    """Return (arrays, cache_hit)."""
    cache_path = None
    if cacheable and cache_dir:
        cache_path = os.path.join(cache_dir, f"tape_{_cache_key(path, full_path_key)}.npz")
        if os.path.isfile(cache_path):
            try:
                with np.load(cache_path) as z:
                    return {k: z[k] for k in ("ts", "bid", "ask", "buy", "sell")}, True
            except Exception:
                pass
    arrays = parser(path)
    if cache_path:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            tmp = cache_path + ".tmp.npz"
            np.savez(tmp, **arrays)
            os.replace(tmp, cache_path)
        except OSError:
            pass
    return arrays, False


@dataclass
class Tape:
    t0: int
    n: int
    bid: np.ndarray
    ask: np.ndarray
    present: np.ndarray
    holerun: np.ndarray
    buy: np.ndarray
    sell: np.ndarray
    files: list = field(default_factory=list)
    cache_hits: int = 0
    source_rows: dict = field(default_factory=dict)
    source_seconds: dict = field(default_factory=dict)
    history: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.mid = (self.bid + self.ask) / 2.0
        with np.errstate(invalid="ignore", divide="ignore"):
            self.spread_bp = (self.ask - self.bid) / self.mid * 1e4
        bad = self.holerun > HOLE_CENSOR_SEC
        nxt = np.full(self.n + 1, self.n + 10, dtype=np.int64)
        idx = np.flatnonzero(bad)
        # next_bad[k] = first censored second at or after k
        if idx.size:
            pos = np.searchsorted(idx, np.arange(self.n), side="left")
            valid = pos < idx.size
            nxt[: self.n][valid] = idx[pos[valid]]
        self.next_bad = nxt
        self._atr = None

    @property
    def t1(self) -> int:
        return self.t0 + self.n - 1

    def index(self, ts) -> np.ndarray:
        return np.asarray(np.ceil(np.asarray(ts, dtype=float)), dtype=np.int64) - self.t0

    def coverage(self, epoch_start: Optional[float] = None, now: Optional[float] = None) -> dict:
        out = {
            "start_ts": int(self.t0),
            "end_ts": int(self.t1),
            "seconds": int(self.n),
            "present_share": round(float(self.present.mean()), 4) if self.n else 0.0,
            "longest_hole_sec": int(self.holerun.max()) if self.n else 0,
            "censored_seconds": int((self.holerun > HOLE_CENSOR_SEC).sum()),
            "files": len(self.files),
            "rotation_cache_hits": int(self.cache_hits),
        }
        rows = self.source_rows or {"mirror": int(self.present.sum())}
        out.update(coverage_summary(
            epoch_start=epoch_start, first_ts=float(self.t0), last_ts=float(self.t1),
            present_units=int(self.present.sum()), window_units=int(self.n), unit_sec=1, now=now,
            sources=rows, unique_by_source=self.source_seconds or None))
        if self.history:
            out["history"] = self.history
        return out

    def atr_abs(self, ts) -> np.ndarray:
        """3 m Wilder ATR14 on mid, usable only after its bar closes (no look-ahead)."""
        if self._atr is None:
            tsec = self.t0 + np.arange(self.n)
            bar = (tsec // ATR_BAR_SEC) * ATR_BAR_SEC
            ub, start = np.unique(bar, return_index=True)
            end = np.append(start[1:], self.n)
            hi = np.maximum.reduceat(self.mid, start)
            lo = np.minimum.reduceat(self.mid, start)
            last = self.mid[end - 1]
            cnt = np.add.reduceat(self.present.astype(np.int64), start)
            keep = cnt >= ATR_MIN_PRESENT
            ub, hi, lo, last = ub[keep], hi[keep], lo[keep], last[keep]
            prev = np.concatenate([[np.nan], last[:-1]])
            tr = np.nanmax(np.vstack([hi - lo, np.abs(hi - prev), np.abs(lo - prev)]), axis=0)
            atr = np.full(tr.shape, np.nan)
            alpha = 1.0 / ATR_PERIOD
            acc = np.nan
            for i, v in enumerate(tr):
                acc = v if not np.isfinite(acc) else acc + alpha * (v - acc)
                if i >= ATR_PERIOD - 1:
                    atr[i] = acc
            self._atr = (ub + ATR_BAR_SEC, atr)
        close, atr = self._atr
        k = np.searchsorted(close, np.asarray(ts, dtype=float), side="right") - 1
        out = np.where(k >= 0, atr[np.maximum(k, 0)], np.nan)
        return out


def build_tape(ts: np.ndarray, bid: np.ndarray, ask: np.ndarray, buy=None, sell=None,
               files=None, cache_hits: int = 0, **extra) -> Optional[Tape]:
    ts = np.asarray(ts, dtype=np.int64)
    if ts.size == 0:
        return None
    order = np.argsort(ts, kind="stable")
    ts = ts[order]
    # duplicate buckets: last write wins
    keep = np.append(ts[1:] != ts[:-1], True)
    ts = ts[keep]
    t0, t1 = int(ts[0]), int(ts[-1])
    n = t1 - t0 + 1
    idx = ts - t0

    def dense(values, fill=np.nan):
        out = np.full(n, fill, dtype=np.float64)
        if values is not None:
            out[idx] = np.asarray(values, dtype=np.float64)[order][keep]
        return out

    b, a = dense(bid), dense(ask)
    present = np.isfinite(b)
    # forward fill quotes
    pos = np.where(present, np.arange(n), 0)
    np.maximum.accumulate(pos, out=pos)
    b, a = b[pos], a[pos]
    run = np.zeros(n, dtype=np.int64)
    if n:
        gap = ~present
        # length of the current run of missing seconds
        csum = np.cumsum(gap)
        reset = np.where(present, csum, 0)
        np.maximum.accumulate(reset, out=reset)
        run = csum - reset
    return Tape(t0=t0, n=n, bid=b, ask=a, present=present, holerun=run,
                buy=np.nan_to_num(dense(buy, 0.0)), sell=np.nan_to_num(dense(sell, 0.0)),
                files=list(files or []), cache_hits=cache_hits, **extra)


def _bounded(arrays: dict, start_ts, end_ts) -> Optional[dict]:
    if arrays["ts"].size == 0:
        return None
    m = np.ones(arrays["ts"].size, dtype=bool)
    if start_ts is not None:
        m &= arrays["ts"] >= int(start_ts)
    if end_ts is not None:
        m &= arrays["ts"] <= int(end_ts)
    return {k: v[m] for k, v in arrays.items()} if m.any() else None


def _history_tape_parts(history: HistorySources, start_ts, end_ts, cache_dir) -> tuple:
    """[(source, arrays)], used file labels, cache hits, refused partitions."""
    parts, used, hits, refused = [], [], 0, []
    for folder in history.archive_dirs:
        for path in generations(os.path.join(folder, BFX_TAPE_FILE)):
            try:
                arrays, hit = _load_file(path, cache_dir, True, full_path_key=True)
            except OSError:
                continue
            hits += int(hit)
            arrays = _bounded(arrays, start_ts, end_ts)
            if arrays is not None:
                parts.append(("archive", arrays))
                used.append(f"archive:{os.path.basename(path)}")
    if history.tier_a_root:
        from strategy_lab.client import tier_a_partition_intact, tier_a_partitions

        try:
            partitions = tier_a_partitions(TIER_A_TAPE_DATASET, since=start_ts, until=end_ts,
                                           root=history.tier_a_root)
        except (KeyError, OSError):
            partitions = []
        for part in partitions:
            if not tier_a_partition_intact(part):
                refused.append(part["path"])
                continue
            try:
                arrays, hit = _load_file(part["path"], cache_dir, True, full_path_key=True,
                                         parser=_parse_tier_a_tape)
            except Exception:
                refused.append(part["path"])
                continue
            hits += int(hit)
            arrays = _bounded(arrays, start_ts, end_ts)
            if arrays is not None:
                parts.append(("tier_a", arrays))
                used.append(f"tier_a:{part['day']}")
    return parts, used, hits, refused


def load_bitfinex_tape(data_dir: str, start_ts: Optional[float] = None, end_ts: Optional[float] = None,
                       cache_dir: Optional[str] = None, history: Optional[HistorySources] = None) -> Optional[Tape]:
    paths = generations(os.path.join(data_dir, BFX_TAPE_FILE))
    parts, hits, used, refused = [], 0, [], []
    if history is not None and history.enabled:
        parts, used, hits, refused = _history_tape_parts(history, start_ts, end_ts, cache_dir)
    for path in paths:
        cacheable = path.rsplit(".", 1)[-1].isdigit()
        try:
            arrays, hit = _load_file(path, cache_dir, cacheable)
        except OSError:
            continue
        hits += int(hit)
        arrays = _bounded(arrays, start_ts, end_ts)
        if arrays is not None:
            parts.append(("mirror", arrays))
            used.append(os.path.basename(path))
    if not parts:
        return None
    # archive, then Tier A, then mirror: build_tape keeps the last row per second
    parts.sort(key=lambda p: SOURCE_ORDER.index(p[0]))
    cat = {k: np.concatenate([p[1][k] for p in parts]) for k in parts[0][1]}
    codes = np.concatenate([np.full(p[1]["ts"].size, SOURCE_ORDER.index(p[0]), dtype=np.int64) for p in parts])
    rows = {name: int(sum(p[1]["ts"].size for p in parts if p[0] == name)) for name in SOURCE_ORDER}
    hist = history.describe() if history is not None and history.enabled else {}
    if refused:
        hist["refused_tier_a_partitions"] = refused
    return build_tape(cat["ts"], cat["bid"], cat["ask"], cat["buy"], cat["sell"], files=used, cache_hits=hits,
                      source_rows=rows, source_seconds=winners_by_source(cat["ts"], codes), history=hist)


def _decode_cross_venue_text(text) -> Optional[dict]:
    if not isinstance(text, str) or not text.startswith("{"):
        return None
    try:
        row = json.loads(text)
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


def load_cross_venue_union(data_dir: str, *, start_ts: Optional[float] = None, max_days: int = 14,
                           history: Optional[HistorySources] = None) -> tuple:
    """Cross-venue 1 m rows from archive + Tier A + mirror, de-duplicated by ``minute_ts`` (mirror wins).

    Returns ``(rows, source_rows, unique_by_source)``; the ``max_days`` window is
    anchored on the newest minute exactly like the mirror-only loader.
    """
    import cross_venue_tape as cvt
    from research.lead_lag_report import load_cross_venue_rows

    tagged = []
    if history is not None and history.enabled:
        for folder in history.archive_dirs:
            tagged.extend(("archive", r) for r in load_cross_venue_rows(folder, max_days=10 ** 4))
        if history.tier_a_root:
            from strategy_lab.client import load_tier_a

            try:
                frame = load_tier_a(TIER_A_CROSS_VENUE_DATASET, since=start_ts, root=history.tier_a_root,
                                    dedupe="ts")
            except Exception:
                frame = None
            if frame is not None and len(frame):
                for text in frame["row"]:
                    row = _decode_cross_venue_text(text)
                    if row is not None and row.get("schema") == cvt.SCHEMA:
                        tagged.append(("tier_a", row))
    tagged.extend(("mirror", r) for r in load_cross_venue_rows(data_dir, max_days=max_days))
    if not tagged:
        return [], {k: 0 for k in SOURCE_ORDER}, {k: 0 for k in SOURCE_ORDER}
    latest = max(int(r.get("minute_ts") or 0) for _, r in tagged)
    floor = latest - max_days * 86400
    if start_ts is not None:
        floor = max(floor, int(start_ts) - 59)
    kept = [(s, r) for s, r in tagged if int(r.get("minute_ts") or 0) >= floor]
    kept.sort(key=lambda sr: SOURCE_ORDER.index(sr[0]))
    rows_by = {k: sum(1 for s, _ in kept if s == k) for k in SOURCE_ORDER}
    dedup, owner = {}, {}
    for source, row in kept:
        ts = int(row.get("minute_ts") or 0)
        dedup[ts] = row
        owner[ts] = source
    unique = {k: sum(1 for v in owner.values() if v == k) for k in SOURCE_ORDER}
    return [dedup[k] for k in sorted(dedup)], rows_by, unique


def cross_venue_mids(data_dir: str, tape: Tape, max_days: int = 14,
                     history: Optional[HistorySources] = None, epoch_start: Optional[float] = None,
                     now: Optional[float] = None) -> dict:
    """Per-second leader-venue mids aligned to ``tape`` (NaN where unobserved).

    Built on the analyzer's existing cross-venue harness
    (``research.lead_lag_report`` loaders + ``cross_venue_tape.decode_minute``)
    so there is one decoder for the shadow tape. With ``history`` the minutes
    are the union of archive, Tier A and mirror rows (see ``load_cross_venue_union``).
    """
    import cross_venue_tape as cvt

    rows, rows_by, unique = load_cross_venue_union(data_dir, start_ts=epoch_start, max_days=max_days,
                                                   history=history)
    out = {}
    for row in rows:
        decoded = cvt.decode_minute(row)
        for venue, cells in decoded.items():
            if venue == "bfx":
                continue
            arr = out.get(venue)
            if arr is None:
                arr = out[venue] = np.full(tape.n, np.nan)
            for sec, cell in (cells or {}).items():
                i = int(sec) - tape.t0
                mid = cell.get("mid") if isinstance(cell, dict) else None
                if 0 <= i < tape.n and mid is not None:
                    arr[i] = float(mid)
    span = None
    first = last = None
    if rows:
        first, last = int(rows[0]["minute_ts"]), int(rows[-1]["minute_ts"])
        span = {"start_ts": first, "end_ts": last + 59, "minutes": len(rows)}
    coverage = coverage_summary(
        epoch_start=epoch_start, first_ts=float(first) if first is not None else None,
        last_ts=float(last) if last is not None else None, present_units=len(rows),
        window_units=(last - first) // 60 + 1 if rows else 0, unit_sec=60, now=now,
        sources=rows_by, unique_by_source=unique)
    return {"venues": out, "span": span, "coverage": coverage}
