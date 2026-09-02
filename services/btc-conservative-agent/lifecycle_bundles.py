"""Immutable, fail-closed Research V3 lifecycle bundle materialization.

Raw V3 ledgers are shared append streams.  They are never removed by this
module.  Instead, complete lifecycle evidence is duplicated into a
content-addressed bundle that can be transferred and acknowledged safely.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from research_v3_contract import LEDGER_NAMES, canonical_json


BUNDLE_SCHEMA = "research_lifecycle_bundle_v1"
TRANSFER_BUNDLE_SCHEMA = "research_lifecycle_transfer_bundle_v1"
TRANSFER_POINTER_SCHEMA = "research_lifecycle_transfer_pointer_v1"
COMPLETION_SCHEMA = "lifecycle_bundle_completion_v1"
EVIDENCE_COLLECTED_SCHEMA = "lifecycle_evidence_collected_v1"
ENTRY_OUTCOMES = frozenset({"FULL_FILL", "PARTIAL_FILL", "NO_FILL", "UNKNOWN"})
INDEX_SCHEMA = "lifecycle_bundle_incremental_index_v1"
DEFAULT_MAX_SCAN_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_SCAN_ROWS = 10_000
MAX_JSONL_RECORD_BYTES = 2 * 1024 * 1024
_SOURCE_ANCHOR_BYTES = 4096
_PROVENANCE_SENTINELS = frozenset({"", "UNKNOWN", "NOT_DEPLOYED_LOCAL"})


def _present(value: Any) -> bool:
    return value is not None and str(value).strip().upper() not in _PROVENANCE_SENTINELS


def _io_path(path: Path) -> str:
    # Sharded content-addressed segment paths can exceed the legacy Win32
    # MAX_PATH boundary.  The file exists, but an unprefixed open is surfaced
    # as FileNotFoundError.  Keep portable manifest paths and use the extended
    # namespace only for the underlying Windows I/O handle.
    resolved = str(path.resolve())
    if os.name == "nt":
        if not resolved.startswith("\\\\?\\"):
            return "\\\\?\\" + resolved
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(_io_path(path), "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc_iso(timestamp: Any) -> str | None:
    try:
        value = float(timestamp)
        if value <= 0:
            return None
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True, order=True)
class LifecycleKey:
    collection_epoch_id: str
    episode_id: str
    policy_signature: str
    research_lane: str

    def as_dict(self) -> dict[str, str]:
        return {
            "collection_epoch_id": self.collection_epoch_id,
            "episode_id": self.episode_id,
            "policy_signature": self.policy_signature,
            "research_lane": self.research_lane,
        }

    @property
    def identity_id(self) -> str:
        digest = hashlib.sha256(canonical_json(self.as_dict()).encode("utf-8")).hexdigest()
        return f"lifecycle-identity-{digest}"


def lifecycle_key(row: dict[str, Any]) -> LifecycleKey:
    """Return the composite lifecycle identity; never guess missing fields."""
    values = {
        "collection_epoch_id": row.get("collection_epoch_id") or row.get("epoch_id"),
        "episode_id": row.get("episode_id"),
        "policy_signature": row.get("policy_signature"),
        "research_lane": row.get("research_lane"),
    }
    missing = sorted(name for name, value in values.items() if not _present(value))
    if missing:
        raise ValueError("LIFECYCLE_IDENTITY_INCOMPLETE:" + ",".join(missing))
    return LifecycleKey(
        str(values["collection_epoch_id"]),
        str(values["episode_id"]),
        str(values["policy_signature"]),
        str(values["research_lane"]).upper(),
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.endswith("\n"):
                raise ValueError(f"TRUNCATED_JSONL_LINE:{path.name}:{line_no}")
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"NON_OBJECT_JSONL_ROW:{path.name}:{line_no}")
            rows.append(row)
    return rows


def collect_lifecycle_rows(root: str | Path) -> dict[LifecycleKey, list[dict[str, Any]]]:
    """Join only exact composite identities from the shared V3 ledgers.

    Sparse rows are intentionally not attached, even if episode_id happens to
    match.  Guessing across multiple policy/lane lifecycles would corrupt the
    very evidence boundary this bundle is intended to prove.
    """
    ledger_dir = Path(root).resolve() / "v3" / "ledgers"
    grouped: dict[LifecycleKey, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[LifecycleKey, str, str]] = set()
    for ledger in LEDGER_NAMES:
        for row in _read_jsonl(ledger_dir / f"{ledger}.jsonl"):
            try:
                key = lifecycle_key(row)
            except ValueError:
                continue
            material = dict(row)
            material.setdefault("ledger", ledger)
            identity = (key, str(material.get("ledger") or ledger), str(material.get("record_id") or ""))
            if identity in seen:
                raise ValueError(f"DUPLICATE_LIFECYCLE_RECORD:{identity[1]}:{identity[2]}")
            seen.add(identity)
            grouped[key].append(material)
    for rows in grouped.values():
        rows.sort(key=lambda row: (
            str(row.get("ledger") or ""),
            float(row.get("observed_ts") or row.get("ts") or row.get("signal_ts") or 0.0),
            str(row.get("record_id") or row.get("event_id") or ""),
        ))
    return dict(grouped)


def _source_anchor(path: Path, end_offset: int) -> str:
    """Hash the already-indexed tail so replacement/truncation fails closed."""
    if end_offset <= 0:
        return hashlib.sha256(b"").hexdigest()
    start = max(0, int(end_offset) - _SOURCE_ANCHOR_BYTES)
    with path.open("rb") as handle:
        handle.seek(start)
        material = handle.read(int(end_offset) - start)
    if len(material) != int(end_offset) - start:
        raise ValueError(f"SOURCE_LEDGER_TRUNCATED:{path.name}")
    return hashlib.sha256(material).hexdigest()


def _open_incremental_index(
    root: Path, *, database_path: Path | None = None,
) -> sqlite3.Connection:
    index_dir = root / "v3" / "lifecycle_bundle_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    database = database_path or (index_dir / "lifecycle_index.sqlite3")
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(database), timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS index_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                schema TEXT NOT NULL,
                next_ledger INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS ledger_cursor (
                ledger TEXT PRIMARY KEY,
                source_dev INTEGER NOT NULL,
                source_ino INTEGER NOT NULL,
                byte_offset INTEGER NOT NULL,
                source_anchor_sha256 TEXT NOT NULL,
                source_mtime_ns INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lifecycle_event (
                ledger TEXT NOT NULL,
                byte_offset INTEGER NOT NULL,
                row_sha256 TEXT NOT NULL,
                collection_epoch_id TEXT NOT NULL,
                episode_id TEXT NOT NULL,
                policy_signature TEXT NOT NULL,
                research_lane TEXT NOT NULL,
                record_id TEXT NOT NULL,
                row_length INTEGER NOT NULL,
                PRIMARY KEY (ledger, byte_offset)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS lifecycle_event_record_identity
            ON lifecycle_event (
                collection_epoch_id, episode_id, policy_signature,
                research_lane, ledger, record_id
            ) WHERE record_id <> '';
            CREATE TABLE IF NOT EXISTS dirty_lifecycle (
                collection_epoch_id TEXT NOT NULL,
                episode_id TEXT NOT NULL,
                policy_signature TEXT NOT NULL,
                research_lane TEXT NOT NULL,
                PRIMARY KEY (
                    collection_epoch_id, episode_id,
                    policy_signature, research_lane
                )
            );
        """)
        meta_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(index_meta)")
        }
        if "next_ledger" not in meta_columns:
            connection.execute(
                "ALTER TABLE index_meta ADD COLUMN next_ledger INTEGER NOT NULL DEFAULT 0"
            )
        row = connection.execute("SELECT schema FROM index_meta WHERE singleton = 1").fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO index_meta(singleton, schema) VALUES (1, ?)",
                (INDEX_SCHEMA,),
            )
            connection.commit()
        elif row["schema"] != INDEX_SCHEMA:
            raise ValueError("LIFECYCLE_INDEX_SCHEMA_MISMATCH")
        return connection
    except BaseException:
        connection.close()
        raise


@contextmanager
def _exclusive_index_lock(root: Path):
    """One portable non-blocking owner for index/cursor mutation."""
    lock_dir = root / "v3" / "lifecycle_bundle_index"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "materializer.lock"
    handle = lock_path.open("a+b")
    try:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError("LIFECYCLE_INDEX_ALREADY_OWNED") from exc
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError("LIFECYCLE_INDEX_ALREADY_OWNED") from exc
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def _validate_source_identity(path: Path, cursor: sqlite3.Row | None) -> os.stat_result:
    stat = path.stat()
    if cursor is None:
        return stat
    offset = int(cursor["byte_offset"])
    if stat.st_size < offset:
        raise ValueError(f"SOURCE_LEDGER_TRUNCATED:{path.name}")
    if int(stat.st_dev) != int(cursor["source_dev"]) or int(stat.st_ino) != int(cursor["source_ino"]):
        raise ValueError(f"SOURCE_LEDGER_ROTATED:{path.name}")
    if _source_anchor(path, offset) != cursor["source_anchor_sha256"]:
        raise ValueError(f"SOURCE_LEDGER_PREFIX_CHANGED:{path.name}")
    return stat


def _index_ledger_chunk(
    connection: sqlite3.Connection, path: Path, ledger: str, *,
    max_bytes: int, max_rows: int,
) -> dict[str, int | bool]:
    cursor = connection.execute(
        "SELECT * FROM ledger_cursor WHERE ledger = ?", (ledger,)
    ).fetchone()
    stat = _validate_source_identity(path, cursor)
    offset = int(cursor["byte_offset"]) if cursor is not None else 0
    available = int(stat.st_size) - offset
    if available <= 0:
        return {"bytes_indexed": 0, "rows_indexed": 0, "rows_scanned": 0, "caught_up": True}
    read_size = min(available, max(1, int(max_bytes)))
    with path.open("rb") as handle:
        handle.seek(offset)
        material = handle.read(read_size)
    # Never index a partial append. If the bounded read split a record, retain
    # it for the next run; a complete source file ending without LF is corrupt.
    last_newline = material.rfind(b"\n")
    if last_newline < 0:
        if available <= read_size:
            raise ValueError(f"TRUNCATED_JSONL_LINE:{path.name}")
        if len(material) >= MAX_JSONL_RECORD_BYTES:
            raise ValueError(f"JSONL_RECORD_TOO_LARGE:{path.name}")
        raise ValueError(f"SCAN_BYTE_LIMIT_SPLITS_RECORD:{path.name}")
    complete = material[:last_newline + 1]
    raw_lines = complete.splitlines(keepends=True)
    if len(raw_lines) > max_rows:
        raw_lines = raw_lines[:max_rows]
        complete = b"".join(raw_lines)
    position = offset
    indexed = 0
    with connection:
        for line_no, raw in enumerate(raw_lines, 1):
            if len(raw) > MAX_JSONL_RECORD_BYTES:
                raise ValueError(f"JSONL_RECORD_TOO_LARGE:{path.name}:{line_no}")
            try:
                row = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"INVALID_JSONL_ROW:{path.name}:{position}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"NON_OBJECT_JSONL_ROW:{path.name}:{position}")
            next_position = position + len(raw)
            try:
                key = lifecycle_key(row)
            except ValueError:
                position = next_position
                continue
            material_row = dict(row)
            material_row.setdefault("ledger", ledger)
            record_id = str(material_row.get("record_id") or "")
            try:
                connection.execute("""
                    INSERT INTO lifecycle_event(
                        ledger, byte_offset, row_sha256, collection_epoch_id,
                        episode_id, policy_signature, research_lane, record_id, row_length
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ledger, position, hashlib.sha256(raw).hexdigest(),
                    key.collection_epoch_id, key.episode_id, key.policy_signature,
                    key.research_lane, record_id, len(raw),
                ))
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    f"DUPLICATE_LIFECYCLE_RECORD:{ledger}:{record_id or position}"
                ) from exc
            connection.execute(
                "INSERT OR IGNORE INTO dirty_lifecycle VALUES (?, ?, ?, ?)",
                (key.collection_epoch_id, key.episode_id, key.policy_signature, key.research_lane),
            )
            indexed += 1
            position = next_position
        # Sparse rows still advance the source cursor. The cursor and indexed
        # events commit together, so a crash cannot acknowledge unseen bytes.
        consumed = len(complete)
        position = offset + consumed
        connection.execute("""
            INSERT INTO ledger_cursor(
                ledger, source_dev, source_ino, byte_offset,
                source_anchor_sha256, source_mtime_ns
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ledger) DO UPDATE SET
                source_dev=excluded.source_dev,
                source_ino=excluded.source_ino,
                byte_offset=excluded.byte_offset,
                source_anchor_sha256=excluded.source_anchor_sha256,
                source_mtime_ns=excluded.source_mtime_ns
        """, (
            ledger, int(stat.st_dev), int(stat.st_ino), position,
            _source_anchor(path, position), int(stat.st_mtime_ns),
        ))
    return {
        "bytes_indexed": position - offset,
        "rows_indexed": indexed,
        "rows_scanned": len(raw_lines),
        "caught_up": position >= int(stat.st_size),
    }


def _dirty_lifecycle_rows(
    connection: sqlite3.Connection, root: Path, *, maximum: int,
    max_events_per_lifecycle: int = 100_000,
    max_bytes_per_lifecycle: int = 64 * 1024 * 1024,
) -> list[tuple[LifecycleKey, list[dict[str, Any]]]]:
    keys = connection.execute("""
        SELECT * FROM dirty_lifecycle
        ORDER BY collection_epoch_id, episode_id, policy_signature, research_lane
        LIMIT ?
    """, (max(1, int(maximum)),)).fetchall()
    result = []
    for item in keys:
        key = LifecycleKey(
            item["collection_epoch_id"], item["episode_id"],
            item["policy_signature"], item["research_lane"],
        )
        indexed = connection.execute("""
            SELECT ledger, byte_offset, row_length, row_sha256 FROM lifecycle_event
            WHERE collection_epoch_id = ? AND episode_id = ?
              AND policy_signature = ? AND research_lane = ?
            ORDER BY ledger, byte_offset
        """, (
            key.collection_epoch_id, key.episode_id,
            key.policy_signature, key.research_lane,
        )).fetchall()
        total_bytes = sum(int(row["row_length"]) for row in indexed)
        if len(indexed) > max_events_per_lifecycle or total_bytes > max_bytes_per_lifecycle:
            raise ValueError(f"LIFECYCLE_INDEX_RESOURCE_LIMIT:{key.identity_id}")
        rows = []
        handles: dict[str, Any] = {}
        try:
            for row in indexed:
                ledger = str(row["ledger"])
                handle = handles.get(ledger)
                if handle is None:
                    handle = (root / "v3" / "ledgers" / f"{ledger}.jsonl").open("rb")
                    handles[ledger] = handle
                handle.seek(int(row["byte_offset"]))
                raw = handle.read(int(row["row_length"]))
                if len(raw) != int(row["row_length"]) or hashlib.sha256(raw).hexdigest() != row["row_sha256"]:
                    raise ValueError(f"INDEXED_SOURCE_ROW_CHANGED:{ledger}:{row['byte_offset']}")
                material = json.loads(raw.decode("utf-8"))
                if not isinstance(material, dict):
                    raise ValueError(f"INDEXED_SOURCE_ROW_INVALID:{ledger}:{row['byte_offset']}")
                material.setdefault("ledger", ledger)
                rows.append(material)
        finally:
            for handle in handles.values():
                handle.close()
        result.append((key, rows))
    return result


def _clear_dirty(connection: sqlite3.Connection, key: LifecycleKey) -> None:
    with connection:
        connection.execute("""
            DELETE FROM dirty_lifecycle
            WHERE collection_epoch_id = ? AND episode_id = ?
              AND policy_signature = ? AND research_lane = ?
        """, (
            key.collection_epoch_id, key.episode_id,
            key.policy_signature, key.research_lane,
        ))


def classify_completion(
    rows: Iterable[dict[str, Any]], *, now: float | None = None,
    lifecycle_horizon_sec: float = 7200.0, reconciliation_allowance_sec: float = 180.0,
) -> dict[str, Any]:
    """Require an explicit, cost-aware completion receipt.

    Historical terminal labels alone are insufficient: they do not prove that
    post-entry/exit paths, costs, or the observation horizon were captured.
    """
    now = time.time() if now is None else float(now)
    candidates = []
    for row in rows:
        receipt = row.get("bundle_completion")
        if isinstance(receipt, dict) and receipt.get("schema") == COMPLETION_SCHEMA:
            candidates.append((float(receipt.get("terminal_ts") or 0.0), receipt))
    if not candidates:
        return {"ready": False, "classification": "UNKNOWN", "blockers": ["COMPLETION_RECEIPT_MISSING"]}
    _, receipt = max(candidates, key=lambda item: item[0])
    outcome = str(receipt.get("entry_outcome") or "").upper()
    blockers: list[str] = []
    if outcome not in ENTRY_OUTCOMES:
        blockers.append("ENTRY_OUTCOME_INVALID")
    terminal_ts = float(receipt.get("terminal_ts") or 0.0)
    horizon_complete_ts = float(receipt.get("horizon_complete_ts") or 0.0)
    minimum_horizon = terminal_ts + max(0.0, float(lifecycle_horizon_sec))
    if receipt.get("terminal") is not True:
        blockers.append("LIFECYCLE_NOT_TERMINAL")
    if receipt.get("entry_schedule_terminal") is not True:
        blockers.append("ENTRY_SCHEDULE_NOT_TERMINAL")
    if receipt.get("position_closed_or_never_opened") is not True:
        blockers.append("POSITION_NOT_CLOSED")
    if receipt.get("post_observation_complete") is not True:
        blockers.append("POST_OBSERVATION_INCOMPLETE")
    if terminal_ts <= 0:
        blockers.append("TERMINAL_TIMESTAMP_MISSING")
    if horizon_complete_ts < minimum_horizon:
        blockers.append("LIFECYCLE_HORIZON_INCOMPLETE")
    if now < horizon_complete_ts + max(0.0, float(reconciliation_allowance_sec)):
        blockers.append("RECONCILIATION_ALLOWANCE_ACTIVE")
    if outcome in {"FULL_FILL", "PARTIAL_FILL"}:
        for field, blocker in (
            ("exit_evidence_complete", "EXIT_EVIDENCE_INCOMPLETE"),
            ("costs_complete", "COST_EVIDENCE_INCOMPLETE"),
            ("mfe_mae_complete", "MFE_MAE_INCOMPLETE"),
            ("net_pnl_reconciled", "NET_PNL_UNRECONCILED"),
        ):
            if receipt.get(field) is not True:
                blockers.append(blocker)
    if outcome == "UNKNOWN" and receipt.get("unknown_reason") in (None, ""):
        blockers.append("UNKNOWN_REASON_MISSING")
    return {
        "ready": not blockers,
        "classification": outcome if outcome in ENTRY_OUTCOMES else "UNKNOWN",
        "terminal_ts": terminal_ts or None,
        "horizon_complete_ts": horizon_complete_ts or None,
        "blockers": sorted(set(blockers)),
    }


def classify_evidence_collection(
    rows: Iterable[dict[str, Any]], key: LifecycleKey,
) -> dict[str, Any]:
    """Require one hash-valid receipt bound to the selected completion."""
    material = list(rows)
    candidates = [
        row.get("evidence_collection_receipt") for row in material
        if isinstance(row.get("evidence_collection_receipt"), dict)
        and row["evidence_collection_receipt"].get("schema") == EVIDENCE_COLLECTED_SCHEMA
    ]
    if not candidates:
        return {"ready": False, "blockers": ["EVIDENCE_COLLECTION_RECEIPT_MISSING"]}
    if len(candidates) != 1:
        return {"ready": False, "blockers": ["EVIDENCE_COLLECTION_RECEIPT_AMBIGUOUS"]}
    receipt = dict(candidates[0])
    supplied = str(receipt.pop("evidence_collected_receipt_sha256", "")).lower()
    actual = hashlib.sha256(canonical_json(receipt).encode("utf-8")).hexdigest()
    blockers = []
    if supplied != actual:
        blockers.append("EVIDENCE_COLLECTION_RECEIPT_SHA256_MISMATCH")
    if float(receipt.get("evidence_collected_at") or 0) < float(receipt.get("qualification_eligible_at") or 0):
        blockers.append("EVIDENCE_COLLECTION_TOO_EARLY")
    completions = [
        row.get("bundle_completion") for row in material
        if isinstance(row.get("bundle_completion"), dict)
        and row["bundle_completion"].get("schema") == COMPLETION_SCHEMA
    ]
    if len(completions) != 1 or (
        receipt.get("completion_receipt_sha256")
        != completions[0].get("completion_receipt_sha256")
    ):
        blockers.append("EVIDENCE_COLLECTION_COMPLETION_BINDING_MISMATCH")
    if receipt.get("identity") != key.as_dict():
        blockers.append("EVIDENCE_COLLECTION_IDENTITY_MISMATCH")
    try:
        if receipt.get("provenance") != _consistent_provenance(material):
            blockers.append("EVIDENCE_COLLECTION_PROVENANCE_MISMATCH")
    except ValueError:
        blockers.append("EVIDENCE_COLLECTION_PROVENANCE_MISMATCH")
    return {
        "ready": not blockers,
        "blockers": sorted(set(blockers)),
        "receipt": candidates[0] if not blockers else None,
    }


def _referenced_market_segments(root: Path, rows: Iterable[dict[str, Any]]) -> list[Path]:
    paths: set[Path] = set()
    for row in rows:
        for field, value in row.items():
            field_name = str(field)
            if field_name.endswith("segment_refs") and isinstance(value, list):
                references = value
            elif field_name.endswith("segment_ref") and isinstance(value, dict):
                # Qualification-horizon writers publish one content-addressed
                # POST_EXIT_PATH.  Treat the singular reference exactly like
                # the existing entry/exit reference arrays so the immutable
                # bundle cannot omit the evidence that proved completion.
                references = [value]
            else:
                continue
            for ref in references:
                if not isinstance(ref, dict):
                    continue
                relative = str(ref.get("relative_path") or "")
                if not relative:
                    continue
                candidate = (root / relative).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError as exc:
                    raise ValueError("MARKET_SEGMENT_PATH_OUTSIDE_ROOT") from exc
                if not candidate.is_file():
                    raise ValueError(f"MARKET_SEGMENT_MISSING:{relative}")
                expected = str(ref.get("sha256") or "").lower()
                if len(expected) != 64 or _sha256_file(candidate) != expected:
                    raise ValueError(f"MARKET_SEGMENT_SHA256_MISMATCH:{relative}")
                paths.add(candidate)
    return sorted(paths)


def _file_receipt(
    path: Path, relative: str, *, role: str, row_count: int,
    first_timestamp: str, last_timestamp: str,
) -> dict[str, Any]:
    stat = os.stat(_io_path(path))
    return {
        "path": relative,
        "role": role,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "sha256": _sha256_file(path),
        "row_count": int(row_count),
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
    }


def _cleanup_manifest_sha256(files: Iterable[dict[str, Any]]) -> str:
    canonical = [{
        "path": str(row["path"]),
        "sha256": str(row["sha256"]).lower(),
        "size": int(row["size"]),
        "mtime_ns": int(row["mtime_ns"]),
        "row_count": int(row["row_count"]),
        "first_timestamp": str(row["first_timestamp"]),
        "last_timestamp": str(row["last_timestamp"]),
    } for row in sorted(files, key=lambda item: str(item["path"]))]
    return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()


def _consistent_provenance(rows: Iterable[dict[str, Any]]) -> dict[str, str]:
    fields = (
        "source_revision", "deployed_revision", "tile_config_signature",
        "config_signature",
    )
    material = list(rows)
    result: dict[str, str] = {}
    for field in fields:
        values = {str(row.get(field) or "").strip() for row in material}
        if len(values) != 1 or not all(_present(row.get(field)) for row in material):
            raise ValueError(f"LIFECYCLE_PROVENANCE_NOT_UNIQUE:{field}")
        result[field] = values.pop()
    return result


def _bundle_content_id(
    key: LifecycleKey, rows: Iterable[dict[str, Any]], completion: dict[str, Any],
    segments: Iterable[Path], *, prefix: str = "lifecycle-",
) -> str:
    material = {
        "identity": key.as_dict(),
        "completion": completion,
        "events_sha256": hashlib.sha256(
            "".join(canonical_json(row) + "\n" for row in rows).encode("utf-8")
        ).hexdigest(),
        "market_segments": [
            {"name": path.name, "sha256": _sha256_file(path)} for path in segments
        ],
    }
    return prefix + hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


def materialize_bundle(
    root: str | Path, key: LifecycleKey, rows: Iterable[dict[str, Any]], *,
    now: float | None = None, lifecycle_horizon_sec: float = 7200.0,
    reconciliation_allowance_sec: float = 180.0,
) -> dict[str, Any]:
    root = Path(root).resolve()
    frozen = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            str(row.get("ledger") or ""),
            float(row.get("observed_ts") or row.get("ts") or row.get("signal_ts") or 0.0),
            str(row.get("record_id") or row.get("event_id") or ""),
        ),
    )
    completion = classify_completion(
        frozen,
        now=now,
        lifecycle_horizon_sec=lifecycle_horizon_sec,
        reconciliation_allowance_sec=reconciliation_allowance_sec,
    )
    if not completion["ready"]:
        return {"written": False, "lifecycle_identity_id": key.identity_id, "completion": completion}
    provenance = _consistent_provenance(frozen)
    evidence_collection = classify_evidence_collection(frozen, key)
    if not evidence_collection["ready"]:
        return {
            "written": False,
            "lifecycle_identity_id": key.identity_id,
            "completion": completion,
            "evidence_collection": evidence_collection,
            "maturity": "QUALIFICATION_PENDING",
        }
    segments = _referenced_market_segments(root, frozen)
    bundle_id = _bundle_content_id(key, frozen, completion, segments)
    bundle_root = root / "v3" / "lifecycle_bundles"
    target = bundle_root / bundle_id[-64:-62] / bundle_id
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        verification = verify_bundle(target)
        if not verification["passed"]:
            raise ValueError("EXISTING_LIFECYCLE_BUNDLE_INVALID")
        return {"written": False, "duplicate": True, "bundle_id": bundle_id, "path": str(target), "manifest": verification["manifest"]}
    staging_root = bundle_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    # Keep transient paths short enough for Windows legacy MAX_PATH while the
    # final directory retains the complete content-bound lifecycle identity.
    temporary = staging_root / f"{os.getpid()}-{uuid.uuid4().hex[:16]}"
    try:
        temporary.mkdir()
        event_path = temporary / "events.jsonl"
        with event_path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in frozen:
                handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        timestamps = [
            float(value) for row in frozen
            for value in (row.get("observed_ts") or row.get("ts") or row.get("signal_ts"),)
            if _utc_iso(value)
        ]
        if not timestamps:
            raise ValueError("LIFECYCLE_EVENT_TIMESTAMPS_MISSING")
        receipts = [_file_receipt(
            event_path, "events.jsonl", role="LIFECYCLE_EVENTS",
            row_count=len(frozen), first_timestamp=_utc_iso(min(timestamps)),
            last_timestamp=_utc_iso(max(timestamps)),
        )]
        for source in segments:
            relative = source.relative_to(root).as_posix()
            destination = temporary / "market_segments" / source.stem[:2] / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if _sha256_file(destination) != _sha256_file(source):
                raise ValueError(f"MARKET_SEGMENT_COPY_SHA256_MISMATCH:{source.name}")
            envelope = json.loads(destination.read_text(encoding="utf-8"))
            segment_rows = envelope.get("rows") if isinstance(envelope, dict) else None
            start_iso = _utc_iso(envelope.get("start_ts") if isinstance(envelope, dict) else None)
            end_iso = _utc_iso(envelope.get("end_ts") if isinstance(envelope, dict) else None)
            if not isinstance(segment_rows, list) or not start_iso or not end_iso:
                raise ValueError(f"MARKET_SEGMENT_METADATA_INCOMPLETE:{source.name}")
            receipts.append(_file_receipt(
                destination, f"market_segments/{source.stem[:2]}/{source.name}", role="MARKET_SEGMENT",
                row_count=len(segment_rows), first_timestamp=start_iso,
                last_timestamp=end_iso,
            ))
            receipts[-1]["source_relative_path"] = relative
        manifest = {
            "schema": BUNDLE_SCHEMA,
            "bundle_id": bundle_id,
            "lifecycle_identity_id": key.identity_id,
            "lifecycle_id": "|".join((key.episode_id, key.policy_signature, key.research_lane)),
            "identity": key.as_dict(),
            "provenance": provenance,
            "maturity": "QUALIFICATION_READY",
            "completion": completion,
            "evidence_collection": evidence_collection,
            "files": sorted(receipts, key=lambda row: row["path"]),
            "source_cleanup_authorized": False,
        }
        manifest["cleanup_manifest_sha256"] = _cleanup_manifest_sha256(manifest["files"])
        manifest["manifest_sha256"] = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
        manifest_path = temporary / "manifest.json"
        with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(manifest) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_dir(temporary)
        os.replace(temporary, target)
        _fsync_dir(target.parent)
        verification = verify_bundle(target)
        if not verification["passed"]:
            raise ValueError("PUBLISHED_LIFECYCLE_BUNDLE_INVALID")
        return {"written": True, "duplicate": False, "bundle_id": bundle_id, "path": str(target), "manifest": verification["manifest"]}
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)


def _write_transfer_pointer(
    pointer_path: Path, key: LifecycleKey, bundle_id: str, manifest_sha256: str,
) -> None:
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer = {
        "schema": TRANSFER_POINTER_SCHEMA,
        "lifecycle_identity_id": key.identity_id,
        "bundle_id": bundle_id,
        "manifest_sha256": manifest_sha256,
        "qualification_ready": False,
        "profitability_supported": False,
        "ranking_eligible": False,
        "source_cleanup_authorized": False,
    }
    pointer["pointer_sha256"] = hashlib.sha256(
        canonical_json(pointer).encode("utf-8")
    ).hexdigest()
    temporary = pointer_path.parent / f".{key.identity_id}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(pointer) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, pointer_path)
        _fsync_dir(pointer_path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _validated_transfer_pointer(pointer_path: Path, key: LifecycleKey) -> dict[str, Any]:
    """Read only a hash-valid, non-qualifying pointer for this exact identity."""
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        supplied_sha = str(pointer.get("pointer_sha256") or "")
        material = dict(pointer)
        material.pop("pointer_sha256", None)
        actual_sha = hashlib.sha256(
            canonical_json(material).encode("utf-8")
        ).hexdigest()
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("TRANSFER_POINTER_INVALID") from exc
    bundle_id = str(pointer.get("bundle_id") or "")
    if not (
        pointer.get("schema") == TRANSFER_POINTER_SCHEMA
        and supplied_sha == actual_sha
        and pointer.get("lifecycle_identity_id") == key.identity_id
        and bundle_id.startswith("transfer-")
        and len(bundle_id) == len("transfer-") + 64
        and all(char in "0123456789abcdef" for char in bundle_id[-64:])
        and pointer.get("qualification_ready") is False
        and pointer.get("profitability_supported") is False
        and pointer.get("ranking_eligible") is False
        and pointer.get("source_cleanup_authorized") is False
        and isinstance(pointer.get("manifest_sha256"), str)
        and len(pointer["manifest_sha256"]) == 64
    ):
        raise ValueError("TRANSFER_POINTER_INVALID")
    return pointer


def _verified_transfer_pointer_bundle(
    candidate: Path, pointer: dict[str, Any], key: LifecycleKey,
) -> dict[str, Any]:
    """Verify content before a pointer target is accepted or recovered."""
    verification = verify_bundle(candidate)
    manifest = verification.get("manifest") or {}
    if not (
        verification.get("passed") is True
        and manifest.get("schema") == TRANSFER_BUNDLE_SCHEMA
        and manifest.get("bundle_id") == pointer.get("bundle_id")
        and manifest.get("lifecycle_identity_id") == key.identity_id
        and manifest.get("manifest_sha256") == pointer.get("manifest_sha256")
        and manifest.get("qualification_ready") is False
        and manifest.get("profitability_supported") is False
        and manifest.get("ranking_eligible") is False
        and manifest.get("source_cleanup_authorized") is False
    ):
        raise ValueError("TRANSFER_POINTER_INVALID")
    return verification


def materialize_transfer_bundle(
    root: str | Path, key: LifecycleKey, rows: Iterable[dict[str, Any]],
    transfer_assessment: dict[str, Any],
) -> dict[str, Any]:
    """Publish terminal-flat evidence without qualifying or authorizing cleanup."""
    root = Path(root).resolve()
    receipt = transfer_assessment.get("receipt")
    if transfer_assessment.get("ready") is not True or not isinstance(receipt, dict):
        return {
            "written": False,
            "lifecycle_identity_id": key.identity_id,
            "transfer": transfer_assessment,
        }
    if not (
        receipt.get("schema") == "lifecycle_bundle_transfer_ready_v1"
        and receipt.get("transfer_ready") is True
        and receipt.get("profitability_supported") is False
        and receipt.get("source_cleanup_authorized") is False
    ):
        raise ValueError("TRANSFER_RECEIPT_INVARIANT_FAILED")
    bundle_root = root / "v3" / "lifecycle_transfer_bundles"
    pointer_root = bundle_root / "index"
    pointer_path = pointer_root / f"{key.identity_id}.json"
    if pointer_path.exists():
        pointer = _validated_transfer_pointer(pointer_path, key)
        pointer_bundle_id = str(pointer["bundle_id"])
        pointer_target = bundle_root / pointer_bundle_id[-64:-62] / pointer_bundle_id
        recovered = False
        if not pointer_target.exists():
            # A hard crash may occur after the pointer fsync but before the
            # atomic directory publication.  The staging name is the content
            # ID, so verify the exact reserved bytes before completing the
            # interrupted rename.  Late ledger rows are deliberately ignored.
            staged = bundle_root / ".staging" / pointer_bundle_id
            verification = _verified_transfer_pointer_bundle(staged, pointer, key)
            pointer_target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, pointer_target)
            _fsync_dir(pointer_target.parent)
            recovered = True
        verification = _verified_transfer_pointer_bundle(
            pointer_target, pointer, key,
        )
        return {
            "written": False, "duplicate": True, "frozen": True,
            "recovered": recovered,
            "bundle_id": pointer_bundle_id, "path": str(pointer_target),
            "pointer_path": str(pointer_path),
            "manifest": verification["manifest"],
        }
    frozen = sorted(
        (dict(row) for row in rows),
        key=lambda row: (
            str(row.get("ledger") or ""),
            float(row.get("observed_ts") or row.get("ts") or row.get("signal_ts") or 0.0),
            str(row.get("record_id") or row.get("event_id") or ""),
        ),
    )
    segments = _referenced_market_segments(root, frozen)
    bundle_id = _bundle_content_id(
        key, frozen, receipt, segments, prefix="transfer-",
    )
    target = bundle_root / bundle_id[-64:-62] / bundle_id
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        verification = verify_bundle(target)
        if not verification["passed"]:
            raise ValueError("EXISTING_TRANSFER_BUNDLE_INVALID")
        _write_transfer_pointer(
            pointer_path, key, bundle_id,
            verification["manifest"]["manifest_sha256"],
        )
        return {
            "written": False, "duplicate": True, "bundle_id": bundle_id,
            "path": str(target), "pointer_path": str(pointer_path),
            "manifest": verification["manifest"],
        }
    staging_root = bundle_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    temporary = staging_root / bundle_id
    pointer_published = False
    try:
        if temporary.exists():
            raise ValueError("EXISTING_TRANSFER_STAGING_INVALID")
        temporary.mkdir()
        event_path = temporary / "events.jsonl"
        with event_path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in frozen:
                handle.write(canonical_json(row) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        timestamps = [
            float(value) for row in frozen
            for value in (row.get("observed_ts") or row.get("ts") or row.get("signal_ts"),)
            if _utc_iso(value)
        ]
        if not timestamps:
            raise ValueError("LIFECYCLE_EVENT_TIMESTAMPS_MISSING")
        files = [_file_receipt(
            event_path, "events.jsonl", role="TRANSFER_LIFECYCLE_EVENTS",
            row_count=len(frozen), first_timestamp=_utc_iso(min(timestamps)),
            last_timestamp=_utc_iso(max(timestamps)),
        )]
        for source in segments:
            relative = source.relative_to(root).as_posix()
            destination = temporary / "market_segments" / source.stem[:2] / source.name
            # Transfer staging paths can exceed Win32 MAX_PATH.  Extended-path
            # I/O also ensures the nested content-addressed parent exists before
            # copyfile publishes a segment into the verified staging bundle.
            os.makedirs(_io_path(destination.parent), exist_ok=True)
            shutil.copyfile(_io_path(source), _io_path(destination))
            if _sha256_file(destination) != _sha256_file(source):
                raise ValueError(f"MARKET_SEGMENT_COPY_SHA256_MISMATCH:{source.name}")
            with open(_io_path(destination), "r", encoding="utf-8") as handle:
                envelope = json.load(handle)
            segment_rows = envelope.get("rows") if isinstance(envelope, dict) else None
            start_iso = _utc_iso(envelope.get("start_ts") if isinstance(envelope, dict) else None)
            end_iso = _utc_iso(envelope.get("end_ts") if isinstance(envelope, dict) else None)
            if not isinstance(segment_rows, list) or not start_iso or not end_iso:
                raise ValueError(f"MARKET_SEGMENT_METADATA_INCOMPLETE:{source.name}")
            files.append(_file_receipt(
                destination, f"market_segments/{source.stem[:2]}/{source.name}",
                role="TRANSFER_MARKET_SEGMENT", row_count=len(segment_rows),
                first_timestamp=start_iso, last_timestamp=end_iso,
            ))
            files[-1]["source_relative_path"] = relative
        manifest = {
            "schema": TRANSFER_BUNDLE_SCHEMA,
            "bundle_id": bundle_id,
            "lifecycle_identity_id": key.identity_id,
            "lifecycle_id": "|".join((key.episode_id, key.policy_signature, key.research_lane)),
            "identity": key.as_dict(),
            "provenance": _consistent_provenance(frozen),
            "maturity": "TRANSFER_READY",
            "transfer_receipt": receipt,
            "qualification_ready": False,
            "qualification_blockers": list(
                transfer_assessment.get("qualification_blockers") or []
            ),
            "profitability_supported": False,
            "ranking_eligible": False,
            "files": sorted(files, key=lambda row: row["path"]),
            "source_cleanup_authorized": False,
        }
        manifest["cleanup_manifest_sha256"] = _cleanup_manifest_sha256(manifest["files"])
        manifest["manifest_sha256"] = hashlib.sha256(
            canonical_json(manifest).encode("utf-8")
        ).hexdigest()
        with (temporary / "manifest.json").open(
            "w", encoding="utf-8", newline="\n",
        ) as handle:
            handle.write(canonical_json(manifest) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_dir(temporary)
        staged_verification = verify_bundle(temporary)
        if not (
            staged_verification["passed"]
            and staged_verification["manifest"].get("bundle_id") == bundle_id
            and staged_verification["manifest"].get("lifecycle_identity_id")
            == key.identity_id
            and staged_verification["manifest"].get("manifest_sha256")
            == manifest["manifest_sha256"]
        ):
            raise ValueError("STAGED_TRANSFER_BUNDLE_INVALID")
        # Reserve the one immutable transfer snapshot before publishing its
        # directory.  The pipeline owns an exclusive materialization lock, so
        # readers cannot observe this short interval.  If the process crashes,
        # the pointer deliberately fails closed instead of allowing late rows
        # to create a second snapshot for the same lifecycle identity.
        _write_transfer_pointer(
            pointer_path, key, bundle_id, manifest["manifest_sha256"],
        )
        pointer_published = True
        os.replace(temporary, target)
        _fsync_dir(target.parent)
        verification = verify_bundle(target)
        if not verification["passed"]:
            raise ValueError("PUBLISHED_TRANSFER_BUNDLE_INVALID")
        return {
            "written": True, "duplicate": False, "bundle_id": bundle_id,
            "path": str(target), "pointer_path": str(pointer_path),
            "manifest": verification["manifest"],
        }
    finally:
        # Once the pointer is durable, this verified staging directory is the
        # only deterministic crash-recovery source.  Never delete it until the
        # atomic publication succeeds (at which point it no longer exists).
        if temporary.exists() and not pointer_published:
            shutil.rmtree(temporary, ignore_errors=True)


def verify_bundle(bundle_path: str | Path) -> dict[str, Any]:
    bundle = Path(bundle_path).resolve()
    manifest_path = bundle / "manifest.json"
    defects: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"passed": False, "defects": [f"MANIFEST_INVALID:{type(exc).__name__}"], "manifest": None}
    supplied_sha = str(manifest.pop("manifest_sha256", ""))
    actual_sha = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    manifest["manifest_sha256"] = supplied_sha
    if supplied_sha != actual_sha:
        defects.append("MANIFEST_SHA256_MISMATCH")
    try:
        key = LifecycleKey(**manifest["identity"])
        events = _read_jsonl(bundle / "events.jsonl")
        segment_paths = sorted((bundle / "market_segments").glob("*/*.json"))
        schema = manifest.get("schema")
        if schema == BUNDLE_SCHEMA:
            evidence = manifest.get("completion") or {}
            prefix = "lifecycle-"
            collection = manifest.get("evidence_collection")
            collection_receipt = (
                collection.get("receipt") if isinstance(collection, dict) else None
            )
            if not (
                manifest.get("maturity") == "QUALIFICATION_READY"
                and isinstance(collection, dict)
                and collection.get("ready") is True
                and isinstance(collection_receipt, dict)
                and collection_receipt.get("schema") == EVIDENCE_COLLECTED_SCHEMA
                and not collection.get("blockers")
            ):
                defects.append("QUALIFICATION_BUNDLE_MATURITY_INVALID")
        elif schema == TRANSFER_BUNDLE_SCHEMA:
            evidence = manifest.get("transfer_receipt") or {}
            prefix = "transfer-"
            if not (
                manifest.get("maturity") == "TRANSFER_READY"
                and manifest.get("qualification_ready") is False
                and manifest.get("profitability_supported") is False
                and manifest.get("ranking_eligible") is False
                and manifest.get("source_cleanup_authorized") is False
                and "completion" not in manifest
            ):
                defects.append("TRANSFER_BUNDLE_ISOLATION_INVALID")
        else:
            evidence = {}
            prefix = "invalid-"
            defects.append("BUNDLE_SCHEMA_INVALID")
        expected_id = _bundle_content_id(
            key, events, evidence, segment_paths, prefix=prefix,
        )
        if (
            manifest.get("bundle_id") != expected_id or bundle.name != expected_id
            or manifest.get("lifecycle_identity_id") != key.identity_id
        ):
            defects.append("BUNDLE_IDENTITY_MISMATCH")
    except (KeyError, TypeError, ValueError):
        defects.append("BUNDLE_IDENTITY_INVALID")
    for receipt in manifest.get("files") or []:
        relative = str(receipt.get("path") or "")
        candidate = (bundle / relative).resolve()
        try:
            candidate.relative_to(bundle)
        except ValueError:
            defects.append(f"FILE_PATH_OUTSIDE_BUNDLE:{relative}")
            continue
        io_candidate = _io_path(candidate)
        if not os.path.isfile(io_candidate):
            defects.append(f"FILE_MISSING:{relative}")
            continue
        if os.stat(io_candidate).st_size != int(receipt.get("size") or -1):
            defects.append(f"FILE_SIZE_MISMATCH:{relative}")
        if _sha256_file(candidate) != str(receipt.get("sha256") or ""):
            defects.append(f"FILE_SHA256_MISMATCH:{relative}")
        if receipt.get("row_count") is not None:
            if receipt.get("role") in ("MARKET_SEGMENT", "TRANSFER_MARKET_SEGMENT"):
                try:
                    with open(io_candidate, "r", encoding="utf-8") as handle:
                        payload = json.load(handle)
                    rows = len(payload.get("rows")) if isinstance(payload.get("rows"), list) else -1
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    rows = -1
            else:
                with open(io_candidate, "rb") as handle:
                    rows = sum(1 for line in handle if line.endswith(b"\n"))
            if rows != int(receipt["row_count"]):
                defects.append(f"FILE_ROW_COUNT_MISMATCH:{relative}")
    return {"passed": not defects, "defects": sorted(set(defects)), "manifest": manifest}


def materialize_ready_bundles(
    root: str | Path, *, now: float | None = None, max_bundles: int = 25,
    max_scan_bytes: int = DEFAULT_MAX_SCAN_BYTES,
    max_scan_rows: int = DEFAULT_MAX_SCAN_ROWS,
    max_runtime_sec: float = 60.0,
) -> dict[str, Any]:
    """Incrementally index append-only ledgers and inspect only dirty lives.

    The durable SQLite cursor and indexed events share transactions.  Late
    events mark their lifecycle dirty again and therefore create a superseding
    content-addressed bundle.  Source ledgers and prior bundles are untouched.
    """
    root = Path(root).resolve()
    bundle_limit = max(1, min(int(max_bundles), 100))
    byte_limit = max(1, min(int(max_scan_bytes), 64 * 1024 * 1024))
    row_limit = max(1, min(int(max_scan_rows), 100_000))
    runtime_limit = max(1.0, min(float(max_runtime_sec), 300.0))
    started = time.monotonic()
    remaining_bytes = byte_limit
    remaining_rows = row_limit
    scan_receipts: dict[str, Any] = {}
    results = []
    with _exclusive_index_lock(root):
        connection = _open_incremental_index(root)
        try:
            ledger_dir = root / "v3" / "ledgers"
            next_ledger = int(connection.execute(
                "SELECT next_ledger FROM index_meta WHERE singleton = 1"
            ).fetchone()[0]) % len(LEDGER_NAMES)
            ledger_order = tuple(LEDGER_NAMES[next_ledger:]) + tuple(LEDGER_NAMES[:next_ledger])
            for ledger in ledger_order:
                path = ledger_dir / f"{ledger}.jsonl"
                if not path.exists():
                    continue
                if remaining_bytes <= 0 or remaining_rows <= 0 or time.monotonic() - started >= runtime_limit:
                    break
                receipt = _index_ledger_chunk(
                    connection, path, ledger,
                    max_bytes=remaining_bytes, max_rows=remaining_rows,
                )
                scan_receipts[ledger] = receipt
                remaining_bytes -= int(receipt["bytes_indexed"])
                remaining_rows -= int(receipt["rows_scanned"])
                with connection:
                    connection.execute(
                        "UPDATE index_meta SET next_ledger = ? WHERE singleton = 1",
                        ((LEDGER_NAMES.index(ledger) + 1) % len(LEDGER_NAMES),),
                    )
            dirty = _dirty_lifecycle_rows(connection, root, maximum=bundle_limit)
            for key, rows in dirty:
                if time.monotonic() - started >= runtime_limit:
                    break
                result = materialize_bundle(root, key, rows, now=now)
                # Evaluation is complete for this exact indexed event set even if
                # it is not mature. Any later append re-dirties the lifecycle.
                _clear_dirty(connection, key)
                if result.get("written") or result.get("duplicate"):
                    results.append(result)
        finally:
            pending_dirty = int(connection.execute(
                "SELECT COUNT(*) FROM dirty_lifecycle"
            ).fetchone()[0])
            connection.close()
    return {
        "schema": "lifecycle_bundle_materialization_result_v1",
        "index_schema": INDEX_SCHEMA,
        "candidate_count": len(dirty),
        "materialized_or_verified": len(results),
        "bundles": results,
        "scan": {
            "ledgers": scan_receipts,
            "bytes_indexed": byte_limit - remaining_bytes,
            "rows_scanned": row_limit - remaining_rows,
            "byte_limit": byte_limit,
            "row_limit": row_limit,
            "runtime_limit_sec": runtime_limit,
            "pending_dirty_lifecycles": pending_dirty,
            "bounded": True,
        },
        "source_cleanup_authorized": False,
    }
