"""/api/state analytics overlay (HM 25 Oct Q3): the bounded refresh re-reads live analytics every call, and
``total_trades`` is the trade-file row count on disk (trades_3factor.csv, append-only, one writer) so the
dashboard can never show the boot-time snapshot. The in-memory count is kept beside it; a mismatch is flagged,
never hidden. Pure apart from a cached stat+read of the CSV (re-read only when size/mtime change)."""
from __future__ import annotations

import copy
import csv
import os
import threading
from typing import Any

TRADE_COUNT_FIELDS = ("total_trades", "total_trades_memory", "total_trades_disk", "total_trades_source",
                      "total_trades_mismatch")
_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int], int]] = {}


def disk_trade_rows(path: str) -> int | None:
    """Data rows (header excluded) in the CSV; None when unreadable."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (st.st_size, st.st_mtime_ns)
    with _lock:
        hit = _cache.get(path)
        if hit and hit[0] == key:
            return hit[1]
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            n = sum(1 for row in reader if any(c.strip() for c in row)) if header is not None else 0
    except OSError:
        return None
    with _lock:
        _cache[path] = (key, n)
    return n


def overlay(live_analytics: Any, analytics_ts: Any, csv_path: str) -> tuple[dict[str, Any], Any]:
    a = copy.deepcopy(live_analytics) if isinstance(live_analytics, dict) else {}
    mem = a.get("total_trades")
    disk = disk_trade_rows(csv_path)
    a["total_trades_memory"] = mem
    a["total_trades_disk"] = disk
    a["total_trades_source"] = "DISK_CSV" if disk is not None else "UNAVAILABLE"
    a["total_trades"] = disk  # None (not a stale number) when the file can't be read
    a["total_trades_mismatch"] = bool(disk is None or mem != disk)
    return a, analytics_ts
