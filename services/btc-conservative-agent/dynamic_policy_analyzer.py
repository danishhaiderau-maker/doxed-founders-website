"""Analyzer-only orchestration for frozen dynamic-policy research.

This module composes the existing dynamic-policy and sealed-holdout engines. It
does not create an execution path and intentionally publishes UNKNOWN when the
causal taxonomy evidence needed for a fold is absent or ambiguous.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from research_dynamic_entry_policy import (
    nested_purged_walk_forward_dynamic,
    train_frozen_dynamic_policy,
    evaluate_frozen_dynamic_policy,
)
from research_v3_contract import canonical_hash


SCHEMA = "dynamic_policy_analyzer_orchestration_v1"


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
    protocol = nested_purged_walk_forward_dynamic(
        training_episodes, candidates=candidate_rows, feature_names=feature_names,
        outer_folds=outer_folds, inner_folds=inner_folds,
        purge_sec=purge_sec, embargo_sec=embargo_sec,
        minimum_bucket_support=minimum_bucket_support,
        protocol_run_id=protocol_run_id,
    )
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
