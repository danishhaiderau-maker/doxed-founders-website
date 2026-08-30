"""Bounded, lazy access to derived policy evidence.

Callers supply evaluator-produced rows.  This layer deliberately does not
enumerate policies, read runtime state, place orders, or infer fills.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from research.policy_evidence_cache import PolicyEvidenceCache, cache_path
from research.policy_evidence_schema import (
    CACHE_SCHEMA_VERSION, CLASSIFICATIONS, EVIDENCE_WORLDS, SCHEMA_VERSION,
    generation_identity, normalize_query, stable_hash,
)


LIBRARY_MANIFEST_FILE = "policy_evidence_library_manifest.json"


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = None
    try:
        fd, candidate = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(candidate, path)
        candidate = None
    finally:
        if candidate:
            Path(candidate).unlink(missing_ok=True)


def build_library_manifest(canonical_root: str | Path, *, analyzer_revision: str,
                           destination: str | Path) -> dict[str, Any]:
    """Publish status for the cache belonging to the current canonical manifest.

    This never evaluates or ingests rows. A cache for any other generation is
    ignored, which makes stale derived data incapable of entering a report.
    """
    from research.canonical_data_store import CURRENT_MANIFEST, validate_manifest_chain

    root = Path(canonical_root).resolve()
    if root.name != "canonical-research-data":
        raise ValueError("LIBRARY_ROOT_NOT_CANONICAL_RESEARCH_DATA")
    validate_manifest_chain(root)
    current_path = root / CURRENT_MANIFEST
    if not current_path.is_file():
        raise ValueError("CANONICAL_DATASET_MANIFEST_MISSING")
    current = json.loads(current_path.read_text(encoding="utf-8-sig"))
    generation = generation_identity(current, analyzer_revision=analyzer_revision)
    source_revision = generation["source_revision"]
    if not (generation["analyzer_revision"].startswith(source_revision)
            or source_revision.startswith(generation["analyzer_revision"])):
        raise ValueError("ANALYZER_CANONICAL_SOURCE_REVISION_MISMATCH")
    path = cache_path(root, generation["generation_key"])
    row_count = 0
    cache_status = "NOT_BUILT"
    if path.is_file():
        try:
            with sqlite3.connect(path) as connection:
                metadata = dict(connection.execute("SELECT key,value FROM cache_meta"))
                expected = {"cache_schema": CACHE_SCHEMA_VERSION, **generation}
                if metadata != expected:
                    raise ValueError("STALE_OR_FOREIGN_POLICY_EVIDENCE_CACHE")
                row_count = int(connection.execute("SELECT COUNT(*) FROM episode_policy_result").fetchone()[0])
            cache_status = "CURRENT_EMPTY" if row_count == 0 else "CURRENT_AVAILABLE"
        except sqlite3.Error as exc:
            raise ValueError("POLICY_EVIDENCE_CACHE_INVALID") from exc
    payload = {
        "schema": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "authority": "DERIVED_REBUILDABLE_CACHE_ONLY",
        "raw_evidence_authority": "CANONICAL_V3_LEDGERS_AND_CONTENT_ADDRESSED_MARKET_SEGMENTS",
        "evaluation_triggered": False,
        "generation": generation,
        "cache_status": cache_status,
        "result_row_count": row_count,
        "cache_relative_path": path.relative_to(root).as_posix(),
        "query_contract": {
            "bounded": True,
            "evidence_world_required": True,
            "arbitrary_sql_exposed": False,
            "max_query_limit": 5000,
        },
        "qualification_allowed": False,
    }
    binding_summary_path = path.parent / "binding-index-summary.json"
    if binding_summary_path.is_file():
        binding = json.loads(binding_summary_path.read_text(encoding="utf-8-sig"))
        if binding.get("generation") != generation:
            raise ValueError("STALE_OR_FOREIGN_POLICY_BINDING_INDEX")
        payload["binding_coverage"] = {
            key: binding.get(key) for key in (
                "decision_binding_count", "exactly_bound_count",
                "unknown_unverifiable_count", "unknown_reason_counts",
                "bindings_sha256", "exhaustive_relative_path", "exhaustive_sha256",
            )
        }
    _atomic_json(Path(destination), payload)
    return payload


def normalize_result(row: Mapping[str, Any], generation: Mapping[str, str]) -> dict[str, Any]:
    result = dict(row)
    # V3 decision ledgers name this identity ``event_id``. Evaluators may use
    # the clearer ``decision_id``; persist one canonical non-empty value.
    result["decision_id"] = str(result.get("decision_id") or result.get("event_id") or "")
    required = (
        "opportunity_id", "episode_id", "decision_id", "policy_signature",
        "evidence_world", "comparison_cohort_key",
    )
    missing = [field for field in required if not str(result.get(field) or "")]
    if missing:
        raise ValueError("RESULT_IDENTITY_MISSING:" + ",".join(missing))
    world = str(result["evidence_world"]).upper()
    if world not in EVIDENCE_WORLDS:
        raise ValueError("INVALID_RESULT_EVIDENCE_WORLD")
    classification = str(result.get("classification") or "UNKNOWN").upper()
    if classification == "UNSUPPORTED":
        classification = "UNKNOWN"
    if classification not in CLASSIFICATIONS:
        raise ValueError("INVALID_RESULT_CLASSIFICATION")
    supported = bool(result.get("supported"))
    if not supported and classification == "NO_FILL":
        raise ValueError("UNSUPPORTED_CANNOT_BE_NO_FILL")
    if classification in {"FULL_FILL", "PARTIAL_FILL"}:
        qty = result.get("filled_qty")
        if qty is None or float(qty) <= 0:
            raise ValueError("POSITIVE_FILL_QUANTITY_REQUIRED")
    result.update({
        "evidence_world": world,
        "classification": classification,
        "supported": supported,
        "entry_offset_pct": normalize_query({
            "evidence_world": world, "entry_offset_pct": result.get("entry_offset_pct"), "limit": 1,
        }).get("entry_offset_pct"),
        "generation_key": generation["generation_key"],
        "epoch_id": generation["epoch_id"],
        "source_revision": generation["source_revision"],
        "tile_config_signature": generation["tile_config_signature"],
    })
    for field in (
        "lane", "family", "chase_policy", "exit_family", "regime", "side", "split",
        "ai_direction", "ai_decision",
    ):
        if result.get(field) is not None:
            result[field] = str(result[field]).strip().upper()
    return result


class PolicyEvidenceLibrary:
    def __init__(self, canonical_root: str, manifest: Mapping[str, Any], *, analyzer_revision: str):
        self.generation = generation_identity(manifest, analyzer_revision=analyzer_revision)
        self.cache = PolicyEvidenceCache(canonical_root, self.generation)

    def ingest(self, evaluated_rows: Iterable[Mapping[str, Any]]) -> int:
        """Persist only rows already produced by a trusted analyzer evaluator."""
        return self.cache.put_rows(normalize_result(row, self.generation) for row in evaluated_rows)

    def query(self, query: Mapping[str, Any]) -> dict[str, Any]:
        normalized = normalize_query(query)
        query_hash = stable_hash("query", {
            "generation_key": self.generation["generation_key"], "query": normalized,
        })
        cached = self.cache.get_query(query_hash)
        if cached is not None:
            return cached
        rows = self.cache.select(normalized)
        total_row_count = self.cache.count(normalized)
        cohort_keys = sorted({str(row["comparison_cohort_key"]) for row in rows})
        policy_cohorts: dict[str, set[str]] = {}
        for row in rows:
            policy_cohorts.setdefault(str(row["policy_signature"]), set()).add(
                str(row["comparison_cohort_key"])
            )
        if len(policy_cohorts) > 1:
            if total_row_count > len(rows):
                raise ValueError("TRUNCATED_POLICY_COMPARISON_FORBIDDEN")
            cohort_sets = {tuple(sorted(values)) for values in policy_cohorts.values()}
            if len(cohort_sets) != 1:
                raise ValueError("MIXED_COMPARISON_COHORTS_FORBIDDEN")
        comparison_group_key = stable_hash("comparison-group", {
            "generation_key": self.generation["generation_key"],
            "evidence_world": normalized["evidence_world"],
            "cohort_keys": cohort_keys,
        }) if cohort_keys else None
        result = {
            "schema": "policy_evidence_query_result_v1",
            "generation": dict(self.generation),
            "query": normalized,
            "query_hash": query_hash,
            "comparison_cohort_key": cohort_keys[0] if len(cohort_keys) == 1 else None,
            "comparison_cohort_keys": cohort_keys,
            "comparison_group_key": comparison_group_key,
            "total_row_count": total_row_count,
            "truncated": total_row_count > len(rows),
            "row_count": len(rows),
            "rows": rows,
        }
        self.cache.put_query(query_hash, result)
        return result
