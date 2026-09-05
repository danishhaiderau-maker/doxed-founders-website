"""Generation-bound adapter for the cross-world discovery scorecard.

This module is deliberately a pure report builder: it validates and reads one
immutable evaluator artifact but never writes or reuses a prior publication.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from discovery_cohort_scorecard import build_episode_matched_scorecard

SCHEMA = "generation_bound_discovery_scorecard_publication_v1"
GENERATION_FIELDS = (
    "manifest_entry_hash", "epoch_id", "source_revision", "deployed_revision",
    "tile_config_signature", "analyzer_revision", "evaluator_version", "generation_key",
)
_SENTINELS = {"", "UNKNOWN", "UNAVAILABLE", "NONE", "NULL", "MISSING"}


def _unknown(expected: Mapping[str, Any], blockers: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "UNKNOWN",
        "generation": dict(expected),
        "blockers": sorted(set(blockers)),
        "input_artifacts": {},
        "input_counts": {},
        "alias_basis": {},
        "missing_field_diagnostics": {},
        "unjoinable_counts": {},
        "scorecard": None,
        "profitability_supported": False,
        "winner": None,
        "live_qualification": False,
        **extra,
    }


def _generation(value: Any, label: str) -> tuple[dict[str, str], list[str]]:
    if not isinstance(value, Mapping):
        return {}, [f"{label}_GENERATION_MISSING"]
    normalized: dict[str, str] = {}
    blockers = []
    for field in GENERATION_FIELDS:
        raw = value.get(field)
        if isinstance(raw, (bool, Mapping, list, tuple, set)):
            blockers.append(f"{label}_GENERATION_INVALID:{field}")
            continue
        text = str(raw or "").strip()
        if text.upper() in _SENTINELS:
            blockers.append(f"{label}_GENERATION_MISSING:{field}")
            continue
        normalized[field] = text
    return normalized, blockers


def _artifact(root: Path, status: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    relative = status.get("relative_path")
    expected_hash = str(status.get("artifact_sha256") or "").strip().lower()
    if not isinstance(relative, str) or not relative.strip():
        return [], {}, ["EVALUATOR_ARTIFACT_PATH_MISSING"]
    if len(expected_hash) != 64 or any(ch not in "0123456789abcdef" for ch in expected_hash):
        return [], {}, ["EVALUATOR_ARTIFACT_SHA256_INVALID"]
    canonical_root = root.resolve()
    path = (canonical_root / relative).resolve()
    try:
        path.relative_to(canonical_root)
    except ValueError:
        return [], {}, ["EVALUATOR_ARTIFACT_OUTSIDE_CANONICAL_ROOT"]
    if not path.is_file():
        return [], {}, ["EVALUATOR_ARTIFACT_MISSING"]
    compressed = path.read_bytes()
    digest = hashlib.sha256(compressed).hexdigest()
    if digest != expected_hash:
        return [], {"relative_path": relative, "actual_sha256": digest}, ["EVALUATOR_ARTIFACT_SHA256_MISMATCH"]
    rows: list[dict[str, Any]] = []
    try:
        with gzip.open(io.BytesIO(compressed), "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, Mapping):
                    return [], {}, [f"EVALUATOR_ARTIFACT_ROW_NOT_OBJECT:{line_number}"]
                rows.append(dict(row))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [], {}, [f"EVALUATOR_ARTIFACT_INVALID:{type(exc).__name__}"]
    try:
        expected_rows = int(status.get("row_count"))
    except (TypeError, ValueError, OverflowError):
        return [], {}, ["EVALUATOR_ROW_COUNT_INVALID"]
    if expected_rows < 0 or len(rows) != expected_rows:
        return [], {}, ["EVALUATOR_ROW_COUNT_MISMATCH"]
    return rows, {
        "relative_path": relative,
        "artifact_sha256": digest,
        "compressed_size_bytes": len(compressed),
        "verified_row_count": len(rows),
    }, []


def _feature(row: Mapping[str, Any], name: str) -> Any:
    features = row.get("regime_features_at_signal")
    item = features.get(name) if isinstance(features, Mapping) else None
    return item.get("value") if isinstance(item, Mapping) and item.get("status") == "OBSERVED" else None


def _base_row(row: Mapping[str, Any]) -> dict[str, Any]:
    receipt = row.get("evaluator_receipt") if isinstance(row.get("evaluator_receipt"), Mapping) else {}
    return {
        "episode_id": row.get("episode_id"),
        "opportunity_id": row.get("opportunity_id"),
        "epoch_id": row.get("epoch_id"),
        "source_revision": row.get("source_revision"),
        "deployed_revision": row.get("deployed_revision"),
        "direction": row.get("side"),
        "adx_bucket": row.get("adx_bucket"),
        "adx_value": _feature(row, "adx"),
        "offset_pct": row.get("entry_offset_pct"),
        "chase_policy": row.get("chase_policy"),
        "exit_family": row.get("exit_family"),
        "policy_id": row.get("policy_id"),
        "policy_signature": row.get("policy_signature"),
        "schedule_sha256": row.get("schedule_sha256"),
        "schedule_id": row.get("schedule_id"),
        "original_requested_qty": row.get("requested_qty"),
        "tape_sha256": row.get("tape_sha256"),
        "tape_hashes": row.get("tape_hashes"),
        "tape_ids": row.get("tape_ids"),
        "tile_config_signature": row.get("tile_config_signature"),
        "config_signature": row.get("config_signature"),
        "cost_model_id": row.get("cost_model_id") or receipt.get("cost_model_id"),
        "simulation_model": row.get("simulation_model") or receipt.get("simulation_model"),
    }


def _row_generation_defects(row: Mapping[str, Any], generation: Mapping[str, str]) -> list[str]:
    aliases = {
        "epoch_id": ("epoch_id", "dataset_epoch"),
        "source_revision": ("source_revision",),
        "deployed_revision": ("deployed_revision",),
        "tile_config_signature": ("tile_config_signature",),
    }
    defects = []
    for expected_field, fields in aliases.items():
        expected = generation.get(expected_field)
        for field in fields:
            raw = row.get(field)
            if raw not in (None, "") and str(raw).strip() != expected:
                defects.append(f"{expected_field}_mismatch")
    return defects


def _missing(row: Mapping[str, Any]) -> list[str]:
    required = (
        "episode_id", "opportunity_id", "epoch_id", "source_revision", "deployed_revision",
        "direction", "adx_bucket", "offset_pct", "chase_policy", "exit_family", "policy_id",
        "policy_signature", "schedule_sha256", "original_requested_qty", "tape_hashes",
        "tape_ids", "tile_config_signature", "config_signature", "cost_model_id", "simulation_model",
    )
    return [field for field in required if row.get(field) in (None, "", [], ())]


def build_discovery_scorecard_publication(
    canonical_root: str | Path, *, expected_generation: Mapping[str, Any],
    evaluator_status: Mapping[str, Any], baseline_report: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a fresh report from two explicitly same-generation inputs."""
    expected, blockers = _generation(expected_generation, "EXPECTED")
    evaluator, defects = _generation(
        evaluator_status.get("generation") if isinstance(evaluator_status, Mapping) else None,
        "EVALUATOR",
    )
    blockers.extend(defects)
    baseline, defects = _generation(
        baseline_report.get("generation") if isinstance(baseline_report, Mapping) else None,
        "BASELINE",
    )
    blockers.extend(defects)
    if not isinstance(evaluator_status, Mapping) or evaluator_status.get("schema") != "v3_conservative_policy_evidence_v1":
        blockers.append("EVALUATOR_SUMMARY_SCHEMA_INVALID")
    if not isinstance(baseline_report, Mapping) or baseline_report.get("schema") != "entry_baseline_same_opportunity_replay_v1":
        blockers.append("BASELINE_REPORT_SCHEMA_INVALID")
    if not blockers and (evaluator != expected or baseline != expected):
        blockers.append("INPUT_GENERATION_MISMATCH")
    if blockers:
        return _unknown(expected, blockers)
    rows, artifact, defects = _artifact(Path(canonical_root), evaluator_status)
    if defects:
        return _unknown(expected, defects, input_artifacts={"evaluator": artifact})
    if not isinstance(baseline_report, Mapping) or not isinstance(baseline_report.get("episode_receipts"), list):
        return _unknown(expected, ["BASELINE_EPISODE_RECEIPTS_MISSING"], input_artifacts={"evaluator": artifact})

    adapted: list[dict[str, Any]] = []
    missing = Counter()
    unknown_counts = Counter()
    for source in rows:
        defects = _row_generation_defects(source, expected)
        if defects:
            for defect in defects:
                unknown_counts[f"evaluator_row:{defect}"] += 1
            continue
        conservative = {**_base_row(source), "evidence_world": "CONSERVATIVE_BBO", "net_pnl_usd": None}
        adapted.append(conservative)
        for field in _missing(conservative):
            missing[f"evaluator_conservative:{field}"] += 1
        if str(source.get("classification") or "UNKNOWN") == "UNKNOWN":
            unknown_counts["evaluator_unknown_classification"] += 1
            for reason in source.get("unknown_reason_codes") or []:
                unknown_counts[f"evaluator_reason:{reason}"] += 1
        terminal = str(source.get("terminal_outcome_status") or "UNKNOWN")
        if terminal == "REALIZED_COST_COMPLETE" and source.get("profitability_supported") is True:
            observed = {**_base_row(source), "evidence_world": "OBSERVED_PAPER",
                        "cost_model_id": source.get("observed_cost_model_id"),
                        "simulation_model": source.get("observed_execution_model"),
                        "net_pnl_usd": source.get("net_pnl_usd")}
            adapted.append(observed)
            for field in _missing(observed):
                missing[f"observed_paper:{field}"] += 1
        else:
            unknown_counts[f"paper_terminal:{terminal}"] += 1
            for reason in source.get("terminal_outcome_reason_codes") or []:
                unknown_counts[f"paper_terminal_reason:{reason}"] += 1

    receipts = sorted(
        (item for item in baseline_report["episode_receipts"] if isinstance(item, Mapping)),
        key=lambda item: (str(item.get("opportunity_id") or ""), str(item.get("episode_id") or "")),
    )
    invalid_receipts = len(baseline_report["episode_receipts"]) - len(receipts)
    unknown_counts["baseline_invalid_receipt"] += invalid_receipts
    try:
        declared_baseline_count = int(baseline_report.get("same_opportunity_count"))
    except (TypeError, ValueError, OverflowError):
        declared_baseline_count = -1
    if declared_baseline_count != len(receipts):
        return _unknown(expected, ["BASELINE_ROW_COUNT_MISMATCH"],
                        input_artifacts={"evaluator": artifact},
                        input_counts={"evaluator_rows": len(rows),
                                      "declared_baseline_episode_receipts": declared_baseline_count,
                                      "valid_baseline_episode_receipts": len(receipts)},
                        unjoinable_counts={"baseline_invalid_receipt": invalid_receipts})
    for receipt in receipts:
        defects = _row_generation_defects(receipt, expected)
        if defects:
            for defect in defects:
                unknown_counts[f"baseline_receipt:{defect}"] += 1
            continue
        results = receipt.get("results") if isinstance(receipt.get("results"), list) else []
        for result in sorted((item for item in results if isinstance(item, Mapping)),
                             key=lambda item: (str(item.get("baseline_id") or ""), str(item.get("policy_signature") or ""))):
            conservative_receipt = result.get("conservative_receipt")
            conservative_receipt = conservative_receipt if isinstance(conservative_receipt, Mapping) else {}
            baseline_row = {
                "evidence_world": "CONSERVATIVE_BBO",
                "episode_id": receipt.get("episode_id"), "opportunity_id": receipt.get("opportunity_id"),
                "epoch_id": receipt.get("dataset_epoch"), "source_revision": receipt.get("source_revision"),
                "deployed_revision": receipt.get("deployed_revision"), "direction": receipt.get("direction"),
                "adx_bucket": receipt.get("adx_bucket"), "offset_pct": receipt.get("entry_offset_pct"),
                "chase_policy": receipt.get("chase_policy"), "exit_family": receipt.get("exit_family"),
                "policy_id": result.get("baseline_id"), "policy_signature": result.get("policy_signature"),
                "schedule_sha256": conservative_receipt.get("schedule_sha256"),
                "original_requested_qty": conservative_receipt.get("requested_qty"),
                "tape_hashes": conservative_receipt.get("tape_hashes"), "tape_ids": conservative_receipt.get("tape_ids"),
                "tile_config_signature": receipt.get("tile_config_signature"),
                "config_signature": receipt.get("config_signature"),
                "cost_model_id": conservative_receipt.get("cost_model_id"),
                "simulation_model": conservative_receipt.get("simulation_model"), "net_pnl_usd": None,
            }
            adapted.append(baseline_row)
            for field in _missing(baseline_row):
                missing[f"entry_baseline:{field}"] += 1
            if str(result.get("outcome_state") or "UNKNOWN") == "UNKNOWN":
                unknown_counts["baseline_unknown_outcome"] += 1
                for reason in result.get("rejection_codes") or []:
                    unknown_counts[f"baseline_reason:{reason}"] += 1

    scorecard = build_episode_matched_scorecard(adapted)
    has_exact_matched_cohort = any(
        item.get("cohort_equality_proven") is True
        for item in scorecard.get("matched_comparisons") or []
    )
    complete_inputs = bool(adapted) and has_exact_matched_cohort and not missing \
        and not any(unknown_counts.values()) and not scorecard.get("blockers")
    return {
        "schema": SCHEMA,
        "status": "BUILT_INCOMPLETE" if not complete_inputs else "BUILT",
        "generation": expected,
        "blockers": sorted(scorecard.get("blockers") or []),
        "input_artifacts": {"evaluator": artifact},
        "input_counts": {"evaluator_rows": len(rows), "baseline_episode_receipts": len(receipts),
                         "adapted_rows": len(adapted)},
        "alias_basis": {
            "CONSERVATIVE_BBO": ["CONSERVATIVE_BBO", "entry_baseline conservative_receipt"],
            "OBSERVED_PAPER": ["REALIZED_COST_COMPLETE and profitability_supported=true only"],
            "IDEAL_TOUCH": [],
        },
        "missing_field_diagnostics": dict(sorted(missing.items())),
        "unjoinable_counts": dict(sorted((key, value) for key, value in unknown_counts.items() if value)),
        "scorecard": scorecard,
        "profitability_supported": complete_inputs and any(
            world.get("independent_episode_count", 0) for world in [scorecard["worlds"]["OBSERVED_PAPER"]]
        ),
        "winner": None,
        "live_qualification": False,
    }
