"""Generation-bound adapter for the cross-world discovery scorecard.

This module is deliberately a pure report builder: it validates and reads one
immutable evaluator artifact but never writes or reuses a prior publication.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import sqlite3
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Mapping

from discovery_cohort_scorecard import build_episode_matched_scorecard, build_disk_episode_matched_scorecard
from research.policy_evidence_schema import canonical_json

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


def _artifact(root: Path, status: Mapping[str, Any], *, resources,
              index_max_bytes, max_bytes=2 * 1024 * 1024 * 1024):
    from research.shadow_result_stream import DiskRows, MAX_LINE
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
    expected_rows = status.get("row_count")
    if type(expected_rows) is not int or expected_rows < 0:
        return [], {}, ["EVALUATOR_ROW_COUNT_INVALID"]
    limit = int(max_bytes)
    def compressed_hash():
        if path.stat().st_size > limit:
            raise ValueError("EVALUATOR_COMPRESSED_BYTE_BUDGET_EXCEEDED")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                if size > limit:
                    raise ValueError("EVALUATOR_COMPRESSED_BYTE_BUDGET_EXCEEDED")
                digest.update(chunk)
        return digest.hexdigest(), size
    try:
        digest, compressed_size = compressed_hash()
        if digest != expected_hash:
            return [], {"relative_path": relative, "actual_sha256": digest}, ["EVALUATOR_ARTIFACT_SHA256_MISMATCH"]
        rows = resources.enter_context(DiskRows(max_bytes=index_max_bytes))
        total = line_number = 0
        with gzip.open(path, "rb") as handle:
            while True:
                line = handle.readline(MAX_LINE + 1)
                if not line:
                    break
                total += len(line)
                line_number += 1
                if total > limit:
                    raise ValueError("EVALUATOR_DECOMPRESSED_BYTE_BUDGET_EXCEEDED")
                if len(line) > MAX_LINE:
                    raise ValueError("EVALUATOR_LINE_BYTE_BUDGET_EXCEEDED")
                if not line.endswith(b"\n"):
                    raise ValueError("EVALUATOR_TRUNCATED_JSONL_LINE")
                if not line.strip():
                    continue
                row = json.loads(line.decode("utf-8"))
                if not isinstance(row, Mapping):
                    return [], {}, [f"EVALUATOR_ARTIFACT_ROW_NOT_OBJECT:{line_number}"]
                rows.append(row)
                if len(rows) > expected_rows:
                    return [], {}, ["EVALUATOR_ROW_COUNT_MISMATCH"]
        if compressed_hash() != (digest, compressed_size):
            return [], {}, ["EVALUATOR_ARTIFACT_CHANGED_DURING_READ"]
    except ValueError as exc:
        code = str(exc)
        if code.startswith(("EVALUATOR_", "SHADOW_STREAM_INDEX_")):
            return [], {}, [code]
        return [], {}, [f"EVALUATOR_ARTIFACT_INVALID:{type(exc).__name__}"]
    except (OSError, EOFError, UnicodeError, sqlite3.Error) as exc:
        return [], {}, [f"EVALUATOR_ARTIFACT_INVALID:{type(exc).__name__}"]
    if len(rows) != expected_rows:
        return [], {}, ["EVALUATOR_ROW_COUNT_MISMATCH"]
    return rows, {
        "relative_path": relative,
        "artifact_sha256": digest,
        "compressed_size_bytes": compressed_size,
        "decompressed_size_bytes": total,
        "consumption_mode": "FULL_VERIFIED_DISK_STREAM",
        "verified_row_count": len(rows),
    }, []


def _feature(row: Mapping[str, Any], name: str) -> Any:
    features = row.get("regime_features_at_signal")
    item = features.get(name) if isinstance(features, Mapping) else None
    return item.get("value") if isinstance(item, Mapping) and item.get("status") == "OBSERVED" else None


def _receipt_sha(value: Mapping[str, Any]) -> str:
    material = {key: item for key, item in value.items() if key != "receipt_sha256"}
    return hashlib.sha256(canonical_json(material).encode()).hexdigest()


def _observed_regime_feature(receipt: Mapping[str, Any], name: str) -> Any:
    features = receipt.get("regime_features_at_signal")
    item = features.get(name) if isinstance(features, Mapping) else None
    return item.get("value") if isinstance(item, Mapping) and item.get("status") == "OBSERVED" else None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _shadow_aggregate(report: Mapping[str, Any], verified_stream=None) -> tuple[dict[str, Any], list[str]]:
    raw_blockers = report.get("blockers", [])
    if not isinstance(raw_blockers, list) or any(
        not isinstance(item, str) or not item.strip() for item in raw_blockers
    ):
        return {}, ["SHADOW_TERMINAL_AGGREGATE_INVALID:blockers"]
    results = report.get("results")
    results = results if isinstance(results, list) else []
    fields = ("candidate_replay_count", "complete_replay_count", "unknown_replay_count", "results_total")
    supplied = any(field in report for field in fields)
    if report.get("status") == "UNKNOWN" and not results and not supplied:
        return {
            "status": "UNKNOWN", "upstream_blockers": list(report.get("blockers") or []),
            "counts_available": False, "candidate_replay_count": 0,
            "complete_replay_count": 0, "unknown_replay_count": 0,
            "results_total": 0, "results_returned_count": 0,
            "results_truncated": False, "reason_counts": {},
        }, []
    defects, counts = [], {}
    for field in fields:
        raw = report.get(field)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            defects.append(f"SHADOW_TERMINAL_AGGREGATE_INVALID:{field}")
        else:
            counts[field] = raw
    reasons = report.get("reason_counts")
    if not isinstance(reasons, Mapping):
        defects.append("SHADOW_TERMINAL_AGGREGATE_INVALID:reason_counts")
        reasons = {}
    normalized_reasons = {}
    for key, raw in reasons.items():
        if not str(key).strip() or isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            defects.append("SHADOW_TERMINAL_AGGREGATE_INVALID:reason_counts")
        else:
            normalized_reasons[str(key)] = raw
    truncated = report.get("results_truncated")
    if not isinstance(truncated, bool):
        defects.append("SHADOW_TERMINAL_AGGREGATE_INVALID:results_truncated")
        truncated = False
    if not defects:
        if any(count > counts["unknown_replay_count"] for count in normalized_reasons.values()):
            defects.append("SHADOW_TERMINAL_REASON_COUNT_EXCEEDS_UNKNOWN_REPLAYS")
        if counts["candidate_replay_count"] != counts["complete_replay_count"] + counts["unknown_replay_count"]:
            defects.append("SHADOW_TERMINAL_AGGREGATE_COUNT_MISMATCH")
        if counts["results_total"] != counts["candidate_replay_count"]:
            defects.append("SHADOW_TERMINAL_RESULTS_TOTAL_MISMATCH")
        if ((not truncated and len(results) != counts["results_total"])
                or (truncated and len(results) >= counts["results_total"])):
            defects.append("SHADOW_TERMINAL_RETURNED_RESULTS_MISMATCH")
        returned_complete = sum(
            isinstance(item, Mapping) and item.get("status") == "COMPLETE"
            for item in results
        )
        if returned_complete != counts["complete_replay_count"] and verified_stream is None:
            defects.append("SHADOW_TERMINAL_COMPLETE_RESULTS_OMITTED_OR_MISMATCHED")
    effective_blockers = list(report.get("blockers") or [])
    effective_status = report.get("status")
    if verified_stream is not None:
        effective_blockers = [item for item in effective_blockers if item not in {
            "RESULT_STREAM_CONSUMER_NOT_BOUND", "RESULTS_TRUNCATED_WITHOUT_STREAM"}]
        if not effective_blockers and counts.get("unknown_replay_count") == 0 and counts.get("candidate_replay_count", 0) > 0:
            effective_status = "BUILT"
    return {
        "status": effective_status, "upstream_blockers": effective_blockers,
        "counts_available": True, **counts,
        "results_returned_count": len(results), "results_truncated": truncated,
        "full_result_stream_verified": verified_stream is not None,
        "reason_counts": dict(sorted(normalized_reasons.items())),
    }, sorted(set(defects))


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
        "tape_ids": row.get("market_segment_ids") if "market_segment_ids" in row else row.get("tape_ids"),
        "tile_config_signature": row.get("tile_config_signature"),
        "config_signature": row.get("config_signature"),
        "cost_model_id": row.get("cost_model_id") or receipt.get("cost_model_id"),
        "simulation_model": row.get("simulation_model") or receipt.get("simulation_model") or receipt.get("evaluator_version"),
        "economics_evidence_basis": row.get("economics_evidence_basis") or receipt.get("economics_evidence_basis"),
        "declared_contract_sha256": row.get("declared_contract_sha256") or receipt.get("declared_contract_sha256"),
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
    shadow_terminal_report: Mapping[str, Any] | None = None,
    stream_artifact_root: str | Path | None = None,
    index_budget_bytes: int = 2 * 1024 * 1024 * 1024,
    evaluator_max_bytes: int = 2 * 1024 * 1024 * 1024,
) -> dict[str, Any]:
    from research.shadow_result_stream import DiskRows, verify_result_stream
    try:
        # Four simultaneous indexes: evaluator, verified shadow, adapted, cell.
        # Legacy no-stream callers also use bounded disk-backed adaptation.
        index_share = int(index_budget_bytes) // 4
        with ExitStack() as resources:
            stream = None
            if isinstance(shadow_terminal_report, Mapping) and shadow_terminal_report.get("result_stream") is not None:
                stream = resources.enter_context(verify_result_stream(stream_artifact_root if stream_artifact_root is not None else canonical_root,
                                      shadow_terminal_report, expected_generation,
                                      index_max_bytes=index_share))
            adapted = resources.enter_context(DiskRows(max_bytes=index_share))
            return _build_discovery_scorecard_publication(canonical_root,
                    expected_generation=expected_generation, evaluator_status=evaluator_status,
                    baseline_report=baseline_report, shadow_terminal_report=shadow_terminal_report,
                    _stream=stream, _adapted=adapted, _index_max_bytes=index_share,
                    _resources=resources, _evaluator_max_bytes=evaluator_max_bytes)
    except (ValueError, OSError, EOFError, sqlite3.Error, json.JSONDecodeError) as exc:
        return _unknown(expected_generation, [f"SHADOW_STREAM_VERIFICATION_FAILED:{exc}"])


def _build_discovery_scorecard_publication(
    canonical_root, *, expected_generation, evaluator_status, baseline_report,
    shadow_terminal_report=None, _stream=None, _adapted=None, _index_max_bytes=None,
    _resources=None, _evaluator_max_bytes=2 * 1024 * 1024 * 1024,
):
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
    shadow_generation = None
    if shadow_terminal_report is not None:
        shadow_generation, defects = _generation(
            shadow_terminal_report.get("generation")
            if isinstance(shadow_terminal_report, Mapping) else None,
            "SHADOW_TERMINAL",
        )
        blockers.extend(defects)
        if (not isinstance(shadow_terminal_report, Mapping)
                or shadow_terminal_report.get("schema")
                != "generation_bound_conservative_shadow_report_v1"):
            blockers.append("SHADOW_TERMINAL_REPORT_SCHEMA_INVALID")
    if not blockers and (evaluator != expected or baseline != expected
                         or (shadow_terminal_report is not None and shadow_generation != expected)):
        blockers.append("INPUT_GENERATION_MISMATCH")
    if blockers:
        return _unknown(expected, blockers)
    shadow_aggregate = None
    if shadow_terminal_report is not None:
        shadow_aggregate, defects = _shadow_aggregate(shadow_terminal_report, _stream)
        if defects:
            return _unknown(expected, defects, shadow_terminal_aggregate=shadow_aggregate)
    rows, artifact, defects = _artifact(Path(canonical_root), evaluator_status,
        resources=_resources, index_max_bytes=_index_max_bytes, max_bytes=_evaluator_max_bytes)
    if defects:
        return _unknown(expected, defects, input_artifacts={"evaluator": artifact})
    if not isinstance(baseline_report, Mapping) or not isinstance(baseline_report.get("episode_receipts"), list):
        return _unknown(expected, ["BASELINE_EPISODE_RECEIPTS_MISSING"], input_artifacts={"evaluator": artifact})

    adapted = _adapted if _adapted is not None else []
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
    baseline_index: dict[tuple[str, str, str], tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    ambiguous_baseline_keys: set[tuple[str, str, str]] = set()
    for receipt in receipts:
        defects = _row_generation_defects(receipt, expected)
        if defects:
            for defect in defects:
                unknown_counts[f"baseline_receipt:{defect}"] += 1
            continue
        results = receipt.get("results") if isinstance(receipt.get("results"), list) else []
        for result in sorted((item for item in results if isinstance(item, Mapping)),
                             key=lambda item: (str(item.get("baseline_id") or ""), str(item.get("policy_signature") or ""))):
            baseline_key = (str(receipt.get("episode_id") or ""),
                            str(receipt.get("opportunity_id") or ""),
                            str(result.get("baseline_id") or ""))
            if baseline_key in baseline_index:
                unknown_counts["baseline_duplicate_entry_identity"] += 1
                ambiguous_baseline_keys.add(baseline_key)
                baseline_index.pop(baseline_key, None)
            elif baseline_key in ambiguous_baseline_keys:
                unknown_counts["baseline_duplicate_entry_identity"] += 1
            else:
                baseline_index[baseline_key] = (receipt, result)
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
                "economics_evidence_basis": conservative_receipt.get("economics_evidence_basis"),
                "declared_contract_sha256": conservative_receipt.get("declared_contract_sha256"),
            }
            adapted.append(baseline_row)
            for field in _missing(baseline_row):
                missing[f"entry_baseline:{field}"] += 1
            if str(result.get("outcome_state") or "UNKNOWN") == "UNKNOWN":
                unknown_counts["baseline_unknown_outcome"] += 1
                for reason in result.get("rejection_codes") or []:
                    unknown_counts[f"baseline_reason:{reason}"] += 1

    shadow_exact_duplicates = shadow_conflicting_duplicates = 0
    shadow_rows_added = 0
    shadow_provenance = []
    if shadow_terminal_report is not None:
        shadow_results = shadow_terminal_report.get("results")
        if not isinstance(shadow_results, list):
            return _unknown(expected, ["SHADOW_TERMINAL_RESULTS_MISSING"],
                            input_artifacts={"evaluator": artifact})
        grouped_shadow: dict[tuple[str, str, str, str], list[Mapping[str, Any]]] = {}
        for source in shadow_results if _stream is None else ():
            if not isinstance(source, Mapping):
                unknown_counts["shadow_terminal_invalid_result"] += 1
                continue
            key = (str(source.get("episode_id") or ""), str(source.get("opportunity_id") or ""),
                   str(source.get("baseline_id") or ""),
                   str(source.get("composite_policy_signature") or source.get("policy_signature") or ""))
            grouped_shadow.setdefault(key, []).append(source)
        groups = ((key, grouped_shadow[key]) for key in sorted(grouped_shadow)) if _stream is None else (
            (tuple(json.loads(key)), variants) for key, variants in _stream.groups())
        for key, variants in groups:
            canonical_variants = {
                canonical_json({
                    "terminal": item.get("terminal"),
                    "policy_signature": item.get("policy_signature"),
                    "composite_policy_signature": item.get("composite_policy_signature"),
                    "entry_baseline_signature": item.get("entry_baseline_signature"),
                    "exit_policy_signature": item.get("exit_policy_signature"),
                    "evaluated_scope": item.get("evaluated_scope"),
                    "portfolio_competition_status": item.get("portfolio_competition_status"),
                }) for item in variants
            }
            if len(canonical_variants) == 1:
                shadow_exact_duplicates += len(variants) - 1
                source = variants[0]
            else:
                shadow_conflicting_duplicates += 1
                unknown_counts["shadow_terminal_conflicting_duplicate"] += 1
                continue
            episode, entry_result = baseline_index.get(key[:3], ({}, {}))
            terminal = source.get("terminal")
            reasons = []
            if source.get("status") != "COMPLETE":
                # Exact UNKNOWN totals and reason counts are carried once by
                # shadow_terminal_aggregate; sampled rows must not double-count them.
                continue
            if not episode:
                reasons.append("BASELINE_ENTRY_JOIN_MISSING")
            if key[:3] in ambiguous_baseline_keys:
                reasons.append("BASELINE_ENTRY_JOIN_AMBIGUOUS")
            if not isinstance(terminal, Mapping):
                reasons.append("TERMINAL_RECEIPT_MISSING")
                terminal = {}
            composite_signature = key[3]
            for field in ("entry_baseline_signature", "exit_policy_signature",
                          "source_candidate_policy_signature"):
                if not str(source.get(field) or "").strip():
                    reasons.append(f"COMPOSITE_PROVENANCE_MISSING:{field}")
            if (source.get("evaluated_scope") != "ENTRY_PLUS_SINGLE_POSITION_EXIT"
                    or source.get("portfolio_competition_status") != "NOT_SIMULATED"):
                reasons.append("COMPOSITE_EVALUATION_SCOPE_INVALID")
            if terminal.get("status") != "COMPLETE":
                reasons.append("SHADOW_TERMINAL_NOT_COMPLETE")
            if (terminal.get("schema") != "generation_bound_conservative_shadow_terminal_v1"
                    or terminal.get("generation") != expected):
                reasons.append("TERMINAL_RECEIPT_GENERATION_OR_SCHEMA_INVALID")
            if (terminal.get("receipt_sha256") != _receipt_sha(terminal)):
                reasons.append("TERMINAL_RECEIPT_SHA256_INVALID")
            if (terminal.get("profitability_supported") is not True
                    or terminal.get("execution_support_status")
                    != "SUPPORTED_CONSERVATIVE_SHADOW_ONLY"):
                reasons.append("TERMINAL_EXECUTION_SUPPORT_INVALID")
            if (terminal.get("policy_signature") != composite_signature
                    or source.get("policy_signature") != composite_signature):
                reasons.append("COMPOSITE_POLICY_SIGNATURE_MISMATCH")
            baseline_signature = source.get("entry_baseline_signature")
            if entry_result.get("policy_signature") != baseline_signature:
                reasons.append("ENTRY_BASELINE_SIGNATURE_MISMATCH")
            conservative_receipt = entry_result.get("conservative_receipt")
            conservative_receipt = conservative_receipt if isinstance(conservative_receipt, Mapping) else {}
            if (entry_result.get("supported") is not True
                    or entry_result.get("outcome_state") not in {"FULL_FILL", "PARTIAL_FILL"}):
                reasons.append("ENTRY_BASELINE_NOT_SUPPORTED_FILL")
            if terminal.get("entry_receipt_sha256") != hashlib.sha256(
                    canonical_json(conservative_receipt).encode()).hexdigest():
                reasons.append("ENTRY_RECEIPT_BINDING_MISMATCH")
            pnl = _finite(terminal.get("net_pnl_usd"))
            if pnl is None:
                reasons.append("TERMINAL_NET_PNL_MISSING_OR_NONFINITE")
            if reasons:
                for reason in sorted(set(reasons)):
                    unknown_counts[f"shadow_terminal:{reason}"] += 1
                continue
            baseline_spec = entry_result.get("baseline_spec")
            baseline_spec = baseline_spec if isinstance(baseline_spec, Mapping) else {}
            shadow_row = {
                "evidence_world": "CONSERVATIVE_BBO", "episode_id": key[0],
                "opportunity_id": key[1], "epoch_id": episode.get("dataset_epoch"),
                "source_revision": episode.get("source_revision"),
                "deployed_revision": episode.get("deployed_revision"),
                "direction": episode.get("direction"),
                "adx_bucket": episode.get("adx_bucket") or _observed_regime_feature(episode, "adx_bucket"),
                "offset_pct": baseline_spec.get("initial_offset_pct")
                if baseline_spec.get("initial_offset_pct") is not None else 0.0
                if baseline_spec.get("entry_type") == "MARKET_ENTRY" else None,
                "chase_policy": baseline_spec.get("chase_policy_id") or key[2],
                "exit_family": source.get("exit_policy_signature"),
                "policy_id": composite_signature, "policy_signature": composite_signature,
                "schedule_sha256": conservative_receipt.get("schedule_sha256"),
                "original_requested_qty": conservative_receipt.get("requested_qty"),
                "tape_hashes": conservative_receipt.get("tape_hashes"),
                "tape_ids": conservative_receipt.get("tape_ids"),
                "tile_config_signature": episode.get("tile_config_signature"),
                "config_signature": episode.get("config_signature"),
                "cost_model_id": terminal.get("cost_model_id"),
                "simulation_model": terminal.get("simulation_model"),
                "economics_evidence_basis": terminal.get("economics_evidence_basis"),
                "declared_contract_sha256": terminal.get("declared_contract_sha256"),
                "net_pnl_usd": pnl,
                "composite_policy_signature": composite_signature,
                "entry_baseline_signature": baseline_signature,
                "exit_policy_signature": source.get("exit_policy_signature"),
                "source_candidate_policy_signature": source.get("source_candidate_policy_signature"),
                "source_candidate_policy_signatures": sorted(set(
                    str(item.get("source_candidate_policy_signature") or "")
                    for item in variants if str(item.get("source_candidate_policy_signature") or ""))),
                "evaluation_scope": source.get("evaluated_scope"),
                "portfolio_competition_status": source.get("portfolio_competition_status"),
            }
            adapted.append(shadow_row)
            shadow_rows_added += 1
            provenance = {
                "episode_id": key[0], "opportunity_id": key[1], "baseline_id": key[2],
                "composite_policy_signature": composite_signature,
                "terminal_receipt_sha256": terminal.get("receipt_sha256"),
                "cost_model_id": terminal.get("cost_model_id"),
                "cost_model_signature": terminal.get("cost_model_signature"),
                "economics_evidence_basis": terminal.get("economics_evidence_basis"),
                "declared_contract_sha256": terminal.get("declared_contract_sha256"),
                "position_context_signature": terminal.get("position_context_signature"),
                "coverage_policy_signature": terminal.get("coverage_policy_signature"),
                "costs": {field: terminal.get(field) for field in (
                    "trading_fees_usd", "funding_usd", "latency_cost_usd", "total_cost_usd")},
            }
            if _stream is None or len(shadow_provenance) < 100:
                shadow_provenance.append(provenance)
            for field in _missing(shadow_row):
                missing[f"shadow_terminal:{field}"] += 1

    scorecard = build_disk_episode_matched_scorecard(adapted, index_max_bytes=_index_max_bytes)
    has_exact_matched_cohort = any(
        item.get("cohort_equality_proven") is True
        for item in scorecard.get("matched_comparisons") or []
    )
    shadow_aggregate_complete = (
        shadow_terminal_report is None
        or (shadow_aggregate.get("status") == "BUILT"
            and not shadow_aggregate.get("upstream_blockers")
            and shadow_aggregate.get("unknown_replay_count") == 0
            and shadow_aggregate.get("complete_replay_count")
            == shadow_aggregate.get("candidate_replay_count")
            and (shadow_aggregate.get("results_truncated") is False
                 or shadow_aggregate.get("full_result_stream_verified") is True))
    )
    complete_inputs = bool(adapted) and has_exact_matched_cohort and not missing \
        and not any(unknown_counts.values()) and not scorecard.get("blockers")
    complete_inputs = complete_inputs and shadow_aggregate_complete
    complete_matched_outcomes = any(
        item.get("complete_matched_pnl_evidence") is True
        for item in scorecard.get("matched_comparisons") or []
    )
    return {
        "schema": SCHEMA,
        "status": "BUILT_INCOMPLETE" if not complete_inputs else "BUILT",
        "generation": expected,
        "blockers": sorted(scorecard.get("blockers") or []),
        "warnings": sorted(scorecard.get("warnings") or []),
        "input_artifacts": {
            "evaluator": artifact,
            "baseline": {"canonical_sha256": hashlib.sha256(canonical_json(baseline_report).encode()).hexdigest(),
                         "generation": expected, "episode_receipt_count": len(receipts)},
            "shadow_terminal": None if shadow_terminal_report is None else {
                "canonical_sha256": hashlib.sha256(canonical_json(shadow_terminal_report).encode()).hexdigest(),
                "generation": expected,
            },
        },
        "shadow_terminal_provenance": shadow_provenance,
        "shadow_terminal_provenance_truncated": len(shadow_provenance) < shadow_rows_added,
        "shadow_terminal_verified_stream": None if _stream is None else {
            **_stream.verified_summary, "receipt": shadow_terminal_report["result_stream"]},
        "shadow_candidate_count_basis": "UPSTREAM_PARAMETER_REPLAYS_NOT_INDEPENDENT_TRADES",
        "input_counts": {"evaluator_rows": len(rows), "baseline_episode_receipts": len(receipts),
                         "shadow_terminal_rows_added": shadow_rows_added,
                         "shadow_terminal_exact_duplicates_deduplicated": shadow_exact_duplicates,
                         "shadow_terminal_conflicting_duplicate_groups": shadow_conflicting_duplicates,
                         "adapted_rows": len(adapted)},
        "alias_basis": {
            "CONSERVATIVE_BBO": ["CONSERVATIVE_BBO", "entry_baseline conservative_receipt"],
            "CONSERVATIVE_SHADOW_TERMINAL": ["generation-bound composite entry+single-position exit only"],
            "OBSERVED_PAPER": ["REALIZED_COST_COMPLETE and profitability_supported=true only"],
            "IDEAL_TOUCH": [],
        },
        "missing_field_diagnostics": dict(sorted(missing.items())),
        "unjoinable_counts": dict(sorted((key, value) for key, value in unknown_counts.items() if value)),
        "shadow_terminal_aggregate": shadow_aggregate,
        "scorecard": scorecard,
        "input_identity_complete": complete_inputs,
        "profitability_evidence_by_world": {
            world: {
                "available": value["profitability_evidence_available"],
                "complete_cell_count": value["complete_pnl_cell_count"],
                "descriptive_leader": value["descriptive_leader"],
            } for world, value in scorecard["worlds"].items()
        },
        "profitability_supported": complete_inputs and complete_matched_outcomes,
        "winner": None,
        "live_qualification": False,
    }
