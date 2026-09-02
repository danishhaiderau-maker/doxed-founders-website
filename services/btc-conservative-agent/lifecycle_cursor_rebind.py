"""One-off, fail-closed cursor rebind for the proven lifecycle tail repair.

This is deliberately incident-specific.  It cannot be used for a generic
SOURCE_LEDGER_ROTATED failure: the old and new identities, repaired prefix,
cursor offset, cursor anchor, and immutable tail-repair receipt are all fenced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from lifecycle_bundles import _exclusive_index_lock, _source_anchor
from lifecycle_tail_repair import (
    PREFIX_SHA256, PREFIX_SIZE, SOURCE_SHA256, SOURCE_SIZE, TAIL_SHA256,
    TAIL_SIZE, _target, _write_once_json,
)
from research_v3_store import V3EvidenceStore, _fsync_directory

SCHEMA = "lifecycle_cursor_identity_rebind_v1"
REPAIR_ID = f"lifecycle-tail-{SOURCE_SHA256[:16]}"
OLD_DEV = 65056
OLD_INO = 607
NEW_DEV = 65056
NEW_INO = 1637
CURSOR_OFFSET = PREFIX_SIZE
CURSOR_ANCHOR_SHA256 = "2ae7b77a086cc542cc91ecffcdec3a071eb8e62897ed0ba39e60d00fbeab331d"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"CURSOR_REBIND_NON_OBJECT:{path.name}")
    return value, raw


def _hash_prefix(path: Path, size: int) -> str:
    digest = hashlib.sha256()
    remaining = int(size)
    with path.open("rb") as handle:
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError("CURSOR_REBIND_ACTIVE_PREFIX_TRUNCATED")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def _cursor(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "ledger": str(row["ledger"]),
        "source_dev": int(row["source_dev"]),
        "source_ino": int(row["source_ino"]),
        "byte_offset": int(row["byte_offset"]),
        "source_anchor_sha256": str(row["source_anchor_sha256"]),
        "source_mtime_ns": int(row["source_mtime_ns"]),
    }


def _verify_tail_receipt(quarantine: Path) -> tuple[dict[str, Any], str]:
    required = (
        "manifest.json", "excluded_unknown.json", "validation.json",
        "repair_receipt.json", "lifecycle.jsonl.original",
        "lifecycle.jsonl.incomplete-tail",
    )
    if quarantine.is_symlink() or not quarantine.is_dir():
        raise ValueError("CURSOR_REBIND_QUARANTINE_INVALID")
    if any((quarantine / name).is_symlink() or not (quarantine / name).is_file() for name in required):
        raise ValueError("CURSOR_REBIND_QUARANTINE_ARTIFACT_INVALID")
    manifest, manifest_raw = _json(quarantine / "manifest.json")
    excluded, excluded_raw = _json(quarantine / "excluded_unknown.json")
    validation, validation_raw = _json(quarantine / "validation.json")
    receipt, receipt_raw = _json(quarantine / "repair_receipt.json")
    original_raw = (quarantine / "lifecycle.jsonl.original").read_bytes()
    tail_raw = (quarantine / "lifecycle.jsonl.incomplete-tail").read_bytes()
    material = dict(receipt)
    claimed = material.pop("receipt_sha256", None)
    if (
        manifest.get("schema") != "lifecycle_incomplete_tail_repair_v1"
        or manifest.get("repair_id") != REPAIR_ID
        or manifest.get("target") != "v3/ledgers/lifecycle.jsonl"
        or manifest.get("source") != {"size": SOURCE_SIZE, "sha256": SOURCE_SHA256}
        or manifest.get("complete_prefix") != {"size": PREFIX_SIZE, "sha256": PREFIX_SHA256}
        or manifest.get("excluded_tail") != {"size": TAIL_SIZE, "sha256": TAIL_SHA256}
        or int((manifest.get("source_stat") or {}).get("inode") or 0) != OLD_INO
        or len(original_raw) != SOURCE_SIZE or _sha(original_raw) != SOURCE_SHA256
        or len(tail_raw) != TAIL_SIZE or _sha(tail_raw) != TAIL_SHA256
        or excluded.get("classification") != "UNKNOWN"
        or excluded.get("ranking_eligible") is not False
        or excluded.get("profitability_supported") is not False
        or excluded.get("tail_size") != TAIL_SIZE or excluded.get("tail_sha256") != TAIL_SHA256
        or validation.get("status") != "VALIDATED"
        or validation.get("active_size") != PREFIX_SIZE
        or validation.get("active_sha256") != PREFIX_SHA256
        or validation.get("invalid_jsonl_lines") != 0
        or validation.get("source_cleanup_authorized") is not False
        or (manifest.get("artifacts") or {}).get("lifecycle.jsonl.original") != SOURCE_SHA256
        or (manifest.get("artifacts") or {}).get("lifecycle.jsonl.incomplete-tail") != TAIL_SHA256
        or (manifest.get("artifacts") or {}).get("excluded_unknown.json") != _sha(excluded_raw)
        or receipt.get("repair_id") != REPAIR_ID
        or receipt.get("status") != "REPAIRED"
        or receipt.get("source_sha256") != SOURCE_SHA256
        or receipt.get("prefix_sha256") != PREFIX_SHA256
        or receipt.get("tail_sha256") != TAIL_SHA256
        or receipt.get("excluded_classification") != "UNKNOWN"
        or receipt.get("ranking_eligible") is not False
        or receipt.get("source_cleanup_authorized") is not False
        or receipt.get("manifest_sha256") != _sha(manifest_raw)
        or receipt.get("excluded_unknown_sha256") != _sha(excluded_raw)
        or receipt.get("validation_sha256") != _sha(validation_raw)
        or claimed != _sha(json.dumps(material, separators=(",", ":"), sort_keys=True).encode())
    ):
        raise ValueError("CURSOR_REBIND_TAIL_REPAIR_RECEIPT_MISMATCH")
    return receipt, _sha(receipt_raw)


def _fsync_file(path: Path) -> None:
    # Windows rejects fsync on a read-only descriptor; r+b is also valid on
    # Linux and does not mutate the file.
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def rebind_lifecycle_cursor(
    root: str | Path, *, expected_old_dev: int = OLD_DEV,
    expected_old_ino: int = OLD_INO, expected_new_dev: int = NEW_DEV,
    expected_new_ino: int = NEW_INO, expected_offset: int = CURSOR_OFFSET,
    expected_anchor_sha256: str = CURSOR_ANCHOR_SHA256,
) -> dict[str, Any]:
    compiled = (OLD_DEV, OLD_INO, NEW_DEV, NEW_INO, CURSOR_OFFSET, CURSOR_ANCHOR_SHA256)
    supplied = (expected_old_dev, expected_old_ino, expected_new_dev, expected_new_ino,
                expected_offset, expected_anchor_sha256)
    if supplied != compiled:
        raise ValueError("CURSOR_REBIND_EXPECTATION_MISMATCH")

    root, ledger = _target(root)
    database_candidate = root / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    if database_candidate.is_symlink():
        raise ValueError("CURSOR_REBIND_DATABASE_SYMLINK_REFUSED")
    database = database_candidate.resolve(strict=True)
    if root not in database.parents:
        raise ValueError("CURSOR_REBIND_DATABASE_ESCAPE_REFUSED")
    quarantine = ledger.parent / "corrupt_evidence_quarantine" / REPAIR_ID
    receipt_path = quarantine / "cursor_rebind_receipt.json"
    intent_path = quarantine / "cursor_rebind_intent.json"
    _tail_receipt, tail_receipt_file_sha = _verify_tail_receipt(quarantine)
    store = V3EvidenceStore(root, epoch_id="lifecycle-cursor-rebind")

    with _exclusive_index_lock(root):
        with store._exclusive(ledger):
            before_stat = ledger.stat()
            if (int(before_stat.st_dev), int(before_stat.st_ino)) != (NEW_DEV, NEW_INO):
                raise ValueError("CURSOR_REBIND_ACTIVE_IDENTITY_MISMATCH")
            if int(before_stat.st_size) < PREFIX_SIZE:
                raise ValueError("CURSOR_REBIND_ACTIVE_PREFIX_TRUNCATED")
            if _hash_prefix(ledger, PREFIX_SIZE) != PREFIX_SHA256:
                raise ValueError("CURSOR_REBIND_ACTIVE_PREFIX_HASH_MISMATCH")
            if _source_anchor(ledger, CURSOR_OFFSET) != CURSOR_ANCHOR_SHA256:
                raise ValueError("CURSOR_REBIND_ACTIVE_ANCHOR_MISMATCH")
            after_proof_stat = ledger.stat()
            if (
                int(after_proof_stat.st_dev) != int(before_stat.st_dev)
                or int(after_proof_stat.st_ino) != int(before_stat.st_ino)
                or int(after_proof_stat.st_size) != int(before_stat.st_size)
                or int(after_proof_stat.st_mtime_ns) != int(before_stat.st_mtime_ns)
            ):
                raise ValueError("CURSOR_REBIND_ACTIVE_CHANGED_DURING_PROOF")

            connection = sqlite3.connect(str(database), timeout=5.0)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("PRAGMA journal_mode=WAL")
                marker_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    ("lifecycle_cursor_repair_receipt",),
                ).fetchone()
                marker = None if marker_table is None else connection.execute(
                    "SELECT payload_json FROM lifecycle_cursor_repair_receipt WHERE repair_id = ?", (REPAIR_ID,),
                ).fetchone()
                row = connection.execute(
                    "SELECT * FROM ledger_cursor WHERE ledger = ?", ("lifecycle",),
                ).fetchone()
                if row is None:
                    raise ValueError("CURSOR_REBIND_CURSOR_MISSING")
                current = _cursor(row)

                if marker is not None:
                    payload = json.loads(str(marker["payload_json"]))
                    material = dict(payload) if isinstance(payload, dict) else {}
                    claimed = material.pop("receipt_sha256", None)
                    if (
                        not isinstance(payload, dict) or payload.get("schema") != SCHEMA
                        or payload.get("tail_repair_receipt_file_sha256") != tail_receipt_file_sha
                        or claimed != _sha(json.dumps(material, separators=(",", ":"), sort_keys=True).encode())
                        or current["source_dev"] != NEW_DEV or current["source_ino"] != NEW_INO
                        or current["byte_offset"] < CURSOR_OFFSET
                    ):
                        raise ValueError("CURSOR_REBIND_DATABASE_MARKER_TAMPERED")
                    if current["byte_offset"] == CURSOR_OFFSET and current["source_anchor_sha256"] != CURSOR_ANCHOR_SHA256:
                        raise ValueError("CURSOR_REBIND_DATABASE_MARKER_TAMPERED")
                    checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    if checkpoint is None or int(checkpoint[0]) != 0:
                        raise ValueError("CURSOR_REBIND_WAL_CHECKPOINT_BUSY")
                    _fsync_file(database)
                    _fsync_directory(database.parent)
                    _write_once_json(receipt_path, payload, "CURSOR_REBIND_RECEIPT_TAMPERED")
                    return payload

                expected_old = {
                    "ledger": "lifecycle", "source_dev": OLD_DEV, "source_ino": OLD_INO,
                    "byte_offset": CURSOR_OFFSET,
                    "source_anchor_sha256": CURSOR_ANCHOR_SHA256,
                }
                if any(current[key] != value for key, value in expected_old.items()):
                    raise ValueError("CURSOR_REBIND_OLD_CURSOR_MISMATCH")

                intent = {
                    "schema": SCHEMA, "repair_id": REPAIR_ID,
                    "tail_repair_receipt_file_sha256": tail_receipt_file_sha,
                    "old_cursor": current,
                    "new_identity": {"source_dev": NEW_DEV, "source_ino": NEW_INO,
                                     "source_mtime_ns": int(after_proof_stat.st_mtime_ns)},
                    "active": {"size": int(after_proof_stat.st_size),
                               "prefix_size": PREFIX_SIZE, "prefix_sha256": PREFIX_SHA256,
                               "anchor_sha256": CURSOR_ANCHOR_SHA256},
                }
                _write_once_json(intent_path, intent, "CURSOR_REBIND_INTENT_TAMPERED")
                intent_sha = _sha(intent_path.read_bytes())
                payload = {
                    **intent, "status": "REBOUND", "intent_sha256": intent_sha,
                    "new_cursor": {**current, "source_dev": NEW_DEV, "source_ino": NEW_INO,
                                   "source_mtime_ns": int(after_proof_stat.st_mtime_ns)},
                    "byte_offset_preserved": True, "anchor_preserved": True,
                    "indexed_rows_rewritten": 0, "source_cleanup_authorized": False,
                }
                payload["receipt_sha256"] = _sha(json.dumps(
                    payload, separators=(",", ":"), sort_keys=True,
                ).encode())
                payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)

                connection.execute("BEGIN IMMEDIATE")
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS lifecycle_cursor_repair_receipt (
                        repair_id TEXT PRIMARY KEY,
                        schema TEXT NOT NULL,
                        payload_json TEXT NOT NULL
                    )
                """)
                result = connection.execute("""
                    UPDATE ledger_cursor
                    SET source_dev = ?, source_ino = ?, source_mtime_ns = ?
                    WHERE ledger = ? AND source_dev = ? AND source_ino = ?
                      AND byte_offset = ? AND source_anchor_sha256 = ?
                """, (
                    NEW_DEV, NEW_INO, int(after_proof_stat.st_mtime_ns), "lifecycle",
                    OLD_DEV, OLD_INO, CURSOR_OFFSET, CURSOR_ANCHOR_SHA256,
                ))
                if result.rowcount != 1:
                    connection.rollback()
                    raise ValueError("CURSOR_REBIND_CONDITIONAL_UPDATE_CONFLICT")
                connection.execute(
                    "INSERT INTO lifecycle_cursor_repair_receipt VALUES (?, ?, ?)",
                    (REPAIR_ID, SCHEMA, payload_json),
                )
                connection.commit()
                checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if checkpoint is None or int(checkpoint[0]) != 0:
                    raise ValueError("CURSOR_REBIND_WAL_CHECKPOINT_BUSY")
                _fsync_file(database)
                _fsync_directory(database.parent)
                _write_once_json(receipt_path, payload, "CURSOR_REBIND_RECEIPT_TAMPERED")
                return payload
            finally:
                connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--expected-old-dev", required=True, type=int)
    parser.add_argument("--expected-old-ino", required=True, type=int)
    parser.add_argument("--expected-new-dev", required=True, type=int)
    parser.add_argument("--expected-new-ino", required=True, type=int)
    parser.add_argument("--expected-offset", required=True, type=int)
    parser.add_argument("--expected-anchor-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = rebind_lifecycle_cursor(
            args.root, expected_old_dev=args.expected_old_dev,
            expected_old_ino=args.expected_old_ino, expected_new_dev=args.expected_new_dev,
            expected_new_ino=args.expected_new_ino, expected_offset=args.expected_offset,
            expected_anchor_sha256=args.expected_anchor_sha256,
        )
    except (OSError, sqlite3.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)}))
        return 2
    print(json.dumps({"ok": True, "receipt": receipt}, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
