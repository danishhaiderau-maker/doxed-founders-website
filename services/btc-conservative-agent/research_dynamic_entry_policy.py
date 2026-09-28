"""Deterministic, research-only trained/frozen dynamic entry policies.

The protocol deliberately separates selection from evaluation.  Candidate
rules are selected only from the validation blocks of *inner* purged,
embargoed chronological folds.  The resulting receipt is content addressed
and may then be applied to a later outer-validation or sealed-holdout cohort.
It has no runtime or relay integration.
"""
from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from research_entry_baselines import ENTRY_BASELINE_REGISTRY
from research_v3_contract import canonical_hash, canonical_json
from research_v3_sealed_holdout import verify_evaluation_receipt
from research_v3_validation import SUPPORTED_TERMINAL_STATES, chronological_folds


RECEIPT_SCHEMA = "frozen_dynamic_entry_policy_v1"
EVALUATION_SCHEMA = "dynamic_entry_policy_evaluation_v1"
PURPOSE = "RESEARCH_ONLY_NOT_RELAY_ELIGIBLE"
UNSEEN_CELL_FALLBACK = {
    "schema": "dynamic_unseen_cell_fallback_v1",
    "decision": "NO_TRADE",
    "execution_policy_id": None,
    "semantics": "ABSTAIN_WITHOUT_LEARNED_GLOBAL_WINNER",
}
DEFAULT_CAUSAL_FEATURES = (
    "atr_bucket", "realized_volatility_bucket", "spread_bucket",
    "depth_bucket", "liquidity_bucket", "regime", "direction",
    "trend_strength_bucket",
)


def _sha256_body(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def _signed_candidates(candidates: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    known_baselines = {
        row["baseline_id"]: row["policy_signature"]
        for row in ENTRY_BASELINE_REGISTRY["baselines"]
    }
    result: dict[str, str] = {}
    for source in candidates:
        policy_id = str(source.get("policy_id") or source.get("baseline_id") or "").strip()
        signature = str(source.get("policy_signature") or "").strip()
        if not policy_id or not signature:
            raise ValueError("UNSIGNED_STATIC_BASELINE")
        if policy_id in known_baselines and signature != known_baselines[policy_id]:
            raise ValueError(f"STATIC_BASELINE_SIGNATURE_MISMATCH:{policy_id}")
        if policy_id in result and result[policy_id] != signature:
            raise ValueError(f"DUPLICATE_POLICY_SIGNATURE_CONFLICT:{policy_id}")
        result[policy_id] = signature
    if not result:
        raise ValueError("EMPTY_STATIC_BASELINE_SET")
    return [
        {"policy_id": policy_id, "policy_signature": result[policy_id]}
        for policy_id in sorted(result)
    ]


def _causal_feature_key(
    row: Mapping[str, Any], feature_names: tuple[str, ...],
) -> tuple[tuple[str, ...] | None, list[str]]:
    episode_id = str(row.get("episode_id") or "UNKNOWN")
    try:
        signal_ts = float(row.get("signal_ts"))
    except (TypeError, ValueError):
        return None, [f"INVALID_SIGNAL_TS:{episode_id}"]
    source = row.get("pre_entry_features")
    if not isinstance(source, Mapping):
        return None, [f"PRE_ENTRY_FEATURES_MISSING:{episode_id}"]
    values, defects = [], []
    for name in feature_names:
        observation = source.get(name)
        if not isinstance(observation, Mapping) or observation.get("value") in (None, ""):
            defects.append(f"MISSING_PRE_ENTRY_FEATURE:{name}:{episode_id}")
            continue
        try:
            observed_ts = float(observation.get("observed_ts"))
        except (TypeError, ValueError):
            defects.append(f"FEATURE_TIMESTAMP_MISSING:{name}:{episode_id}")
            continue
        if observed_ts > signal_ts:
            defects.append(f"POST_ENTRY_FEATURE_LEAKAGE:{name}:{episode_id}")
            continue
        values.append(str(observation["value"]).strip().upper())
    return (tuple(values) if not defects else None), defects


def _opportunity_value(row: Mapping[str, Any], policy_id: str) -> float | None:
    outcome = (row.get("policy_outcomes") or {}).get(policy_id)
    if not isinstance(outcome, Mapping):
        return None
    state = str(outcome.get("outcome_state") or "UNSUPPORTED").upper()
    if state not in SUPPORTED_TERMINAL_STATES:
        return None
    if state in {"NO_FILL", "NO_TRADE", "REJECTED"}:
        return 0.0
    value = outcome.get("net_pnl_usd")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _choose(scores: Mapping[str, list[float]], minimum_support: int) -> str | None:
    eligible = []
    for policy_id, values in scores.items():
        if len(values) >= minimum_support:
            eligible.append((sum(values) / len(values), len(values), policy_id))
    if not eligible:
        return None
    # Stable tie-break: expectancy, support, then lexicographically smallest ID.
    eligible.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return eligible[0][2]


def train_frozen_dynamic_policy(
    episodes: list[dict[str, Any]], *, candidates: Iterable[Mapping[str, Any]],
    feature_names: Iterable[str] = DEFAULT_CAUSAL_FEATURES,
    inner_folds: int = 4, purge_sec: float = 7200, embargo_sec: float = 300,
    minimum_bucket_support: int = 3, training_run_id: str,
) -> dict[str, Any]:
    """Train a deterministic mapping using inner-fold OOS evidence only."""
    signed = _signed_candidates(candidates)
    candidate_ids = [row["policy_id"] for row in signed]
    names = tuple(str(name).strip() for name in feature_names if str(name).strip())
    if not names or len(set(names)) != len(names):
        raise ValueError("INVALID_CAUSAL_FEATURE_SCHEMA")
    folds = chronological_folds(
        episodes, outer_folds=int(inner_folds), purge_sec=purge_sec,
        embargo_sec=embargo_sec,
    )
    if not folds or not any(fold["train"] for fold in folds):
        raise ValueError("INSUFFICIENT_INNER_PURGED_FOLDS")
    bucket_scores: dict[tuple[str, ...], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    defects, scored_ids = [], []
    # Inner validation is used for selection. Inner training exists to establish
    # chronological, purged nesting; no outer/holdout observation enters here.
    for fold in folds:
        if not fold["train"]:
            continue
        for row in fold["validation"]:
            key, row_defects = _causal_feature_key(row, names)
            if row_defects:
                defects.extend(row_defects)
                continue
            complete = True
            values: dict[str, float] = {}
            for policy_id in candidate_ids:
                value = _opportunity_value(row, policy_id)
                if value is None:
                    complete = False
                    defects.append(
                        f"UNKNOWN_CANDIDATE_OUTCOME:{policy_id}:{row.get('episode_id')}"
                    )
                else:
                    values[policy_id] = value
            if not complete:
                continue
            scored_ids.append(str(row.get("episode_id") or ""))
            for policy_id, value in values.items():
                bucket_scores[key][policy_id].append(value)
    if not scored_ids:
        raise ValueError("INSUFFICIENT_COMPLETE_INNER_FOLD_EVIDENCE")
    rules = []
    for key in sorted(bucket_scores):
        selected = _choose(bucket_scores[key], int(minimum_bucket_support))
        if selected is None:
            continue
        rules.append({
            "feature_values": list(key), "selected_policy_id": selected,
            "support": len(bucket_scores[key][selected]),
            "candidate_expectancy_usd_per_opportunity": {
                policy_id: round(sum(values) / len(values), 8)
                for policy_id, values in sorted(bucket_scores[key].items())
            },
        })
    training_rows = sorted(
        ({
            "episode_id": str(row.get("episode_id") or ""),
            "signal_ts": float(row.get("signal_ts")),
            "required_end_ts": float(row.get("required_end_ts")),
        } for row in episodes), key=lambda row: (row["signal_ts"], row["episode_id"]),
    )
    unique_defects = sorted(set(defects))
    body = {
        "schema": RECEIPT_SCHEMA, "purpose": PURPOSE,
        "execution_class": "RESEARCH_ONLY", "relay_eligible": False,
        "training_run_id": str(training_run_id),
        "selection_semantics": "NESTED_INNER_PURGED_VALIDATION_ONLY_FROZEN_BEFORE_OUTER_EVALUATION",
        "feature_semantics": "ONLY_PRE_ENTRY_OBSERVATIONS_AT_OR_BEFORE_SIGNAL_TIME",
        "missing_feature_outcome": "UNKNOWN",
        "post_hoc_regime_selection_allowed": False,
        "feature_names": list(names), "candidates": signed,
        "entry_baseline_registry_signature": ENTRY_BASELINE_REGISTRY["registry_signature"],
        "inner_folds": int(inner_folds), "purge_sec": float(purge_sec),
        "embargo_sec": float(embargo_sec),
        "minimum_bucket_support": int(minimum_bucket_support),
        "training_episode_identities": training_rows,
        "training_cohort_hash": canonical_hash("dynamic-training-cohort", training_rows, length=64),
        "inner_validation_episode_ids": sorted(set(scored_ids)),
        "training_cutoff_required_end_ts": max(row["required_end_ts"] for row in training_rows),
        "unseen_cell_fallback": UNSEEN_CELL_FALLBACK, "rules": rules,
        "training_unknown_reasons": unique_defects,
        "training_evidence_complete": not unique_defects,
    }
    receipt = dict(body)
    receipt["policy_id"] = canonical_hash("frozen-dynamic-entry-policy", body, length=32)
    receipt["content_sha256"] = _sha256_body(body)
    return receipt


def verify_frozen_dynamic_policy(receipt: Mapping[str, Any]) -> bool:
    if not isinstance(receipt, Mapping):
        return False
    body = {key: value for key, value in receipt.items() if key not in {"policy_id", "content_sha256"}}
    return bool(
        receipt.get("schema") == RECEIPT_SCHEMA
        and receipt.get("purpose") == PURPOSE
        and receipt.get("execution_class") == "RESEARCH_ONLY"
        and receipt.get("relay_eligible") is False
        and receipt.get("post_hoc_regime_selection_allowed") is False
        and receipt.get("unseen_cell_fallback") == UNSEEN_CELL_FALLBACK
        and receipt.get("entry_baseline_registry_signature") == ENTRY_BASELINE_REGISTRY["registry_signature"]
        and receipt.get("policy_id") == canonical_hash("frozen-dynamic-entry-policy", body, length=32)
        and receipt.get("content_sha256") == _sha256_body(body)
    )


def write_immutable_policy_receipt(root: Path, relative_path: str, receipt: Mapping[str, Any]) -> Path:
    """Write once beneath an explicit research root; conflicts fail closed."""
    if not verify_frozen_dynamic_policy(receipt):
        raise ValueError("INVALID_FROZEN_DYNAMIC_POLICY_RECEIPT")
    root = Path(root).resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"DYNAMIC_POLICY_RECEIPT_OUTSIDE_ROOT:{target}") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (canonical_json(dict(receipt)) + "\n").encode("utf-8")
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        # Windows text-mode descriptors translate LF to CRLF even for
        # os.write; immutable equality must compare the canonical bytes.
        flags |= getattr(os, "O_BINARY", 0)
        fd = os.open(str(target), flags, 0o444)
    except FileExistsError:
        if target.read_bytes() != payload:
            raise ValueError(f"IMMUTABLE_DYNAMIC_POLICY_CONFLICT:{target.name}")
        return target
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    return target


def evaluate_frozen_dynamic_policy(
    receipt: Mapping[str, Any], episodes: list[dict[str, Any]], *,
    evaluation_mode: str,
    sealed_holdout_evaluation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate without fitting, mutating, or selecting on evaluation rows."""
    if not verify_frozen_dynamic_policy(receipt):
        raise ValueError("INVALID_FROZEN_DYNAMIC_POLICY_RECEIPT")
    mode = str(evaluation_mode).upper()
    if mode not in {"OUTER_PURGED_VALIDATION", "SEALED_HOLDOUT"}:
        raise ValueError("INVALID_DYNAMIC_POLICY_EVALUATION_MODE")
    names = tuple(receipt["feature_names"])
    rules = {tuple(row["feature_values"]): row["selected_policy_id"] for row in receipt["rules"]}
    cutoff = float(receipt["training_cutoff_required_end_ts"])
    candidate_ids = [row["policy_id"] for row in receipt["candidates"]]
    dynamic_values, selections, unknown = [], [], []
    abstention_cost = 0.0
    missed_opportunity_cost = 0.0
    avoided_loss = 0.0
    unseen_cell_count = 0
    static_values: dict[str, list[float]] = {policy_id: [] for policy_id in candidate_ids}
    for row in sorted(episodes, key=lambda item: (float(item.get("signal_ts") or 0), str(item.get("episode_id") or ""))):
        episode_id = str(row.get("episode_id") or "")
        try:
            signal_ts = float(row.get("signal_ts"))
        except (TypeError, ValueError):
            unknown.append({"episode_id": episode_id, "reasons": ["INVALID_SIGNAL_TS"]})
            continue
        if signal_ts <= cutoff:
            unknown.append({"episode_id": episode_id, "reasons": ["TRAIN_EVALUATION_OVERLAP_OR_EMBARGO_BREACH"]})
            continue
        key, defects = _causal_feature_key(row, names)
        if defects:
            unknown.append({"episode_id": episode_id, "reasons": defects})
            continue
        candidate_values = {
            policy_id: _opportunity_value(row, policy_id)
            for policy_id in candidate_ids
        }
        missing_candidates = [
            policy_id for policy_id, value in candidate_values.items()
            if value is None
        ]
        if missing_candidates:
            unknown.append({
                "episode_id": episode_id,
                "reasons": [
                    f"STATIC_BASELINE_OUTCOME_UNKNOWN:{policy_id}"
                    for policy_id in missing_candidates
                ],
            })
            continue
        selected = rules.get(key)
        if selected is None:
            unseen_cell_count += 1
            best_observed = max(candidate_values.values())
            missed = max(0.0, best_observed)
            avoided = max(0.0, -best_observed)
            abstention_cost += missed
            missed_opportunity_cost += missed
            avoided_loss += avoided
            value = 0.0
            selections.append({
                "episode_id": episode_id, "selected_policy_id": "NO_TRADE",
                "selection_reason": "UNSEEN_DYNAMIC_CELL_SIGNED_FALLBACK",
                "feature_values": list(key),
            })
        else:
            value = candidate_values[selected]
            selections.append({"episode_id": episode_id, "selected_policy_id": selected, "selection_reason": "FROZEN_CELL_RULE", "feature_values": list(key)})
        dynamic_values.append(value)
        for policy_id, static_value in candidate_values.items():
            static_values[policy_id].append(static_value)
    def summary(values: list[float]) -> dict[str, Any]:
        return {
            "episodes": len(values),
            "net_pnl_usd": None if not values else round(sum(values), 8),
            "expectancy_usd_per_opportunity": None if not values else round(sum(values) / len(values), 8),
        }
    holdout_verified = bool(
        mode != "SEALED_HOLDOUT"
        or verify_evaluation_receipt(
            sealed_holdout_evaluation,
            policy_id=str(receipt["policy_id"]),
            holdout_episodes=episodes,
        )
    )
    training_complete = receipt.get("training_evidence_complete") is True
    body = {
        "schema": EVALUATION_SCHEMA, "purpose": PURPOSE,
        "policy_id": receipt["policy_id"], "evaluation_mode": mode,
        "policy_frozen_before_evaluation": True,
        "selection_after_evaluation": False,
        "episodes_supplied": len(episodes), "episodes_scored": len(dynamic_values),
        "unknown_episodes": unknown, "selections": selections,
        "dynamic": summary(dynamic_values),
        "unseen_cell_fallback": receipt["unseen_cell_fallback"],
        "fallback_accounting": {
            "unseen_cell_abstentions": unseen_cell_count,
            "abstention_cost_usd": round(abstention_cost, 8),
            "missed_opportunity_cost_usd": round(missed_opportunity_cost, 8),
            "avoided_loss_usd": round(avoided_loss, 8),
            "accounting_semantics": "SAME_EPISODE_SIGNED_CANDIDATE_OUTCOMES_ONLY",
        },
        "signed_static_baselines": {
            policy_id: summary(values) for policy_id, values in sorted(static_values.items())
        },
        "sealed_holdout_evaluation_verified": holdout_verified,
        "qualification_blockers": (
            ([] if training_complete else ["INCOMPLETE_INNER_TRAINING_EVIDENCE"])
            + ([] if not unknown else ["UNKNOWN_OR_INCOMPARABLE_EVALUATION_EPISODES"])
            + ([] if holdout_verified else ["VALID_SEALED_HOLDOUT_EVALUATION_REQUIRED"])
        ),
        "qualification_eligible": training_complete and not unknown and bool(dynamic_values) and holdout_verified,
    }
    body["evaluation_receipt_id"] = canonical_hash("dynamic-policy-evaluation", body, length=64)
    return body


def nested_purged_walk_forward_dynamic(
    episodes: list[dict[str, Any]], *, candidates: Iterable[Mapping[str, Any]],
    feature_names: Iterable[str] = DEFAULT_CAUSAL_FEATURES,
    outer_folds: int = 5, inner_folds: int = 4,
    purge_sec: float = 7200, embargo_sec: float = 300,
    minimum_bucket_support: int = 3, protocol_run_id: str,
) -> dict[str, Any]:
    """Train inside each outer fold, freeze, then score only its later block."""
    candidate_rows = list(candidates)
    outer = chronological_folds(
        episodes, outer_folds=outer_folds, purge_sec=purge_sec,
        embargo_sec=embargo_sec,
    )
    results = []
    for fold in outer:
        try:
            receipt = train_frozen_dynamic_policy(
                fold["train"], candidates=candidate_rows,
                feature_names=feature_names, inner_folds=inner_folds,
                purge_sec=purge_sec, embargo_sec=embargo_sec,
                minimum_bucket_support=minimum_bucket_support,
                training_run_id=f"{protocol_run_id}:outer-{fold['fold']}",
            )
            evaluation = evaluate_frozen_dynamic_policy(
                receipt, fold["validation"],
                evaluation_mode="OUTER_PURGED_VALIDATION",
            )
            results.append({
                "outer_fold": fold["fold"], "status": "EVALUATED",
                "train_episode_ids": fold["train_episode_ids"],
                "validation_episode_ids": fold["validation_episode_ids"],
                "frozen_policy_receipt": receipt,
                "evaluation": evaluation,
            })
        except ValueError as exc:
            results.append({
                "outer_fold": fold["fold"], "status": "UNKNOWN",
                "train_episode_ids": fold["train_episode_ids"],
                "validation_episode_ids": fold["validation_episode_ids"],
                "reasons": [str(exc)],
            })
    complete = [
        row for row in results
        if row["status"] == "EVALUATED"
        and row["evaluation"]["qualification_eligible"] is True
    ]
    body = {
        "schema": "nested_purged_dynamic_policy_protocol_v1",
        "purpose": PURPOSE,
        "selection_semantics": "INNER_PURGED_SELECTION_OUTER_PURGED_EVALUATION_NO_HOLDOUT_REUSE",
        "outer_folds_requested": int(outer_folds),
        "outer_folds_materialized": len(outer),
        "complete_outer_folds": len(complete),
        "purge_sec": float(purge_sec), "embargo_sec": float(embargo_sec),
        "folds": results,
        "passed": bool(results) and len(complete) == len(results),
    }
    body["protocol_receipt_id"] = canonical_hash("nested-dynamic-protocol", body, length=64)
    return body
