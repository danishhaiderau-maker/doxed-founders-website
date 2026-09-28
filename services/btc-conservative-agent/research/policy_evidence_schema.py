"""Contracts for the rebuildable policy-evidence query cache.

This module describes derived analyzer data only.  The canonical V3 ledgers,
market segments and their signed dataset manifest remain the sole evidence
authority.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


SCHEMA_VERSION = "policy_evidence_library_v1"
CACHE_SCHEMA_VERSION = "policy_evidence_cache_v3"
EVALUATOR_VERSION = "lazy_policy_evaluator_v2"
EVIDENCE_WORLDS = frozenset({
    "IDEAL_TOUCH_DIAGNOSTIC",
    "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
    "CONSERVATIVE_BBO_DEPTH_TAPE",
    "CONSERVATIVE_BBO_DEPTH_V1",
    "AUTHENTICATED_ACTUAL",
})
CLASSIFICATIONS = frozenset({"FULL_FILL", "PARTIAL_FILL", "NO_FILL", "UNKNOWN"})
SPLITS = frozenset({"TRAIN", "OOS", "HOLDOUT"})
QUERY_LIST_FIELDS = (
    "comparison_cohort_key", "opportunity_id", "episode_id", "decision_id",
    "policy_signature", "lane", "family", "chase_policy", "exit_family",
    "regime", "side", "split", "ai_direction", "ai_decision",
)
MAX_FILTER_VALUES = 64
MAX_QUERY_LIMIT = 5000
CASE_SENSITIVE_QUERY_FIELDS = frozenset({
    "comparison_cohort_key", "opportunity_id", "episode_id", "decision_id",
    "policy_signature",
})


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_hash(prefix: str, value: Any) -> str:
    return prefix + "-" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _normalized_text_values(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    if len(values) > MAX_FILTER_VALUES:
        raise ValueError(f"TOO_MANY_{field.upper()}_FILTER_VALUES")
    return sorted({str(item).strip().upper() for item in values if str(item).strip()})


def _normalized_identity_values(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    if len(values) > MAX_FILTER_VALUES:
        raise ValueError(f"TOO_MANY_{field.upper()}_FILTER_VALUES")
    return sorted({str(item).strip() for item in values if str(item).strip()})


def _offset(value: Any) -> str | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError("INVALID_ENTRY_OFFSET_PCT") from exc
    if number < Decimal("0.00") or number > Decimal("100.00"):
        raise ValueError("ENTRY_OFFSET_PCT_OUT_OF_RANGE")
    return format(number, ".2f")


def normalize_query(query: Mapping[str, Any]) -> dict[str, Any]:
    world = str(query.get("evidence_world") or "").strip().upper()
    if world not in EVIDENCE_WORLDS:
        raise ValueError("EVIDENCE_WORLD_REQUIRED_OR_INVALID")
    normalized: dict[str, Any] = {"evidence_world": world}
    for field in QUERY_LIST_FIELDS:
        values = (
            _normalized_identity_values(query.get(field), field)
            if field in CASE_SENSITIVE_QUERY_FIELDS
            else _normalized_text_values(query.get(field), field)
        )
        if values:
            allowed_directions = (
                {"LONG", "SHORT", "NO_TRADE"} if field == "ai_direction"
                else {"LONG", "SHORT"}
            )
            if field in {"side", "ai_direction"} and any(
                item not in allowed_directions for item in values
            ):
                raise ValueError("INVALID_SIDE")
            if field == "split" and any(item not in SPLITS for item in values):
                raise ValueError("INVALID_SPLIT")
            normalized[field] = values
    offset = _offset(query.get("entry_offset_pct"))
    if offset is not None:
        normalized["entry_offset_pct"] = offset
    classifications = _normalized_text_values(query.get("classification"), "classification")
    if classifications:
        if any(item not in CLASSIFICATIONS for item in classifications):
            raise ValueError("INVALID_CLASSIFICATION")
        normalized["classification"] = classifications
    limit = int(query.get("limit", 500))
    if limit < 1 or limit > MAX_QUERY_LIMIT:
        raise ValueError("QUERY_LIMIT_OUT_OF_RANGE")
    normalized["limit"] = limit
    return normalized


def generation_identity(manifest: Mapping[str, Any], *, analyzer_revision: str,
                        evaluator_version: str = EVALUATOR_VERSION) -> dict[str, str]:
    fields = {
        "manifest_entry_hash": str(manifest.get("entry_hash") or ""),
        "epoch_id": str(manifest.get("dataset_epoch") or ""),
        "source_revision": str(manifest.get("source_revision") or ""),
        "deployed_revision": str(manifest.get("deployed_revision") or ""),
        "tile_config_signature": str(manifest.get("tile_config_signature") or ""),
        "analyzer_revision": str(analyzer_revision or ""),
        "evaluator_version": str(evaluator_version or ""),
    }
    missing = [key for key, value in fields.items() if not value]
    if missing:
        raise ValueError("GENERATION_IDENTITY_MISSING:" + ",".join(missing))
    return {**fields, "generation_key": stable_hash("generation", fields)}
