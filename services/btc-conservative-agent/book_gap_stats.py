"""Read-only book-gap statistics over the collector's bfx_book_10l_1s.jsonl tail (monitoring, no fill logic).

missing_seconds = whole seconds in the window with no row; carried rows (collector carried an unchanged book on a live
book channel, #723) are counted separately and are NOT missing. HM targets: missing share < 0.2 %, longest gap <= 20 s
only when the socket is dead.
"""
from __future__ import annotations

import json
import os
import time

TAIL_BYTES = 8 * 1024 * 1024


def _gap_stats(secs: set, carried: set, start: int, end: int) -> dict:
    total = max(0, end - start + 1)
    missing = [s for s in range(start, end + 1) if s not in secs]
    gaps, run = [], 0
    for s in range(start, end + 2):
        if s <= end and s not in secs:
            run += 1
        elif run:
            gaps.append(run)
            run = 0
    return {"window_seconds": total, "rows": len(secs), "missing_seconds": len(missing),
            "missing_share": round(len(missing) / total, 5) if total else None,
            "carried_rows": len(carried), "carried_share": round(len(carried) / len(secs), 5) if secs else None,
            "gaps": len(gaps), "gaps_ge_3s": sum(1 for g in gaps if g >= 3), "longest_gap_s": max(gaps, default=0)}


def book_gap_stats(path: str, hours: float = 1.0, now: float | None = None, since: float | None = None) -> dict:
    now = time.time() if now is None else now
    start_ts = max(now - hours * 3600, since or 0.0)
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            fh.seek(max(0, size - TAIL_BYTES))
            lines = fh.read().splitlines()
        lines = lines[1:] if size > TAIL_BYTES else lines
    except OSError as exc:
        return {"status": "UNAVAILABLE", "error": type(exc).__name__}
    secs, carried = set(), set()
    for line in lines:
        try:
            r = json.loads(line)
            s = int(r["s"])
        except (ValueError, KeyError, TypeError):
            continue
        if s >= start_ts:
            secs.add(s)
            if r.get("carried"):
                carried.add(s)
    if not secs:
        return {"status": "NO_ROWS", "window_hours": hours}
    first, last = int(max(start_ts, min(secs))), max(secs)
    return {"status": "OK", "window_hours": hours, "since_ts": since, "tail_covers_window": first <= start_ts + 5 or size <= TAIL_BYTES,
            **_gap_stats(secs, carried, first, last)}
