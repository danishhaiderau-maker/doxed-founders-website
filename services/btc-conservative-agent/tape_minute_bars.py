"""Causal one-minute bars rebuilt from the durable 1s microstructure tape.

The collector's order-multiverse path needs signal-1h .. signal+3h of 1m
coverage.  The in-memory exchange candle cache holds only 200 bars and is
filled as a side effect of AI-context builds, so it cannot be the maturation
source.  This store incrementally reads ``market_microstructure_1s.jsonl``
and its numbered rotations (immutable once renamed) and aggregates fresh,
valid buckets into minute OHLCV rows shaped like Bitfinex REST candles:
``[minute_start_ms, open, high, low, close, volume]``.

Price points per second are the last trade price plus the buy/sell VWAPs of
trades inside that second; bid/ask are never used as prices, so a bar never
claims a touch that no trade printed.  A minute is emitted only when it is
closed on the tape and has at least ``min_fresh_seconds`` fresh buckets;
otherwise it is a proven hole.  Aggregation is idempotent per second, so a
file read twice (live tail, then the same bytes after rotation) never double
counts.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from typing import Any, Callable, Mapping, Optional

from microstructure_tape import FILE_NAME as TAPE_FILE_NAME, SCHEMA as TAPE_SCHEMA

SCHEMA = "tape_minute_bars_v1"
SOURCE_LABEL = "MARKET_MICROSTRUCTURE_1S"
MIN_FRESH_SECONDS = 20
DEFAULT_RETENTION_MINUTES = 14 * 1440
_ROTATION_RE = re.compile(r"^(?P<base>.+)\.(?P<index>\d+)$")

WINDOW_AVAILABLE = "AVAILABLE"
WINDOW_SOURCE_NOT_READY = "SOURCE_NOT_READY"
WINDOW_SOURCE_BEHIND = "SOURCE_BEHIND"
WINDOW_BEFORE_TAPE = "BEFORE_TAPE"
RETRYABLE_WINDOW_STATES = frozenset({WINDOW_SOURCE_NOT_READY, WINDOW_SOURCE_BEHIND})

# bar layout: [first_sec, open, high, low, last_sec, close, volume, seen_mask, fresh_mask]
_FIRST, _OPEN, _HIGH, _LOW, _LAST, _CLOSE, _VOL, _SEEN, _FRESH = range(9)


def _finite_positive(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def row_prices(row: Mapping[str, Any]) -> list[float]:
    """Trade-printed prices for one fresh, valid bucket; empty when unusable."""
    if row.get("fresh") is not True or row.get("valid_bbo") is not True:
        return []
    prices = []
    last = _finite_positive(row.get("last"))
    if last is not None:
        prices.append(last)
    for key in ("buy_vwap", "sell_vwap"):
        vwap = _finite_positive(row.get(key))
        if vwap is not None:
            prices.append(vwap)
    return prices


class TapeMinuteBarStore:
    """Thread-safe incremental minute-bar view over the 1s tape family."""

    def __init__(
        self,
        data_dir: str,
        *,
        file_name: str = TAPE_FILE_NAME,
        min_fresh_seconds: int = MIN_FRESH_SECONDS,
        retention_minutes: int = DEFAULT_RETENTION_MINUTES,
    ) -> None:
        self.data_dir = str(data_dir)
        self.file_name = file_name
        self.min_fresh_seconds = max(1, int(min_fresh_seconds))
        self.retention_minutes = max(60, int(retention_minutes))
        self._lock = threading.Lock()
        self._bars: dict[int, list] = {}
        self._done_rotations: set[tuple] = set()
        self._live_key: Optional[tuple] = None
        self._live_offset = 0
        self._partial: Optional[tuple] = None
        self.earliest_ts: Optional[int] = None
        self.latest_ts: Optional[int] = None
        self.initial_scan_complete = False
        self.rows_ingested = 0
        self.rows_duplicate = 0
        self.parse_errors = 0
        self.last_refresh_ts: Optional[float] = None
        self.last_error: Optional[str] = None
        self.files_seen = 0

    # ------------------------------------------------------------------ ingest
    def ingest_row(self, row: Mapping[str, Any]) -> bool:
        """Fold one tape bucket into its minute; idempotent per second."""
        if not isinstance(row, Mapping) or row.get("schema") != TAPE_SCHEMA:
            return False
        try:
            bucket = int(row.get("bucket_ts"))
        except (TypeError, ValueError):
            return False
        minute = bucket - bucket % 60
        bit = 1 << (bucket - minute)
        prices = row_prices(row)
        with self._lock:
            bar = self._bars.get(minute)
            if bar is None:
                bar = [None, None, None, None, None, None, 0.0, 0, 0]
                self._bars[minute] = bar
            if bar[_SEEN] & bit:
                self.rows_duplicate += 1
                return False
            bar[_SEEN] |= bit
            self.rows_ingested += 1
            if self.earliest_ts is None or bucket < self.earliest_ts:
                self.earliest_ts = bucket
            if self.latest_ts is None or bucket > self.latest_ts:
                self.latest_ts = bucket
            if not prices:
                return True
            bar[_FRESH] |= bit
            first, last = prices[0], prices[0]
            if bar[_FIRST] is None or bucket < bar[_FIRST]:
                bar[_FIRST], bar[_OPEN] = bucket, first
            if bar[_LAST] is None or bucket > bar[_LAST]:
                bar[_LAST], bar[_CLOSE] = bucket, last
            high, low = max(prices), min(prices)
            bar[_HIGH] = high if bar[_HIGH] is None else max(bar[_HIGH], high)
            bar[_LOW] = low if bar[_LOW] is None else min(bar[_LOW], low)
            for key in ("buy_qty", "sell_qty"):
                qty = _finite_positive(row.get(key))
                if qty is not None:
                    bar[_VOL] += qty
            return True

    def _ingest_line(self, raw: bytes) -> None:
        line = raw.strip()
        if not line:
            return
        try:
            row = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            self.parse_errors += 1
            return
        self.ingest_row(row)

    def _evict_locked(self) -> None:
        if self.latest_ts is None:
            return
        floor = self.latest_ts - self.retention_minutes * 60
        stale = [minute for minute in self._bars if minute < floor]
        for minute in stale:
            del self._bars[minute]
        if stale:
            self.earliest_ts = min(self._bars) if self._bars else None

    # ----------------------------------------------------------------- files
    def _family(self) -> tuple[Optional[str], list[str]]:
        try:
            names = os.listdir(self.data_dir)
        except OSError:
            return None, []
        live = os.path.join(self.data_dir, self.file_name)
        rotations = []
        for name in names:
            match = _ROTATION_RE.match(name)
            if match and match.group("base") == self.file_name:
                rotations.append((int(match.group("index")), os.path.join(self.data_dir, name)))
        return (live if os.path.isfile(live) else None), [path for _, path in sorted(rotations)]

    @staticmethod
    def _file_key(stat: os.stat_result) -> tuple:
        if getattr(stat, "st_ino", 0):
            return ("ino", stat.st_dev, stat.st_ino)
        return ("sig", stat.st_size, stat.st_mtime_ns)

    def _read_lines(self, path: str, offset: int, budget: int,
                    pause: Callable[[], None]) -> tuple[int, int, bool]:
        """Consume complete lines from ``offset``; returns (new_offset, lines, eof)."""
        lines = 0
        with open(path, "rb") as handle:
            handle.seek(offset)
            while lines < budget:
                raw = handle.readline()
                if not raw:
                    return offset, lines, True
                if not raw.endswith(b"\n"):
                    # A writer is mid-append; retry this partial line later.
                    return offset, lines, True
                offset += len(raw)
                self._ingest_line(raw)
                lines += 1
                if lines % 2000 == 0:
                    pause()
            return offset, lines, False

    def refresh(self, *, max_lines: int = 20000,
                pause: Optional[Callable[[], None]] = None) -> dict:
        """Advance over unread tape bytes within a bounded line budget."""
        pause = pause or (lambda: time.sleep(0.05))
        budget = max(1, int(max_lines))
        consumed = 0
        try:
            live, rotations = self._family()
            self.files_seen = len(rotations) + (1 if live else 0)
            # Finish the previously tailed live file if it has been rotated.
            live_stat = os.stat(live) if live else None
            live_key = self._file_key(live_stat) if live_stat else None
            if self._live_key is not None and live_key != self._live_key:
                for path in rotations:
                    stat = os.stat(path)
                    if self._file_key(stat) == self._live_key:
                        self._partial = (path, self._file_key(stat), self._live_offset)
                        break
                self._live_key, self._live_offset = None, 0
            if self._partial is not None:
                path, key, offset = self._partial
                offset, lines, eof = self._read_lines(path, offset, budget - consumed, pause)
                consumed += lines
                self._partial = None if eof else (path, key, offset)
                if eof:
                    self._done_rotations.add(key)
            for path in rotations:
                if consumed >= budget:
                    break
                stat = os.stat(path)
                key = self._file_key(stat)
                if key in self._done_rotations or (self._partial and self._partial[1] == key):
                    continue
                offset, lines, eof = self._read_lines(path, 0, budget - consumed, pause)
                consumed += lines
                if eof:
                    self._done_rotations.add(key)
                else:
                    self._partial = (path, key, offset)
                    break
            live_eof = live is None
            if live and consumed < budget and self._partial is None:
                if self._live_key != live_key or (live_stat and live_stat.st_size < self._live_offset):
                    self._live_key, self._live_offset = live_key, 0
                offset, lines, live_eof = self._read_lines(
                    live, self._live_offset, budget - consumed, pause,
                )
                consumed += lines
                self._live_offset = offset
            if self._partial is None and live_eof and consumed < budget:
                self.initial_scan_complete = True
            with self._lock:
                self._evict_locked()
            self.last_error = None
        except OSError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
        self.last_refresh_ts = time.time()
        return {"lines": consumed, "initial_scan_complete": self.initial_scan_complete,
                "error": self.last_error}

    # ------------------------------------------------------------------ read
    def candles(self, start_ts: float, end_ts: float) -> list:
        """Closed minutes overlapping [start_ts, end_ts] with enough fresh seconds."""
        with self._lock:
            if self.latest_ts is None:
                return []
            closed_before = self.latest_ts - 59
            first = int(float(start_ts)) - int(float(start_ts)) % 60
            last = int(float(end_ts))
            out = []
            for minute in sorted(m for m in self._bars if first <= m <= last and m <= closed_before):
                bar = self._bars[minute]
                if bar[_OPEN] is None or bin(bar[_FRESH]).count("1") < self.min_fresh_seconds:
                    continue
                out.append([minute * 1000, bar[_OPEN], bar[_HIGH], bar[_LOW], bar[_CLOSE],
                            round(bar[_VOL], 8)])
            return out

    def window_state(self, start_ts: float, end_ts: float) -> str:
        """Whether the tape can decide the window: retry, before-tape, or available."""
        if not self.initial_scan_complete:
            return WINDOW_SOURCE_NOT_READY
        with self._lock:
            earliest, latest = self.earliest_ts, self.latest_ts
        if earliest is None or latest is None:
            return WINDOW_SOURCE_NOT_READY
        if latest + 1 < float(end_ts):
            return WINDOW_SOURCE_BEHIND
        if earliest > float(start_ts) + 60:
            return WINDOW_BEFORE_TAPE
        return WINDOW_AVAILABLE

    def status(self, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else float(now)
        with self._lock:
            minutes = len(self._bars)
            earliest, latest = self.earliest_ts, self.latest_ts
        return {
            "schema": SCHEMA,
            "source": SOURCE_LABEL,
            "initial_scan_complete": self.initial_scan_complete,
            "minutes_indexed": minutes,
            "earliest_bucket_ts": earliest,
            "latest_bucket_ts": latest,
            "latest_bucket_age_sec": None if latest is None else round(max(0.0, now - latest), 1),
            "rows_ingested": self.rows_ingested,
            "rows_duplicate": self.rows_duplicate,
            "parse_errors": self.parse_errors,
            "files_seen": self.files_seen,
            "min_fresh_seconds_per_bar": self.min_fresh_seconds,
            "last_refresh_ts": self.last_refresh_ts,
            "last_error": self.last_error,
        }


def merge_path_candles(tape: list, cache: list) -> tuple[list, dict]:
    """Tape bars first; exchange-cache bars fill minutes the tape does not cover."""
    by_minute: dict[int, list] = {}
    for row in tape or []:
        by_minute[int(float(row[0]) // 60000)] = list(row)
    filled = 0
    for row in cache or []:
        try:
            raw = float(row[0])
        except (TypeError, ValueError, IndexError):
            continue
        minute = int((raw / 1000.0 if raw > 1e12 else raw) // 60)
        if minute not in by_minute:
            by_minute[minute] = [minute * 60000, *list(row[1:6])]
            filled += 1
    merged = [by_minute[m] for m in sorted(by_minute)]
    return merged, {"tape_bars": len(tape or []), "cache_bars_filled": filled}
