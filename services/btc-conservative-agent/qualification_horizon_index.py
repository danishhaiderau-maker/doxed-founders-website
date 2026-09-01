"""Bounded, restart-safe post-terminal market-horizon production.

The one-second tape is append-only and can be much larger than one lifecycle.
This module maintains a durable SQLite byte-offset index and reads only the
exact requested interval.  It never upgrades elapsed wall time, stale BBO, or
missing depth into complete evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterable, Mapping

from lifecycle_qualification_horizon import qualification_post_observation
from microstructure_tape import FILE_NAME, SCHEMA, validate_window, window_reference
from research_v3_store import V3EvidenceStore


INDEX_SCHEMA = "qualification_tape_offset_index_v1"
PRODUCER_SCHEMA = "bounded_qualification_horizon_producer_v1"
DEFAULT_MAX_INDEX_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_INDEX_ROWS = 20_000
DEFAULT_MAX_QUERY_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_QUERY_ROWS = 8_000
DEFAULT_MAX_RUNTIME_SEC = 2.0
CONTINUITY_BYTES = 4096


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_id(path: Path, stat: os.stat_result) -> str:
    # Device/inode is the immutable file identity. The active tape is rotated
    # by rename, so including its pathname would strand indexed offsets on the
    # old path and make otherwise intact rows unreadable after rotation.
    material = f"{int(stat.st_dev)}|{int(stat.st_ino)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _tape_paths(root: Path) -> list[Path]:
    active = (root / FILE_NAME).resolve()
    candidates: list[tuple[int, Path]] = []
    for path in root.glob(FILE_NAME + ".*"):
        suffix = path.name[len(FILE_NAME) + 1:]
        if suffix.isdigit() and path.is_file():
            candidates.append((int(suffix), path.resolve()))
    return [path for _n, path in sorted(candidates)] + ([active] if active.is_file() else [])


class TapeIndexError(RuntimeError):
    pass


class TapeOffsetIndex:
    def __init__(self, root: str | Path, *, index_path: str | Path | None = None):
        self.root = Path(root).resolve()
        self.path = Path(index_path).resolve() if index_path else self.root / "v3" / "qualification_horizon_index.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=2.0)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=2000")
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        try:
            with self._connect() as db:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise TapeIndexError("QUALIFICATION_TAPE_INDEX_CORRUPT")
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS source_cursor(
                      source_id TEXT PRIMARY KEY, path TEXT NOT NULL,
                      device INTEGER NOT NULL, inode INTEGER NOT NULL,
                      offset INTEGER NOT NULL, continuity_start INTEGER NOT NULL,
                      continuity_sha256 TEXT NOT NULL, updated_ns INTEGER NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS tape_row(
                      source_id TEXT NOT NULL, bucket_ts INTEGER NOT NULL,
                      byte_offset INTEGER NOT NULL, byte_length INTEGER NOT NULL,
                      row_sha256 TEXT NOT NULL,
                      PRIMARY KEY(source_id, byte_offset)
                    );
                    CREATE INDEX IF NOT EXISTS tape_row_ts ON tape_row(bucket_ts);
                """)
                db.commit()
        except sqlite3.DatabaseError as exc:
            raise TapeIndexError("QUALIFICATION_TAPE_INDEX_CORRUPT") from exc

    @staticmethod
    def _continuity(path: Path, offset: int) -> tuple[int, str]:
        start = max(0, int(offset) - CONTINUITY_BYTES)
        with path.open("rb") as handle:
            handle.seek(start)
            payload = handle.read(int(offset) - start)
        return start, _sha(payload)

    def refresh(self, *, max_bytes: int = DEFAULT_MAX_INDEX_BYTES,
                max_rows: int = DEFAULT_MAX_INDEX_ROWS,
                max_runtime_sec: float = DEFAULT_MAX_RUNTIME_SEC) -> dict[str, Any]:
        started = time.monotonic()
        byte_budget, row_budget = max(1, int(max_bytes)), max(1, int(max_rows))
        bytes_read = rows_indexed = parse_errors = 0
        incomplete_line = False
        sources = _tape_paths(self.root)
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                for path in sources:
                    if bytes_read >= byte_budget or rows_indexed >= row_budget or time.monotonic() - started >= max_runtime_sec:
                        break
                    stat = path.stat()
                    sid = _source_id(path, stat)
                    cursor = db.execute(
                        "SELECT offset, continuity_start, continuity_sha256 FROM source_cursor WHERE source_id=?",
                        (sid,),
                    ).fetchone()
                    offset = int(cursor[0]) if cursor else 0
                    if stat.st_size < offset:
                        raise TapeIndexError("QUALIFICATION_TAPE_TRUNCATED")
                    if cursor:
                        start, digest = self._continuity(path, offset)
                        if start != int(cursor[1]) or digest != cursor[2]:
                            raise TapeIndexError("QUALIFICATION_TAPE_PREFIX_MUTATED")
                    with path.open("rb") as handle:
                        handle.seek(offset)
                        while bytes_read < byte_budget and rows_indexed < row_budget and time.monotonic() - started < max_runtime_sec:
                            line_offset = handle.tell()
                            remaining = byte_budget - bytes_read
                            raw = handle.readline(remaining + 1)
                            if not raw:
                                break
                            if len(raw) > remaining or not raw.endswith(b"\n"):
                                incomplete_line = True
                                handle.seek(line_offset)
                                break
                            bytes_read += len(raw)
                            try:
                                row = json.loads(raw.decode("utf-8-sig"))
                                ts = int(row.get("bucket_ts")) if isinstance(row, Mapping) else None
                            except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, OverflowError):
                                parse_errors += 1
                                ts = None
                            if ts is not None:
                                db.execute(
                                    "INSERT OR IGNORE INTO tape_row VALUES(?,?,?,?,?)",
                                    (sid, ts, line_offset, len(raw), _sha(raw)),
                                )
                                rows_indexed += 1
                        offset = handle.tell()
                    continuity_start, continuity_sha = self._continuity(path, offset)
                    db.execute(
                        "INSERT OR REPLACE INTO source_cursor VALUES(?,?,?,?,?,?,?,?)",
                        (sid, str(path), int(stat.st_dev), int(stat.st_ino), offset,
                         continuity_start, continuity_sha, time.time_ns()),
                    )
                db.commit()
        except sqlite3.DatabaseError as exc:
            raise TapeIndexError("QUALIFICATION_TAPE_INDEX_CORRUPT") from exc
        return {
            "schema": INDEX_SCHEMA, "bytes_read": bytes_read,
            "rows_indexed": rows_indexed, "parse_errors": parse_errors,
            "incomplete_line_deferred": incomplete_line,
            "budget_exhausted": bytes_read >= byte_budget or rows_indexed >= row_budget,
            "runtime_sec": time.monotonic() - started,
        }

    def query(self, start_ts: float, end_ts: float, *,
              max_bytes: int = DEFAULT_MAX_QUERY_BYTES,
              max_rows: int = DEFAULT_MAX_QUERY_ROWS,
              max_runtime_sec: float = DEFAULT_MAX_RUNTIME_SEC) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        started = time.monotonic()
        low, high = int(math.floor(start_ts)), int(math.ceil(end_ts))
        byte_budget, row_budget = max(1, int(max_bytes)), max(1, int(max_rows))
        rows_by_ts: dict[int, list[dict[str, Any]]] = {}
        bytes_read = hash_errors = read_errors = 0
        bounded = True
        try:
            with self._connect() as db:
                records = db.execute(
                    "SELECT r.bucket_ts,s.path,r.byte_offset,r.byte_length,r.row_sha256 "
                    "FROM tape_row r JOIN source_cursor s USING(source_id) "
                    "WHERE r.bucket_ts>=? AND r.bucket_ts<? ORDER BY r.bucket_ts,r.source_id,r.byte_offset LIMIT ?",
                    (low, high, row_budget + 1),
                ).fetchall()
                if len(records) > row_budget:
                    records, bounded = records[:row_budget], False
                for ts, path_text, offset, length, expected_sha in records:
                    if bytes_read + int(length) > byte_budget or time.monotonic() - started >= max_runtime_sec:
                        bounded = False
                        break
                    try:
                        with Path(path_text).open("rb") as handle:
                            handle.seek(int(offset)); raw = handle.read(int(length))
                        bytes_read += len(raw)
                        if len(raw) != int(length) or _sha(raw) != expected_sha:
                            hash_errors += 1; continue
                        row = json.loads(raw.decode("utf-8-sig"))
                        if not isinstance(row, Mapping) or int(row.get("bucket_ts")) != int(ts):
                            hash_errors += 1; continue
                        normalized = dict(row)
                        normalized["ts"] = float(ts)
                        normalized["price"] = _finite(row.get("last") or row.get("price") or row.get("mark"))
                        rows_by_ts.setdefault(int(ts), []).append(normalized)
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
                        read_errors += 1
        except sqlite3.DatabaseError as exc:
            raise TapeIndexError("QUALIFICATION_TAPE_INDEX_CORRUPT") from exc
        rows = [bucket[0] for ts in sorted(rows_by_ts) for bucket in [rows_by_ts[ts]]]
        duplicates = sorted(ts for ts, bucket in rows_by_ts.items() if len(bucket) != 1)
        return rows, {
            "schema": "bounded_qualification_tape_query_v1",
            "requested_start_ts": float(start_ts), "requested_end_ts": float(end_ts),
            "bytes_read": bytes_read, "rows_read": len(records) if 'records' in locals() else 0,
            "rows_returned": len(rows), "hash_errors": hash_errors,
            "read_errors": read_errors, "duplicate_buckets": duplicates[:100],
            "bounds_respected": bounded, "runtime_sec": time.monotonic() - started,
        }


def produce_post_exit_path(candidate: Mapping[str, Any], *, data_dir: str | Path,
                           epoch_id: str, now_ts: float | None = None,
                           horizon_sec: float = 7200.0,
                           index: TapeOffsetIndex | None = None,
                           max_query_bytes: int = DEFAULT_MAX_QUERY_BYTES,
                           max_query_rows: int = DEFAULT_MAX_QUERY_ROWS,
                           max_runtime_sec: float = DEFAULT_MAX_RUNTIME_SEC) -> dict[str, Any]:
    """Append one idempotent POST_EXIT_PATH for an explicitly terminal lifecycle."""
    event_id = str(candidate.get("event_id") or candidate.get("trade_id") or "")
    episode_id = str(candidate.get("episode_id") or "")
    terminal_ts = _finite(candidate.get("terminal_ts") or candidate.get("close_ts") or candidate.get("terminal_ts_exact"))
    now = time.time() if now_ts is None else float(now_ts)
    required_end = terminal_ts + float(horizon_sec) if terminal_ts is not None else None
    blockers: list[str] = []
    if candidate.get("terminal") is not True:
        blockers.append("LIFECYCLE_NOT_TERMINAL")
    if not event_id or not episode_id:
        blockers.append("LIFECYCLE_IDENTITY_INCOMPLETE")
    if terminal_ts is None or terminal_ts <= 0:
        blockers.append("TERMINAL_TS_MISSING")
    if required_end is None or now < required_end:
        blockers.append("QUALIFICATION_HORIZON_NOT_MATURE")
    rows: list[dict[str, Any]] = []
    query_receipt: dict[str, Any] = {"schema": "bounded_qualification_tape_query_v1", "not_run": True}
    post = {"complete": False, "blockers": sorted(set(blockers))}
    store = V3EvidenceStore(data_dir, epoch_id=str(epoch_id))
    segment_ref = None
    if not blockers:
        tape_index = index or TapeOffsetIndex(data_dir)
        rows, query_receipt = tape_index.query(
            terminal_ts, required_end, max_bytes=max_query_bytes,
            max_rows=max_query_rows, max_runtime_sec=max_runtime_sec,
        )
        reference = window_reference(terminal_ts, required_end)
        eligibility = validate_window(rows, reference)
        duplicate = query_receipt.get("duplicate_buckets") or []
        depth_bad = [
            int(row.get("bucket_ts")) for row in rows
            if row.get("schema") != SCHEMA
            or _finite(row.get("bid_qty")) is None or _finite(row.get("bid_qty")) <= 0
            or _finite(row.get("ask_qty")) is None or _finite(row.get("ask_qty")) <= 0
        ]
        coverage = {
            "requested_start_ts": terminal_ts, "requested_end_ts": required_end,
            "observed_start_ts": rows[0]["ts"] if rows else None,
            "observed_end_ts": (rows[-1]["ts"] + 1.0) if rows else None,
            "requested_bounds_complete": eligibility["eligible"],
            "two_second_or_better": eligibility["eligible"],
            "max_gap_sec": 1.0 if eligibility["eligible"] else None,
            "all_rows_have_valid_bbo": eligibility["eligible"],
            "all_rows_have_visible_depth": bool(rows) and not depth_bad,
            "parse_errors": query_receipt.get("read_errors", 0),
            "invalid_timestamp_rows": 0,
            "invalid_price_rows": sum(row.get("price") is None for row in rows),
            "invalid_bbo_rows": len(eligibility["invalid_or_stale_buckets"]),
            "invalid_depth_rows": len(depth_bad),
        }
        post = qualification_post_observation(
            coverage, terminal_ts=terminal_ts, lifecycle_horizon_sec=horizon_sec,
            max_gap_sec=1.0,
        )
        if not query_receipt.get("bounds_respected"):
            post["blockers"] = sorted(set(post["blockers"] + ["POST_OBSERVATION_QUERY_BOUNDS_EXHAUSTED"]))
            post["complete"] = post["gaps_absent"] = False
        if query_receipt.get("hash_errors") or duplicate:
            post["blockers"] = sorted(set(post["blockers"] + ["POST_OBSERVATION_SOURCE_INTEGRITY_FAILED"]))
            post["complete"] = post["gaps_absent"] = False
        if post["complete"]:
            segment_ref = store.put_market_segment(
                source="LIVE_MICROSTRUCTURE_1S_INDEXED", symbol=str(candidate.get("symbol") or "UNKNOWN"),
                timeframe="1s", start_ts=terminal_ts, end_ts=required_end, rows=rows,
                lifecycle_existing=True,
            )
            record_id = f"market-segment:{event_id}:post-exit:{segment_ref['sha256']}"
            write = store.append("market_segment", {
                "record_id": record_id, "event_id": event_id, "episode_id": episode_id,
                "context_role": "POST_EXIT_PATH", "segment_role": "POST_EXIT_PATH",
                "segment_ref": segment_ref, "coverage": {**post, "query_receipt": query_receipt},
                "opportunity_id": candidate.get("opportunity_id"),
                "shared_ai_call_id": candidate.get("shared_ai_call_id"),
                "policy_signature": candidate.get("policy_signature"),
                "policy_epoch_id": candidate.get("policy_epoch_id"),
                "research_lane": candidate.get("research_lane"),
            })
        else:
            write = {"written": False, "duplicate": False, "reason": "POST_OBSERVATION_INCOMPLETE"}
    else:
        write = {"written": False, "duplicate": False, "reason": blockers[0]}
    return {
        "schema": PRODUCER_SCHEMA, "event_id": event_id, "episode_id": episode_id,
        "mature": not blockers, "post_observation": post, "query_receipt": query_receipt,
        "segment_ref": segment_ref, "write": write,
    }
