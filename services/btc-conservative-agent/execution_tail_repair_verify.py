"""Independent readback for the exact execution ledger tail and cursor repair."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path

SOURCE_SIZE = 266_240
SOURCE_SHA256 = "a1d83c507265143f0cd4c37ab065c85985234cae6cf10b046d185b842c349250"
PREFIX_SIZE = 265_886
PREFIX_SHA256 = "2614f49656ae19f8ca3b1c09652ee27cf9d016cd2a58f63092df68894f632b34"
TAIL_SIZE = 354
TAIL_SHA256 = "586328be5ab061de70ff01568023d7d3c5eaeca87a86563ceb7805be055b2b61"
ANCHOR_SHA256 = "f0eb3ff1db39a0d2908c3618f691aa8ad645cf1ba7e4340fd4a3f3517ba7245a"
OLD_DEV = 65056
OLD_INO = 663
OLD_MTIME_NS = 1788303277103206804
REPAIR_ID = f"execution-tail-{SOURCE_SHA256[:16]}"
SCHEMA = "execution_incomplete_tail_repair_v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _object(path: Path):
    raw = path.read_bytes(); value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict): raise ValueError(f"READBACK_NON_OBJECT:{path.name}")
    return value, raw


def verify(root: str | Path, *, expected_revision: str) -> dict:
    deployed = str(os.environ.get("SOURCE_GIT_REV") or "").strip().lower()
    if deployed[:12] != str(expected_revision).strip().lower():
        raise ValueError("READBACK_DEPLOYED_REVISION_MISMATCH")
    root = Path(root).resolve(strict=True)
    ledger_lexical = Path(os.path.abspath(root / "v3/ledgers/execution.jsonl"))
    db_lexical = Path(os.path.abspath(root / "v3/lifecycle_bundle_index/lifecycle_index.sqlite3"))
    ledger = ledger_lexical.resolve(strict=True); db = db_lexical.resolve(strict=True)
    if (ledger_lexical != ledger or db_lexical != db or ledger_lexical.is_symlink() or db_lexical.is_symlink()
        or not ledger.is_file() or not db.is_file() or root not in ledger.parents or root not in db.parents):
        raise ValueError("READBACK_PATH_CONTAINMENT_FAILED")
    quarantine = ledger.parent / "corrupt_evidence_quarantine" / REPAIR_ID
    if quarantine.is_symlink() or not quarantine.is_dir(): raise ValueError("READBACK_QUARANTINE_INVALID")
    before_stat = ledger.stat(); active = ledger.read_bytes(); after_read_stat = ledger.stat()
    if (before_stat.st_dev, before_stat.st_ino, before_stat.st_size, before_stat.st_mtime_ns) != (
        after_read_stat.st_dev, after_read_stat.st_ino, after_read_stat.st_size, after_read_stat.st_mtime_ns):
        raise ValueError("READBACK_ACTIVE_CHANGED_DURING_PROOF")
    if len(active) < PREFIX_SIZE or _sha(active[:PREFIX_SIZE]) != PREFIX_SHA256 or not active[:PREFIX_SIZE].endswith(b"\n"):
        raise ValueError("READBACK_ACTIVE_PREFIX_MISMATCH")
    for row, line in enumerate(active.splitlines(keepends=True), 1):
        if not line.endswith(b"\n") or not isinstance(json.loads(line.decode("utf-8")), dict):
            raise ValueError(f"READBACK_ACTIVE_JSONL_INVALID:{row}")
    required = ("execution.jsonl.original", "execution.jsonl.incomplete-tail", "manifest.json",
        "excluded_unknown.json", "validation.json", "repair_receipt.json")
    if any((quarantine / name).is_symlink() or not (quarantine / name).is_file() for name in required):
        raise ValueError("READBACK_ARTIFACT_LINKED_OR_MISSING")
    original = (quarantine / "execution.jsonl.original").read_bytes()
    tail = (quarantine / "execution.jsonl.incomplete-tail").read_bytes()
    if len(original) != SOURCE_SIZE or _sha(original) != SOURCE_SHA256: raise ValueError("READBACK_ORIGINAL_MISMATCH")
    if len(tail) != TAIL_SIZE or _sha(tail) != TAIL_SHA256: raise ValueError("READBACK_TAIL_MISMATCH")
    manifest, manifest_raw = _object(quarantine / "manifest.json")
    excluded, excluded_raw = _object(quarantine / "excluded_unknown.json")
    validation, _ = _object(quarantine / "validation.json")
    receipt, _ = _object(quarantine / "repair_receipt.json")
    material = dict(receipt); claimed = material.pop("receipt_sha256", None)
    prefix_rows = len(active[:PREFIX_SIZE].splitlines())
    if (manifest.get("schema") != SCHEMA or manifest.get("repair_id") != REPAIR_ID
        or manifest.get("source") != {"size": SOURCE_SIZE, "sha256": SOURCE_SHA256}
        or manifest.get("complete_prefix") != {"size": PREFIX_SIZE, "sha256": PREFIX_SHA256}
        or manifest.get("excluded_tail") != {"size": TAIL_SIZE, "sha256": TAIL_SHA256}
        or manifest.get("target") != "v3/ledgers/execution.jsonl"
        or manifest.get("source_stat") != {"dev": OLD_DEV, "inode": OLD_INO, "mtime_ns": OLD_MTIME_NS}
        or manifest.get("cursor") != {"source_dev": OLD_DEV, "source_ino": OLD_INO, "offset": PREFIX_SIZE, "anchor_sha256": ANCHOR_SHA256}
        or manifest.get("artifacts") != {"execution.jsonl.original": SOURCE_SHA256,
            "execution.jsonl.incomplete-tail": TAIL_SHA256, "excluded_unknown.json": _sha(excluded_raw)}
        or excluded.get("schema") != SCHEMA or excluded.get("classification") != "UNKNOWN"
        or excluded.get("reason") != "INCOMPLETE_JSONL_TAIL_EXCLUDED"
        or excluded.get("tail_size") != TAIL_SIZE or excluded.get("tail_sha256") != TAIL_SHA256
        or excluded.get("source_sha256") != SOURCE_SHA256 or excluded.get("ranking_eligible") is not False
        or excluded.get("profitability_supported") is not False
        or validation.get("schema") != SCHEMA or validation.get("repair_id") != REPAIR_ID
        or validation.get("status") != "VALIDATED" or validation.get("active_prefix_size") != PREFIX_SIZE
        or validation.get("active_prefix_sha256") != PREFIX_SHA256 or validation.get("invalid_prefix_jsonl_lines") != 0
        or validation.get("valid_prefix_jsonl_lines") != prefix_rows
        or validation.get("cursor_boundary_offset") != PREFIX_SIZE
        or validation.get("cursor_boundary_anchor_sha256") != ANCHOR_SHA256
        or validation.get("source_preserved") is not True or validation.get("tail_preserved") is not True
        or validation.get("source_cleanup_authorized") is not False
        or receipt.get("schema") != SCHEMA or receipt.get("repair_id") != REPAIR_ID
        or receipt.get("status") != "REPAIRED" or receipt.get("source_sha256") != SOURCE_SHA256
        or receipt.get("prefix_sha256") != PREFIX_SHA256 or receipt.get("tail_sha256") != TAIL_SHA256
        or receipt.get("manifest_sha256") != _sha(manifest_raw)
        or receipt.get("excluded_unknown_sha256") != _sha(excluded_raw)
        or receipt.get("validation_sha256") != _sha((quarantine / "validation.json").read_bytes())
        or receipt.get("byte_offset_preserved") is not True or receipt.get("anchor_preserved") is not True
        or receipt.get("old_cursor_identity") != {"dev": OLD_DEV, "ino": OLD_INO}
        or receipt.get("cursor_offset") != PREFIX_SIZE or receipt.get("cursor_anchor_sha256") != ANCHOR_SHA256
        or receipt.get("ranking_eligible") is not False or receipt.get("profitability_supported") is not False
        or receipt.get("excluded_classification") != "UNKNOWN"
        or receipt.get("source_cleanup_authorized") is not False
        or claimed != _sha(json.dumps(material, separators=(",", ":"), sort_keys=True).encode())):
        raise ValueError("READBACK_HASH_GRAPH_MISMATCH")
    stat = ledger.stat()
    with sqlite3.connect(str(db)) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute("SELECT * FROM ledger_cursor WHERE ledger='execution'").fetchone()
        marker = connection.execute("SELECT payload_json FROM ledger_tail_repair_receipt WHERE repair_id=?", (REPAIR_ID,)).fetchone()
    final_stat = ledger.stat()
    if (final_stat.st_dev, final_stat.st_ino, final_stat.st_size, final_stat.st_mtime_ns) != (
        after_read_stat.st_dev, after_read_stat.st_ino, after_read_stat.st_size, after_read_stat.st_mtime_ns):
        raise ValueError("READBACK_ACTIVE_CHANGED_DURING_DATABASE_PROOF")
    if cursor is None or marker is None or json.loads(str(marker[0])) != receipt:
        raise ValueError("READBACK_DATABASE_MARKER_MISMATCH")
    offset = int(cursor["byte_offset"]); start = max(0, offset - 4096)
    anchor = _sha(active[start:offset])
    if ((int(cursor["source_dev"]), int(cursor["source_ino"])) != (int(stat.st_dev), int(stat.st_ino))
        or offset < PREFIX_SIZE or offset > len(active) or anchor != str(cursor["source_anchor_sha256"])):
        raise ValueError("READBACK_CURSOR_MISMATCH")
    if (receipt.get("new_cursor_identity") != {"dev": int(stat.st_dev), "ino": int(stat.st_ino)}
        or validation.get("cursor_identity") != receipt.get("new_cursor_identity")):
        raise ValueError("READBACK_REPAIRED_IDENTITY_MISMATCH")
    return {"ok": True, "schema": "execution_tail_repair_independent_readback_v1",
        "deployed_revision": deployed[:12], "active_size": len(active), "active_sha256": _sha(active),
        "cursor_offset": offset, "cursor_identity_matches": True, "cursor_anchor_matches": True,
        "original_preserved": True, "tail_preserved": True, "excluded_classification": "UNKNOWN",
        "ranking_eligible": False, "profitability_supported": False, "source_cleanup_authorized": False}


def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument("--root",required=True); p.add_argument("--expected-revision",required=True)
    a=p.parse_args(argv)
    try: result=verify(a.root,expected_revision=a.expected_revision)
    except (OSError,sqlite3.Error,UnicodeDecodeError,json.JSONDecodeError,ValueError) as exc:
        print(json.dumps({"ok":False,"error_code":str(exc)})); return 2
    print(json.dumps(result,separators=(",",":"),sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
