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


_ids_cache: dict[str, tuple[tuple[int, int], frozenset]] = {}
_mismatch_since: dict[str, float] = {}
MISMATCH_PERSIST_S = 120.0  # a mismatch older than this is RED in system-health (writes land in <1 s)
MAX_DIFF_IDS = 50


def disk_trade_ids(path: str) -> frozenset | None:
    """trade_id set of the CSV data rows (cached on size/mtime like disk_trade_rows); None when unreadable."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (st.st_size, st.st_mtime_ns)
    with _lock:
        hit = _ids_cache.get(path)
        if hit and hit[0] == key:
            return hit[1]
    try:
        csv.field_size_limit(1 << 30)
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            ids = frozenset(str(r.get("trade_id") or "") for r in csv.DictReader(fh)
                            if any((v or "").strip() for v in r.values() if isinstance(v, str)))
    except OSError:
        return None
    with _lock:
        _ids_cache[path] = (key, ids)
    return ids


def id_diff(memory_trades, csv_path: str, now: float) -> dict[str, Any]:
    """Which trade ids differ (memory-only = deleted on disk but still counted; disk-only = not loaded) and how long
    the mismatch has persisted. Capped at MAX_DIFF_IDS per side; counts are exact."""
    disk = disk_trade_ids(csv_path)
    mem = {str(t.get("trade_id") or "") for t in (memory_trades or []) if isinstance(t, dict)}
    if disk is None:
        only_mem, only_disk = sorted(mem), []
    else:
        only_mem, only_disk = sorted(mem - disk), sorted(disk - mem)
    mismatch = bool(disk is None or only_mem or only_disk)
    with _lock:
        if mismatch:
            since = _mismatch_since.setdefault(csv_path, now)
        else:
            _mismatch_since.pop(csv_path, None)
            since = None
    age = (now - since) if since is not None else 0.0
    return {"memory_only_ids": only_mem[:MAX_DIFF_IDS], "memory_only_count": len(only_mem),
            "disk_only_ids": only_disk[:MAX_DIFF_IDS], "disk_only_count": len(only_disk),
            "mismatch_since_ts": since, "mismatch_age_s": round(age, 1),
            "persistent": bool(mismatch and age >= MISMATCH_PERSIST_S)}
