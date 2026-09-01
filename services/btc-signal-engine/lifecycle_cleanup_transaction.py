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
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


SCHEMA = "lifecycle_cleanup_transaction_v2"
PURGE_SCHEMA = "lifecycle_cleanup_purge_v1"
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


def _sha256_canonical(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _cleanup_manifest_sha256(files: list[Mapping[str, Any]]) -> str:
    material = [{
        "path": str(row["path"]),
        "sha256": str(row["sha256"]).lower(),
        "size": int(row["size"]),
        "mtime_ns": int(row["mtime_ns"]),
        "row_count": int(row["row_count"]),
        "first_timestamp": str(row["first_timestamp"]),
        "last_timestamp": str(row["last_timestamp"]),
    } for row in sorted(files, key=lambda item: str(item["path"]))]
    return _sha256_canonical(material)


def _immutable_identity_sha256(receipt: Mapping[str, Any]) -> str:
    material = {
        "bundle_id": str(receipt.get("bundle_id") or ""),
        "lifecycle_id": str(receipt.get("lifecycle_id") or ""),
        "source_git_rev": str(receipt.get("source_git_rev") or ""),
        "deployed_git_rev": str(receipt.get("deployed_git_rev") or ""),
        "collection_epoch_id": str(receipt.get("collection_epoch_id") or ""),
        "tile_registry_signature": str(receipt.get("tile_registry_signature") or ""),
        "terminal_outcome": str(receipt.get("terminal_outcome") or ""),
        "terminal_at": str(receipt.get("terminal_at") or ""),
        "manifest_sha256": str(receipt.get("manifest_sha256") or "").lower(),
    }
    return _sha256_canonical(material)


def _commit_binding(receipt: Mapping[str, Any]) -> dict[str, Any]:
    attestation = receipt.get("laptop_attestation") or {}
    return {
        "receipt_sha256": _sha256_canonical(receipt),
        "attestation_sha256": _sha256_canonical(attestation),
        "attestation_key_id": str(attestation.get("key_id") or ""),
        "cleanup_manifest_sha256": str(receipt.get("manifest_sha256") or "").lower(),
        "immutable_identity_sha256": str(receipt.get("immutable_identity_sha256") or "").lower(),
        "bundle_id": str(receipt.get("bundle_id") or ""),
        "lifecycle_id": str(receipt.get("lifecycle_id") or ""),
        "source_git_rev": str(receipt.get("source_git_rev") or ""),
        "deployed_git_rev": str(receipt.get("deployed_git_rev") or ""),
        "collection_epoch_id": str(receipt.get("collection_epoch_id") or ""),
        "tile_registry_signature": str(receipt.get("tile_registry_signature") or ""),
        "config_signature": str(receipt.get("config_signature") or ""),
    }


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

    cleanup_manifest_sha = str(manifest.get("cleanup_manifest_sha256") or "").lower()
    try:
        recomputed_cleanup_sha = _cleanup_manifest_sha256(files)
    except (KeyError, TypeError, ValueError):
        recomputed_cleanup_sha = ""
    if not _SHA256.fullmatch(cleanup_manifest_sha) or cleanup_manifest_sha != recomputed_cleanup_sha:
        reasons.append("CLEANUP_MANIFEST_SHA256_MISMATCH")
    if str(receipt.get("manifest_sha256") or "").lower() != cleanup_manifest_sha:
        reasons.append("RECEIPT_CLEANUP_MANIFEST_SHA256_MISMATCH")
    supplied_identity = str(receipt.get("immutable_identity_sha256") or "").lower()
    if not _SHA256.fullmatch(supplied_identity) or not hmac.compare_digest(
        supplied_identity, _immutable_identity_sha256(receipt)
    ):
        reasons.append("IMMUTABLE_IDENTITY_SHA256_MISMATCH")

    laptop = receipt.get("laptop_acknowledgement") or {}
    for copy_name in ("canonical", "archive", "index"):
        copy_ack = laptop.get(copy_name) if isinstance(laptop, Mapping) else None
        if not (
            isinstance(copy_ack, Mapping)
            and copy_ack.get("complete") is True
            and str(copy_ack.get("bundle_id") or "") == str(receipt.get("bundle_id") or "")
            and str(copy_ack.get("lifecycle_id") or "") == str(receipt.get("lifecycle_id") or "")
            and _SHA256.fullmatch(str(copy_ack.get("sha256") or "").lower())
            and str(copy_ack.get("manifest_sha256") or "").lower() == cleanup_manifest_sha
            and _utc(copy_ack.get("acknowledged_at")) is not None
        ):
            reasons.append(f"LAPTOP_{copy_name.upper()}_ACK_INVALID")
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
    return {
        "manifest": manifest, "recomputed_files": recomputed,
        "commit_binding": _commit_binding(receipt),
    }


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
            if os.name != "nt":
                directory_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
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
        binding = _commit_binding(receipt)
        if proof.get("commit_binding") != binding:
            raise CleanupRejected(["PROOF_RECEIPT_BINDING_MISMATCH"])
        transaction = {
            "schema": SCHEMA, "bundle_id": bundle_id,
            "source": source.relative_to(self.volume_root).as_posix(),
            "quarantine": quarantine.relative_to(self.volume_root).as_posix(),
            "proof_sha256": proof_sha, **binding,
        }
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
        self._write_once(committed, {
            **transaction, "state": "COMMITTED",
            "committed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        })
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
            committed_payload["committed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            self._write_once(committed, committed_payload)
            results.append({"bundle_id": row["bundle_id"], "status": "COMMITTED_QUARANTINED"})
        return results


class PurgeTransaction(CleanupTransaction):
    """Proof-bound deletion of an aged v2 committed lifecycle quarantine.

    This class has no production caller and defaults disabled.  A durable plan
    is published before the quarantine is atomically isolated in purge staging;
    retries can then remove only the exact declared files still present.
    """

    def __init__(
        self, volume_root: Path, *, enabled: bool = False,
        minimum_age_seconds: float = 86_400,
    ):
        super().__init__(volume_root, enabled=enabled)
        self.minimum_age_seconds = max(0.0, float(minimum_age_seconds))
        self.purge_tx_root = self.volume_root / "v3" / "lifecycle_purge_transactions"
        self.purge_staging_root = self.volume_root / "v3" / "lifecycle_purge_staging"

    @staticmethod
    def _now(value: datetime | None) -> datetime:
        current = value or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise CleanupRejected(["PURGE_NOW_TIMEZONE_MISSING"])
        return current.astimezone(timezone.utc)

    def _load_committed(self, path: Path) -> tuple[Path, dict[str, Any]]:
        committed = self._contained(path, self.tx_root)
        if committed.name != "COMMITTED.json":
            raise CleanupRejected(["COMMITTED_PATH_INVALID"])
        try:
            row = json.loads(committed.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise CleanupRejected(["COMMITTED_RECORD_UNREADABLE"])
        if row.get("schema") != SCHEMA or row.get("state") != "COMMITTED":
            raise CleanupRejected(["V2_COMMITTED_RECORD_REQUIRED"])
        return committed, row

    @staticmethod
    def _guard_current(
        committed: Mapping[str, Any], current_identity: Mapping[str, Any],
        active_references: Mapping[str, list[str]],
    ) -> None:
        reasons = []
        for key in (
            "source_git_rev", "deployed_git_rev", "collection_epoch_id",
            "tile_registry_signature", "config_signature",
        ):
            if str(current_identity.get(key) or "") != str(committed.get(key) or ""):
                reasons.append(f"CURRENT_{key.upper()}_MISMATCH")
        for kind in ("runtime", "sync", "analyzer", "lifecycle_worker"):
            if active_references.get(kind):
                reasons.append(f"ACTIVE_{kind.upper()}_REFERENCE")
        if reasons:
            raise CleanupRejected(reasons)

    @staticmethod
    def _declared_plan(bundle: Path, proof: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows = [{
            "path": "manifest.json", "sha256": _sha256_path(bundle / "manifest.json"),
            "size": (bundle / "manifest.json").stat().st_size,
        }]
        rows.extend({
            "path": str(row["path"]), "sha256": str(row["sha256"]),
            "size": int(row["size"]),
        } for row in proof.get("recomputed_files") or [])
        return sorted(rows, key=lambda row: row["path"])

    def execute_purge(
        self, committed_path: Path, receipt: Mapping[str, Any], *,
        current_identity: Mapping[str, Any],
        active_references: Mapping[str, list[str]],
        attestation_keys: Mapping[str, bytes],
        now: datetime | None = None,
        disk_free_bytes: Callable[[], int] | None = None,
        delete_file: Callable[[Path], None] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"status": "DISABLED_QUARANTINE_RETAINED", "purge_authorized": False}
        committed_path, committed = self._load_committed(committed_path)
        current = self._now(now)
        committed_at = _utc(committed.get("committed_at"))
        if committed_at is None:
            raise CleanupRejected(["IMMUTABLE_COMMITTED_AT_MISSING"])
        committed_time = datetime.fromisoformat(committed_at.replace("Z", "+00:00"))
        if (current - committed_time).total_seconds() < self.minimum_age_seconds:
            raise CleanupRejected(["MINIMUM_QUARANTINE_AGE_NOT_MET"])
        self._guard_current(committed, current_identity, active_references)

        binding = _commit_binding(receipt)
        for key, value in binding.items():
            if not hmac.compare_digest(str(committed.get(key) or ""), str(value)):
                raise CleanupRejected([f"COMMITTED_{key.upper()}_MISMATCH"])
        attestation = receipt.get("laptop_attestation") or {}
        attestation_key = attestation_keys.get(str(attestation.get("key_id") or ""))
        expected_attestation = hmac.new(
            attestation_key or b"", _canonical(attestation_material(receipt)), hashlib.sha256,
        ).hexdigest()
        if (
            attestation.get("schema") != ATTESTATION_SCHEMA
            or not attestation_key
            or not hmac.compare_digest(
                expected_attestation, str(attestation.get("hmac_sha256") or "")
            )
        ):
            raise CleanupRejected(["LAPTOP_ATTESTATION_INVALID"])
        bundle_id = str(committed.get("bundle_id") or "")
        if not re.fullmatch(r"lifecycle-[0-9a-f]{64}", bundle_id):
            raise CleanupRejected(["BUNDLE_ID_INVALID"])
        quarantine = self._contained(
            self.volume_root / str(committed.get("quarantine") or ""),
            self.quarantine_root,
        )
        if quarantine.name != bundle_id:
            raise CleanupRejected(["QUARANTINE_BUNDLE_ID_MISMATCH"])
        staging = self._contained(self.purge_staging_root / bundle_id, self.purge_staging_root)
        transaction_dir = self.purge_tx_root / hashlib.sha256(bundle_id.encode()).hexdigest()[:24]
        prepared_path = transaction_dir / "PREPARED.json"
        purged_path = transaction_dir / "PURGED.json"
        if purged_path.exists():
            result = json.loads(purged_path.read_text(encoding="utf-8"))
            if result.get("schema") != PURGE_SCHEMA or result.get("receipt_sha256") != binding["receipt_sha256"]:
                raise CleanupRejected(["PURGED_RECEIPT_CONFLICT"])
            return result

        free_bytes = disk_free_bytes or (lambda: int(shutil.disk_usage(self.volume_root).free))
        if prepared_path.exists():
            plan = json.loads(prepared_path.read_text(encoding="utf-8"))
            if plan.get("schema") != PURGE_SCHEMA or plan.get("receipt_sha256") != binding["receipt_sha256"]:
                raise CleanupRejected(["PURGE_PLAN_CONFLICT"])
            if not hmac.compare_digest(
                str(plan.get("committed_sha256") or ""), _sha256_path(committed_path)
            ):
                raise CleanupRejected(["COMMITTED_RECORD_CHANGED_AFTER_PREPARE"])
        else:
            if not quarantine.is_dir() or staging.exists():
                raise CleanupRejected(["QUARANTINE_STATE_INVALID"])
            proof = verify_bundle(
                quarantine, receipt, current_identity=current_identity,
                active_references=active_references, attestation_keys=attestation_keys,
            )
            if _sha256_canonical(proof) != str(committed.get("proof_sha256") or ""):
                raise CleanupRejected(["COMMITTED_PROOF_SHA256_MISMATCH"])
            files = self._declared_plan(quarantine, proof)
            plan = {
                "schema": PURGE_SCHEMA, "state": "PREPARED",
                "bundle_id": bundle_id, "receipt_sha256": binding["receipt_sha256"],
                "committed_sha256": _sha256_path(committed_path),
                "prepared_at": current.isoformat().replace("+00:00", "Z"),
                "before_free_bytes": int(free_bytes()),
                "declared_bytes": sum(int(row["size"]) for row in files),
                "files": files,
            }
            self._write_once(prepared_path, plan)
            staging.parent.mkdir(parents=True, exist_ok=True)
            quarantine.replace(staging)
            if os.name != "nt":
                for parent in (quarantine.parent, staging.parent):
                    directory_fd = os.open(str(parent), os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)

        if quarantine.exists() or not staging.is_dir():
            raise CleanupRejected(["PURGE_STAGING_STATE_INVALID"])
        declared = {str(row["path"]): row for row in plan.get("files") or []}
        expected_directories = {
            parent.as_posix()
            for relative in declared
            for parent in Path(relative).parents
            if parent != Path(".")
        }
        tree_entries = list(staging.rglob("*"))
        if any(path.is_symlink() for path in tree_entries):
            raise CleanupRejected(["PURGE_STAGING_REPARSE_POINT"])
        actual = {
            path.relative_to(staging).as_posix(): path
            for path in tree_entries if path.is_file()
        }
        unexpected = set(actual) - set(declared)
        if unexpected:
            raise CleanupRejected(["PURGE_STAGING_UNDECLARED_CONTENT"])
        actual_directories = {
            path.relative_to(staging).as_posix()
            for path in tree_entries if path.is_dir()
        }
        if actual_directories - expected_directories:
            raise CleanupRejected(["PURGE_STAGING_UNDECLARED_DIRECTORY"])
        remover = delete_file or (lambda path: path.unlink())
        for relative in sorted(actual):
            path = actual[relative]
            expected = declared[relative]
            if path.stat().st_size != int(expected["size"]) or not hmac.compare_digest(
                _sha256_path(path), str(expected["sha256"])
            ):
                raise CleanupRejected(["PURGE_STAGING_CONTENT_MISMATCH"])
            remover(path)
        for directory in sorted(
            (path for path in tree_entries if path.is_dir()),
            key=lambda path: len(path.parts), reverse=True,
        ):
            directory.rmdir()
        staging.rmdir()
        if os.name != "nt":
            directory_fd = os.open(str(staging.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        after_free = int(free_bytes())
        result = {
            "schema": PURGE_SCHEMA, "state": "PURGED", "status": "PURGED",
            "bundle_id": bundle_id, "receipt_sha256": binding["receipt_sha256"],
            "committed_sha256": plan["committed_sha256"],
            "before_free_bytes": int(plan["before_free_bytes"]),
            "after_free_bytes": after_free,
            "freed_bytes": max(0, after_free - int(plan["before_free_bytes"])),
            "declared_bytes": int(plan["declared_bytes"]),
            "purged_at": current.isoformat().replace("+00:00", "Z"),
        }
        self._write_once(purged_path, result)
        return result
