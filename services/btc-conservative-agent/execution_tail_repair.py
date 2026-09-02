"""Exact fail-closed repair for the proven execution.jsonl incomplete tail."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from lifecycle_bundles import _exclusive_index_lock, _source_anchor
from lifecycle_tail_repair import _atomic_bytes, _atomic_json, _validate_jsonl, _write_once_json
from research_v3_store import V3EvidenceStore, _fsync_directory

SOURCE_SIZE = 266_240
SOURCE_SHA256 = "a1d83c507265143f0cd4c37ab065c85985234cae6cf10b046d185b842c349250"
PREFIX_SIZE = 265_886
PREFIX_SHA256 = "2614f49656ae19f8ca3b1c09652ee27cf9d016cd2a58f63092df68894f632b34"
TAIL_SIZE = 354
TAIL_SHA256 = "586328be5ab061de70ff01568023d7d3c5eaeca87a86563ceb7805be055b2b61"
OLD_DEV = 65056
OLD_INO = 663
OLD_MTIME_NS = 1788303277103206804
CURSOR_ANCHOR_SHA256 = "f0eb3ff1db39a0d2908c3618f691aa8ad645cf1ba7e4340fd4a3f3517ba7245a"
SCHEMA = "execution_incomplete_tail_repair_v1"
REPAIR_ID = f"execution-tail-{SOURCE_SHA256[:16]}"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _target(root: str | Path) -> tuple[Path, Path, Path]:
    root = Path(root).resolve(strict=True)
    lexical = Path(os.path.abspath(root / "v3" / "ledgers" / "execution.jsonl"))
    target = lexical.resolve(strict=True)
    if lexical != target or lexical.is_symlink() or not target.is_file():
        raise ValueError("EXECUTION_REPAIR_TARGET_LINKED_OR_INVALID")
    if target.parent != (root / "v3" / "ledgers").resolve(strict=True):
        raise ValueError("EXECUTION_REPAIR_TARGET_OUTSIDE_LEDGER_ROOT")
    db_candidate = root / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    if db_candidate.is_symlink():
        raise ValueError("EXECUTION_REPAIR_DATABASE_SYMLINK_REFUSED")
    database = db_candidate.resolve(strict=True)
    if root not in database.parents:
        raise ValueError("EXECUTION_REPAIR_DATABASE_ESCAPE_REFUSED")
    return root, target, database


def _json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes(); value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"EXECUTION_REPAIR_NON_OBJECT:{path.name}")
    return value, raw


def _artifact(path: Path, size: int, digest: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"EXECUTION_REPAIR_ARTIFACT_MISSING:{path.name}")
    raw = path.read_bytes()
    if len(raw) != size or _sha(raw) != digest:
        raise ValueError(f"EXECUTION_REPAIR_ARTIFACT_TAMPERED:{path.name}")
    return raw


def _fsync_file(path: Path) -> None:
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def _cursor(connection: sqlite3.Connection) -> sqlite3.Row:
    connection.row_factory = sqlite3.Row
    row = connection.execute("SELECT * FROM ledger_cursor WHERE ledger=?", ("execution",)).fetchone()
    if row is None:
        raise ValueError("EXECUTION_REPAIR_CURSOR_MISSING")
    return row


def repair_execution_tail(
    root: str | Path, *, expected_source_size: int = SOURCE_SIZE,
    expected_source_sha256: str = SOURCE_SHA256, expected_prefix_size: int = PREFIX_SIZE,
    expected_prefix_sha256: str = PREFIX_SHA256, expected_tail_size: int = TAIL_SIZE,
    expected_tail_sha256: str = TAIL_SHA256, expected_inode: int = OLD_INO,
    expected_mtime_ns: int = OLD_MTIME_NS,
) -> dict[str, Any]:
    supplied = (expected_source_size, expected_source_sha256, expected_prefix_size,
                expected_prefix_sha256, expected_tail_size, expected_tail_sha256,
                expected_inode, expected_mtime_ns)
    compiled = (SOURCE_SIZE, SOURCE_SHA256, PREFIX_SIZE, PREFIX_SHA256, TAIL_SIZE,
                TAIL_SHA256, OLD_INO, OLD_MTIME_NS)
    if supplied != compiled or SOURCE_SIZE != PREFIX_SIZE + TAIL_SIZE:
        raise ValueError("EXECUTION_REPAIR_EXPECTATION_MISMATCH")
    root, target, database = _target(root)
    quarantine_root = target.parent / "corrupt_evidence_quarantine"
    quarantine = quarantine_root / REPAIR_ID
    original_path = quarantine / "execution.jsonl.original"
    tail_path = quarantine / "execution.jsonl.incomplete-tail"
    store = V3EvidenceStore(root, epoch_id="execution-tail-repair")

    with _exclusive_index_lock(root):
      with store._exclusive(target):
        before_stat = target.stat(); active = target.read_bytes(); stat = target.stat(); active_sha = _sha(active)
        if (int(before_stat.st_dev), int(before_stat.st_ino), int(before_stat.st_size), int(before_stat.st_mtime_ns)) != (
            int(stat.st_dev), int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns)):
            raise ValueError("EXECUTION_REPAIR_SOURCE_CHANGED_DURING_PROOF")
        source_generation = active_sha == SOURCE_SHA256 and len(active) == SOURCE_SIZE
        prefix_generation = len(active) >= PREFIX_SIZE and _sha(active[:PREFIX_SIZE]) == PREFIX_SHA256
        if source_generation:
            if (int(stat.st_dev), int(stat.st_ino), int(stat.st_mtime_ns)) != (OLD_DEV, OLD_INO, OLD_MTIME_NS):
                raise ValueError("EXECUTION_REPAIR_SOURCE_IDENTITY_MISMATCH")
            prefix, tail = active[:PREFIX_SIZE], active[PREFIX_SIZE:]
            if _sha(prefix) != PREFIX_SHA256 or _sha(tail) != TAIL_SHA256:
                raise ValueError("EXECUTION_REPAIR_SOURCE_PARTITION_MISMATCH")
            try:
                tail_value = json.loads(tail.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                tail_value = None
            if isinstance(tail_value, dict):
                raise ValueError("EXECUTION_REPAIR_TAIL_IS_COMPLETE_JSON")
        elif prefix_generation:
            if not quarantine.is_dir():
                raise ValueError("EXECUTION_REPAIR_REPLAY_RECEIPT_MISSING")
            prefix = active[:PREFIX_SIZE]
            _validate_jsonl(active)
            _artifact(original_path, SOURCE_SIZE, SOURCE_SHA256)
            tail = _artifact(tail_path, TAIL_SIZE, TAIL_SHA256)
        else:
            raise ValueError("EXECUTION_REPAIR_SOURCE_SHA256_MISMATCH")
        rows = _validate_jsonl(prefix)
        if _source_anchor(target, PREFIX_SIZE) != CURSOR_ANCHOR_SHA256:
            raise ValueError("EXECUTION_REPAIR_ACTIVE_ANCHOR_MISMATCH")

        if not quarantine.exists():
            quarantine_root.mkdir(parents=True, exist_ok=True)
            staging = quarantine_root / f".{REPAIR_ID}.{uuid.uuid4().hex[:8]}.tmp"
            staging.mkdir()
            try:
                _atomic_bytes(staging / original_path.name, prefix + tail)
                _atomic_bytes(staging / tail_path.name, tail)
                excluded = {"schema": SCHEMA, "classification": "UNKNOWN",
                    "ranking_eligible": False, "profitability_supported": False,
                    "reason": "INCOMPLETE_JSONL_TAIL_EXCLUDED", "tail_size": TAIL_SIZE,
                    "tail_sha256": TAIL_SHA256, "source_sha256": SOURCE_SHA256}
                _atomic_json(staging / "excluded_unknown.json", excluded)
                manifest = {"schema": SCHEMA, "repair_id": REPAIR_ID,
                    "target": "v3/ledgers/execution.jsonl",
                    "source": {"size": SOURCE_SIZE, "sha256": SOURCE_SHA256},
                    "source_stat": {"dev": OLD_DEV, "inode": OLD_INO, "mtime_ns": OLD_MTIME_NS},
                    "complete_prefix": {"size": PREFIX_SIZE, "sha256": PREFIX_SHA256},
                    "excluded_tail": {"size": TAIL_SIZE, "sha256": TAIL_SHA256},
                    "cursor": {"source_dev": OLD_DEV, "source_ino": OLD_INO,
                        "offset": PREFIX_SIZE, "anchor_sha256": CURSOR_ANCHOR_SHA256},
                    "artifacts": {original_path.name: SOURCE_SHA256, tail_path.name: TAIL_SHA256,
                        "excluded_unknown.json": _sha((staging / "excluded_unknown.json").read_bytes())}}
                _atomic_json(staging / "manifest.json", manifest)
                os.replace(staging, quarantine); _fsync_directory(quarantine_root)
            finally:
                if staging.exists(): shutil.rmtree(staging)
        _artifact(original_path, SOURCE_SIZE, SOURCE_SHA256)
        _artifact(tail_path, TAIL_SIZE, TAIL_SHA256)
        manifest, manifest_raw = _json(quarantine / "manifest.json")
        excluded, excluded_raw = _json(quarantine / "excluded_unknown.json")
        if (manifest.get("schema") != SCHEMA or manifest.get("repair_id") != REPAIR_ID
            or manifest.get("source") != {"size": SOURCE_SIZE, "sha256": SOURCE_SHA256}
            or manifest.get("complete_prefix") != {"size": PREFIX_SIZE, "sha256": PREFIX_SHA256}
            or manifest.get("excluded_tail") != {"size": TAIL_SIZE, "sha256": TAIL_SHA256}
            or manifest.get("target") != "v3/ledgers/execution.jsonl"
            or manifest.get("cursor") != {"source_dev": OLD_DEV, "source_ino": OLD_INO,
                "offset": PREFIX_SIZE, "anchor_sha256": CURSOR_ANCHOR_SHA256}
            or manifest.get("source_stat") != {"dev": OLD_DEV, "inode": OLD_INO, "mtime_ns": OLD_MTIME_NS}
            or manifest.get("artifacts") != {original_path.name: SOURCE_SHA256,
                tail_path.name: TAIL_SHA256, "excluded_unknown.json": _sha(excluded_raw)}
            or excluded.get("schema") != SCHEMA or excluded.get("classification") != "UNKNOWN"
            or excluded.get("ranking_eligible") is not False or excluded.get("profitability_supported") is not False
            or excluded.get("reason") != "INCOMPLETE_JSONL_TAIL_EXCLUDED"
            or excluded.get("tail_size") != TAIL_SIZE or excluded.get("tail_sha256") != TAIL_SHA256
            or excluded.get("source_sha256") != SOURCE_SHA256):
            raise ValueError("EXECUTION_REPAIR_QUARANTINE_METADATA_TAMPERED")

        connection = sqlite3.connect(str(database), timeout=5.0)
        try:
            connection.execute("PRAGMA synchronous=FULL"); connection.execute("PRAGMA journal_mode=WAL")
            row = _cursor(connection)
            current_identity = (int(row["source_dev"]), int(row["source_ino"]))
            current_offset = int(row["byte_offset"])
            current_anchor = str(row["source_anchor_sha256"])
            old_cursor = current_identity == (OLD_DEV, OLD_INO) and current_offset == PREFIX_SIZE and current_anchor == CURSOR_ANCHOR_SHA256
            active_identity = (int(stat.st_dev), int(stat.st_ino))
            current_cursor = current_identity == active_identity and PREFIX_SIZE <= current_offset <= len(active) and _source_anchor(target, current_offset) == current_anchor
            if not old_cursor and not current_cursor:
                raise ValueError("EXECUTION_REPAIR_CURSOR_BOUNDARY_MISMATCH")
            if source_generation and not old_cursor:
                raise ValueError("EXECUTION_REPAIR_OLD_CURSOR_IDENTITY_MISMATCH")
            if source_generation:
                _atomic_bytes(target, prefix)
            new_stat = target.stat(); rebuilt = target.read_bytes()
            if len(rebuilt) < PREFIX_SIZE or _sha(rebuilt[:PREFIX_SIZE]) != PREFIX_SHA256 or not rebuilt.endswith(b"\n"):
                raise ValueError("EXECUTION_REPAIR_ATOMIC_REPLACE_VALIDATION_FAILED")
            if _source_anchor(target, PREFIX_SIZE) != CURSOR_ANCHOR_SHA256:
                raise ValueError("EXECUTION_REPAIR_REBUILT_ANCHOR_MISMATCH")
            new_identity = (int(new_stat.st_dev), int(new_stat.st_ino))
            if current_identity not in {(OLD_DEV, OLD_INO), new_identity}:
                raise ValueError("EXECUTION_REPAIR_REPLAY_CURSOR_IDENTITY_MISMATCH")

            validation = {"schema": SCHEMA, "status": "VALIDATED", "repair_id": REPAIR_ID,
                "active_prefix_size": PREFIX_SIZE, "active_prefix_sha256": PREFIX_SHA256,
                "valid_prefix_jsonl_lines": rows, "invalid_prefix_jsonl_lines": 0,
                "cursor_identity": {"dev": new_identity[0], "ino": new_identity[1]},
                "cursor_boundary_offset": PREFIX_SIZE, "cursor_boundary_anchor_sha256": CURSOR_ANCHOR_SHA256,
                "source_preserved": True, "tail_preserved": True, "source_cleanup_authorized": False}
            _write_once_json(quarantine / "validation.json", validation, "EXECUTION_REPAIR_VALIDATION_TAMPERED")
            validation_raw = (quarantine / "validation.json").read_bytes()

            payload = {"schema": SCHEMA, "repair_id": REPAIR_ID, "status": "REPAIRED",
                "source_sha256": SOURCE_SHA256, "prefix_sha256": PREFIX_SHA256,
                "tail_sha256": TAIL_SHA256, "valid_jsonl_rows": rows,
                "old_cursor_identity": {"dev": OLD_DEV, "ino": OLD_INO},
                "new_cursor_identity": {"dev": new_identity[0], "ino": new_identity[1]},
                "cursor_offset": PREFIX_SIZE, "cursor_anchor_sha256": CURSOR_ANCHOR_SHA256,
                "byte_offset_preserved": True, "anchor_preserved": True,
                "excluded_classification": "UNKNOWN", "ranking_eligible": False,
                "profitability_supported": False, "source_cleanup_authorized": False,
                "manifest_sha256": _sha(manifest_raw), "excluded_unknown_sha256": _sha(excluded_raw),
                "validation_sha256": _sha(validation_raw)}
            payload["receipt_sha256"] = _sha(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
            payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""CREATE TABLE IF NOT EXISTS ledger_tail_repair_receipt (
                repair_id TEXT PRIMARY KEY, schema TEXT NOT NULL, payload_json TEXT NOT NULL)""")
            marker = connection.execute("SELECT payload_json FROM ledger_tail_repair_receipt WHERE repair_id=?", (REPAIR_ID,)).fetchone()
            if marker is None:
                changed = connection.execute("""UPDATE ledger_cursor SET source_dev=?, source_ino=?, source_mtime_ns=?
                    WHERE ledger='execution' AND source_dev=? AND source_ino=? AND byte_offset=? AND source_anchor_sha256=?""",
                    (new_identity[0], new_identity[1], int(new_stat.st_mtime_ns),
                     current_identity[0], current_identity[1], PREFIX_SIZE, CURSOR_ANCHOR_SHA256))
                if changed.rowcount != 1:
                    connection.rollback(); raise ValueError("EXECUTION_REPAIR_CURSOR_UPDATE_CONFLICT")
                connection.execute("INSERT INTO ledger_tail_repair_receipt VALUES (?,?,?)", (REPAIR_ID, SCHEMA, payload_json))
            else:
                existing = json.loads(str(marker[0]))
                if existing != payload:
                    connection.rollback(); raise ValueError("EXECUTION_REPAIR_DATABASE_MARKER_TAMPERED")
            connection.commit()
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is None or int(checkpoint[0]) != 0:
                raise ValueError("EXECUTION_REPAIR_WAL_CHECKPOINT_BUSY")
            _fsync_file(database); _fsync_directory(database.parent)
            _write_once_json(quarantine / "repair_receipt.json", payload, "EXECUTION_REPAIR_RECEIPT_TAMPERED")
            return payload
        finally:
            connection.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(); p.add_argument("--root", required=True)
    p.add_argument("--expected-source-size", required=True, type=int); p.add_argument("--expected-source-sha256", required=True)
    p.add_argument("--expected-prefix-size", required=True, type=int); p.add_argument("--expected-prefix-sha256", required=True)
    p.add_argument("--expected-tail-size", required=True, type=int); p.add_argument("--expected-tail-sha256", required=True)
    p.add_argument("--expected-inode", required=True, type=int); p.add_argument("--expected-mtime-ns", required=True, type=int)
    a = p.parse_args(argv)
    try:
        result = repair_execution_tail(a.root, expected_source_size=a.expected_source_size,
            expected_source_sha256=a.expected_source_sha256, expected_prefix_size=a.expected_prefix_size,
            expected_prefix_sha256=a.expected_prefix_sha256, expected_tail_size=a.expected_tail_size,
            expected_tail_sha256=a.expected_tail_sha256, expected_inode=a.expected_inode,
            expected_mtime_ns=a.expected_mtime_ns)
    except (OSError, sqlite3.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)})); return 2
    print(json.dumps({"ok": True, "receipt": result}, separators=(",", ":"), sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
