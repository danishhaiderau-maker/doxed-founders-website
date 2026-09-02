"""Independent readback for the incident-bound lifecycle cursor rebind."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from lifecycle_bundles import _exclusive_index_lock, _source_anchor
from lifecycle_cursor_rebind import (
    CURSOR_ANCHOR_SHA256, CURSOR_OFFSET, NEW_DEV, NEW_INO, OLD_DEV, OLD_INO,
    PREFIX_SHA256, PREFIX_SIZE, REPAIR_ID, SCHEMA, _hash_prefix,
    _verify_tail_receipt,
)
from lifecycle_tail_repair import _target
from research_v3_store import V3EvidenceStore


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def verify(root: str | Path, *, expected_revision: str) -> dict:
    deployed = str(os.environ.get("SOURCE_GIT_REV") or "").strip().lower()[:12]
    if deployed != str(expected_revision).strip().lower():
        raise ValueError("CURSOR_REBIND_READBACK_REVISION_MISMATCH")
    root, ledger = _target(root)
    db_candidate = root / "v3/lifecycle_bundle_index/lifecycle_index.sqlite3"
    if db_candidate.is_symlink():
        raise ValueError("CURSOR_REBIND_READBACK_DATABASE_SYMLINK_REFUSED")
    db = db_candidate.resolve(strict=True)
    if root not in db.parents:
        raise ValueError("CURSOR_REBIND_READBACK_DATABASE_ESCAPE_REFUSED")
    quarantine = ledger.parent / "corrupt_evidence_quarantine" / REPAIR_ID
    _tail_receipt, tail_receipt_file_sha = _verify_tail_receipt(quarantine)
    receipt_raw = (quarantine / "cursor_rebind_receipt.json").read_bytes()
    intent_raw = (quarantine / "cursor_rebind_intent.json").read_bytes()
    receipt = json.loads(receipt_raw.decode("utf-8"))
    intent = json.loads(intent_raw.decode("utf-8"))
    if not isinstance(receipt, dict) or not isinstance(intent, dict):
        raise ValueError("CURSOR_REBIND_READBACK_RECEIPT_INVALID")
    material = dict(receipt); claimed = material.pop("receipt_sha256", None)
    old_cursor = receipt.get("old_cursor") or {}
    new_identity = receipt.get("new_identity") or {}
    new_cursor = receipt.get("new_cursor") or {}
    active = receipt.get("active") or {}
    if (
        receipt.get("schema") != SCHEMA or receipt.get("status") != "REBOUND"
        or receipt.get("repair_id") != REPAIR_ID
        or claimed != _sha(json.dumps(material, separators=(",", ":"), sort_keys=True).encode())
        or receipt.get("tail_repair_receipt_file_sha256") != tail_receipt_file_sha
        or receipt.get("intent_sha256") != _sha(intent_raw)
        or any(receipt.get(key) != value for key, value in intent.items())
        or old_cursor.get("ledger") != "lifecycle"
        or int(old_cursor.get("source_dev") or -1) != OLD_DEV
        or int(old_cursor.get("source_ino") or -1) != OLD_INO
        or int(old_cursor.get("byte_offset") or -1) != CURSOR_OFFSET
        or old_cursor.get("source_anchor_sha256") != CURSOR_ANCHOR_SHA256
        or int(new_identity.get("source_dev") or -1) != NEW_DEV
        or int(new_identity.get("source_ino") or -1) != NEW_INO
        or int(new_cursor.get("source_dev") or -1) != NEW_DEV
        or int(new_cursor.get("source_ino") or -1) != NEW_INO
        or int(new_cursor.get("byte_offset") or -1) != CURSOR_OFFSET
        or new_cursor.get("source_anchor_sha256") != CURSOR_ANCHOR_SHA256
        or new_cursor.get("source_mtime_ns") != new_identity.get("source_mtime_ns")
        or int(active.get("prefix_size") or -1) != PREFIX_SIZE
        or active.get("prefix_sha256") != PREFIX_SHA256
        or active.get("anchor_sha256") != CURSOR_ANCHOR_SHA256
        or receipt.get("byte_offset_preserved") is not True
        or receipt.get("anchor_preserved") is not True
        or receipt.get("indexed_rows_rewritten") != 0
        or receipt.get("source_cleanup_authorized") is not False
    ):
        raise ValueError("CURSOR_REBIND_READBACK_RECEIPT_MISMATCH")

    store = V3EvidenceStore(root, epoch_id="lifecycle-cursor-rebind-readback")
    with _exclusive_index_lock(root):
        with store._exclusive(ledger):
            stat = ledger.stat()
            if (int(stat.st_dev), int(stat.st_ino)) != (NEW_DEV, NEW_INO):
                raise ValueError("CURSOR_REBIND_READBACK_ACTIVE_IDENTITY_MISMATCH")
            if int(stat.st_size) < PREFIX_SIZE or _hash_prefix(ledger, PREFIX_SIZE) != PREFIX_SHA256:
                raise ValueError("CURSOR_REBIND_READBACK_PREFIX_MISMATCH")
            with sqlite3.connect(str(db)) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute("SELECT * FROM ledger_cursor WHERE ledger=?", ("lifecycle",)).fetchone()
                marker = connection.execute(
                    "SELECT schema,payload_json FROM lifecycle_cursor_repair_receipt WHERE repair_id=?",
                    (REPAIR_ID,),
                ).fetchone()
            if row is None or marker is None or marker["schema"] != SCHEMA:
                raise ValueError("CURSOR_REBIND_READBACK_DATABASE_RECEIPT_MISSING")
            if json.loads(marker["payload_json"]) != receipt:
                raise ValueError("CURSOR_REBIND_READBACK_DATABASE_RECEIPT_MISMATCH")
            offset = int(row["byte_offset"])
            if (
                int(row["source_dev"]) != NEW_DEV or int(row["source_ino"]) != NEW_INO
                or offset < CURSOR_OFFSET or offset > int(stat.st_size)
                or _source_anchor(ledger, offset) != row["source_anchor_sha256"]
            ):
                raise ValueError("CURSOR_REBIND_READBACK_CURSOR_MISMATCH")
            if offset == CURSOR_OFFSET and row["source_anchor_sha256"] != CURSOR_ANCHOR_SHA256:
                raise ValueError("CURSOR_REBIND_READBACK_CURSOR_MISMATCH")
    return {
        "ok": True, "schema": "lifecycle_cursor_rebind_readback_v1",
        "deployed_revision": deployed, "active_dev": int(stat.st_dev),
        "active_ino": int(stat.st_ino), "active_size": int(stat.st_size),
        "cursor_offset": offset, "cursor_advanced_after_rebind": offset > CURSOR_OFFSET,
        "prefix_sha256": PREFIX_SHA256, "receipt_sha256": claimed,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--expected-revision", required=True)
    args = parser.parse_args(argv)
    try:
        result = verify(args.root, expected_revision=args.expected_revision)
    except (OSError, sqlite3.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)})); return 2
    print(json.dumps(result, separators=(",", ":"), sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
