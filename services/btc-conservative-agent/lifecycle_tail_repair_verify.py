"""Independent readback for the one exact lifecycle incomplete-tail repair."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

SOURCE_SHA256 = "d2eaa4fb87c2b9870b5f6791c634e369218fe4b76e2c5a5c64a375f1c72a5c63"
SOURCE_SIZE = 22_118_400
PREFIX_SHA256 = "bdb22b92a32ab082cdcaf30002b95276962c6a6ff6b7c2e69205487be5de92c5"
PREFIX_SIZE = 22_116_009
TAIL_SHA256 = "7b660b110c16c04c4ed0ed65f3791b8abc3cadbb5e5eaf7b6078430a67fa07eb"
TAIL_SIZE = 2_391
REPAIR_ID = f"lifecycle-tail-{SOURCE_SHA256[:16]}"
SCHEMA = "lifecycle_incomplete_tail_repair_v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"READBACK_NON_OBJECT:{path.name}")
    return value, raw


def verify(root: str | Path, *, expected_revision: str, expected_inode: int, expected_mtime_ns: int) -> dict[str, Any]:
    deployed = str(os.environ.get("SOURCE_GIT_REV") or "").strip().lower()
    if deployed[:12] != str(expected_revision).strip().lower():
        raise ValueError("READBACK_DEPLOYED_REVISION_MISMATCH")
    root = Path(root).resolve(strict=True)
    active = (root / "v3" / "ledgers" / "lifecycle.jsonl").resolve(strict=True)
    quarantine_root = active.parent / "corrupt_evidence_quarantine"
    matching = [path for path in quarantine_root.iterdir() if path.name == REPAIR_ID]
    if len(matching) != 1 or matching[0].is_symlink() or not matching[0].is_dir():
        raise ValueError("READBACK_QUARANTINE_CARDINALITY_MISMATCH")
    quarantine = matching[0]

    active_raw = active.read_bytes()
    if len(active_raw) != PREFIX_SIZE or _sha(active_raw) != PREFIX_SHA256 or not active_raw.endswith(b"\n"):
        raise ValueError("READBACK_ACTIVE_PREFIX_MISMATCH")
    rows = 0
    for rows, line in enumerate(active_raw.splitlines(keepends=True), 1):
        if not line.endswith(b"\n") or not isinstance(json.loads(line.decode("utf-8")), dict):
            raise ValueError(f"READBACK_ACTIVE_JSONL_INVALID:{rows}")

    original = (quarantine / "lifecycle.jsonl.original").read_bytes()
    tail = (quarantine / "lifecycle.jsonl.incomplete-tail").read_bytes()
    if len(original) != SOURCE_SIZE or _sha(original) != SOURCE_SHA256:
        raise ValueError("READBACK_ORIGINAL_MISMATCH")
    if len(tail) != TAIL_SIZE or _sha(tail) != TAIL_SHA256:
        raise ValueError("READBACK_TAIL_MISMATCH")

    manifest, manifest_raw = _json(quarantine / "manifest.json")
    excluded, excluded_raw = _json(quarantine / "excluded_unknown.json")
    validation, validation_raw = _json(quarantine / "validation.json")
    receipt, _receipt_raw = _json(quarantine / "repair_receipt.json")
    source_stat = manifest.get("source_stat") or {}
    if (
        manifest.get("schema") != SCHEMA
        or manifest.get("repair_id") != REPAIR_ID
        or manifest.get("source") != {"size": SOURCE_SIZE, "sha256": SOURCE_SHA256}
        or manifest.get("complete_prefix") != {"size": PREFIX_SIZE, "sha256": PREFIX_SHA256}
        or manifest.get("excluded_tail") != {"size": TAIL_SIZE, "sha256": TAIL_SHA256}
        or int(source_stat.get("inode") or 0) != int(expected_inode)
        or int(source_stat.get("mtime_ns") or 0) != int(expected_mtime_ns)
        or (manifest.get("artifacts") or {}).get("lifecycle.jsonl.original") != SOURCE_SHA256
        or (manifest.get("artifacts") or {}).get("lifecycle.jsonl.incomplete-tail") != TAIL_SHA256
        or (manifest.get("artifacts") or {}).get("excluded_unknown.json") != _sha(excluded_raw)
    ):
        raise ValueError("READBACK_MANIFEST_MISMATCH")
    if not (
        excluded.get("classification") == "UNKNOWN"
        and excluded.get("ranking_eligible") is False
        and excluded.get("profitability_supported") is False
        and excluded.get("tail_sha256") == TAIL_SHA256
    ):
        raise ValueError("READBACK_EXCLUSION_MISMATCH")
    if not (
        validation.get("status") == "VALIDATED"
        and validation.get("active_size") == PREFIX_SIZE
        and validation.get("active_sha256") == PREFIX_SHA256
        and validation.get("invalid_jsonl_lines") == 0
        and validation.get("source_cleanup_authorized") is False
    ):
        raise ValueError("READBACK_VALIDATION_MISMATCH")
    receipt_material = dict(receipt)
    claimed_receipt_sha = receipt_material.pop("receipt_sha256", None)
    calculated_receipt_sha = _sha(json.dumps(receipt_material, separators=(",", ":"), sort_keys=True).encode())
    if not (
        receipt.get("status") == "REPAIRED"
        and receipt.get("source_sha256") == SOURCE_SHA256
        and receipt.get("prefix_sha256") == PREFIX_SHA256
        and receipt.get("tail_sha256") == TAIL_SHA256
        and receipt.get("excluded_classification") == "UNKNOWN"
        and receipt.get("ranking_eligible") is False
        and receipt.get("source_cleanup_authorized") is False
        and receipt.get("manifest_sha256") == _sha(manifest_raw)
        and receipt.get("excluded_unknown_sha256") == _sha(excluded_raw)
        and receipt.get("validation_sha256") == _sha(validation_raw)
        and claimed_receipt_sha == calculated_receipt_sha
    ):
        raise ValueError("READBACK_RECEIPT_MISMATCH")
    return {
        "ok": True,
        "schema": "lifecycle_tail_repair_independent_readback_v1",
        "deployed_revision": deployed[:12],
        "active_size": len(active_raw),
        "active_sha256": _sha(active_raw),
        "valid_jsonl_rows": rows,
        "original_preserved": True,
        "tail_preserved": True,
        "excluded_classification": "UNKNOWN",
        "ranking_eligible": False,
        "profitability_supported": False,
        "source_cleanup_authorized": False,
        "receipt_sha256": claimed_receipt_sha,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--expected-inode", required=True, type=int)
    parser.add_argument("--expected-mtime-ns", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        result = verify(
            args.root,
            expected_revision=args.expected_revision,
            expected_inode=args.expected_inode,
            expected_mtime_ns=args.expected_mtime_ns,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)}))
        return 2
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
