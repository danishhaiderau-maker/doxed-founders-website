"""Append-only gzip members and checksummed journals. No rename, no truncate."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import zlib
from pathlib import Path

from .config import CrashFault

RAW_KEYS = (
    "schema_v",
    "venue",
    "symbol",
    "channel",
    "conn_epoch",
    "seq_local",
    "venue_seq",
    "ts_exchange_ms",
    "ts_recv_utc_ns",
    "ts_recv_mono_ns",
    "payload",
    "payload_verbatim",
    "raw_id",
    "segment_id",
    "stream",
    "ingest_order",
    "seq_missing",
)

NORM_KEYS = (
    "norm_schema_v",
    "raw_id",
    "segment_id",
    "stream",
    "venue",
    "symbol",
    "channel",
    "conn_epoch",
    "seq_local",
    "venue_seq",
    "ts_exchange_ms",
    "ts_recv_utc_ns",
    "ts_recv_mono_ns",
    "duplicate",
    "out_of_order",
    "counted",
    "trade_id",
    "price",
    "amount",
    "side",
    "update_kind",
    "book_valid",
    "validity_interval_id",
    "interval_event",
    "applied",
    "checksum_ok",
    "queue_position",
    "norm_ordinal",
)


def dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def record_line(obj: dict) -> bytes:
    body = {k: v for k, v in obj.items() if k != "record_checksum"}
    digest = sha256_bytes(dumps(body).encode("utf-8"))
    full = {**body, "record_checksum": digest}
    return (dumps(full) + "\n").encode("utf-8")


def checksum_ok(obj: dict) -> bool:
    if "record_checksum" not in obj:
        return False
    body = {k: v for k, v in obj.items() if k != "record_checksum"}
    digest = sha256_bytes(dumps(body).encode("utf-8"))
    return digest == obj["record_checksum"]


def gzip_member(payload: bytes) -> bytes:
    """One complete gzip member. mtime is 0 so the bytes do not depend on the clock."""
    return gzip.compress(payload, compresslevel=9, mtime=0)


def _header_end(blob: bytes, start: int) -> int:
    if start + 10 > len(blob) or blob[start : start + 2] != b"\x1f\x8b" or blob[start + 2] != 8:
        raise ValueError("not a gzip member")
    flags = blob[start + 3]
    pos = start + 10
    if flags & 4:
        if pos + 2 > len(blob):
            raise ValueError("torn")
        extra = int.from_bytes(blob[pos : pos + 2], "little")
        pos += 2 + extra
    if flags & 8:
        end = blob.index(b"\x00", pos)
        pos = end + 1
    if flags & 16:
        end = blob.index(b"\x00", pos)
        pos = end + 1
    if flags & 2:
        pos += 2
    if pos > len(blob):
        raise ValueError("torn")
    return pos


def member_end(blob: bytes, start: int) -> int:
    pos = _header_end(blob, start)
    decomp = zlib.decompressobj(-15)
    while not decomp.eof:
        if pos >= len(blob):
            raise ValueError("torn")
        chunk = blob[pos : pos + 65536]
        decomp.decompress(chunk)
        if decomp.eof:
            pos += len(chunk) - len(decomp.unused_data)
            break
        pos += len(chunk)
    if pos + 8 > len(blob):
        raise ValueError("torn trailer")
    return pos + 8


def split_members(blob: bytes) -> tuple[list[tuple[int, int, bytes]], int | None]:
    """Return complete members and the offset of a torn tail, if any."""
    members = []
    pos = 0
    while pos < len(blob):
        try:
            end = member_end(blob, pos)
        except ValueError:
            return members, pos
        members.append((pos, end, blob[pos:end]))
        pos = end
    return members, None


def decode_member(chunk: bytes) -> list[dict]:
    text = gzip.decompress(chunk).decode("utf-8")
    rows = []
    if not text:
        return rows
    for line in text.splitlines():
        rows.append(json.loads(line))
    return rows


def decode_prefix(blob: bytes) -> tuple[list[dict], int | None]:
    members, torn = split_members(blob)
    rows: list[dict] = []
    for _start, _end, chunk in members:
        rows.extend(decode_member(chunk))
    return rows, torn


def jsonl_bytes(rows: list[dict]) -> bytes:
    return b"".join((dumps(row) + "\n").encode("utf-8") for row in rows)


def confine(root: Path, relative: str) -> Path:
    root_r = root.resolve()
    path = (root_r / relative).resolve()
    if path != root_r and root_r not in path.parents:
        raise ValueError(f"path escapes root: {relative}")
    return path


def read_journal(path: Path) -> tuple[list[dict], bool]:
    """Contiguous checksum-valid prefix. A partial tail or a bad line stops the prefix.

    Returns (records, stopped_early).
    """
    if not path.exists():
        return [], False
    data = path.read_bytes()
    if not data:
        return [], False
    if data.endswith(b"\n"):
        complete = data
        partial = False
    else:
        cut = data.rfind(b"\n")
        complete = data[: cut + 1] if cut >= 0 else b""
        partial = True
    records = []
    stopped = partial
    for line in complete.splitlines():
        try:
            obj = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return records, True
        if not isinstance(obj, dict) or not checksum_ok(obj):
            return records, True
        records.append(obj)
    return records, stopped


def _write_all(fd: int, blob: bytes) -> None:
    view = memoryview(blob)
    sent = 0
    while sent < len(view):
        n = os.write(fd, view[sent:])
        if n <= 0:
            raise OSError("short write")
        sent += n


class AppendJournal:
    def __init__(self, path: Path, trace: list[str], *, exclusive: bool = True):
        self.path = path
        self.trace = trace
        flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY
        if exclusive:
            flags |= os.O_EXCL
        self.fd = os.open(path, flags, 0o644)

    def append_obj(self, obj: dict, *, label: str, fault_torn: bool = False) -> None:
        line = record_line(obj)
        if fault_torn:
            torn = line[: max(1, len(line) // 3)]
            torn = torn.split(b"\n", 1)[0]
            _write_all(self.fd, torn)
            os.fsync(self.fd)
            self.trace.append(f"{label}:torn")
            raise CrashFault("torn_journal")
        _write_all(self.fd, line)
        self.trace.append(label)
        os.fsync(self.fd)
        self.trace.append(f"{label}:fsync_journal")
        return len(line)

    def close(self) -> None:
        os.close(self.fd)


class SegmentFile:
    def __init__(self, path: Path, trace: list[str]):
        self.path = path
        self.trace = trace
        self.fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_WRONLY, 0o644)
        self.size = 0
        self.closed = False
        self._prefix = hashlib.sha256()
        self.last_journal_bytes = 0

    def read_bytes(self) -> bytes:
        with open(self.path, "rb") as handle:
            return handle.read()

    def commit_member(self, rows: list[dict], journal: AppendJournal, *, label: str, fault: str | None, meta: dict) -> dict:
        if self.closed:
            raise ValueError("segment closed")
        member = gzip_member(jsonl_bytes(rows))
        self.trace.append(f"{label}:close_member")
        if fault == "io_error":
            raise OSError("injected write failure")
        if fault == "partial_member":
            partial = member[:8]
            _write_all(self.fd, partial)
            self.size += len(partial)
            self._prefix.update(partial)
            self.trace.append(f"{label}:partial_member")
            raise CrashFault("partial_member")
        start = self.size
        _write_all(self.fd, member)
        self.size += len(member)
        os.fsync(self.fd)
        self.trace.append(f"{label}:fsync_segment")
        self._prefix.update(member)
        if fault == "after_fsync_before_commit":
            raise CrashFault("after_fsync_before_commit")
        end = start + len(member)
        record = {
            "kind": "commit",
            "stream": meta["stream"],
            "segment_id": meta["segment_id"],
            "segment_rel": meta["segment_rel"],
            "member_index": meta["member_index"],
            "member_start_offset": start,
            "member_end_offset": end,
            "last_raw_id": rows[-1].get("raw_id"),
            "record_count": len(rows),
            "member_sha256": sha256_bytes(member),
            "prefix_sha256": self._prefix.hexdigest(),
        }
        if "segment_sha256" in record:
            raise AssertionError("commit must not carry the whole-segment digest")
        self.last_journal_bytes = journal.append_obj(
            record, label=f"{label}:checksummed_commit", fault_torn=(fault == "torn_journal")
        )
        return record

    def mark_closed(self) -> None:
        self.closed = True
        os.close(self.fd)


def verify_segment(segment_path: Path, journal_records: list[dict], segment_id: str) -> dict:
    """Hash each committed range on its own bytes. The whole-file digest is separate."""
    blob = segment_path.read_bytes() if segment_path.exists() else b""
    commits = [
        row for row in journal_records if row.get("kind") == "commit" and row.get("segment_id") == segment_id
    ]
    closes = [
        row for row in journal_records if row.get("kind") == "close" and row.get("segment_id") == segment_id
    ]
    errors: list[str] = []
    decoded: list[dict] = []
    for commit in commits:
        start = int(commit["member_start_offset"])
        end = int(commit["member_end_offset"])
        if end > len(blob) or start < 0 or start > end:
            errors.append("range_outside_file")
            break
        member_sha = sha256_bytes(blob[start:end])
        prefix_sha = sha256_bytes(blob[:end])
        if member_sha != commit["member_sha256"]:
            errors.append("member_range")
        if prefix_sha != commit["prefix_sha256"]:
            errors.append("prefix_range")
        try:
            rows = decode_member(blob[start:end])
        except (OSError, EOFError, zlib.error, gzip.BadGzipFile, UnicodeError, json.JSONDecodeError, ValueError):
            errors.append("member_undecodable")
            break
        ids = [row.get("raw_id") for row in rows]
        if len(ids) != int(commit["record_count"]) or (ids and ids[-1] != commit["last_raw_id"]):
            errors.append("raw_id_membership")
        if str(commit.get("stream", "")).startswith("norm"):
            if any(raw_id is None for raw_id in ids):
                errors.append("raw_id_membership")
        else:
            expected_prefix = f"{segment_id}:{int(commit['member_index'])}:"
            if any(not str(raw_id).startswith(expected_prefix) for raw_id in ids):
                errors.append("raw_id_membership")
        decoded.extend(rows)
    whole = sha256_bytes(blob)
    close_ok = None
    if closes:
        close = closes[-1]
        close_ok = whole == close.get("segment_sha256") and int(close.get("bytes", -1)) == len(blob)
        if not close_ok:
            errors.append("segment_sha")
    return {
        "ok": not errors,
        "errors": errors,
        "records": decoded if not errors else [],
        "member_sha256": commits[-1]["member_sha256"] if commits else None,
        "prefix_sha256": commits[-1]["prefix_sha256"] if commits else None,
        "segment_sha256": whole,
        "close_checked": close_ok,
        "commit_count": len(commits),
    }


def durable_view(segment_path: Path, journal_records: list[dict], segment_id: str) -> dict:
    """Records inside the last valid commit only. Later bytes are an uncommitted tail."""
    commits = [
        row for row in journal_records if row.get("kind") == "commit" and row.get("segment_id") == segment_id
    ]
    blob = segment_path.read_bytes() if segment_path.exists() else b""
    if not commits:
        members, _torn = split_members(blob)
        return {
            "records": [],
            "durable_count": 0,
            "lost_uncommitted_tail_bytes": len(blob),
            "decodable_uncommitted_members": len(members),
            "fsynced_bytes_are_not_records": True,
            "last_raw_id": None,
        }
    end = int(commits[-1]["member_end_offset"])
    prefix = blob[:end]
    tail = blob[end:]
    verified = verify_segment(segment_path, journal_records, segment_id)
    range_errors = [item for item in verified["errors"] if item != "segment_sha"]
    if range_errors:
        return {
            "records": [],
            "durable_count": 0,
            "lost_uncommitted_tail_bytes": len(blob),
            "decodable_uncommitted_members": 0,
            "fsynced_bytes_are_not_records": True,
            "last_raw_id": None,
            "verify_errors": range_errors,
        }
    rows, torn = decode_prefix(prefix)
    if torn is not None:
        return {
            "records": [],
            "durable_count": 0,
            "lost_uncommitted_tail_bytes": len(blob),
            "decodable_uncommitted_members": 0,
            "fsynced_bytes_are_not_records": True,
            "last_raw_id": None,
            "prefix_undecodable": True,
        }
    tail_members, _tail_torn = split_members(tail)
    return {
        "records": rows,
        "durable_count": len(rows),
        "lost_uncommitted_tail_bytes": len(tail),
        "decodable_uncommitted_members": len(tail_members),
        "fsynced_bytes_are_not_records": True,
        "last_raw_id": rows[-1]["raw_id"] if rows else None,
        "verify_errors": [],
    }
