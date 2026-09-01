"""Immutable, research-only sealed holdout receipts.

The seal freezes *identity*, not results.  It is created after training and
before any holdout evidence exists, then consumed exactly once by a
single-purpose evaluator.  Historical rows whose collection timestamp is not
strictly after the seal are rejected, so an ordinary chronological split can
never be relabelled as a sealed holdout.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from research_v3_contract import canonical_hash, canonical_json


SEAL_SCHEMA = "sealed_holdout_freeze_v1"
EVALUATION_SCHEMA = "sealed_holdout_evaluation_v1"
PURPOSE = "RESEARCH_POLICY_QUALIFICATION_ONLY"


def _text(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"MISSING_{field.upper()}")
    return result


def _candidates(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    candidates: dict[str, str] = {}
    for row in rows:
        policy_id = _text(row.get("policy_id"), "policy_id")
        signature = _text(row.get("policy_signature"), "policy_signature")
        if policy_id in candidates and candidates[policy_id] != signature:
            raise ValueError(f"DUPLICATE_POLICY_ID_WITH_DIFFERENT_SIGNATURE:{policy_id}")
        candidates[policy_id] = signature
    if not candidates:
        raise ValueError("EMPTY_POLICY_CANDIDATE_SET")
    return [
        {"policy_id": policy_id, "policy_signature": candidates[policy_id]}
        for policy_id in sorted(candidates)
    ]


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _contained(root: Path, path: Path) -> Path:
    root = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"SEALED_HOLDOUT_PATH_OUTSIDE_ROOT:{resolved}") from exc
    return resolved


def _write_once(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (canonical_json(value) + "\n").encode("utf-8")
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError:
        existing = path.read_bytes()
        if existing != payload:
            raise ValueError(f"IMMUTABLE_RECEIPT_CONFLICT:{path.name}")
        return
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def create_seal(
    root: str | Path,
    *,
    dataset_epoch: str,
    source_revision: str,
    deployed_revision: str,
    tile_config_signature: str,
    cohort_signature: str,
    training_snapshot_hash: str,
    training_completed_at: float,
    sealed_at: float,
    holdout_start_ts: float,
    policy_candidates: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Create an immutable freeze receipt before the holdout begins."""
    training_completed_at = float(training_completed_at)
    sealed_at = float(sealed_at)
    holdout_start_ts = float(holdout_start_ts)
    if training_completed_at > sealed_at:
        raise ValueError("SEAL_PRECEDES_TRAINING_COMPLETION")
    if sealed_at >= holdout_start_ts:
        raise ValueError("HOLDOUT_BOUNDARY_NOT_AFTER_SEAL")
    body = {
        "schema": SEAL_SCHEMA,
        "purpose": PURPOSE,
        "dataset_epoch": _text(dataset_epoch, "dataset_epoch"),
        "source_revision": _text(source_revision, "source_revision"),
        "deployed_revision": _text(deployed_revision, "deployed_revision"),
        "tile_config_signature": _text(tile_config_signature, "tile_config_signature"),
        "cohort_signature": _text(cohort_signature, "cohort_signature"),
        "training_snapshot_hash": _text(training_snapshot_hash, "training_snapshot_hash"),
        "training_completed_at": training_completed_at,
        "sealed_at": sealed_at,
        "holdout_start_ts": holdout_start_ts,
        "policy_candidates": _candidates(policy_candidates),
        "candidate_selection_frozen": True,
        "holdout_inspection_state": "UNINSPECTED_AT_SEAL",
        "evaluation_limit": 1,
    }
    seal_id = canonical_hash("holdout-seal", body, length=64)
    receipt = {**body, "seal_id": seal_id, "content_sha256": _sha256(body)}
    base = Path(root).resolve()
    target = _contained(base, base / "sealed_holdout" / "seals" / f"{seal_id}.json")
    _write_once(target, receipt)
    return receipt


def load_seal(root: str | Path, seal_id: str) -> dict[str, Any]:
    base = Path(root).resolve()
    target = _contained(base, base / "sealed_holdout" / "seals" / f"{_text(seal_id, 'seal_id')}.json")
    seal = json.loads(target.read_text(encoding="utf-8"))
    body = {key: value for key, value in seal.items() if key not in {"seal_id", "content_sha256"}}
    if seal.get("schema") != SEAL_SCHEMA or seal.get("purpose") != PURPOSE:
        raise ValueError("INVALID_SEAL_SCHEMA_OR_PURPOSE")
    if seal.get("seal_id") != canonical_hash("holdout-seal", body, length=64):
        raise ValueError("SEALED_HOLDOUT_IDENTITY_MISMATCH")
    if seal.get("content_sha256") != _sha256(body):
        raise ValueError("SEALED_HOLDOUT_CHECKSUM_MISMATCH")
    return seal


def consume_seal(
    root: str | Path,
    *,
    seal_id: str,
    policy_candidates: Iterable[Mapping[str, Any]],
    holdout_episodes: Iterable[Mapping[str, Any]],
    evaluation_started_at: float,
) -> dict[str, Any]:
    """Consume one seal once; this function records identities, never selects."""
    seal = load_seal(root, seal_id)
    evaluation_started_at = float(evaluation_started_at)
    if evaluation_started_at <= float(seal["sealed_at"]):
        raise ValueError("EVALUATION_NOT_AFTER_SEAL")
    supplied_candidates = _candidates(policy_candidates)
    if supplied_candidates != seal["policy_candidates"]:
        raise ValueError("POST_SEAL_POLICY_CANDIDATE_CHANGE")

    episode_rows = [dict(row) for row in holdout_episodes]
    defects: list[str] = []
    identities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in episode_rows:
        episode_id = str(row.get("episode_id") or "").strip()
        if not episode_id or episode_id in seen:
            defects.append(f"INVALID_OR_DUPLICATE_EPISODE_ID:{episode_id or 'UNKNOWN'}")
        seen.add(episode_id)
        try:
            signal_ts = float(row.get("signal_ts"))
            collected_at = float(row.get("evidence_collected_at"))
        except (TypeError, ValueError):
            defects.append(f"MISSING_CAUSAL_OR_COLLECTION_TIME:{episode_id or 'UNKNOWN'}")
            continue
        if signal_ts < float(seal["holdout_start_ts"]):
            defects.append(f"PRE_BOUNDARY_EPISODE:{episode_id}")
        if collected_at <= float(seal["sealed_at"]):
            defects.append(f"HISTORICAL_OR_PREINSPECTED_EVIDENCE:{episode_id}")
        if collected_at > evaluation_started_at:
            defects.append(f"EVIDENCE_COLLECTED_AFTER_EVALUATION_STARTED:{episode_id}")
        for field in ("dataset_epoch", "source_revision", "deployed_revision", "tile_config_signature", "cohort_signature"):
            if str(row.get(field) or "") != str(seal[field]):
                defects.append(f"{field.upper()}_MISMATCH:{episode_id}")
        identities.append({
            "episode_id": episode_id,
            "signal_ts": signal_ts,
            "evidence_collected_at": collected_at,
            "episode_hash": canonical_hash("episode", row, length=64),
        })
    if not episode_rows:
        defects.append("EMPTY_HOLDOUT_COHORT")

    body = {
        "schema": EVALUATION_SCHEMA,
        "purpose": PURPOSE,
        "seal_id": seal["seal_id"],
        "seal_content_sha256": seal["content_sha256"],
        "evaluation_started_at": evaluation_started_at,
        "policy_candidates": supplied_candidates,
        "holdout_episode_identities": identities,
        "holdout_cohort_hash": canonical_hash("holdout-cohort", identities, length=64),
        "selection_after_seal": False,
        "historical_data_retroactively_sealed": False,
        "blockers": sorted(set(defects)),
        "passed": not defects,
        "consumption_status": "CONSUMED" if not defects else "REJECTED",
    }
    receipt_id = canonical_hash("holdout-evaluation", body, length=64)
    receipt = {**body, "receipt_id": receipt_id, "content_sha256": _sha256(body)}
    base = Path(root).resolve()
    target = _contained(base, base / "sealed_holdout" / "evaluations" / f"{seal_id}.json")
    _write_once(target, receipt)
    return receipt


def verify_evaluation_receipt(
    receipt: Any,
    *,
    policy_id: str,
    policy_signature: str | None = None,
    holdout_episodes: Iterable[Mapping[str, Any]] | None = None,
) -> bool:
    """Cryptographically verify the in-memory receipt and frozen candidate."""
    if not isinstance(receipt, Mapping):
        return False
    body = {key: value for key, value in receipt.items() if key not in {"receipt_id", "content_sha256"}}
    candidates = receipt.get("policy_candidates")
    if holdout_episodes is None:
        return False
    supplied_identities = []
    for row in holdout_episodes:
        try:
            supplied_identities.append({
                "episode_id": str(row.get("episode_id") or "").strip(),
                "signal_ts": float(row.get("signal_ts")),
                "evidence_collected_at": float(row.get("evidence_collected_at")),
                "episode_hash": canonical_hash("episode", dict(row), length=64),
            })
        except (AttributeError, TypeError, ValueError):
            return False
    expected_candidate = {
        "policy_id": str(policy_id),
        "policy_signature": str(policy_signature or ""),
    }
    candidate_matches = bool(
        isinstance(candidates, list)
        and (
            expected_candidate in candidates
            if policy_signature is not None
            else any(
                str(row.get("policy_id") or "") == str(policy_id)
                for row in candidates if isinstance(row, Mapping)
            )
        )
    )
    return bool(
        receipt.get("schema") == EVALUATION_SCHEMA
        and receipt.get("purpose") == PURPOSE
        and receipt.get("passed") is True
        and receipt.get("consumption_status") == "CONSUMED"
        and receipt.get("selection_after_seal") is False
        and receipt.get("historical_data_retroactively_sealed") is False
        and candidate_matches
        and receipt.get("holdout_episode_identities") == supplied_identities
        and receipt.get("holdout_cohort_hash") == canonical_hash(
            "holdout-cohort", supplied_identities, length=64,
        )
        and receipt.get("receipt_id") == canonical_hash("holdout-evaluation", body, length=64)
        and receipt.get("content_sha256") == _sha256(body)
    )
