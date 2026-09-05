"""Explicit, quiesced research-only deletion. Receipts retain metadata, never payloads."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Mapping
from research_reset_paths import io_path, resolved_path


class ResearchDeletionRejected(ValueError):
    pass


def _checked_path(raw, root: Path) -> Path:
    path = Path(os.path.abspath(os.fspath(raw)))
    if path == root or root not in path.parents:
        raise ResearchDeletionRejected("PATH_OUTSIDE_EXPLICIT_ROOT")
    for part in (path, *path.parents):
        if io_path(part).exists() or io_path(part).is_symlink():
            info = io_path(part).lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ResearchDeletionRejected("SYMLINK_OR_REPARSE_POINT")
        if part == root:
            break
    if resolved_path(path) != path:
        raise ResearchDeletionRejected("PATH_RESOLUTION_CHANGED")
    return path


def _fingerprint(path: Path) -> dict:
    info = io_path(path).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ResearchDeletionRejected("TARGET_NOT_REGULAR_FILE")
    digest = hashlib.sha256()
    with io_path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = io_path(path).lstat()
    if (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ResearchDeletionRejected("TARGET_CHANGED_WHILE_HASHING")
    return {"path": str(path), "bytes": info.st_size, "sha256": digest.hexdigest()}


def _write_receipt(path: Path, payload: dict, *, first: bool = False) -> None:
    encoded = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
    if first:
        with io_path(path).open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        return
    temporary = path.with_name(path.name + ".updating")
    with io_path(temporary).open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(io_path(temporary), io_path(path))


def _progress_seed(*, root: Path, receipt_path: Path, inventory: list, context=None) -> str:
    identity = {"schema": "research_exact_deletion_progress_binding_v1",
                "root": str(root), "receipt_path": str(receipt_path), "inventory": inventory}
    if context is not None:
        identity["context"] = context
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _append_progress(path: Path, *, index: int, phase: str, previous: str) -> str:
    row = {"index": index, "phase": phase, "previous_sha256": previous}
    digest = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    row["sha256"] = digest
    with io_path(path).open("ab") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    return digest


def reconcile_research_deletion(receipt_path) -> dict:
    """Read-only crash reconciliation; absence after intent is not actor proof.

    INTENT is durable before unlink and UNLINKED after it. A crash between those
    writes yields ABSENT_AFTER_INTENT, not an invented successful outcome.
    No deletion, retry, or receipt mutation is performed by this helper.
    """
    receipt_path = Path(receipt_path)
    if io_path(receipt_path).stat().st_size > 64 * 1024**2:
        raise ResearchDeletionRejected("RECEIPT_LIMIT_EXCEEDED")
    receipt = json.loads(io_path(receipt_path).read_text(encoding="utf-8"))
    root = Path(receipt["root"])
    receipt_path = _checked_path(receipt_path, root)
    if receipt.get("receipt_path") != str(receipt_path):
        raise ResearchDeletionRejected("RECEIPT_PATH_IDENTITY_MISMATCH")
    inventory = receipt.get("inventory")
    if not isinstance(inventory, list) or len(inventory) > 100000:
        raise ResearchDeletionRejected("INVALID_RECEIPT_INVENTORY")
    journal = _checked_path(str(receipt_path) + ".progress.jsonl", root)
    if receipt.get("progress_journal") != str(journal):
        raise ResearchDeletionRejected("JOURNAL_IDENTITY_MISMATCH")
    phases = {}
    previous = _progress_seed(root=root, receipt_path=receipt_path, inventory=inventory,
                              context=receipt.get("context"))
    if receipt.get("progress_seed_sha256") != previous:
        raise ResearchDeletionRejected("JOURNAL_SEED_MISMATCH")
    if io_path(journal).exists():
        with io_path(journal).open("rb") as handle:
            for sequence in range(2 * len(inventory) + 1):
                line = handle.readline(4097)
                if not line:
                    break
                if sequence >= 2 * len(inventory) or len(line) > 4096 or not line.endswith(b"\n"):
                    raise ResearchDeletionRejected("JOURNAL_INCOMPLETE_OR_OVERSIZED")
                row = json.loads(line)
                digest = row.pop("sha256", None)
                if (row.get("previous_sha256") != previous or digest != hashlib.sha256(
                        json.dumps(row, sort_keys=True, separators=(",", ":")).encode()).hexdigest()):
                    raise ResearchDeletionRejected("JOURNAL_HASH_MISMATCH")
                index, phase = row.get("index"), row.get("phase")
                expected_index, expected_phase = sequence // 2, "INTENT" if sequence % 2 == 0 else "UNLINKED"
                if index != expected_index or phase != expected_phase:
                    raise ResearchDeletionRejected("JOURNAL_SEQUENCE_INVALID")
                phases[index] = phase
                previous = digest
    rows = []
    for index, row in enumerate(inventory):
        path = _checked_path(row["path"], root)
        phase = phases.get(index)
        if not io_path(path).exists():
            status = "UNLINKED_CONFIRMED" if phase == "UNLINKED" else "ABSENT_AFTER_INTENT" if phase == "INTENT" else "UNEXPECTED_ABSENCE"
        elif _fingerprint(path) == row:
            status = "RETAINED" if phase != "UNLINKED" else "REAPPEARED_AFTER_UNLINK"
        else:
            status = "CHANGED_UNKNOWN"
        rows.append({"path": str(path), "status": status, "journal_phase": phase})
    return {"schema": "research_exact_deletion_reconciliation_v1", "receipt_status": receipt.get("status"),
            "rows": rows, "counts": {key: sum(row["status"] == key for row in rows)
                                       for key in sorted({row["status"] for row in rows})},
            "deletion_performed": False}


def delete_exact_research_files(*, root, targets, allowed_paths, receipt_path,
                                quiescent: bool, recovery_states: Mapping[str, str],
                                protected_paths=(), max_files: int = 100000,
                                max_total_bytes: int = 64 * 1024**3,
                                expected_sha256_by_path: Mapping[str, str] | None = None,
                                receipt_context: Mapping | None = None) -> dict:
    """Delete exact files only while the caller holds all writer/reader barriers.

    ``recovery_states`` must be authoritative caller observations, not assumed
    empty because execution is paused. Unknown/PREPARED/DEFERRED blocks deletion.
    Integration must supply explicit relevant proof keys (lifecycle owner,
    emergency WAL, sync/readers); this leaf cannot obtain runtime ownership.
    Failures after unlink retain a PARTIAL receipt; they are never success.
    """
    if quiescent is not True:
        raise ResearchDeletionRejected("QUIESCENCE_NOT_PROVEN")
    if not isinstance(recovery_states, Mapping) or not 0 < len(recovery_states) <= 32 or any(
            value not in {"EMPTY", "REPLAYED", "RECONCILED", "NOT_PRESENT"}
            for value in recovery_states.values()):
        raise ResearchDeletionRejected("RECOVERY_NOT_RECONCILED")
    if any(not isinstance(key, str) or not key.strip() or len(key) > 128 for key in recovery_states):
        raise ResearchDeletionRejected("RECOVERY_PROOF_KEY_INVALID")
    context = None
    if receipt_context is not None:
        keys = {"proof_sha256", "plan_sha256", "retained", "bytes_basis", "hardlinked_target_count"}
        if not isinstance(receipt_context, Mapping) or set(receipt_context) != keys:
            raise ResearchDeletionRejected("INVALID_RECEIPT_CONTEXT")
        if any(not isinstance(receipt_context.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", receipt_context[key])
               for key in ("proof_sha256", "plan_sha256")):
            raise ResearchDeletionRejected("INVALID_RECEIPT_CONTEXT_IDENTITY")
        retained = receipt_context.get("retained")
        allowed_row_keys = {"path", "absolute_path", "reason", "category", "size_bytes", "hardlinked", "verified_sibling_target"}
        if not isinstance(retained, list) or len(retained) > 200000 or any(
                not isinstance(row, dict) or set(row) - allowed_row_keys for row in retained):
            raise ResearchDeletionRejected("INVALID_RETAINED_METADATA")
        if any("verified_sibling_target" in row and (
                not isinstance(row["verified_sibling_target"], str)
                or not row["verified_sibling_target"] or len(row["verified_sibling_target"]) > 4096)
                for row in retained):
            raise ResearchDeletionRejected("INVALID_RETAINED_ALIAS_TARGET")
        encoded_context = json.dumps(dict(receipt_context), sort_keys=True, allow_nan=False)
        if len(encoded_context.encode()) > 32 * 1024**2:
            raise ResearchDeletionRejected("RECEIPT_CONTEXT_LIMIT_EXCEEDED")
        context = json.loads(encoded_context)
    root = Path(os.path.abspath(os.fspath(root)))
    if not io_path(root).is_dir() or root.parent == root:
        raise ResearchDeletionRejected("UNSAFE_ROOT")
    # Validate root and its ancestors too, including Windows junctions.
    for part in (root, *root.parents):
        info = io_path(part).lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ResearchDeletionRejected("ROOT_SYMLINK_OR_REPARSE_POINT")
    if not isinstance(max_files, int) or isinstance(max_files, bool) or not 0 < max_files <= 100000:
        raise ResearchDeletionRejected("INVALID_FILE_LIMIT")
    if not isinstance(max_total_bytes, int) or isinstance(max_total_bytes, bool) or not 0 < max_total_bytes <= 1024**4:
        raise ResearchDeletionRejected("INVALID_BYTE_LIMIT")
    bounded = []
    for raw in targets:
        if len(bounded) >= max_files:
            raise ResearchDeletionRejected("FILE_LIMIT_EXCEEDED")
        bounded.append(_checked_path(raw, root))
    allowed = set()
    for index, raw in enumerate(allowed_paths):
        if index >= max_files:
            raise ResearchDeletionRejected("ALLOWLIST_LIMIT_EXCEEDED")
        allowed.add(_checked_path(raw, root))
    protected = set()
    for index, raw in enumerate(protected_paths):
        if index >= max_files:
            raise ResearchDeletionRejected("PROTECTED_LIST_LIMIT_EXCEEDED")
        protected.add(_checked_path(raw, root))
    paths = sorted(set(bounded))
    receipt = _checked_path(receipt_path, root)
    if receipt in paths or receipt in allowed or io_path(receipt).exists() or not io_path(receipt.parent).is_dir():
        raise ResearchDeletionRejected("UNSAFE_RECEIPT_PATH")
    journal = _checked_path(str(receipt) + ".progress.jsonl", root)
    if journal in paths or journal in allowed or io_path(journal).exists():
        raise ResearchDeletionRejected("UNSAFE_PROGRESS_PATH")
    forbidden_suffixes = {".py", ".ps1", ".js", ".mjs", ".ts", ".toml", ".yaml", ".yml", ".pem", ".key", ".exe", ".dll"}
    for path in paths:
        parts = tuple(p.lower() for p in path.relative_to(root).parts)
        if path not in allowed:
            raise ResearchDeletionRejected("TARGET_NOT_ALLOWLISTED")
        if path in protected or any(p in path.parents for p in protected):
            raise ResearchDeletionRejected("PROTECTED_PATH")
        if (path.suffix.lower() in forbidden_suffixes or path.name.lower().startswith(".env")
                or any(any(token in part for token in ("credential", "secret", "recovery", "emergency", "config", "owner")) for part in parts)
                or any(part in {".git", "locks", "owner"} for part in parts)
                or path.suffix.lower() == ".lock"):
            raise ResearchDeletionRejected("PROTECTED_SOURCE_OR_RECOVERY")
    if sum(io_path(path).lstat().st_size for path in paths) > max_total_bytes:
        raise ResearchDeletionRejected("BYTE_LIMIT_EXCEEDED")
    inventory = [_fingerprint(path) for path in paths]
    expected_hashes = {}
    if expected_sha256_by_path is not None:
        if not isinstance(expected_sha256_by_path, Mapping) or len(expected_sha256_by_path) > max_files:
            raise ResearchDeletionRejected("INVALID_EXPECTED_HASH_MAP")
        target_set = set(paths)
        for raw, digest in expected_sha256_by_path.items():
            path = _checked_path(raw, root)
            if path not in target_set or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ResearchDeletionRejected("INVALID_EXPECTED_HASH_BINDING")
            if str(path) in expected_hashes and expected_hashes[str(path)] != digest:
                raise ResearchDeletionRejected("AMBIGUOUS_EXPECTED_HASH_BINDING")
            expected_hashes[str(path)] = digest
        if any(row["path"] in expected_hashes and row["sha256"] != expected_hashes[row["path"]]
               for row in inventory):
            raise ResearchDeletionRejected("EXPECTED_SHA256_MISMATCH")
    result = {"schema": "research_exact_deletion_v1", "status": "PREPARED", "root": str(root),
              "raw_payloads_retained": False, "payload_copy_performed": False,
              "recovery_states": dict(recovery_states), "inventory": inventory,
              "deleted": [], "deleted_bytes": 0, "receipt_path": str(receipt),
              "progress_journal": str(journal), "expected_sha256_by_path": expected_hashes,
              "context": context}
    result["progress_seed_sha256"] = _progress_seed(root=root, receipt_path=receipt, inventory=inventory, context=context)
    _write_receipt(receipt, result, first=True)
    with io_path(journal).open("xb") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    previous_progress = result["progress_seed_sha256"]
    try:
        # Recheck the complete set before the first unlink, and each exact file
        # again immediately before removal. No glob/tree expansion occurs here.
        for row in inventory:
            if _fingerprint(_checked_path(row["path"], root)) != row:
                raise ResearchDeletionRejected("TARGET_CHANGED_BEFORE_DELETE")
        for index, row in enumerate(inventory):
            path = _checked_path(row["path"], root)
            if _fingerprint(path) != row:
                raise ResearchDeletionRejected("TARGET_CHANGED_BEFORE_DELETE")
            previous_progress = _append_progress(journal, index=index, phase="INTENT", previous=previous_progress)
            io_path(path).unlink()
            result["deleted"].append(row["path"])
            result["deleted_bytes"] += row["bytes"]
            previous_progress = _append_progress(journal, index=index, phase="UNLINKED", previous=previous_progress)
        result["status"] = "COMPLETE"
    except Exception as exc:
        result["status"] = "PARTIAL" if result["deleted"] else "ABORTED"
        result["error"] = type(exc).__name__
        _write_receipt(receipt, result)
        raise
    _write_receipt(receipt, result)
    return result
