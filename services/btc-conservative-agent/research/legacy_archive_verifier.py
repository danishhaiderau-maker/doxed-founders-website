"""Bounded, resumable verification for retained legacy research archives.

The legacy ``research_archive/session_*`` trees remain immutable inputs.  This
module writes only an append-first verification journal and an atomic summary
below the canonical research store.  A completed file receipt can be reused
only while the archive receipt identity and the file's size/mtime identity are
unchanged; otherwise the payload is hashed again.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "legacy_archive_verification_index_v1"
JOURNAL_SCHEMA = "legacy_archive_file_verification_v1"
OUTPUT_DIRNAME = "legacy-archive-verification"
JOURNAL_NAME = "verification_receipts.jsonl"
INDEX_NAME = "verification_index.json"


class LegacyArchiveVerificationError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contained(root: Path, candidate: Path, code: str) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise LegacyArchiveVerificationError(code) from exc
    return resolved


def _output_root(canonical_root: Path) -> Path:
    canonical = canonical_root.resolve()
    if canonical.name != "canonical-research-data":
        raise LegacyArchiveVerificationError("CANONICAL_ROOT_NAME_INVALID")
    return _contained(canonical, canonical / "archive" / OUTPUT_DIRNAME,
                      "OUTPUT_OUTSIDE_CANONICAL_ROOT")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def _load_latest(journal: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    if not journal.is_file():
        return latest
    with journal.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LegacyArchiveVerificationError(
                    f"VERIFICATION_JOURNAL_INVALID_LINE:{line_number}"
                ) from exc
            if row.get("schema") != JOURNAL_SCHEMA:
                raise LegacyArchiveVerificationError(
                    f"VERIFICATION_JOURNAL_SCHEMA_INVALID:{line_number}"
                )
            key = (str(row.get("session_id")), str(row.get("receipt_sha256")),
                   str(row.get("preserved_path")))
            latest[key] = row
    return latest


def verify_legacy_archive(
    archive_root: str | Path,
    canonical_root: str | Path,
    *,
    max_files: int = 500,
) -> dict[str, Any]:
    """Verify at most ``max_files`` new/stale payloads and publish a summary."""
    if max_files < 0:
        raise ValueError("MAX_FILES_MUST_BE_NONNEGATIVE")
    archive = Path(archive_root).resolve()
    canonical = Path(canonical_root).resolve()
    output = _output_root(canonical)
    output.mkdir(parents=True, exist_ok=True)
    journal = _contained(canonical, output / JOURNAL_NAME, "JOURNAL_OUTSIDE_CANONICAL_ROOT")
    index_path = _contained(canonical, output / INDEX_NAME, "INDEX_OUTSIDE_CANONICAL_ROOT")
    latest = _load_latest(journal)
    processed = reused = 0
    session_material: list[dict[str, Any]] = []

    for session in sorted(archive.glob("session_*")):
        if not session.is_dir():
            continue
        _contained(archive, session, "SESSION_OUTSIDE_ARCHIVE_ROOT")
        receipt_path = session / "archive_meta.json"
        errors: set[str] = set()
        if not receipt_path.is_file():
            session_material.append({"session_id": session.name, "receipt_sha256": None,
                                     "declared_verified": False, "inventory": [],
                                     "errors": {"ARCHIVE_RECEIPT_MISSING"}})
            continue
        receipt_sha = _sha256(receipt_path)
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            session_material.append({"session_id": session.name, "receipt_sha256": receipt_sha,
                                     "declared_verified": False, "inventory": [],
                                     "errors": {"ARCHIVE_RECEIPT_INVALID"}})
            continue
        inventory = receipt.get("source_inventory") or []
        if not isinstance(inventory, list):
            inventory = []
            errors.add("ARCHIVE_INVENTORY_INVALID")
        declared = bool((receipt.get("integrity") or {}).get("verified"))
        if not inventory:
            errors.add("ARCHIVE_INVENTORY_EMPTY")
        material = {"session_id": session.name, "receipt_sha256": receipt_sha,
                    "declared_verified": declared, "inventory": [], "errors": errors}
        for item in inventory:
            if not isinstance(item, Mapping):
                material["inventory"].append({"path": None, "status": "INVALID",
                                              "error": "ARCHIVE_INVENTORY_ROW_INVALID"})
                continue
            relative_text = str(item.get("preserved_path") or "")
            expected = str(item.get("preserved_sha256") or "").lower()
            row: dict[str, Any] = {"path": relative_text, "expected_sha256": expected}
            try:
                candidate = _contained(session, session / relative_text,
                                       "ARCHIVE_PATH_OUTSIDE_SESSION")
            except LegacyArchiveVerificationError:
                row.update(status="INVALID", error="ARCHIVE_PATH_OUTSIDE_SESSION")
                material["inventory"].append(row)
                continue
            if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
                row.update(status="INVALID", error="ARCHIVE_EXPECTED_CHECKSUM_INVALID")
            elif not candidate.is_file():
                row.update(status="INVALID", error="ARCHIVE_PAYLOAD_MISSING")
            else:
                stat = candidate.stat()
                key = (session.name, receipt_sha, relative_text)
                prior = latest.get(key)
                reusable = bool(
                    prior and prior.get("status") == "VERIFIED"
                    and prior.get("expected_sha256") == expected
                    and prior.get("size") == stat.st_size
                    and prior.get("mtime_ns") == stat.st_mtime_ns
                )
                if reusable:
                    row.update(status="VERIFIED", size=stat.st_size,
                               mtime_ns=stat.st_mtime_ns, reused=True)
                    reused += 1
                elif processed >= max_files:
                    row.update(status="PENDING", size=stat.st_size, mtime_ns=stat.st_mtime_ns)
                else:
                    actual = _sha256(candidate)
                    status = "VERIFIED" if actual == expected else "INVALID"
                    receipt_row = {
                        "schema": JOURNAL_SCHEMA, "recorded_at": _utc_now(),
                        "session_id": session.name, "receipt_sha256": receipt_sha,
                        "preserved_path": relative_text, "expected_sha256": expected,
                        "actual_sha256": actual, "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns, "status": status,
                        "error": None if status == "VERIFIED" else "ARCHIVE_CHECKSUM_MISMATCH",
                    }
                    with journal.open("a", encoding="utf-8", newline="\n") as handle:
                        handle.write(json.dumps(receipt_row, sort_keys=True, separators=(",", ":")) + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    latest[key] = receipt_row
                    row.update(status=status, size=stat.st_size, mtime_ns=stat.st_mtime_ns,
                               error=receipt_row["error"], reused=False)
                    processed += 1
            material["inventory"].append(row)
        session_material.append(material)

    sessions: list[dict[str, Any]] = []
    unique_checksums: set[str] = set()
    for material in session_material:
        rows = material["inventory"]
        counts = {name: sum(row.get("status") == name for row in rows)
                  for name in ("VERIFIED", "PENDING", "INVALID")}
        errors = set(material["errors"])
        errors.update(str(row.get("error")) for row in rows if row.get("error"))
        if counts["INVALID"]:
            status = "INVALID"
        elif counts["PENDING"]:
            status = "PENDING"
        elif material["declared_verified"] and rows:
            status = "VERIFIED"
        else:
            status = "UNVERIFIABLE"
        unique_checksums.update(row["expected_sha256"] for row in rows
                                if row.get("status") == "VERIFIED")
        sessions.append({
            "session_id": material["session_id"],
            "receipt_sha256": material["receipt_sha256"],
            "verification_status": status,
            "declared_integrity_verified": material["declared_verified"],
            "inventory_file_count": len(rows),
            "verified_file_count": counts["VERIFIED"],
            "pending_file_count": counts["PENDING"],
            "invalid_file_count": counts["INVALID"],
            "error_codes": sorted(errors),
        })
    status_counts = {name: sum(row["verification_status"] == name for row in sessions)
                     for name in ("VERIFIED", "PENDING", "UNVERIFIABLE", "INVALID")}
    index = {
        "schema": SCHEMA, "generated_at": _utc_now(),
        "source_archive_root": str(archive), "canonical_root": str(canonical),
        "append_first_journal": str(journal.relative_to(canonical)).replace("\\", "/"),
        "archive_session_count": len(sessions),
        "verified_session_count": status_counts["VERIFIED"],
        "pending_session_count": status_counts["PENDING"],
        "unverifiable_session_count": status_counts["UNVERIFIABLE"],
        "invalid_session_count": status_counts["INVALID"],
        "verified_file_count": sum(row["verified_file_count"] for row in sessions),
        "pending_file_count": sum(row["pending_file_count"] for row in sessions),
        "invalid_file_count": sum(row["invalid_file_count"] for row in sessions),
        "verified_unique_checksum_count": len(unique_checksums),
        "files_hashed_this_run": processed, "file_receipts_reused_this_run": reused,
        "complete": status_counts["PENDING"] == 0,
        "sessions": sessions,
    }
    stable = dict(index)
    stable.pop("generated_at")
    index["index_payload_sha256"] = hashlib.sha256(
        json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    _atomic_json(index_path, index)
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", required=True, type=Path)
    parser.add_argument("--canonical-root", required=True, type=Path)
    parser.add_argument("--max-files", type=int, default=500)
    args = parser.parse_args(argv)
    report = verify_legacy_archive(args.archive_root, args.canonical_root,
                                   max_files=args.max_files)
    print(json.dumps({key: report[key] for key in (
        "archive_session_count", "verified_session_count", "pending_session_count",
        "unverifiable_session_count", "invalid_session_count", "verified_file_count",
        "pending_file_count", "invalid_file_count", "files_hashed_this_run",
        "file_receipts_reused_this_run", "complete")}, sort_keys=True))
    return 0 if not report["invalid_session_count"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
