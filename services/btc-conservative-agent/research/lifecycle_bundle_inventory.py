"""Bounded manifest-only inventory for immutable lifecycle evidence bundles."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator, Mapping

from lifecycle_bundles import BUNDLE_SCHEMA, TRANSFER_BUNDLE_SCHEMA, LifecycleKey
from research_v3_contract import canonical_json

REPORT_SCHEMA = "lifecycle_bundle_inventory_v1"


def _contained(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


class _ScanLimit(Exception):
    pass


def _manifest_paths(root: Path, *, started: float, runtime: float,
                    directory_limit: int, scan: Counter) -> Iterator[Path]:
    """Lazily walk only the exact two bundle directory levels."""
    def budget() -> None:
        if time.monotonic() - started > max(0.0, runtime):
            raise _ScanLimit("RUNTIME_LIMIT_EXCEEDED")

    def directories(parent: Path) -> Iterator[os.DirEntry[str]]:
        budget()
        try:
            with os.scandir(parent) as entries:
                for entry in entries:
                    budget()
                    if entry.is_dir(follow_symlinks=False):
                        scan["directories_discovered"] += 1
                        if scan["directories_discovered"] > max(0, directory_limit):
                            raise _ScanLimit("DIRECTORY_LIMIT_EXCEEDED")
                        yield entry
        except FileNotFoundError:
            return

    for name, prefix in (("lifecycle_bundles", "lifecycle-"),
                         ("lifecycle_transfer_bundles", "transfer-")):
        base = root / "v3" / name
        if not base.is_dir():
            continue
        for shard in directories(base):
            if len(shard.name) != 2:
                continue
            for bundle in directories(Path(shard.path)):
                if bundle.name.startswith(prefix):
                    manifest = Path(bundle.path) / "manifest.json"
                    if manifest.is_file():
                        scan["manifest_paths_discovered"] += 1
                        yield manifest


def _read_manifest(path: Path, remaining: int):
    try:
        size = path.stat().st_size
    except OSError:
        return None, "MANIFEST_STAT_FAILED", 0
    if size > remaining:
        return None, "MANIFEST_BYTE_LIMIT_EXCEEDED", 0
    try:
        with path.open("rb") as handle:
            raw = handle.read(size + 1)
    except OSError:
        return None, "MANIFEST_READ_FAILED", 0
    if len(raw) != size:
        return None, "MANIFEST_CHANGED_DURING_READ", len(raw)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "MANIFEST_INVALID", len(raw)
    return (value, None, len(raw)) if isinstance(value, dict) else (None, "MANIFEST_INVALID", len(raw))


def _manifest_defects(path: Path, manifest: dict[str, Any]) -> list[str]:
    material = dict(manifest)
    supplied = str(material.pop("manifest_sha256", ""))
    defects = []
    if supplied != hashlib.sha256(canonical_json(material).encode()).hexdigest():
        defects.append("MANIFEST_SHA256_MISMATCH")
    bundle_id = str(manifest.get("bundle_id") or "")
    if bundle_id != path.parent.name or path.parent.parent.name != bundle_id[-64:-62]:
        defects.append("BUNDLE_PATH_IDENTITY_MISMATCH")
    try:
        key = LifecycleKey(**manifest["identity"])
        if manifest.get("lifecycle_identity_id") != key.identity_id:
            defects.append("LIFECYCLE_IDENTITY_MISMATCH")
    except (KeyError, TypeError, ValueError):
        defects.append("LIFECYCLE_IDENTITY_INVALID")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        defects.append("FILE_RECEIPTS_MISSING")
    elif any(not isinstance(row, dict) or not row.get("path") for row in files):
        defects.append("FILE_RECEIPT_INVALID")
    return defects


def build_lifecycle_bundle_inventory(
    data_dir: str | Path, destination: str | Path | None = None, *,
    analysis_provenance: Mapping[str, Any] | None = None,
    max_manifests: int = 100_000, max_manifest_bytes: int = 256 * 1024 * 1024,
    max_runtime_sec: float = 5.0, max_directories: int = 300_000,
) -> dict[str, Any]:
    root, started = Path(data_dir).resolve(), time.monotonic()
    invalid, blockers, scan = Counter(), Counter(), Counter()
    outcomes = {"qualification": Counter(), "transfer": Counter()}
    provenance = {"qualification": defaultdict(set), "transfer": defaultdict(set)}
    identities = {"qualification": [], "transfer": []}
    counts, bytes_read, processed, truncated = Counter(), 0, 0, False
    try:
        paths = _manifest_paths(root, started=started, runtime=max_runtime_sec,
                                directory_limit=max_directories, scan=scan)
        for path in paths:
            if processed >= max(0, int(max_manifests)):
                raise _ScanLimit("MANIFEST_LIMIT_EXCEEDED")
            if not _contained(path, root):
                invalid["MANIFEST_OUTSIDE_CANONICAL_ROOT"] += 1
                continue
            manifest, error, consumed = _read_manifest(
                path, max(0, int(max_manifest_bytes)) - bytes_read)
            if error == "MANIFEST_BYTE_LIMIT_EXCEEDED":
                raise _ScanLimit(error)
            bytes_read += consumed
            processed += 1
            if error:
                invalid[error] += 1
                continue
            defects = _manifest_defects(path, manifest)
            if defects:
                invalid.update(defects)
                continue
            schema = manifest.get("schema")
            if schema == BUNDLE_SCHEMA:
                tier, evidence = "qualification", manifest.get("completion") or {}
                if manifest.get("maturity") not in (None, "QUALIFICATION_READY"):
                    invalid["QUALIFICATION_BUNDLE_MATURITY_INVALID"] += 1
                    continue
            elif schema == TRANSFER_BUNDLE_SCHEMA:
                tier, evidence = "transfer", manifest.get("transfer_receipt") or {}
                if not (manifest.get("maturity") == "TRANSFER_READY"
                        and manifest.get("qualification_ready") is False
                        and manifest.get("profitability_supported") is False
                        and manifest.get("ranking_eligible") is False
                        and manifest.get("source_cleanup_authorized") is False
                        and "completion" not in manifest):
                    invalid["TRANSFER_AUDIT_ISOLATION_INVALID"] += 1
                    continue
            else:
                invalid["SCHEMA_UNSUPPORTED"] += 1
                continue
            identity = str(manifest["lifecycle_identity_id"])
            counts[tier] += 1
            identities[tier].append(identity)
            outcomes[tier][str(evidence.get("entry_outcome") or "UNKNOWN").upper()] += 1
            prov = manifest.get("provenance") or {}
            provenance[tier][identity].add(tuple(str(prov.get(k) or "") for k in
                ("source_revision", "deployed_revision", "tile_config_signature")))
    except _ScanLimit as exc:
        truncated = True
        blockers[str(exc)] += 1

    unique = {tier: set(values) for tier, values in identities.items()}
    duplicates = {tier: sum(n - 1 for n in Counter(values).values() if n > 1)
                  for tier, values in identities.items()}
    intersection = unique["qualification"] & unique["transfer"]
    mismatches = sum(provenance["qualification"][i] != provenance["transfer"][i]
                     for i in intersection)
    complete = not truncated
    report = {
        "schema": REPORT_SCHEMA, "inventory_scope": "MANIFEST_ONLY",
        "complete": complete, "complete_scope": "MANIFEST_INVENTORY",
        "payload_verification_status": "UNKNOWN_NOT_SCANNED",
        "payload_files_read": 0,
        "audit_only": False,
        "scan": {"manifest_paths_discovered": scan["manifest_paths_discovered"],
                 "manifests_processed": processed,
                 "directories_discovered": scan["directories_discovered"],
                 "manifest_bytes_read": bytes_read, "payload_files_read": 0,
                 "runtime_sec": time.monotonic() - started, "truncated": truncated,
                 "blocker_counts": dict(sorted(blockers.items()))},
        "qualification": {"label": "manifest-verified qualification bundles",
            "manifest_count": counts["qualification"],
            "unique_lifecycle_count": len(unique["qualification"]),
            "duplicate_identity_count": duplicates["qualification"],
            "entry_outcome_counts": dict(sorted(outcomes["qualification"].items()))},
        "transfer": {"label": "transfer-ready audit copies", "audit_only": True,
            "profitability_supported": False, "ranking_eligible": False,
            "source_cleanup_authorized": False, "manifest_count": counts["transfer"],
            "unique_lifecycle_count": len(unique["transfer"]),
            "duplicate_identity_count": duplicates["transfer"],
            "entry_outcome_counts": dict(sorted(outcomes["transfer"].items()))},
        "parity": {"scope": "MANIFEST_INVENTORY",
            "intersection_count": len(intersection),
            "qualification_only_count": len(unique["qualification"] - unique["transfer"]),
            "transfer_only_count": len(unique["transfer"] - unique["qualification"]),
            "provenance_mismatch_count": mismatches,
            "complete": complete and not any(duplicates.values()) and not mismatches},
        "invalid_manifest_count": sum(invalid.values()),
        "invalid_reason_counts": dict(sorted(invalid.items())),
        "analysis_provenance": dict(analysis_provenance or {}),
    }
    if destination is not None:
        target = Path(destination).resolve()
        if not _contained(target, root):
            raise ValueError("DESTINATION_OUTSIDE_CANONICAL_ROOT")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(canonical_json(report) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return report
