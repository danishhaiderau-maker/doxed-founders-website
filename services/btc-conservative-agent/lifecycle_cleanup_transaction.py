"""Fail-closed lifecycle bundle quarantine transaction.

Production execution is disabled unless the caller explicitly supplies
``enabled=True``.  COMMITTED means the exact bundle was atomically moved into a
contained quarantine; permanent deletion is deliberately outside this module.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


SCHEMA = "lifecycle_cleanup_transaction_v1"
ATTESTATION_SCHEMA = "lifecycle_laptop_attestation_v1"
TERMINAL_OUTCOMES = frozenset({"FULL_FILL", "PARTIAL_FILL", "NO_FILL", "UNKNOWN"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REVISION = re.compile(r"[0-9a-f]{7,64}")
_TIMESTAMP_KEYS = (
    "timestamp", "ts", "observed_at", "observed_ts", "created_at", "created_ts",
    "event_at", "event_ts", "terminal_at", "terminal_ts", "closed_at", "closed_ts",
)


class CleanupRejected(RuntimeError):
    def __init__(self, reasons: list[str]):
        self.reasons = sorted(set(reasons))
        super().__init__(",".join(self.reasons))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _utc(value: Any) -> str | None:
    if isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def _row_timestamp(row: Mapping[str, Any]) -> str | None:
    for key in _TIMESTAMP_KEYS:
        if key in row:
            value = _utc(row.get(key))
            if value:
                return value
    return None


def recompute_file(path: Path) -> dict[str, Any]:
    stat = path.stat()
    result = {"size": stat.st_size, "sha256": _sha256_path(path)}
    if path.suffix.lower() != ".jsonl":
        result.update({"row_count": 0, "first_timestamp": None, "last_timestamp": None})
        return result
    count = 0
    timestamps: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("JSONL_ROW_NOT_OBJECT")
            count += 1
            timestamp = _row_timestamp(row)
            if timestamp:
                timestamps.append(timestamp)
    result.update({
        "row_count": count,
        "first_timestamp": min(timestamps) if timestamps else None,
        "last_timestamp": max(timestamps) if timestamps else None,
    })
    return result


def attestation_material(receipt: Mapping[str, Any]) -> dict[str, Any]:
    laptop = receipt.get("laptop_acknowledgement") or {}
    return {
        "schema": ATTESTATION_SCHEMA,
        "bundle_id": receipt.get("bundle_id"),
        "lifecycle_id": receipt.get("lifecycle_id"),
        "immutable_identity_sha256": receipt.get("immutable_identity_sha256"),
        "manifest_sha256": receipt.get("manifest_sha256"),
        "canonical_sha256": (laptop.get("canonical") or {}).get("sha256"),
        "archive_sha256": (laptop.get("archive") or {}).get("sha256"),
        "index_sha256": (laptop.get("index") or {}).get("sha256"),
    }


def sign_attestation(receipt: Mapping[str, Any], key: bytes, key_id: str) -> dict[str, str]:
    material = attestation_material(receipt)
    return {
        "schema": ATTESTATION_SCHEMA,
        "key_id": key_id,
        "hmac_sha256": hmac.new(key, _canonical(material), hashlib.sha256).hexdigest(),
    }


def verify_bundle(
    bundle_root: Path,
    receipt: Mapping[str, Any],
    *,
    current_identity: Mapping[str, Any],
    active_references: Mapping[str, list[str]],
    attestation_keys: Mapping[str, bytes],
) -> dict[str, Any]:
    reasons: list[str] = []
    root = bundle_root.resolve(strict=True)
    if root.is_symlink():
        reasons.append("SOURCE_REPARSE_POINT")
    manifest_path = root / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise CleanupRejected(["MANIFEST_UNREADABLE"])
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list) or not files:
        raise CleanupRejected(["MANIFEST_FILES_INVALID"])

    declared: set[str] = set()
    recomputed = []
    for row in files:
        relative = str((row or {}).get("path") or "")
        try:
            candidate = (root / relative).resolve(strict=True)
            candidate.relative_to(root)
        except (OSError, ValueError):
            reasons.append("SOURCE_PATH_UNSAFE_OR_MISSING")
            continue
        if not relative or relative in declared or candidate.is_symlink() or not candidate.is_file():
            reasons.append("SOURCE_PATH_UNSAFE_OR_DUPLICATE")
            continue
        declared.add(relative)
        try:
            actual = recompute_file(candidate)
        except (OSError, ValueError, json.JSONDecodeError):
            reasons.append("SOURCE_PAYLOAD_UNREADABLE")
            continue
        expected_first = _utc(row.get("first_timestamp"))
        expected_last = _utc(row.get("last_timestamp"))
        if actual["sha256"] != str(row.get("sha256") or "").lower() or actual["size"] != row.get("size"):
            reasons.append("SOURCE_SHA_OR_SIZE_MISMATCH")
        if actual["row_count"] != row.get("row_count"):
            reasons.append("SOURCE_ROW_COUNT_MISMATCH")
        if actual["first_timestamp"] != expected_first or actual["last_timestamp"] != expected_last:
            reasons.append("SOURCE_TIMESTAMP_RANGE_MISMATCH")
        recomputed.append({"path": relative, **actual})
    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*") if path.is_file() and path.name != "manifest.json"
    }
    if actual_paths != declared:
        reasons.append("SOURCE_MANIFEST_INCOMPLETE")

    identity_pairs = {
        "source_git_rev": "source_revision",
        "deployed_git_rev": "deployed_revision",
        "collection_epoch_id": "collection_epoch_id",
        "tile_registry_signature": "tile_config_signature",
        "config_signature": "config_signature",
    }
    manifest_identity = manifest.get("identity") or {}
    manifest_provenance = manifest.get("provenance") or {}
    combined = {**manifest_identity, **manifest_provenance}
    for receipt_key, manifest_key in identity_pairs.items():
        value = str(receipt.get(receipt_key) or "")
        if not value or value != str(combined.get(manifest_key) or "") or value != str(current_identity.get(receipt_key) or ""):
            reasons.append(f"CURRENT_{receipt_key.upper()}_MISMATCH")
    if not _REVISION.fullmatch(str(receipt.get("source_git_rev") or "")) or receipt.get("source_git_rev") != receipt.get("deployed_git_rev"):
        reasons.append("EXACT_REVISION_PARITY_MISSING")
    if not _SHA256.fullmatch(str(receipt.get("tile_registry_signature") or "")) or not _SHA256.fullmatch(str(receipt.get("config_signature") or "")):
        reasons.append("CONFIG_IDENTITY_INVALID")
    if str(receipt.get("terminal_outcome") or "").upper() not in TERMINAL_OUTCOMES or not _utc(receipt.get("terminal_at")):
        reasons.append("TERMINAL_OR_UNKNOWN_INVALID")
    for kind in ("runtime", "sync", "analyzer", "lifecycle_worker"):
        if active_references.get(kind):
            reasons.append(f"ACTIVE_{kind.upper()}_REFERENCE")

    attestation = receipt.get("laptop_attestation") or {}
    key_id = str(attestation.get("key_id") or "")
    key = attestation_keys.get(key_id)
    expected = hmac.new(key or b"", _canonical(attestation_material(receipt)), hashlib.sha256).hexdigest()
    if attestation.get("schema") != ATTESTATION_SCHEMA or not key or not hmac.compare_digest(expected, str(attestation.get("hmac_sha256") or "")):
        reasons.append("LAPTOP_ATTESTATION_INVALID")
    if reasons:
        raise CleanupRejected(reasons)
    return {"manifest": manifest, "recomputed_files": recomputed}


class CleanupTransaction:
    def __init__(self, volume_root: Path, *, enabled: bool = False):
        self.volume_root = volume_root.resolve()
        self.enabled = bool(enabled)
        self.tx_root = self.volume_root / "v3" / "lifecycle_cleanup_transactions"
        self.quarantine_root = self.volume_root / "v3" / "lifecycle_cleanup_quarantine"

    def _contained(self, path: Path, parent: Path) -> Path:
        resolved = path.resolve()
        resolved.relative_to(parent.resolve())
        return resolved

    def _write_once(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = _canonical(payload) + b"\n"
        if path.exists():
            if path.read_bytes() != data:
                raise CleanupRejected(["TRANSACTION_CONFLICT"])
            return
        temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def execute(
        self, bundle_root: Path, receipt: Mapping[str, Any], proof: Mapping[str, Any], *,
        revalidate: Callable[[], Mapping[str, Any]] | None = None,
        failpoint: str | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "DISABLED_SOURCE_RETAINED", "source_cleanup_authorized": False}
        source_parent = (self.volume_root / "v3" / "lifecycle_bundles").resolve()
        source = self._contained(bundle_root, source_parent)
        bundle_id = str(receipt.get("bundle_id") or "")
        if source.name != bundle_id or not re.fullmatch(r"lifecycle-[0-9a-f]{64}", bundle_id):
            raise CleanupRejected(["BUNDLE_PATH_IDENTITY_MISMATCH"])
        quarantine = self._contained(self.quarantine_root / bundle_id, self.quarantine_root)
        transaction_dir = self.tx_root / hashlib.sha256(bundle_id.encode("utf-8")).hexdigest()[:24]
        prepared = transaction_dir / "PREPARED.json"
        committed = transaction_dir / "COMMITTED.json"
        proof_sha = hashlib.sha256(_canonical(proof)).hexdigest()
        transaction = {"schema": SCHEMA, "bundle_id": bundle_id, "source": source.relative_to(self.volume_root).as_posix(), "quarantine": quarantine.relative_to(self.volume_root).as_posix(), "proof_sha256": proof_sha}
        self._write_once(prepared, {**transaction, "state": "PREPARED"})
        if failpoint == "AFTER_PREPARED":
            raise RuntimeError("FAILPOINT_AFTER_PREPARED")
        if revalidate is None:
            raise CleanupRejected(["PRE_MOVE_REVALIDATION_REQUIRED"])
        fresh_proof = revalidate()
        if hashlib.sha256(_canonical(fresh_proof)).hexdigest() != proof_sha:
            raise CleanupRejected(["PRE_MOVE_PROOF_DRIFT"])
        quarantine.parent.mkdir(parents=True, exist_ok=True)
        if source.exists() and not quarantine.exists():
            source.replace(quarantine)
        elif source.exists() == quarantine.exists():
            raise CleanupRejected(["QUARANTINE_STATE_CONFLICT"])
        if failpoint == "AFTER_QUARANTINE":
            raise RuntimeError("FAILPOINT_AFTER_QUARANTINE")
        self._write_once(committed, {**transaction, "state": "COMMITTED"})
        return {"status": "COMMITTED_QUARANTINED", "source_cleanup_authorized": True, "quarantine": transaction["quarantine"]}

    def reconcile(self) -> list[dict[str, Any]]:
        results = []
        if not self.enabled or not self.tx_root.exists():
            return results
        for prepared in self.tx_root.glob("*/PREPARED.json"):
            row = json.loads(prepared.read_text(encoding="utf-8"))
            committed = prepared.parent / "COMMITTED.json"
            if committed.exists():
                continue
            source = self._contained(self.volume_root / row["source"], self.volume_root / "v3" / "lifecycle_bundles")
            quarantine = self._contained(self.volume_root / row["quarantine"], self.quarantine_root)
            if source.exists() and not quarantine.exists():
                results.append({"bundle_id": row["bundle_id"], "status": "PREPARED_AWAITING_REVALIDATION"})
                continue
            elif source.exists() == quarantine.exists():
                raise CleanupRejected(["QUARANTINE_STATE_CONFLICT"])
            committed_payload = dict(row)
            committed_payload["state"] = "COMMITTED"
            self._write_once(committed, committed_payload)
            results.append({"bundle_id": row["bundle_id"], "status": "COMMITTED_QUARANTINED"})
        return results
