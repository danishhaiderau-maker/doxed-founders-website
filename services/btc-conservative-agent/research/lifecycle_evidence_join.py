"""Verify and index qualification-complete lifecycle bundle receipts."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

from lifecycle_bundles import BUNDLE_SCHEMA, verify_bundle
from research.policy_evidence_schema import canonical_json


SCHEMA = "lifecycle_evidence_join_index_v1"
RECEIPT_SCHEMA = "lifecycle_evidence_collected_v1"
COMPLETION_SCHEMA = "lifecycle_bundle_completion_v1"
IDENTITY_FIELDS = (
    "collection_epoch_id", "episode_id", "policy_signature", "research_lane",
)


def lifecycle_join_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(row.get(name) or "").upper() if name == "research_lane"
                 else str(row.get(name) or "") for name in IDENTITY_FIELDS)


def _hash_valid(value: Any) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def verify_manifest_collection_receipt(
    manifest: Mapping[str, Any], events: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Verify schema, hashes, exact identity/provenance and completion binding."""
    blockers: list[str] = []
    if manifest.get("schema") != BUNDLE_SCHEMA:
        blockers.append("LIFECYCLE_BUNDLE_SCHEMA_INVALID")
    identity = manifest.get("identity") if isinstance(manifest.get("identity"), Mapping) else {}
    completion_candidates = [
        row.get("bundle_completion") for row in (events or [])
        if isinstance(row.get("bundle_completion"), Mapping)
        and row["bundle_completion"].get("schema") == COMPLETION_SCHEMA
    ]
    completion = completion_candidates[0] if len(completion_candidates) == 1 else {}
    collection = manifest.get("evidence_collection") if isinstance(manifest.get("evidence_collection"), Mapping) else {}
    receipt = collection.get("receipt") if isinstance(collection.get("receipt"), Mapping) else {}
    provenance = manifest.get("provenance") if isinstance(manifest.get("provenance"), Mapping) else {}

    if collection.get("ready") is not True or not receipt:
        blockers.append("EVIDENCE_COLLECTION_RECEIPT_MISSING")
    if receipt.get("schema") != RECEIPT_SCHEMA:
        blockers.append("EVIDENCE_COLLECTION_RECEIPT_SCHEMA_INVALID")
    supplied = str(receipt.get("evidence_collected_receipt_sha256") or "").lower()
    receipt_body = dict(receipt)
    receipt_body.pop("evidence_collected_receipt_sha256", None)
    actual = hashlib.sha256(canonical_json(receipt_body).encode("utf-8")).hexdigest()
    if not _hash_valid(supplied) or supplied != actual:
        blockers.append("EVIDENCE_COLLECTION_RECEIPT_SHA256_MISMATCH")

    completion_supplied = str(completion.get("completion_receipt_sha256") or "").lower()
    completion_body = dict(completion)
    completion_body.pop("completion_receipt_sha256", None)
    completion_actual = hashlib.sha256(canonical_json(completion_body).encode("utf-8")).hexdigest()
    if len(completion_candidates) != 1:
        blockers.append("LIFECYCLE_COMPLETION_RECEIPT_NOT_UNIQUE")
    if not _hash_valid(completion_supplied) or completion_supplied != completion_actual:
        blockers.append("LIFECYCLE_COMPLETION_SHA256_MISMATCH")
    if receipt.get("completion_receipt_sha256") != completion_supplied:
        blockers.append("EVIDENCE_COLLECTION_COMPLETION_BINDING_MISMATCH")
    if dict(receipt.get("identity") or {}) != dict(identity):
        blockers.append("EVIDENCE_COLLECTION_IDENTITY_MISMATCH")
    if dict(receipt.get("provenance") or {}) != dict(provenance):
        blockers.append("EVIDENCE_COLLECTION_PROVENANCE_MISMATCH")
    if receipt.get("entry_outcome") != completion.get("entry_outcome"):
        blockers.append("EVIDENCE_COLLECTION_OUTCOME_MISMATCH")
    try:
        if float(receipt.get("evidence_collected_at") or 0) < float(receipt.get("qualification_eligible_at") or 0):
            blockers.append("EVIDENCE_COLLECTION_TOO_EARLY")
    except (TypeError, ValueError):
        blockers.append("EVIDENCE_COLLECTION_TIMESTAMP_INVALID")
    if not all(str(identity.get(name) or "") for name in IDENTITY_FIELDS):
        blockers.append("LIFECYCLE_IDENTITY_INCOMPLETE")
    return {
        "valid": not blockers,
        "blockers": sorted(set(blockers)),
        "key": lifecycle_join_key(identity),
        "receipt": dict(receipt) if not blockers else None,
        "completion": dict(completion) if not blockers else None,
        "lifecycle_identity_id": manifest.get("lifecycle_identity_id"),
    }


def build_lifecycle_evidence_index(v3_root: str | Path) -> dict[str, Any]:
    root = Path(v3_root).resolve()
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    defects: Counter[str] = Counter()
    manifests = sorted((root / "lifecycle_bundles").glob("*/*/manifest.json"))
    for path in manifests:
        verification = verify_bundle(path.parent)
        if verification.get("passed") is not True:
            for defect in verification.get("defects") or ["LIFECYCLE_BUNDLE_INVALID"]:
                defects[str(defect)] += 1
            continue
        try:
            events_path = path.parent / "events.jsonl"
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
            if not all(isinstance(row, dict) for row in events):
                raise ValueError("NON_OBJECT_EVENT")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            defects[f"LIFECYCLE_EVENTS_PAYLOAD_INVALID:{type(exc).__name__}"] += 1
            continue
        result = verify_manifest_collection_receipt(verification.get("manifest") or {}, events)
        if not result["valid"]:
            for blocker in result["blockers"]:
                defects[blocker] += 1
        grouped[result["key"]].append({**result, "manifest_path": str(path)})
    duplicate_keys = {key for key, rows in grouped.items() if len(rows) != 1}
    return {
        "schema": SCHEMA,
        "manifest_count": len(manifests),
        "valid_unique_count": sum(len(rows) == 1 and rows[0]["valid"] for rows in grouped.values()),
        "invalid_count": sum(not row["valid"] for rows in grouped.values() for row in rows),
        "duplicate_identity_count": len(duplicate_keys),
        "defect_counts": dict(sorted(defects.items())),
        "by_key": dict(grouped),
    }


def join_lifecycle_evidence(index: Mapping[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    key = (
        str(row.get("epoch_id") or row.get("collection_epoch_id") or ""),
        str(row.get("episode_id") or ""),
        str(row.get("policy_signature") or ""),
        str(row.get("research_lane") or row.get("lane") or "").upper(),
    )
    matches = (index.get("by_key") or {}).get(key, [])
    if not matches:
        return {"status": "UNKNOWN", "reason_codes": ["UNKNOWN_LIFECYCLE_EVIDENCE_RECEIPT_MISSING"]}
    if len(matches) != 1:
        return {"status": "UNKNOWN", "reason_codes": ["UNKNOWN_LIFECYCLE_EVIDENCE_RECEIPT_DUPLICATE"]}
    match = matches[0]
    if match.get("valid") is not True:
        return {"status": "UNKNOWN", "reason_codes": [
            "UNKNOWN_" + str(reason) for reason in match.get("blockers") or ["LIFECYCLE_EVIDENCE_RECEIPT_INVALID"]
        ]}
    return {
        "status": "VERIFIED", "reason_codes": [],
        "receipt": match["receipt"], "completion": match["completion"],
        "lifecycle_identity_id": match.get("lifecycle_identity_id"),
    }
