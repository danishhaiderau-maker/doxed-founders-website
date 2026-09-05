"""Analyzer-only orchestration for frozen dynamic-policy research.

This module composes the existing dynamic-policy and sealed-holdout engines. It
does not create an execution path and intentionally publishes UNKNOWN when the
causal taxonomy evidence needed for a fold is absent or ambiguous.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from research_dynamic_entry_policy import (
    nested_purged_walk_forward_dynamic,
    train_frozen_dynamic_policy,
    evaluate_frozen_dynamic_policy,
)
from research_v3_contract import canonical_hash


SCHEMA = "dynamic_policy_analyzer_orchestration_v1"
INPUT_SCHEMA = "dynamic_policy_analysis_input_v1"
REPORT_FILE = "dynamic_policy_analysis_report.json"
INPUT_FILE = "v3/dynamic_policy_analysis_input.json"


def _unknown(*reasons: str, input_receipt: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "UNKNOWN",
        "purpose": "RESEARCH_ONLY_NOT_RELAY_ELIGIBLE",
        "execution_class": "RESEARCH_ONLY",
        "relay_eligible": False,
        "live_policy_change_allowed": False,
        "input_receipt": dict(input_receipt or {}),
        "nested_protocol": None,
        "fold_local_taxonomy_bindings": [],
        "sealed_holdout": None,
        "blockers": sorted(set(reason for reason in reasons if reason)),
    }


def _load_verified_canonical_input(data_root: str | os.PathLike[str]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Load only a checksum-bound file from the completed canonical mirror."""
    root = Path(data_root).resolve()
    receipt: dict[str, Any] = {"relative_path": INPUT_FILE, "verification": "UNKNOWN"}
    if root.name != "canonical-research-data":
        return None, {**receipt, "blocker": "CANONICAL_STORE_ROOT_NOT_SELECTED"}
    try:
        sync_state = json.loads((root / ".fly-sync-state.json").read_text(encoding="utf-8-sig"))
        record = sync_state.get(INPUT_FILE) or sync_state.get(INPUT_FILE.replace("/", "\\"))
        if not isinstance(record, Mapping):
            return None, {**receipt, "blocker": "DYNAMIC_INPUT_NOT_IN_VERIFIED_MIRROR_MANIFEST"}
        # Batch checkpoints carry full_sha256; older verified inputs used
        # sha256. Never ignore a malformed or contradictory populated digest.
        digests = set()
        for field in ("full_sha256", "sha256"):
            value = record.get(field)
            if value is None or isinstance(value, str) and not value.strip():
                continue
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value.strip()):
                return None, {**receipt, "blocker": "DYNAMIC_INPUT_MANIFEST_CHECKSUM_INVALID"}
            digests.add(value.strip().lower())
        if not digests:
            return None, {**receipt, "blocker": "DYNAMIC_INPUT_NOT_IN_VERIFIED_MIRROR_MANIFEST"}
        if len(digests) != 1:
            return None, {**receipt, "blocker": "DYNAMIC_INPUT_MANIFEST_CHECKSUM_CONFLICT"}
        expected_digest = next(iter(digests))
        path = root / Path(INPUT_FILE)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        receipt.update({"sha256": digest, "size_bytes": len(raw)})
        if digest != expected_digest:
            return None, {**receipt, "blocker": "DYNAMIC_INPUT_CHECKSUM_MISMATCH"}
        payload = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("schema") != INPUT_SCHEMA:
            return None, {**receipt, "blocker": "DYNAMIC_INPUT_SCHEMA_INVALID"}
        receipt["verification"] = "CHECKSUM_VERIFIED_CANONICAL_MIRROR"
        return payload, receipt
    except FileNotFoundError:
        return None, {**receipt, "blocker": "DYNAMIC_INPUT_MISSING"}
    except (OSError, UnicodeError, ValueError, TypeError):
        return None, {**receipt, "blocker": "DYNAMIC_INPUT_INVALID"}


def build_dynamic_policy_analysis_report(
    data_root: str | os.PathLike[str], *, generation_revision: str,
    dataset_epoch: str | None, source_revision: str | None,
) -> dict[str, Any]:
    """Build one manifest-ready report, failing closed on every missing binding."""
    from research.runtime_identity_incidents import load_incident_input, IncidentEpisodeIndex, REASON
    incident_input = load_incident_input()
    payload, receipt = _load_verified_canonical_input(data_root)
    if payload is None:
        return _unknown(str(receipt.get("blocker") or "DYNAMIC_INPUT_UNKNOWN"), input_receipt=receipt)
    bindings = {
        "generation_revision": generation_revision,
        "dataset_epoch": dataset_epoch,
        "source_revision": source_revision,
    }
    mismatches = [
        f"DYNAMIC_INPUT_{key.upper()}_MISMATCH"
        for key, expected in bindings.items()
        if expected not in (None, "", "UNKNOWN")
        and str(payload.get(key) or "") != str(expected)
    ]
    if mismatches:
        return _unknown(*mismatches, input_receipt=receipt)
    training = list(payload.get("training_episodes") or [])
    holdout = list(payload.get("sealed_holdout_episodes") or [])
    incident_index = IncidentEpisodeIndex(incident_input)
    incident_index.add(row for row in training + holdout if isinstance(row, Mapping))
    excluded_training = [row for row in training if not isinstance(row, Mapping) or incident_index.reasons(row)]
    excluded_holdout = [row for row in holdout if not isinstance(row, Mapping) or incident_index.reasons(row)]
    if incident_input.enabled:
        training = [row for row in training if isinstance(row, Mapping) and not incident_index.reasons(row)]
        holdout = [row for row in holdout if isinstance(row, Mapping) and not incident_index.reasons(row)]
        receipt = {**receipt, "runtime_identity_incident_input": incident_input.provenance(),
                   "incident_excluded_training_episodes": len(excluded_training),
                   "incident_excluded_holdout_episodes": len(excluded_holdout)}
    changed_cohort = incident_input.enabled and bool(excluded_training or excluded_holdout)
    try:
        result = orchestrate_dynamic_policy_analysis(
            training,
            holdout,
            candidates=list(payload.get("candidates") or []),
            feature_names=list(payload.get("feature_names") or []),
            outer_folds=int(payload.get("outer_folds") or 0),
            inner_folds=int(payload.get("inner_folds") or 0),
            purge_sec=float(payload.get("purge_sec") or 0),
            embargo_sec=float(payload.get("embargo_sec") or 0),
            minimum_bucket_support=int(payload.get("minimum_bucket_support") or 0),
            protocol_run_id=str(payload.get("protocol_run_id") or ""),
            sealed_holdout_evaluation=None if changed_cohort else payload.get("sealed_holdout_evaluation"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        incident_input.assert_unchanged()
        return _unknown(f"DYNAMIC_ANALYSIS_INPUT_REJECTED:{type(exc).__name__}:{exc}", input_receipt=receipt)
    incident_input.assert_unchanged()
    if changed_cohort:
        # A previously sealed result cannot be rebound to a filtered cohort.
        # Keep the remaining-cohort diagnostic, but require a new sealed input.
        return {**_unknown(REASON, "INCIDENT_FILTERED_COHORT_RESEAL_REQUIRED", input_receipt=receipt),
                "descriptive_filtered_cohort": result,
                "descriptive_filtered_cohort_qualification_allowed": False}
    result.update({
        "execution_class": "RESEARCH_ONLY", "relay_eligible": False,
        "live_policy_change_allowed": False, "input_receipt": receipt,
        "generation_revision": generation_revision, "dataset_epoch": dataset_epoch,
        "source_revision": source_revision,
    })
    return result


def _fold_taxonomy_binding(
    episodes: Iterable[Mapping[str, Any]], *, fold_id: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    episode_ids, bucket_signatures, regimes, reasons = [], set(), set(), []
    for row in episodes:
        episode_id = str(row.get("episode_id") or "UNKNOWN")
        episode_ids.append(episode_id)
        signature = str(row.get("bucket_definition_signature") or "").strip()
        if not signature:
            reasons.append(f"BUCKET_DEFINITION_SIGNATURE_MISSING:{episode_id}")
        else:
            bucket_signatures.add(signature)
        observation = (row.get("pre_entry_features") or {}).get("regime")
        regime = str(observation.get("value") or "").strip().upper() if isinstance(observation, Mapping) else ""
        if not regime:
            reasons.append(f"RUNTIME_REGIME_MISSING:{episode_id}")
        else:
            regimes.add(regime)
    if len(bucket_signatures) > 1:
        reasons.append("AMBIGUOUS_BUCKET_DEFINITION_SIGNATURE")
    if reasons:
        return None, sorted(set(reasons))
    body = {
        "schema": "fold_local_runtime_taxonomy_binding_v1",
        "fold_id": fold_id,
        "fit_semantics": "TRAINING_FOLD_ONLY_IDENTITY_PROJECTION",
        "projection": "IDENTITY_RUNTIME_TAXONOMY",
        "bull_bear_range_projection": None,
        "training_episode_ids": sorted(episode_ids),
        "bucket_definition_signature": next(iter(bucket_signatures)),
        "observed_runtime_regimes": sorted(regimes),
    }
    return {**body, "signature": canonical_hash("fold-local-taxonomy", body, length=64)}, []


def orchestrate_dynamic_policy_analysis(
    training_episodes: list[dict[str, Any]],
    sealed_holdout_episodes: list[dict[str, Any]], *,
    candidates: Iterable[Mapping[str, Any]], feature_names: Iterable[str],
    outer_folds: int, inner_folds: int, purge_sec: float, embargo_sec: float,
    minimum_bucket_support: int, protocol_run_id: str,
    sealed_holdout_evaluation: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Run nested OOS selection, freeze once, then consume a sealed holdout."""
    candidate_rows = list(candidates)
    try:
        protocol = nested_purged_walk_forward_dynamic(
            training_episodes, candidates=candidate_rows, feature_names=feature_names,
            outer_folds=outer_folds, inner_folds=inner_folds,
            purge_sec=purge_sec, embargo_sec=embargo_sec,
            minimum_bucket_support=minimum_bucket_support,
            protocol_run_id=protocol_run_id,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _unknown(f"NESTED_PROTOCOL_UNAVAILABLE:{type(exc).__name__}:{exc}")
    by_id = {str(row.get("episode_id") or ""): row for row in training_episodes}
    bindings, blockers = [], []
    for fold in protocol["folds"]:
        rows = [by_id[episode_id] for episode_id in fold["train_episode_ids"] if episode_id in by_id]
        binding, reasons = _fold_taxonomy_binding(rows, fold_id=f"{protocol_run_id}:outer-{fold['outer_fold']}")
        bindings.append({"outer_fold": fold["outer_fold"], "binding": binding, "reasons": reasons})
        blockers.extend(reasons)
    if blockers:
        return {
            "schema": SCHEMA, "status": "UNKNOWN", "purpose": "RESEARCH_ONLY_NOT_RELAY_ELIGIBLE",
            "nested_protocol": protocol, "fold_local_taxonomy_bindings": bindings,
            "sealed_holdout": None, "blockers": sorted(set(blockers)),
        }
    try:
        frozen = train_frozen_dynamic_policy(
            training_episodes, candidates=candidate_rows, feature_names=feature_names,
            inner_folds=inner_folds, purge_sec=purge_sec, embargo_sec=embargo_sec,
            minimum_bucket_support=minimum_bucket_support,
            training_run_id=f"{protocol_run_id}:final-pre-holdout",
        )
        holdout = evaluate_frozen_dynamic_policy(
            frozen, sealed_holdout_episodes, evaluation_mode="SEALED_HOLDOUT",
            sealed_holdout_evaluation=sealed_holdout_evaluation,
        )
    except ValueError as exc:
        return {
            "schema": SCHEMA, "status": "UNKNOWN", "purpose": "RESEARCH_ONLY_NOT_RELAY_ELIGIBLE",
            "nested_protocol": protocol, "fold_local_taxonomy_bindings": bindings,
            "sealed_holdout": None, "blockers": [str(exc)],
        }
    blockers = ([] if protocol["passed"] else ["NESTED_PURGED_WALK_FORWARD_INCOMPLETE"]) + list(holdout["qualification_blockers"])
    body = {
        "schema": SCHEMA, "status": "PASS" if not blockers else "UNKNOWN",
        "purpose": "RESEARCH_ONLY_NOT_RELAY_ELIGIBLE", "nested_protocol": protocol,
        "fold_local_taxonomy_bindings": bindings, "frozen_policy": frozen,
        "sealed_holdout": holdout, "blockers": blockers,
    }
    body["orchestration_receipt_id"] = canonical_hash("dynamic-policy-analyzer", body, length=64)
    return body
