"""Canonical local research-store identity, manifest, and safe retention helpers.

Fly's persistent ``bot_data`` volume remains the production authority.  The
desktop store is a verified derivative used by the analyzer; it never uploads
raw evidence back to Fly.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


STORE_SCHEMA = "canonical_research_store_v1"
MANIFEST_SCHEMA = "canonical_research_manifest_v1"
STORE_DIRNAME = "canonical-research-data"
CURRENT_MANIFEST = "canonical_dataset_current.json"
MANIFEST_JOURNAL = "canonical_dataset_manifest.jsonl"
PARITY_STATUS = "canonical_dataset_parity.json"


class CanonicalStoreError(RuntimeError):
    """A canonical-store safety or identity contract was violated."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_store_root(project_root: str | os.PathLike[str]) -> Path:
    project = Path(project_root).resolve()
    return project / "services" / "btc-conservative-agent" / STORE_DIRNAME


def assert_store_root(
    root: str | os.PathLike[str], project_root: str | os.PathLike[str]
) -> Path:
    project = Path(project_root).resolve()
    resolved = Path(root).resolve()
    try:
        relative = resolved.relative_to(project)
    except ValueError as exc:
        raise CanonicalStoreError("STORE_ROOT_OUTSIDE_PROJECT") from exc
    if not relative.parts or resolved.name != STORE_DIRNAME:
        raise CanonicalStoreError("STORE_ROOT_NAME_OR_SCOPE_INVALID")
    if "onedrive" in {part.lower() for part in resolved.parts}:
        raise CanonicalStoreError("STORE_ROOT_ONEDRIVE_FORBIDDEN")
    return resolved


def contained_path(
    root: str | os.PathLike[str], candidate: str | os.PathLike[str], *, allow_root: bool = False
) -> Path:
    resolved_root = Path(root).resolve()
    resolved = Path(candidate).resolve()
    try:
        relative = resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise CanonicalStoreError("PATH_OUTSIDE_CANONICAL_STORE") from exc
    if not allow_root and not relative.parts:
        raise CanonicalStoreError("CANONICAL_STORE_ROOT_OPERATION_FORBIDDEN")
    return resolved


def initialize_store(
    root: str | os.PathLike[str], project_root: str | os.PathLike[str]
) -> Path:
    resolved = assert_store_root(root, project_root)
    resolved.mkdir(parents=True, exist_ok=True)
    for name in ("archive", "backups", "migration"):
        (resolved / name).mkdir(exist_ok=True)
    receipt = {
        "schema": STORE_SCHEMA,
        "authority": "FLY_PERSISTENT_VOLUME_READ_ONLY_DERIVATIVE",
        "fly_volume": "bot_data:/app/data",
        "bidirectional_sync": False,
        "root": str(resolved),
    }
    _atomic_json(resolved / "canonical_store.json", receipt)
    return resolved


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate: str | None = None
    try:
        fd, candidate = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(candidate, path)
        candidate = None
    finally:
        if candidate:
            Path(candidate).unlink(missing_ok=True)


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_current(root: Path) -> dict[str, Any] | None:
    path = root / CURRENT_MANIFEST
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise CanonicalStoreError("CURRENT_MANIFEST_INVALID")
    return payload


def append_manifest(root: str | os.PathLike[str], fields: Mapping[str, Any]) -> dict[str, Any]:
    store = Path(root).resolve()
    required = (
        "dataset_epoch",
        "source_revision",
        "tile_config_signature",
        "collection_started_at",
        "collection_observed_at",
        "row_count",
        "opportunity_count",
        "dataset_checksum",
        "analyzer_status",
        "analyzer_completed_at",
        "analyzer_schema_version",
    )
    missing = [name for name in required if name not in fields]
    if missing:
        raise CanonicalStoreError("MANIFEST_FIELDS_MISSING:" + ",".join(missing))
    journal = store / MANIFEST_JOURNAL
    current_path = store / CURRENT_MANIFEST
    if journal.exists() or current_path.exists():
        validate_manifest_chain(store)
    previous = _read_current(store)
    previous_hash = str((previous or {}).get("entry_hash") or "")
    body = {
        "schema": MANIFEST_SCHEMA,
        "recorded_at": _utc_now(),
        "previous_entry_hash": previous_hash or None,
        **dict(fields),
    }
    body["entry_hash"] = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    _atomic_json(store / CURRENT_MANIFEST, body)
    return body


def record_analyzer_completion(
    root: str | os.PathLike[str],
    *,
    report_manifest_path: str | os.PathLike[str],
    analyzer_schema_version: str,
    completed_at: str,
) -> dict[str, Any]:
    """Append completion for the current immutable dataset generation."""
    store = Path(root).resolve()
    validate_manifest_chain(store)
    current = _read_current(store)
    if not current:
        raise CanonicalStoreError("CANONICAL_DATASET_MANIFEST_MISSING")
    report_path = contained_path(store, report_manifest_path)
    if not report_path.is_file():
        raise CanonicalStoreError("ANALYZER_REPORT_MANIFEST_MISSING")
    fields = {
        key: value
        for key, value in current.items()
        if key not in {"schema", "recorded_at", "previous_entry_hash", "entry_hash"}
    }
    fields.update(
        {
            "analyzer_status": "COMPLETE",
            "analyzer_completed_at": completed_at,
            "analyzer_schema_version": analyzer_schema_version,
            "analyzer_report_manifest_relative": str(report_path.relative_to(store)).replace("\\", "/"),
            "analyzer_report_manifest_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        }
    )
    return append_manifest(store, fields)


def validate_manifest_chain(root: str | os.PathLike[str]) -> list[dict[str, Any]]:
    store = Path(root).resolve()
    rows: list[dict[str, Any]] = []
    previous = ""
    journal = store / MANIFEST_JOURNAL
    if not journal.is_file():
        return rows
    for line_number, raw in enumerate(journal.read_text(encoding="utf-8").splitlines(), 1):
        row = json.loads(raw)
        if not isinstance(row, dict) or row.get("schema") != MANIFEST_SCHEMA:
            raise CanonicalStoreError(f"MANIFEST_ROW_INVALID:{line_number}")
        expected = str(row.get("entry_hash") or "")
        unhashed = dict(row)
        unhashed.pop("entry_hash", None)
        actual = hashlib.sha256(_canonical_bytes(unhashed)).hexdigest()
        if expected != actual or str(row.get("previous_entry_hash") or "") != previous:
            raise CanonicalStoreError(f"MANIFEST_CHAIN_INVALID:{line_number}")
        rows.append(row)
        previous = expected
    current = _read_current(store)
    if rows and (not current or current.get("entry_hash") != rows[-1].get("entry_hash")):
        raise CanonicalStoreError("CURRENT_MANIFEST_POINTER_MISMATCH")
    return rows


def parity_status(
    local: Mapping[str, Any] | None,
    remote: Mapping[str, Any],
) -> dict[str, Any]:
    local = dict(local or {})
    keys = {
        "dataset_epoch": "dataset_epoch",
        "source_revision": "source_revision",
        "tile_config_signature": "tile_config_signature",
    }
    mismatches = {
        local_key: {"local": local.get(local_key), "fly": remote.get(remote_key)}
        for local_key, remote_key in keys.items()
        if not local.get(local_key) or local.get(local_key) != remote.get(remote_key)
    }
    return {
        "schema": "canonical_research_parity_v1",
        "authority": "FLY_PERSISTENT_VOLUME",
        "ok": not mismatches,
        "status": "MATCH" if not mismatches else "MISMATCH",
        "mismatches": mismatches,
    }


def publish_parity_status(
    root: str | os.PathLike[str], remote: Mapping[str, Any]
) -> dict[str, Any]:
    """Atomically publish parity for the latest append-only manifest entry."""
    store = Path(root).resolve()
    validate_manifest_chain(store)
    current = _read_current(store)
    status = parity_status(current, remote)
    status["manifest_entry_hash"] = (current or {}).get("entry_hash")
    status["recorded_at"] = _utc_now()
    _atomic_json(store / PARITY_STATUS, status)
    return status


def require_analyzer_dataset(
    root: str | os.PathLike[str], expected: Mapping[str, Any]
) -> dict[str, Any]:
    store = Path(root).resolve()
    validate_manifest_chain(store)
    current = _read_current(store)
    if not current:
        raise CanonicalStoreError("CANONICAL_DATASET_MANIFEST_MISSING")
    parity = parity_status(current, expected)
    if not parity["ok"]:
        raise CanonicalStoreError("CANONICAL_DATASET_PARITY_MISMATCH")
    return current


def archive_before_cleanup(
    root: str | os.PathLike[str], candidate: str | os.PathLike[str], *, reason: str
) -> dict[str, Any]:
    store = Path(root).resolve()
    source = contained_path(store, candidate)
    if not source.exists():
        raise CanonicalStoreError("CLEANUP_TARGET_MISSING")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = contained_path(
        store, store / "archive" / f"{stamp}-{source.name}", allow_root=False
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise CanonicalStoreError("ARCHIVE_DESTINATION_EXISTS")
    shutil.move(str(source), str(destination))
    receipt = {
        "schema": "canonical_research_cleanup_receipt_v1",
        "archived_at": _utc_now(),
        "reason": reason,
        "source_relative": str(source.relative_to(store)).replace("\\", "/"),
        "archive_relative": str(destination.relative_to(store)).replace("\\", "/"),
        "recoverable": True,
    }
    _atomic_json(destination.parent / f"{destination.name}.receipt.json", receipt)
    return receipt
