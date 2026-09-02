"""Fail-closed cleanup foundation for sealed V3 and V22 source generations.

This authority is intentionally separate from lifecycle bundle cleanup: a bundle
copy cannot prove that every row in a raw generation is represented.  Callers
must first publish a content manifest that maps every source member to one or
more lifecycle identities.  Mutation is disabled by default and bounded to one
generation per transaction.
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


MANIFEST_SCHEMA = "raw_generation_content_manifest_v1"
ACK_SCHEMA = "raw_generation_laptop_ack_v1"
TX_SCHEMA = "raw_generation_cleanup_transaction_v1"
PURGE_SCHEMA = "raw_generation_purge_receipt_v1"
_SHA = re.compile(r"[0-9a-f]{64}")
_REV = re.compile(r"[0-9a-f]{7,64}")
_KINDS = frozenset({"V3", "V22"})
_LEASE_KINDS = ("reader", "sync", "analyzer")


class RawGenerationCleanupRejected(RuntimeError):
    def __init__(self, reasons: list[str]):
        self.reasons = sorted(set(reasons))
        super().__init__(",".join(self.reasons))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _material_hash(value: Mapping[str, Any], field: str) -> str:
    material = dict(value)
    material.pop(field, None)
    return hashlib.sha256(_canonical(material)).hexdigest()


def _has_symlink_component(path: Path, boundary: Path) -> bool:
    """Check lexical components before resolution can erase symlink evidence."""
    candidate = Path(os.path.abspath(path))
    root = Path(os.path.abspath(boundary))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return True
    current = root
    if current.is_symlink():
        return True
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    encoded = _canonical(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise RawGenerationCleanupRejected(["TRANSACTION_CONFLICT"])
        return
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_quarantine_members(
    quarantine: Path, declared: Any, expected_bytes: Any,
) -> int:
    """Revalidate the exact proof-bound quarantine contents before deletion."""
    if not isinstance(declared, list) or not declared:
        raise RawGenerationCleanupRejected(["QUARANTINE_MEMBER_PROOF_MISSING"])
    actual_paths: set[str] = set()
    exact_bytes = 0
    for row in declared:
        relative = str((row or {}).get("path") or "").replace("\\", "/")
        lexical = quarantine / relative
        if _has_symlink_component(lexical, quarantine):
            raise RawGenerationCleanupRejected(["QUARANTINE_MEMBER_SYMLINK"])
        try:
            member = lexical.resolve(strict=True)
            member.relative_to(quarantine.resolve())
        except (OSError, ValueError):
            raise RawGenerationCleanupRejected(["QUARANTINE_MEMBER_UNSAFE_OR_MISSING"])
        if not member.is_file():
            raise RawGenerationCleanupRejected(["QUARANTINE_MEMBER_INVALID"])
        size = member.stat().st_size
        exact_bytes += size
        actual_paths.add(relative)
        if size != row.get("size") or not hmac.compare_digest(
            _digest(member), str(row.get("sha256") or "")
        ):
            raise RawGenerationCleanupRejected(["QUARANTINE_MEMBER_HASH_OR_SIZE_DRIFT"])
    on_disk = {
        path.relative_to(quarantine).as_posix()
        for path in quarantine.rglob("*") if path.is_file()
    }
    if on_disk != actual_paths:
        raise RawGenerationCleanupRejected(["QUARANTINE_CONTENT_SET_DRIFT"])
    if exact_bytes != expected_bytes:
        raise RawGenerationCleanupRejected(["QUARANTINE_BYTE_COUNT_DRIFT"])
    return exact_bytes


def verify_generation(
    source_root: Path,
    manifest: Mapping[str, Any],
    acknowledgement: Mapping[str, Any],
    *,
    current_identity: Mapping[str, str],
    active_leases: Mapping[str, list[str]],
) -> dict[str, Any]:
    """Verify complete source membership and return an immutable proof.

    UNKNOWN is acceptable only when it is explicit, terminal, horizon-complete,
    and reconciled.  This retains unknown outcomes without pretending that they
    qualified.
    """
    reasons: list[str] = []
    lexical_root = Path(os.path.abspath(source_root))
    if _has_symlink_component(lexical_root, lexical_root.parent):
        raise RawGenerationCleanupRejected(["SOURCE_REPARSE_POINT"])
    root = lexical_root.resolve(strict=True)
    if manifest.get("schema") != MANIFEST_SCHEMA:
        reasons.append("MANIFEST_OR_ROOT_INVALID")
    kind = str(manifest.get("generation_kind") or "")
    generation = manifest.get("generation")
    generation_id = str(manifest.get("generation_id") or "")
    if kind not in _KINDS or not isinstance(generation, int) or isinstance(generation, bool) or generation <= 0:
        reasons.append("SEALED_GENERATION_IDENTITY_INVALID")
    if generation_id != f"{kind}:{generation}":
        reasons.append("SEALED_GENERATION_IDENTITY_INVALID")

    identity = manifest.get("identity") or {}
    required_identity = ("source_revision", "deployed_revision", "collection_epoch_id", "config_signature")
    for field in required_identity:
        value = str(identity.get(field) or "")
        if not value or value != str(current_identity.get(field) or ""):
            reasons.append(f"CURRENT_{field.upper()}_MISMATCH")
    if (not _REV.fullmatch(str(identity.get("source_revision") or ""))
            or identity.get("source_revision") != identity.get("deployed_revision")
            or not _SHA.fullmatch(str(identity.get("config_signature") or ""))):
        reasons.append("EXACT_REV_EPOCH_CONFIG_INVALID")

    members = manifest.get("members")
    declared: set[str] = set()
    exact_bytes = 0
    lifecycle_ids: set[str] = set()
    if not isinstance(members, list) or not members:
        reasons.append("GENERATION_MEMBERS_MISSING")
        members = []
    for member in members:
        relative = str((member or {}).get("path") or "").replace("\\", "/")
        try:
            lexical_path = root / relative
            if _has_symlink_component(lexical_path, root):
                raise ValueError("symlink member")
            path = lexical_path.resolve(strict=True)
            path.relative_to(root)
        except (OSError, ValueError):
            reasons.append("GENERATION_MEMBER_UNSAFE_OR_MISSING")
            continue
        if not relative or relative in declared or path.is_symlink() or not path.is_file():
            reasons.append("GENERATION_MEMBER_UNSAFE_OR_DUPLICATE")
            continue
        declared.add(relative)
        actual_size = path.stat().st_size
        exact_bytes += actual_size
        actual_sha = _digest(path)
        if actual_size != member.get("size") or not hmac.compare_digest(actual_sha, str(member.get("sha256") or "")):
            reasons.append("GENERATION_MEMBER_HASH_OR_SIZE_MISMATCH")
        seal = member.get("seal") or {}
        seal_schema = "v3_ledger_rotation_seal_v1" if kind == "V3" else "research_event_v22_seal_v1"
        seal_size = seal.get("size") if kind == "V3" else seal.get("size_bytes")
        if not (seal.get("schema") == seal_schema and seal.get("generation") == generation
                and (kind != "V22" or seal.get("state") == "SEALED")
                and str(seal.get("relative_path") or "").replace("\\", "/").endswith(relative)
                and seal_size == actual_size and hmac.compare_digest(str(seal.get("sha256") or ""), actual_sha)):
            reasons.append("GENERATION_MEMBER_SEAL_AUTHORITY_INVALID")
        identities = member.get("lifecycle_ids")
        if not isinstance(identities, list) or not identities or any(not str(value) for value in identities):
            reasons.append("GENERATION_MEMBER_LIFECYCLE_MAPPING_MISSING")
        else:
            lifecycle_ids.update(str(value) for value in identities)
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != declared:
        reasons.append("GENERATION_CONTENT_MANIFEST_INCOMPLETE")

    outcomes = manifest.get("lifecycles")
    outcome_by_id = {str(row.get("lifecycle_id") or ""): row for row in outcomes or [] if isinstance(row, Mapping)}
    if set(outcome_by_id) != lifecycle_ids:
        reasons.append("LIFECYCLE_COVERAGE_INCOMPLETE")
    for lifecycle_id in lifecycle_ids:
        row = outcome_by_id.get(lifecycle_id) or {}
        qualified = row.get("qualification_ready") is True and row.get("terminal") is True
        explicit_unknown = (
            row.get("outcome") == "UNKNOWN" and row.get("terminal") is True
            and row.get("horizon_complete") is True and row.get("reconciled") is True
        )
        if not (qualified or explicit_unknown):
            reasons.append("LIFECYCLE_NOT_QUALIFICATION_READY_OR_EXPLICIT_UNKNOWN")

    manifest_hash = str(manifest.get("manifest_sha256") or "")
    if not _SHA.fullmatch(manifest_hash) or not hmac.compare_digest(manifest_hash, _material_hash(manifest, "manifest_sha256")):
        reasons.append("CONTENT_MANIFEST_HASH_INVALID")
    ack_hash = str(acknowledgement.get("acknowledgement_sha256") or "")
    if (acknowledgement.get("schema") != ACK_SCHEMA or acknowledgement.get("immutable") is not True
            or not _SHA.fullmatch(ack_hash)
            or not hmac.compare_digest(ack_hash, _material_hash(acknowledgement, "acknowledgement_sha256"))):
        reasons.append("LAPTOP_ACK_NOT_IMMUTABLE")
    for copy in ("canonical", "archive", "index"):
        row = acknowledgement.get(copy) or {}
        if not (row.get("complete") is True and row.get("generation_id") == generation_id
                and row.get("manifest_sha256") == manifest_hash
                and _SHA.fullmatch(str(row.get("sha256") or ""))):
            reasons.append(f"LAPTOP_{copy.upper()}_ACK_INVALID")
    if acknowledgement.get("identity") != identity:
        reasons.append("LAPTOP_ACK_IDENTITY_MISMATCH")
    for lease_kind in _LEASE_KINDS:
        if active_leases.get(lease_kind):
            reasons.append(f"ACTIVE_GENERATION_{lease_kind.upper()}_LEASE")
    if reasons:
        raise RawGenerationCleanupRejected(reasons)
    proof = {
        "schema": "raw_generation_cleanup_proof_v1", "generation_id": generation_id,
        "manifest_sha256": manifest_hash, "identity": identity,
        "member_count": len(declared), "lifecycle_count": len(lifecycle_ids),
        "source_bytes": exact_bytes,
        "members": [{"path": str(row["path"]), "size": int(row["size"]),
                     "sha256": str(row["sha256"])}
                    for row in sorted(members, key=lambda value: str(value["path"]))],
    }
    proof["proof_sha256"] = _material_hash(proof, "proof_sha256")
    return proof


class RawGenerationCleanupTransaction:
    """Two-phase, restart-safe quarantine and purge; mutation defaults OFF."""

    def __init__(self, authority_root: Path, *, enabled: bool = False):
        self.root = authority_root.resolve()
        self.enabled = bool(enabled)
        self.tx_root = self.root / "raw_generation_cleanup_transactions"
        self.quarantine_root = self.root / "raw_generation_cleanup_quarantine"

    def _paths(self, generation_id: str) -> tuple[Path, Path]:
        key = hashlib.sha256(generation_id.encode()).hexdigest()[:24]
        return self.tx_root / key, self.quarantine_root / key

    def quarantine(self, source: Path, proof: Mapping[str, Any], *,
                   revalidate: Callable[[], Mapping[str, Any]] | None = None,
                   dry_run: bool = True, failpoint: str | None = None) -> dict[str, Any]:
        generation_id = str(proof.get("generation_id") or "")
        tx, destination = self._paths(generation_id)
        preview = {"status": "DRY_RUN_SOURCE_RETAINED", "generation_id": generation_id,
                   "planned_bytes": int(proof.get("source_bytes") or 0), "freed_bytes": 0,
                   "source_cleanup_authorized": False}
        if dry_run or not self.enabled:
            return preview if dry_run else {**preview, "status": "DISABLED_SOURCE_RETAINED"}
        if revalidate is None:
            raise RawGenerationCleanupRejected(["PRE_MOVE_REVALIDATION_REQUIRED"])
        source = source.resolve(strict=True)
        source.relative_to(self.root)
        prepared = {"schema": TX_SCHEMA, "state": "PREPARED", "generation_id": generation_id,
                    "source": source.relative_to(self.root).as_posix(),
                    "quarantine": destination.relative_to(self.root).as_posix(),
                    "proof_sha256": proof.get("proof_sha256"), "source_bytes": proof.get("source_bytes"),
                    "members": proof.get("members")}
        _write_once(tx / "PREPARED.json", prepared)
        if failpoint == "AFTER_PREPARED":
            raise RuntimeError("FAILPOINT_AFTER_PREPARED")
        fresh = revalidate()
        if fresh != proof:
            raise RawGenerationCleanupRejected(["PRE_MOVE_PROOF_DRIFT"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.exists() and not destination.exists():
            source.replace(destination)
        elif source.exists() == destination.exists():
            raise RawGenerationCleanupRejected(["QUARANTINE_STATE_CONFLICT"])
        if failpoint == "AFTER_MOVE":
            raise RuntimeError("FAILPOINT_AFTER_MOVE")
        committed = {**prepared, "state": "QUARANTINED", "committed_at": datetime.now(timezone.utc).isoformat()}
        _write_once(tx / "QUARANTINED.json", committed)
        return {"status": "QUARANTINED_SOURCE_RETAINED", "generation_id": generation_id,
                "quarantined_bytes": int(proof["source_bytes"]), "freed_bytes": 0,
                "source_cleanup_authorized": True}

    def purge(self, generation_id: str, *, dry_run: bool = True,
              failpoint: str | None = None) -> dict[str, Any]:
        tx, quarantine = self._paths(generation_id)
        committed_path = tx / "QUARANTINED.json"
        if not committed_path.is_file() or not quarantine.is_dir():
            raise RawGenerationCleanupRejected(["COMMITTED_QUARANTINE_REQUIRED"])
        committed = json.loads(committed_path.read_text("utf-8"))
        declared = committed.get("members")
        exact_bytes = _verify_quarantine_members(
            quarantine, declared, committed.get("source_bytes"),
        )
        if dry_run or not self.enabled:
            return {"status": "DRY_RUN_QUARANTINE_RETAINED" if dry_run else "DISABLED_QUARANTINE_RETAINED",
                    "generation_id": generation_id, "planned_freed_bytes": exact_bytes, "freed_bytes": 0}
        staging = quarantine.with_name(f".{quarantine.name}.purging")
        purge_prepared = {"schema": TX_SCHEMA, "state": "PURGE_PREPARED",
                          "generation_id": generation_id,
                          "quarantine": quarantine.relative_to(self.root).as_posix(),
                          "staging": staging.relative_to(self.root).as_posix(),
                          "exact_freed_bytes": exact_bytes, "members": declared,
                          "proof_sha256": committed.get("proof_sha256")}
        _write_once(tx / "PURGE_PREPARED.json", purge_prepared)
        if quarantine.exists() and not staging.exists():
            quarantine.replace(staging)
        elif quarantine.exists() or not staging.is_dir():
            raise RawGenerationCleanupRejected(["PURGE_STATE_CONFLICT"])
        if failpoint == "AFTER_PURGE_ISOLATION":
            raise RuntimeError("FAILPOINT_AFTER_PURGE_ISOLATION")
        shutil.rmtree(staging)
        receipt = {"schema": PURGE_SCHEMA, "state": "PURGED", "generation_id": generation_id,
                   "freed_bytes": exact_bytes, "purged_at": datetime.now(timezone.utc).isoformat()}
        receipt["receipt_sha256"] = _material_hash(receipt, "receipt_sha256")
        _write_once(tx / "PURGED.json", receipt)
        return receipt

    def reconcile_purges(self) -> list[dict[str, Any]]:
        """Finish only a previously isolated purge staging tree."""
        results = []
        if not self.enabled or not self.tx_root.is_dir():
            return results
        for prepared_path in sorted(self.tx_root.glob("*/PURGE_PREPARED.json")):
            tx = prepared_path.parent
            if (tx / "PURGED.json").exists():
                continue
            row = json.loads(prepared_path.read_text("utf-8"))
            quarantine = self.root / row["quarantine"]
            staging = self.root / row["staging"]
            if quarantine.exists() or not staging.is_dir():
                results.append({"generation_id": row["generation_id"],
                                "status": "PURGE_PREPARED_REQUIRES_EXPLICIT_REPLAY"})
                continue
            actual = _verify_quarantine_members(
                staging, row.get("members"), row.get("exact_freed_bytes"),
            )
            shutil.rmtree(staging)
            receipt = {"schema": PURGE_SCHEMA, "state": "PURGED",
                       "generation_id": row["generation_id"], "freed_bytes": actual,
                       "purged_at": datetime.now(timezone.utc).isoformat()}
            receipt["receipt_sha256"] = _material_hash(receipt, "receipt_sha256")
            _write_once(tx / "PURGED.json", receipt)
            results.append({"generation_id": row["generation_id"],
                            "status": "PURGED_RECOVERED", "freed_bytes": actual})
        return results

    def reconcile(self) -> list[dict[str, Any]]:
        """Record moved quarantines after restart; never performs deletion."""
        results = []
        if not self.enabled or not self.tx_root.is_dir():
            return results
        for prepared_path in sorted(self.tx_root.glob("*/PREPARED.json")):
            tx = prepared_path.parent
            if (tx / "QUARANTINED.json").exists():
                continue
            row = json.loads(prepared_path.read_text("utf-8"))
            source = self.root / row["source"]
            quarantine = self.root / row["quarantine"]
            if source.exists() and not quarantine.exists():
                results.append({"generation_id": row["generation_id"], "status": "PREPARED_REVALIDATION_REQUIRED"})
            elif not source.exists() and quarantine.is_dir():
                _write_once(tx / "QUARANTINED.json", {**row, "state": "QUARANTINED", "committed_at": datetime.now(timezone.utc).isoformat()})
                results.append({"generation_id": row["generation_id"], "status": "QUARANTINED_RECOVERED"})
            else:
                raise RawGenerationCleanupRejected(["QUARANTINE_STATE_CONFLICT"])
        return results
