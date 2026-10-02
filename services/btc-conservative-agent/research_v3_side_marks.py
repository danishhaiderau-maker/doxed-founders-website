"""Side-correct 1 s exit marks for the Safe Policy Genome protection replay.

A LONG position can only be closed into the bid and a SHORT into the ask, so
the replay path is the executable side of the 1 s Bitfinex BBO tape, not the
last trade.  Entry prices stay those of each episode's fill model.  The
quote acceptance rule (bid and ask present, ask >= bid; last row per second
wins) is the one ``research/genome_grid_study.load_tape`` uses so both
evaluators replay identical paths.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

TAPE_FILE = "market_microstructure_1s.jsonl"
TAPE_SYMBOL = "tBTCF0:USTF0"
PATH_HORIZON_SEC = 7200

BASIS_SEGMENT = "EVENT_SEGMENT_1S_SIDE_BID_ASK"
BASIS_TAPE = "TAPE_1S_SIDE_BID_ASK"
BASIS_SEGMENT_AND_TAPE = "EVENT_SEGMENT_AND_TAPE_1S_SIDE_BID_ASK"
BASIS_SEGMENT_LAST_PRICE = "EVENT_SEGMENT_1S_LAST_PRICE"
BASIS_1M_ADVERSE_FIRST = "CANONICAL_1M_ADVERSE_FIRST_OHLC"
BASIS_NONE = "NO_ORDERED_PRICE_PATH"
SIDE_CORRECT_BASES = frozenset({BASIS_SEGMENT, BASIS_TAPE, BASIS_SEGMENT_AND_TAPE})
FALLBACK_BASES = frozenset({BASIS_SEGMENT_LAST_PRICE, BASIS_1M_ADVERSE_FIRST, BASIS_NONE})
SIDE_CONVENTION = (
    "Exits are marked on the executable side of the 1 s BBO: LONG on bid, SHORT on ask; "
    "entries use each episode's fill-model price."
)

_BUCKET_RE = re.compile(r'"bucket_ts"\s*:\s*(-?\d+(?:\.\d+)?)')


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def side_quote(row: Mapping[str, Any]) -> tuple[int, float, float] | None:
    """(bucket second, bid, ask) for an acceptable BBO row, else None."""
    ts = _number(row.get("bucket_ts"))
    bid, ask = _number(row.get("bid")), _number(row.get("ask"))
    if ts is None or not bid or not ask or ask < bid:
        return None
    return int(ts), bid, ask


def tape_paths(data_dir: str | Path) -> list[Path]:
    base = Path(data_dir) / TAPE_FILE
    rotations = sorted(
        (path for path in base.parent.glob(base.name + ".*") if path.name[len(base.name) + 1:].isdigit()),
        key=lambda path: int(path.name.rsplit(".", 1)[-1]),
    )
    return rotations + ([base] if base.is_file() else [])


class SideMarkTape:
    """Dense per-second bid/ask arrays (NaN where a second has no quote)."""

    def __init__(self, t0: int, bid: np.ndarray, ask: np.ndarray, receipt: dict[str, Any]) -> None:
        self.t0, self.bid, self.ask, self.receipt = t0, bid, ask, receipt

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping[str, Any]], *, start_ts: float, end_ts: float,
                  symbol: str = TAPE_SYMBOL, receipt: dict[str, Any] | None = None) -> "SideMarkTape":
        t0 = int(math.floor(start_ts))
        size = max(0, int(math.ceil(end_ts)) - t0 + 1)
        bid = np.full(size, np.nan)
        ask = np.full(size, np.nan)
        accepted = 0
        for row in rows:
            if row.get("symbol") not in (None, symbol):
                continue
            quote = side_quote(row)
            if quote is None or not 0 <= quote[0] - t0 < size:
                continue
            bid[quote[0] - t0], ask[quote[0] - t0] = quote[1], quote[2]
            accepted += 1
        details = dict(receipt or {})
        details.update({"start_ts": t0, "end_ts": t0 + size, "rows_accepted": accepted,
                        "seconds_with_quote": int(np.count_nonzero(~np.isnan(bid)))})
        return cls(t0, bid, ask, details)

    def side(self, direction: str, start: int, end: int) -> np.ndarray:
        values = self.bid if direction == "LONG" else self.ask
        a, b = max(0, start - self.t0), max(0, min(len(values), end - self.t0))
        out = np.full(max(0, end - start), np.nan)
        if b > a:
            out[a + self.t0 - start: b + self.t0 - start] = values[a:b]
        return out


def load_side_mark_tape(data_dir: str | Path, *, start_ts: float, end_ts: float,
                        symbol: str = TAPE_SYMBOL) -> SideMarkTape:
    """Read the 1 s tape (oldest rotation first) restricted to [start_ts, end_ts]."""
    paths = tape_paths(data_dir)
    lo, hi = math.floor(start_ts), math.ceil(end_ts)
    stats = {"rows_read": 0, "rows_in_window": 0, "decode_errors": 0}

    def rows() -> Iterable[dict[str, Any]]:
        for path in paths:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    stats["rows_read"] += 1
                    match = _BUCKET_RE.search(line)
                    if match is None or not lo <= float(match.group(1)) <= hi:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        stats["decode_errors"] += 1
                        continue
                    if isinstance(row, dict):
                        stats["rows_in_window"] += 1
                        yield row

    tape = SideMarkTape.from_rows(rows(), start_ts=start_ts, end_ts=end_ts, symbol=symbol)
    tape.receipt.update(stats)
    tape.receipt.update({"schema": "replay_side_mark_tape_v1", "source_files": [p.name for p in paths]})
    return tape


def event_side_marks(*, direction: str, segment_rows: Iterable[Mapping[str, Any]],
                     tape: SideMarkTape | None, start_ts: float, end_ts: float) -> dict[str, Any]:
    """Executable-side marks for one event; the event's own segment rows win per second."""
    if direction not in ("LONG", "SHORT"):
        return {"basis": BASIS_NONE, "ts": np.empty(0), "price": np.empty(0),
                "segment_marks": 0, "tape_marks": 0}
    start, end = int(math.floor(start_ts)), int(math.ceil(end_ts)) + 1
    segment: dict[int, float] = {}
    for row in segment_rows:
        quote = side_quote(row)
        if quote is not None and start <= quote[0] < end:
            segment[quote[0]] = quote[1] if direction == "LONG" else quote[2]
    covered = [(min(segment), max(segment) + 1)] if segment else []
    if tape is not None and len(tape.bid):
        covered.append((tape.t0, tape.t0 + len(tape.bid)))
    lo = max(start, min((a for a, _ in covered), default=start))
    hi = min(end, max((b for _, b in covered), default=start))
    hi = max(lo, hi)
    start, end = lo, hi
    marks = tape.side(direction, start, end) if tape is not None else np.full(end - start, np.nan)
    from_tape = ~np.isnan(marks)
    from_segment = np.zeros(end - start, dtype=bool)
    for ts, price in segment.items():
        marks[ts - start] = price
        from_segment[ts - start] = True
    present = from_tape | from_segment
    segment_count = int(np.count_nonzero(from_segment))
    tape_count = int(np.count_nonzero(from_tape & ~from_segment))
    if segment_count and tape_count:
        basis = BASIS_SEGMENT_AND_TAPE
    elif segment_count:
        basis = BASIS_SEGMENT
    elif tape_count:
        basis = BASIS_TAPE
    else:
        basis = BASIS_NONE
    index = np.nonzero(present)[0]
    return {"basis": basis, "ts": (index + start).astype(np.float64), "price": marks[index],
            "segment_marks": segment_count, "tape_marks": tape_count}


def mark_source_summary(events_by_basis: Mapping[str, int], episodes_by_basis: Mapping[str, int],
                        tape_receipt: Mapping[str, Any] | None) -> dict[str, Any]:
    """Health view: any replayed event not on side-correct 1 s marks is an alert."""
    total = sum(int(v) for v in events_by_basis.values())
    fallback = sum(int(v) for k, v in events_by_basis.items() if k in FALLBACK_BASES)
    if not total:
        level, reason = "AMBER", "NO_REPLAYED_EVENTS"
    elif fallback * 2 > total:
        level, reason = "RED", "REPLAY_MARK_FALLBACK_MAJORITY"
    elif fallback:
        level, reason = "AMBER", "REPLAY_MARK_FALLBACK"
    else:
        level, reason = "GREEN", None
    return {
        "schema": "replay_mark_source_summary_v1",
        "side_convention": SIDE_CONVENTION,
        "path_horizon_sec": PATH_HORIZON_SEC,
        "events_by_basis": dict(sorted(events_by_basis.items())),
        "episodes_by_basis": dict(sorted(episodes_by_basis.items())),
        "events_total": total,
        "fallback_events": fallback,
        "side_correct_ratio": round((total - fallback) / total, 6) if total else None,
        "alert_level": level,
        "reason": reason,
        "tape": dict(tape_receipt or {}),
    }
