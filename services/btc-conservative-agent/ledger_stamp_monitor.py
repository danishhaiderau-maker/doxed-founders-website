"""HM monitor (R16) ``v3_ledger_unstamped_since``: V3 ledger rows written since ``since_ts`` without a countable stamp.

Reads each ledger from the END backwards and stops at the first row whose ``write_stamp.written_at_ts`` is before
``since_ts`` (rows are append-only, so everything older is out of range). Every row after that boundary that has
no countable write stamp is counted. Rows whose sha256 is in the exclusion list (the RK-approved 10,771 list,
``analyzer_epoch_unplaced_rows.jsonl``) are reported separately and not counted.

Status: GREEN when count == 0 and every file reached its boundary; RED (alert) when count > 0; AMBER/UNVERIFIED
when a file was not readable or the scan hit ``max_bytes`` before a boundary (fail closed: never GREEN).
CLI: ``python ledger_stamp_monitor.py --data-dir DIR --since TS [--exclusion-list F]``; exit 0 GREEN, 2 RED, 3 AMBER.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Iterator

import ledger_write_stamp as lws

CHECK_ID = "v3_ledger_unstamped_since"
LEDGERS = ("opportunity", "decision", "order_intent", "execution", "lifecycle", "market_segment")
DEFAULT_MAX_BYTES = 256 * 1024 * 1024
_CHUNK = 1 << 20


def _reverse_lines(path: Path, max_bytes: int) -> Iterator[tuple[bytes, bool]]:
    """(line, exhausted_budget) newest first; the trailing partial line (no newline) is skipped."""
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        read = 0
        buf = b""
        first = True
        while pos > 0:
            if read >= max_bytes:
                yield b"", True
                return
            step = min(_CHUNK, pos, max(1, max_bytes - read))
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + buf
            read += step
            parts = buf.split(b"\n")
            buf = parts[0]
            tail = parts[1:]
            if first:
                first = False
                if tail and tail[-1] != b"":
                    tail = tail[:-1]  # incomplete last row: being written, not yet a row
            for line in reversed(tail):
                if line.strip():
                    yield line, False
        if buf.strip():
            yield buf, False


def load_exclusions(path: str | os.PathLike | None) -> set[str]:
    out: set[str] = set()
    if not path:
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                sha = json.loads(line).get("row_sha256")
                if sha:
                    out.add(sha)
    return out


def check(data_dir: str | os.PathLike, since_ts: float, *, exclusions: set[str] | None = None,
          max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    exclusions = exclusions or set()
    ledger_dir = Path(data_dir) / "v3" / "ledgers"
    per: dict[str, dict] = {}
    unverified: list[str] = []
    for name in LEDGERS:
        path = ledger_dir / f"{name}.jsonl"
        info = {"unstamped": 0, "excluded_listed": 0, "stamped": 0, "boundary_reached": False,
                "reasons": {}, "sample_row_ids": []}
        per[name] = info
        if not path.exists():
            info["boundary_reached"] = True
            continue
        try:
            for line, exhausted in _reverse_lines(path, max_bytes):
                if exhausted:
                    unverified.append(f"{name}:SCAN_BUDGET_EXHAUSTED")
                    break
                try:
                    row = json.loads(line)
                except ValueError:
                    row = None
                stamp = row.get(lws.STAMP_KEY) if isinstance(row, dict) else None
                written = stamp.get("written_at_ts") if isinstance(stamp, dict) else None
                if isinstance(written, (int, float)) and written < since_ts:
                    info["boundary_reached"] = True
                    break
                reason = lws.row_non_countable_reason(row)
                if reason is None:
                    info["stamped"] += 1
                    continue
                if hashlib.sha256(line.rstrip(b"\r")).hexdigest() in exclusions:
                    info["excluded_listed"] += 1
                    continue
                info["unstamped"] += 1
                info["reasons"][reason] = info["reasons"].get(reason, 0) + 1
                if len(info["sample_row_ids"]) < 5 and isinstance(row, dict):
                    info["sample_row_ids"].append(row.get("record_id"))
            else:
                info["boundary_reached"] = True  # whole file read
        except OSError as exc:
            unverified.append(f"{name}:UNREADABLE:{type(exc).__name__}")
    count = sum(v["unstamped"] for v in per.values())
    status = "RED" if count > 0 else ("AMBER" if unverified else "GREEN")
    return {"check": CHECK_ID, "since_ts": float(since_ts), "status": status, "alert": status != "GREEN",
            "unstamped_rows_since": count, "unverified": unverified, "ledgers": per}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--since", type=float, required=True)
    ap.add_argument("--exclusion-list")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    a = ap.parse_args(argv)
    result = check(a.data_dir, a.since, exclusions=load_exclusions(a.exclusion_list), max_bytes=a.max_bytes)
    print(json.dumps(result, sort_keys=True))
    return {"GREEN": 0, "RED": 2}.get(result["status"], 3)


if __name__ == "__main__":
    sys.exit(main())
