"""Exact, standalone repair for the proven lifecycle JSONL incomplete tail."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from lifecycle_bundles import _exclusive_index_lock
from research_v3_store import V3EvidenceStore, _fsync_directory

SOURCE_SHA256 = "d2eaa4fb87c2b9870b5f6791c634e369218fe4b76e2c5a5c64a375f1c72a5c63"
SOURCE_SIZE = 22_118_400
PREFIX_SIZE = 22_116_009
PREFIX_SHA256 = "bdb22b92a32ab082cdcaf30002b95276962c6a6ff6b7c2e69205487be5de92c5"
TAIL_SIZE = 2_391
TAIL_SHA256 = "7b660b110c16c04c4ed0ed65f3791b8abc3cadbb5e5eaf7b6078430a67fa07eb"
MAX_JSONL_RECORD_BYTES = 2 * 1024 * 1024
SCHEMA = "lifecycle_incomplete_tail_repair_v1"


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _atomic_bytes(path: Path, raw: bytes) -> None:
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_bytes(
        path, json.dumps(payload, separators=(",", ":"), sort_keys=True).encode() + b"\n",
    )


def _write_once_json(path: Path, payload: dict[str, Any], error: str) -> None:
    if path.exists():
        try: existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(error) from exc
        if existing != payload:
            raise ValueError(error)
    else:
        _atomic_json(path, payload)


def _validate_jsonl(raw: bytes) -> int:
    if raw and not raw.endswith(b"\n"):
        raise ValueError("LIFECYCLE_PREFIX_NOT_LF_TERMINATED")
    count = 0
    for count, line in enumerate(raw.splitlines(keepends=True), 1):
        if len(line) > MAX_JSONL_RECORD_BYTES:
            raise ValueError(f"LIFECYCLE_PREFIX_RECORD_TOO_LARGE:{count}")
        try: value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"LIFECYCLE_PREFIX_INVALID_JSON:{count}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"LIFECYCLE_PREFIX_NON_OBJECT:{count}")
    return count


def _target(root: str | Path) -> tuple[Path, Path]:
    root = Path(root).resolve(strict=True)
    lexical = Path(os.path.abspath(root / "v3" / "ledgers" / "lifecycle.jsonl"))
    resolved = lexical.resolve(strict=True)
    if lexical != resolved or lexical.is_symlink() or not resolved.is_file():
        raise ValueError("LIFECYCLE_REPAIR_TARGET_LINKED_OR_INVALID")
    expected_parent = (root / "v3" / "ledgers").resolve(strict=True)
    if resolved.parent != expected_parent:
        raise ValueError("LIFECYCLE_REPAIR_TARGET_OUTSIDE_LEDGER_ROOT")
    return root, resolved


def _artifact(path: Path, expected_sha: str, expected_size: int) -> None:
    if not path.is_file():
        raise ValueError(f"LIFECYCLE_REPAIR_QUARANTINE_MISSING:{path.name}")
    raw = path.read_bytes()
    if len(raw) != expected_size or _sha(raw) != expected_sha:
        raise ValueError(f"LIFECYCLE_REPAIR_QUARANTINE_TAMPERED:{path.name}")


def _verify_metadata(quarantine: Path, repair_id: str) -> None:
    try:
        excluded_raw = (quarantine / "excluded_unknown.json").read_bytes()
        excluded = json.loads(excluded_raw.decode("utf-8"))
        manifest = json.loads((quarantine / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("LIFECYCLE_REPAIR_QUARANTINE_METADATA_TAMPERED") from exc
    if (
        excluded.get("schema") != SCHEMA
        or excluded.get("classification") != "UNKNOWN"
        or excluded.get("ranking_eligible") is not False
        or excluded.get("tail_size") != TAIL_SIZE
        or excluded.get("tail_sha256") != TAIL_SHA256
        or manifest.get("schema") != SCHEMA
        or manifest.get("repair_id") != repair_id
        or manifest.get("source") != {"size": PREFIX_SIZE + TAIL_SIZE, "sha256": SOURCE_SHA256}
        or manifest.get("complete_prefix") != {"size": PREFIX_SIZE, "sha256": PREFIX_SHA256}
        or manifest.get("excluded_tail") != {"size": TAIL_SIZE, "sha256": TAIL_SHA256}
        or not isinstance((manifest.get("source_stat") or {}).get("inode"), int)
        or int((manifest.get("source_stat") or {}).get("inode") or 0) <= 0
        or not isinstance((manifest.get("source_stat") or {}).get("mtime_ns"), int)
        or int((manifest.get("source_stat") or {}).get("mtime_ns") or 0) <= 0
        or (manifest.get("artifacts") or {}).get("excluded_unknown.json") != _sha(excluded_raw)
    ):
        raise ValueError("LIFECYCLE_REPAIR_QUARANTINE_METADATA_TAMPERED")


def _repair_lifecycle_incomplete_tail(
    root: str | Path,
    *,
    original_stat: dict[str, int] | None = None,
) -> dict[str, Any]:
    _root, target = _target(root)
    active = target.read_bytes()
    active_sha = _sha(active)
    repair_id = f"lifecycle-tail-{SOURCE_SHA256[:16]}"
    quarantine_root = target.parent / "corrupt_evidence_quarantine"
    quarantine = quarantine_root / repair_id
    original_path = quarantine / "lifecycle.jsonl.original"
    tail_path = quarantine / "lifecycle.jsonl.incomplete-tail"

    if active_sha == SOURCE_SHA256:
        if not original_stat:
            raise ValueError("LIFECYCLE_REPAIR_SOURCE_STAT_MISSING")
        if len(active) != PREFIX_SIZE + TAIL_SIZE:
            raise ValueError("LIFECYCLE_REPAIR_SOURCE_SIZE_MISMATCH")
        prefix, tail = active[:PREFIX_SIZE], active[PREFIX_SIZE:]
        if _sha(prefix) != PREFIX_SHA256 or len(prefix) != PREFIX_SIZE:
            raise ValueError("LIFECYCLE_REPAIR_PREFIX_MISMATCH")
        if _sha(tail) != TAIL_SHA256 or len(tail) != TAIL_SIZE:
            raise ValueError("LIFECYCLE_REPAIR_TAIL_MISMATCH")
    elif active_sha == PREFIX_SHA256 and len(active) == PREFIX_SIZE:
        prefix = active
        if not quarantine.is_dir():
            raise ValueError("LIFECYCLE_REPAIR_RECEIPT_MISSING")
        _artifact(original_path, SOURCE_SHA256, PREFIX_SIZE + TAIL_SIZE)
        _artifact(tail_path, TAIL_SHA256, TAIL_SIZE)
        tail = tail_path.read_bytes()
    else:
        raise ValueError("LIFECYCLE_REPAIR_SOURCE_SHA256_MISMATCH")

    line_count = _validate_jsonl(prefix)
    if quarantine.exists():
        _artifact(original_path, SOURCE_SHA256, PREFIX_SIZE + TAIL_SIZE)
        _artifact(tail_path, TAIL_SHA256, TAIL_SIZE)
        _verify_metadata(quarantine, repair_id)
    else:
        quarantine_root.mkdir(parents=True, exist_ok=True)
        staging = quarantine_root / f".{repair_id}.{uuid.uuid4().hex[:8]}.tmp"
        staging.mkdir()
        try:
            _atomic_bytes(staging / original_path.name, prefix + tail)
            _atomic_bytes(staging / tail_path.name, tail)
            exclusion = {
                "schema": SCHEMA, "classification": "UNKNOWN",
                "ranking_eligible": False, "profitability_supported": False,
                "reason": "INCOMPLETE_JSONL_TAIL_EXCLUDED",
                "tail_size": TAIL_SIZE, "tail_sha256": TAIL_SHA256,
                "source_sha256": SOURCE_SHA256,
            }
            _atomic_json(staging / "excluded_unknown.json", exclusion)
            manifest = {
                "schema": SCHEMA, "repair_id": repair_id,
                "target": "v3/ledgers/lifecycle.jsonl",
                "source": {"size": PREFIX_SIZE + TAIL_SIZE, "sha256": SOURCE_SHA256},
                "source_stat": {
                    "inode": int(original_stat["inode"]),
                    "mtime_ns": int(original_stat["mtime_ns"]),
                },
                "complete_prefix": {"size": PREFIX_SIZE, "sha256": PREFIX_SHA256},
                "excluded_tail": {"size": TAIL_SIZE, "sha256": TAIL_SHA256},
                "artifacts": {
                    original_path.name: SOURCE_SHA256, tail_path.name: TAIL_SHA256,
                    "excluded_unknown.json": _sha((staging / "excluded_unknown.json").read_bytes()),
                },
            }
            _atomic_json(staging / "manifest.json", manifest)
            os.replace(staging, quarantine)
            _fsync_directory(quarantine_root)
        finally:
            if staging.exists(): shutil.rmtree(staging)

    _verify_metadata(quarantine, repair_id)

    if _sha(target.read_bytes()) == SOURCE_SHA256:
        _atomic_bytes(target, prefix)
    rebuilt = target.read_bytes()
    if len(rebuilt) != PREFIX_SIZE or _sha(rebuilt) != PREFIX_SHA256:
        raise ValueError("LIFECYCLE_REPAIR_ATOMIC_REPLACE_VALIDATION_FAILED")
    validated_lines = _validate_jsonl(rebuilt)
    validation = {
        "schema": SCHEMA, "status": "VALIDATED", "repair_id": repair_id,
        "active_size": len(rebuilt), "active_sha256": _sha(rebuilt),
        "valid_jsonl_lines": validated_lines, "invalid_jsonl_lines": 0,
        "source_preserved": True, "tail_preserved": True,
        "source_cleanup_authorized": False,
    }
    _write_once_json(
        quarantine / "validation.json", validation,
        "LIFECYCLE_REPAIR_VALIDATION_TAMPERED",
    )
    receipt = {
        "schema": SCHEMA, "status": "REPAIRED", "repair_id": repair_id,
        "target": "v3/ledgers/lifecycle.jsonl",
        "source_sha256": SOURCE_SHA256, "prefix_sha256": PREFIX_SHA256,
        "tail_sha256": TAIL_SHA256, "excluded_classification": "UNKNOWN",
        "ranking_eligible": False, "source_cleanup_authorized": False,
        "valid_jsonl_lines": line_count,
        "manifest_sha256": _sha((quarantine / "manifest.json").read_bytes()),
        "excluded_unknown_sha256": _sha((quarantine / "excluded_unknown.json").read_bytes()),
        "validation_sha256": _sha((quarantine / "validation.json").read_bytes()),
    }
    receipt["receipt_sha256"] = _sha(json.dumps(
        receipt, separators=(",", ":"), sort_keys=True
    ).encode())
    receipt_path = quarantine / "repair_receipt.json"
    _write_once_json(receipt_path, receipt, "LIFECYCLE_REPAIR_RECEIPT_TAMPERED")
    return receipt


def repair_lifecycle_incomplete_tail(
    root: str | Path,
    *,
    expected_source_size: int | None = None,
    expected_source_sha256: str | None = None,
    expected_prefix_size: int | None = None,
    expected_prefix_sha256: str | None = None,
    expected_tail_size: int | None = None,
    expected_tail_sha256: str | None = None,
    expected_inode: int | None = None,
    expected_mtime_ns: int | None = None,
) -> dict[str, Any]:
    supplied = (
        SOURCE_SIZE if expected_source_size is None else expected_source_size,
        SOURCE_SHA256 if expected_source_sha256 is None else expected_source_sha256,
        PREFIX_SIZE if expected_prefix_size is None else expected_prefix_size,
        PREFIX_SHA256 if expected_prefix_sha256 is None else expected_prefix_sha256,
        TAIL_SIZE if expected_tail_size is None else expected_tail_size,
        TAIL_SHA256 if expected_tail_sha256 is None else expected_tail_sha256,
    )
    compiled = (
        SOURCE_SIZE, SOURCE_SHA256, PREFIX_SIZE, PREFIX_SHA256, TAIL_SIZE, TAIL_SHA256,
    )
    if supplied != compiled:
        raise ValueError("LIFECYCLE_REPAIR_EXPECTATION_MISMATCH")
    if SOURCE_SIZE != PREFIX_SIZE + TAIL_SIZE:
        raise ValueError("LIFECYCLE_REPAIR_COMPILED_SIZE_INCONSISTENT")
    resolved_root, target = _target(root)
    store = V3EvidenceStore(resolved_root, epoch_id="lifecycle-tail-repair")
    # Match the lifecycle pipeline's lock order: freeze index/materializer reads,
    # then serialize the exact append ledger path. Both locks are released by the
    # OS if the process is killed by the workflow wall-clock bound.
    with _exclusive_index_lock(resolved_root):
        with store._exclusive(target):
            stat = target.stat()
            active = target.read_bytes()
            active_sha = _sha(active)
            replay_manifest: dict[str, Any] = {}
            if active_sha == PREFIX_SHA256 and len(active) == PREFIX_SIZE:
                repair_id = f"lifecycle-tail-{SOURCE_SHA256[:16]}"
                manifest_path = (
                    target.parent / "corrupt_evidence_quarantine" / repair_id / "manifest.json"
                )
                try:
                    replay_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("LIFECYCLE_REPAIR_RECEIPT_MISSING") from exc
            original_stat = replay_manifest.get("source_stat") or {
                "inode": int(stat.st_ino), "mtime_ns": int(stat.st_mtime_ns),
            }
            if expected_inode is not None and int(original_stat.get("inode") or 0) != int(expected_inode):
                raise ValueError("LIFECYCLE_REPAIR_INODE_MISMATCH")
            if expected_mtime_ns is not None and int(original_stat.get("mtime_ns") or 0) != int(expected_mtime_ns):
                raise ValueError("LIFECYCLE_REPAIR_MTIME_MISMATCH")
            return _repair_lifecycle_incomplete_tail(
                resolved_root,
                original_stat={
                    "inode": int(original_stat["inode"]),
                    "mtime_ns": int(original_stat["mtime_ns"]),
                },
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--expected-source-size", required=True, type=int)
    parser.add_argument("--expected-source-sha256", required=True)
    parser.add_argument("--expected-prefix-size", required=True, type=int)
    parser.add_argument("--expected-prefix-sha256", required=True)
    parser.add_argument("--expected-tail-size", required=True, type=int)
    parser.add_argument("--expected-tail-sha256", required=True)
    parser.add_argument("--expected-inode", type=int)
    parser.add_argument("--expected-mtime-ns", type=int)
    args = parser.parse_args(argv)
    try:
        receipt = repair_lifecycle_incomplete_tail(
            args.root,
            expected_source_size=args.expected_source_size,
            expected_source_sha256=args.expected_source_sha256,
            expected_prefix_size=args.expected_prefix_size,
            expected_prefix_sha256=args.expected_prefix_sha256,
            expected_tail_size=args.expected_tail_size,
            expected_tail_sha256=args.expected_tail_sha256,
            expected_inode=args.expected_inode,
            expected_mtime_ns=args.expected_mtime_ns,
        )
    except ValueError as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)}))
        return 2
    print(json.dumps({"ok": True, "receipt": receipt}, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
