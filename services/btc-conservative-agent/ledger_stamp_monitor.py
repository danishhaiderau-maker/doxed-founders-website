"""HM monitor (R16) ``v3_ledger_unstamped_since``: V3 ledger rows written since ``since_ts`` without a countable stamp.

Reads every generation of each ledger (``{name}.jsonl`` then sealed ``{name}.jsonl.N``, newest first) from the END
backwards. M1 (HM 25 Oct): the ONLY boundary is ``write_stamp.written_at_ts`` < ``since_ts``; legacy top-level keys
(ts/decision_ts/...) are never trusted. A row with no stamp or no written_at_ts is UNSTAMPED and is never skipped by
the scan window (fail closed): every unstamped row newer than the boundary, or newer than any stamped row, counts.
M3: unstamped rows that lie strictly BELOW the oldest stamped row of the stream (append order proves they were
written before stamping began) are reported as ``legacy_unstamped_before_first_stamp`` and are not RED. A generation
whose file mtime is before ``since_ts`` provably holds nothing written since (no read needed). A stream with no
stamped row and no write since ``since_ts`` that is retired by code is reported IDLE with its reason (never GREEN).
Rows whose sha256 is in the exclusion list (RK list) are reported separately and not counted.

Status: RED when count > 0; AMBER/UNVERIFIED when a file was unreadable or the byte cap stopped a scan before rows
could be classified; IDLE when a ledger is idle (reason given); GREEN only when none of these apply.
CLI: ``python ledger_stamp_monitor.py --data-dir DIR --since TS [--exclusion-list F]``; exit 0 GREEN, 2 RED, 3 other.
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


def _retired_reason(name: str) -> str | None:
    """Code-level retirement of a stream's writers (R17). Import failure = not retired (fail closed)."""
    if name != "market_segment":
        return None
    try:
        import research_v3_bridge as bridge
    except Exception:  # noqa: BLE001
        return None
    return "MARKET_SEGMENTS_RETIRED_R17" if getattr(bridge, "MARKET_SEGMENT_WRITES_RETIRED", False) is True else None


def generation_paths(ledger_dir: Path, name: str) -> list[Path]:
    """M2: active ``{name}.jsonl`` first, then sealed ``{name}.jsonl.N`` newest (highest N) first."""
    out: list[Path] = []
    active = ledger_dir / f"{name}.jsonl"
    if active.exists():
        out.append(active)
    sealed = []
    prefix = f"{name}.jsonl."
    for cand in ledger_dir.glob(f"{prefix}*"):
        suffix = cand.name[len(prefix):]
        if suffix.isdigit() and int(suffix) > 0 and suffix == str(int(suffix)):
            sealed.append((int(suffix), cand))
    out.extend(p for _, p in sorted(sealed, reverse=True))
    return out


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


def _new_info() -> dict:
    return {"status": None, "unstamped": 0, "excluded_listed": 0, "stamped": 0, "boundary_reached": False,
            "boundary": None, "legacy_unstamped_before_first_stamp": 0, "unclassified_at_cap": 0,
            "generations_scanned": [], "idle_reason": None, "reasons": {}, "sample_row_ids": []}


def _scan_ledger(name: str, ledger_dir: Path, since_ts: float, exclusions: set[str], max_bytes: int,
                 unverified: list[str]) -> dict:
    info = _new_info()
    gens = generation_paths(ledger_dir, name)
    if not gens:
        info.update(boundary_reached=True, boundary="NO_FILE")
        return info
    pending: list[tuple[str, object]] = []  # unstamped rows newer than nothing-yet-stamped (newest first)
    seen_stamped = False
    budget = max_bytes
    newest_mtime = None
    capped = False

    def commit():
        for reason, rid in pending:
            info["unstamped"] += 1
            info["reasons"][reason] = info["reasons"].get(reason, 0) + 1
            if len(info["sample_row_ids"]) < 5:
                info["sample_row_ids"].append(rid)
        pending.clear()

    done = False
    for path in gens:
        try:
            st = path.stat()
        except OSError as exc:
            unverified.append(f"{name}:UNREADABLE:{type(exc).__name__}")
            return info
        newest_mtime = st.st_mtime if newest_mtime is None else newest_mtime
        info["generations_scanned"].append(path.name)
        if st.st_mtime < since_ts:
            commit()  # rows above this point in newer generations: fail closed
            info.update(boundary_reached=True, boundary="FILE_MTIME_BEFORE_SINCE")
            done = True
            break
        try:
            used = 0
            for line, exhausted in _reverse_lines(path, budget):
                if exhausted:
                    capped = True
                    break
                used += len(line) + 1
                try:
                    row = json.loads(line)
                except ValueError:
                    row = None
                stamp = row.get(lws.STAMP_KEY) if isinstance(row, dict) else None
                written = stamp.get("written_at_ts") if isinstance(stamp, dict) else None
                if isinstance(written, bool) or not isinstance(written, (int, float)):
                    written = None
                rid = row.get("record_id") if isinstance(row, dict) else None
                if written is not None:
                    commit()  # unstamped rows above a stamped row were written after it: count them
                    seen_stamped = True
                    if written < since_ts:
                        info.update(boundary_reached=True, boundary="WRITTEN_AT_TS")
                        done = True
                        break
                reason = lws.row_non_countable_reason(row)
                if reason is None and written is None:
                    reason = "WRITE_STAMP_WRITTEN_AT_TS_MISSING"  # M1: never counted as stamped
                if reason is None:
                    info["stamped"] += 1
                    continue
                if hashlib.sha256(line.rstrip(b"\r")).hexdigest() in exclusions:
                    info["excluded_listed"] += 1
                    continue
                if written is not None:  # stamped but non-countable: always counted
                    pending.append((reason, rid))
                    commit()
                else:
                    pending.append((reason, rid))
            budget = max(0, budget - used)
        except OSError as exc:
            unverified.append(f"{name}:UNREADABLE:{type(exc).__name__}")
            return info
        if done or capped:
            break
    if capped:
        if seen_stamped:
            info["unclassified_at_cap"] = len(pending)
            pending.clear()
        else:
            commit()
        unverified.append(f"{name}:SCAN_BUDGET_EXHAUSTED")
    elif not done:
        info["boundary_reached"] = True
        info["boundary"] = "END_OF_OLDEST_GENERATION"
        if seen_stamped:
            info["legacy_unstamped_before_first_stamp"] = len(pending)
            pending.clear()
        else:
            commit()
    if not seen_stamped and info["unstamped"] == 0 and newest_mtime is not None and newest_mtime < since_ts:
        retired = _retired_reason(name)
        info["idle_reason"] = retired or "NO_WRITE_SINCE"
        info["last_write_ts"] = newest_mtime
    return info


def check(data_dir: str | os.PathLike, since_ts: float, *, exclusions: set[str] | None = None,
          max_bytes: int = DEFAULT_MAX_BYTES) -> dict:
    exclusions = exclusions or set()
    ledger_dir = Path(data_dir) / "v3" / "ledgers"
    per: dict[str, dict] = {}
    unverified: list[str] = []
    for name in LEDGERS:
        n_before = len(unverified)
        info = _scan_ledger(name, ledger_dir, float(since_ts), exclusions, max_bytes, unverified)
        if info["unstamped"] > 0:
            info["status"] = "RED"
        elif len(unverified) > n_before:
            info["status"] = "AMBER"
        elif info["idle_reason"]:
            info["status"] = "IDLE"
        else:
            info["status"] = "GREEN"
        per[name] = info
    count = sum(v["unstamped"] for v in per.values())
    idle = {k: v["idle_reason"] for k, v in per.items() if v["status"] == "IDLE"}
    status = "RED" if count > 0 else ("AMBER" if unverified else ("IDLE" if idle else "GREEN"))
    return {"check": CHECK_ID, "since_ts": float(since_ts), "status": status, "alert": status in ("RED", "AMBER"),
            "unstamped_rows_since": count, "unverified": unverified, "idle_ledgers": idle,
            "legacy_unstamped_before_first_stamp": sum(v["legacy_unstamped_before_first_stamp"] for v in per.values()),
            "boundary_key": "write_stamp.written_at_ts", "ledgers": per}


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
