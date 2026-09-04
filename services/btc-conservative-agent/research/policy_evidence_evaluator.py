"""Generation-bound conservative policy evidence evaluation.

This is a read-only analyzer component.  It consumes exact V3 causal bindings,
content-addressed one-second market segments and authoritative order schedules.
It never supplies defaults for missing evidence and never changes an order.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from research.conservative_limit_fill import evaluate_limit_fill
from research.policy_evidence_bindings import (
    ALL_OPPORTUNITY_FUTURE_ROLE,
    authoritative_future_path_segments,
    authoritative_schedule_intents,
    build_v3_binding_index,
    complete_conservative_future_path,
    segment_role,
)
from research.policy_evidence_schema import canonical_json, generation_identity, stable_hash
from research.lifecycle_evidence_join import (
    build_lifecycle_evidence_index,
    join_lifecycle_evidence,
)
from combo_pathway_config import (
    ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY, active_tile_registry_signature,
)


SCHEMA = "v3_conservative_policy_evidence_v1"
EVIDENCE_WORLD = "CONSERVATIVE_BBO_DEPTH_TAPE"

# This is an explicit research-support floor, not a profitability threshold.
# It prevents a Phase-7 segmented result from being described as supported from
# a handful of repeated policy rows that all belong to the same opportunity.
RUNTIME_REGIME_TAXONOMY_V1 = {
    "schema": "frozen_runtime_regime_taxonomy_v1",
    "version": "btc_v31_runtime_regimes_v1",
    "regimes": [
        "WEAKENING", "TRANSITION", "RANGE", "COMPRESSION", "EXPANSION", "TRENDING",
    ],
    "directions": ["LONG", "SHORT"],
}
RUNTIME_REGIME_TAXONOMY_V1["signature"] = stable_hash(
    "runtime-regime-taxonomy", RUNTIME_REGIME_TAXONOMY_V1
)

PHASE7_SUPPORT_GATE_V1 = {
    "schema": "phase7_regime_support_gate_config_v2",
    "minimum_independent_cohorts": 30,
    "minimum_effective_cohorts": 30,
    "minimum_cohorts_per_regime_direction": 3,
    "runtime_taxonomy": RUNTIME_REGIME_TAXONOMY_V1,
    # Eligibility is supplied by the signed policy/tile registry for the exact
    # generation.  The evaluator must not silently assume every taxonomy cell
    # is tradable, nor omit a cell merely because no evidence was observed.
    "eligible_cells": [],
    "required_dimensions": [
        "realized_volatility", "volatility_of_volatility", "market_spread_bps",
        "bid_depth_qty", "ask_depth_qty", "liquidity", "regime", "adx",
        "trend_strength", "market_structure", "session", "signal_timestamp",
    ],
    "purpose": "RESEARCH_FEATURE_SUPPORT_ONLY",
    "threshold_basis": (
        "Conservative eligibility floor: 30 canonical independent decisions overall and "
        "at least 3 in every frozen runtime-taxonomy cell eligible to trade. This does not prove "
        "profitability, statistical power, execution quality, or live readiness."
    ),
}


def _canonical_decision_identity(row: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Return a lane-independent identity and an explicit failure reason.

    A research lane, policy signature, tape, or schedule must never multiply N.
    """
    fields = {
        name: str(row.get(name) or "")
        for name in ("epoch_id", "opportunity_id", "episode_id")
    }
    if not all(fields.values()):
        return None, "MISSING_CANONICAL_DECISION_IDENTITY"
    return stable_hash("independent-decision", fields), None


def _validate_runtime_taxonomy(config: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    taxonomy = config.get("runtime_taxonomy")
    reasons: list[str] = []
    if not isinstance(taxonomy, Mapping):
        return {}, ["FROZEN_RUNTIME_TAXONOMY_MISSING"]
    unsigned = {key: value for key, value in taxonomy.items() if key != "signature"}
    expected = stable_hash("runtime-regime-taxonomy", unsigned)
    signature = str(taxonomy.get("signature") or "")
    if taxonomy.get("schema") != "frozen_runtime_regime_taxonomy_v1":
        reasons.append("FROZEN_RUNTIME_TAXONOMY_SCHEMA_MISMATCH")
    if signature != expected:
        reasons.append("FROZEN_RUNTIME_TAXONOMY_SIGNATURE_MISMATCH")
    regimes = [str(value).upper() for value in taxonomy.get("regimes") or []]
    directions = [str(value).upper() for value in taxonomy.get("directions") or []]
    if not regimes or len(regimes) != len(set(regimes)) or not directions:
        reasons.append("FROZEN_RUNTIME_TAXONOMY_INVALID")
    return {
        "schema": taxonomy.get("schema"), "version": taxonomy.get("version"),
        "signature": signature, "regimes": regimes, "directions": directions,
    }, reasons


def _validate_eligible_cell_registry(
    config: Mapping[str, Any], taxonomy: Mapping[str, Any], rows: list[dict[str, Any]],
) -> tuple[list[Mapping[str, Any]], dict[str, Any], list[str]]:
    receipt = config.get("eligible_cell_registry")
    if not isinstance(receipt, Mapping):
        return [], {}, ["SIGNED_ELIGIBLE_CELL_REGISTRY_MISSING"]
    reasons: list[str] = []
    unsigned = {key: value for key, value in receipt.items() if key != "signature"}
    if receipt.get("schema") != "eligible_regime_direction_cells_v1":
        reasons.append("ELIGIBLE_CELL_REGISTRY_SCHEMA_MISMATCH")
    if str(receipt.get("signature") or "") != stable_hash(
        "eligible-regime-direction-cells", unsigned
    ):
        reasons.append("ELIGIBLE_CELL_REGISTRY_SIGNATURE_MISMATCH")
    if receipt.get("runtime_taxonomy_signature") != taxonomy.get("signature"):
        reasons.append("ELIGIBLE_CELL_REGISTRY_TAXONOMY_MISMATCH")
    current_tile_signature = active_tile_registry_signature()
    if receipt.get("active_tile_registry_signature") != current_tile_signature:
        reasons.append("ELIGIBLE_CELL_REGISTRY_ACTIVE_TILE_SIGNATURE_MISMATCH")
    if receipt.get("tile_config_signature") != current_tile_signature:
        reasons.append("ELIGIBLE_CELL_REGISTRY_GENERATION_TILE_SIGNATURE_MISMATCH")
    provenance = {
        name: str(receipt.get(name) or "")
        for name in (
            "source_revision", "epoch_id", "manifest_entry_hash", "tile_config_signature",
        )
    }
    if not all(provenance.values()):
        reasons.append("ELIGIBLE_CELL_REGISTRY_PROVENANCE_MISSING")
    for row in rows:
        for name, expected in provenance.items():
            observed = str(row.get(name) or "")
            if observed and observed != expected:
                reasons.append("ELIGIBLE_CELL_REGISTRY_PROVENANCE_MISMATCH")
                break
    cells = receipt.get("eligible_cells")
    if not isinstance(cells, list):
        reasons.append("ELIGIBLE_CELL_REGISTRY_CELLS_INVALID")
        cells = []
    return cells, {
        "schema": receipt.get("schema"), "signature": receipt.get("signature"),
        "runtime_taxonomy_signature": receipt.get("runtime_taxonomy_signature"),
        **provenance,
    }, list(dict.fromkeys(reasons))


def build_signed_eligible_cell_registry(generation: Mapping[str, Any]) -> dict[str, Any]:
    """Bind tradable cells to the sole active tile registry and exact generation."""
    active_tiles = [
        {
            "tile_id": lane,
            "policy_signature": ACTIVE_TILE_REGISTRY[lane]["policy_signature"],
            "paper_eligible": bool(ACTIVE_TILE_REGISTRY[lane]["paper_eligible"]),
        }
        for lane in ACTIVE_TILE_ORDER
    ]
    body = {
        "schema": "eligible_regime_direction_cells_v1",
        "runtime_taxonomy_signature": RUNTIME_REGIME_TAXONOMY_V1["signature"],
        "source_revision": str(generation.get("source_revision") or ""),
        "epoch_id": str(generation.get("epoch_id") or ""),
        "manifest_entry_hash": str(generation.get("manifest_entry_hash") or ""),
        "tile_config_signature": str(generation.get("tile_config_signature") or ""),
        "active_tile_registry_signature": active_tile_registry_signature(),
        "active_tiles": active_tiles,
        # The canonical registry declares no regime or direction exclusions;
        # every paper-eligible active tile therefore has both directions in
        # every frozen runtime regime when its independent toggle is ON.
        "eligibility_basis": "ACTIVE_TILE_REGISTRY_NO_REGIME_OR_DIRECTION_EXCLUSIONS",
        "eligible_cells": [
            {"regime": regime, "direction": direction}
            for regime in RUNTIME_REGIME_TAXONOMY_V1["regimes"]
            for direction in RUNTIME_REGIME_TAXONOMY_V1["directions"]
        ],
    }
    return {**body, "signature": stable_hash("eligible-regime-direction-cells", body)}


def _validate_projection(config: Mapping[str, Any], decision_ids: set[str]) -> tuple[dict[str, Any], list[str]]:
    receipt = config.get("regime_projection")
    if not isinstance(receipt, Mapping):
        return {"status": "NOT_USED"}, ["SIGNED_FOLD_FITTED_REGIME_PROJECTION_MISSING"]
    reasons: list[str] = []
    unsigned = {key: value for key, value in receipt.items() if key != "signature"}
    expected = stable_hash("fold-fitted-regime-projection", unsigned)
    if receipt.get("schema") != "fold_fitted_regime_projection_v1":
        reasons.append("REGIME_PROJECTION_SCHEMA_MISMATCH")
    if str(receipt.get("signature") or "") != expected:
        reasons.append("REGIME_PROJECTION_SIGNATURE_MISMATCH")
    if receipt.get("fit_scope") != "TRAINING_FOLD_ONLY" or not receipt.get("fold_id"):
        reasons.append("REGIME_PROJECTION_NOT_FOLD_FITTED")
    taxonomy = config.get("runtime_taxonomy") or {}
    if receipt.get("source_taxonomy_signature") != taxonomy.get("signature"):
        reasons.append("REGIME_PROJECTION_SOURCE_TAXONOMY_MISMATCH")
    fit_ids = {str(value) for value in receipt.get("fit_decision_ids") or [] if value}
    evaluation_ids = {str(value) for value in receipt.get("evaluation_decision_ids") or [] if value}
    if fit_ids.intersection(evaluation_ids) or fit_ids.intersection(decision_ids):
        reasons.append("REGIME_PROJECTION_FIT_EVALUATION_LEAKAGE")
    if evaluation_ids != decision_ids:
        reasons.append("REGIME_PROJECTION_EVALUATION_COHORT_MISMATCH")
    return {
        "status": "VALID" if not reasons else "INVALID",
        "schema": receipt.get("schema"), "fold_id": receipt.get("fold_id"),
        "signature": receipt.get("signature"), "fit_decision_count": len(fit_ids),
        "evaluation_decision_count": len(evaluation_ids),
    }, reasons


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"V3_LEDGER_ROW_NOT_OBJECT:{path.name}:{number}")
            rows.append(value)
    return rows


def _identity(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return tuple(str(row.get(field) or "") for field in ("epoch_id", "opportunity_id", "episode_id"))


def _policy_identity(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (*_identity(row), str(row.get("policy_signature") or ""))


def _number(*values: Any) -> float | None:
    for value in values:
        try:
            result = float(value)
        except (TypeError, ValueError):
            continue
        if result == result:
            return result
    return None


def _observed_feature(value: Any, source: str) -> dict[str, Any]:
    """Expose a research feature without inventing a missing observation."""
    return {
        "status": "OBSERVED" if value is not None and value != "" else "UNKNOWN",
        "value": value if value is not None and value != "" else None,
        "source": source if value is not None and value != "" else None,
    }


def _first_feature(*candidates: tuple[Any, str]) -> dict[str, Any]:
    for value, source in candidates:
        if value is not None and value != "":
            return _observed_feature(value, source)
    return _observed_feature(None, "")


_NORMALIZED_PRE_ENTRY_FEATURES = (
    "atr_bucket", "realized_volatility_bucket", "spread_bucket",
    "depth_bucket", "liquidity_bucket", "regime", "direction",
    "trend_strength_bucket",
)
_PARTIAL_PRE_ENTRY_BLOCKER_PREFIXES = (
    "MISSING_PRE_ENTRY_FEATURE:",
    "FEATURE_TIMESTAMP_MISSING:",
    "POST_ENTRY_FEATURE_LEAKAGE:",
)


def _validated_pre_entry_features(
    opportunity: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return only join-validated, finite, pre-signal observations.

    ``research_v3_report.join_pre_entry_feature_receipts`` owns receipt
    identity and ambiguity validation.  The evaluator consumes its normalized
    projection only when that join completed without blockers, and repeats the
    temporal/finite checks so a hand-crafted analyzer input cannot bypass the
    fail-closed boundary.
    """
    status = opportunity.get("pre_entry_feature_status")
    blockers = opportunity.get("pre_entry_feature_blockers")
    if status not in {"COMPLETE", "UNKNOWN"} or not isinstance(blockers, list):
        return {}
    if status == "COMPLETE" and blockers:
        return {}
    if status == "UNKNOWN" and (
        not blockers
        or any(
            not isinstance(blocker, str)
            or not blocker.startswith(_PARTIAL_PRE_ENTRY_BLOCKER_PREFIXES)
            for blocker in blockers
        )
    ):
        return {}
    source = opportunity.get("pre_entry_features")
    if not isinstance(source, Mapping):
        return {}
    if isinstance(opportunity.get("signal_ts"), bool):
        return {}
    try:
        signal_ts = float(opportunity.get("signal_ts"))
    except (TypeError, ValueError):
        return {}
    if not math.isfinite(signal_ts):
        return {}

    observed: dict[str, dict[str, Any]] = {}
    for name in _NORMALIZED_PRE_ENTRY_FEATURES:
        item = source.get(name)
        if not isinstance(item, Mapping):
            continue
        value = item.get("value")
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if isinstance(value, str) and value.strip().upper() in {
            "NAN", "INFINITY", "+INFINITY", "-INFINITY", "INF", "+INF", "-INF",
        }:
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(float(value)):
                continue
        if isinstance(item.get("observed_ts"), bool):
            continue
        try:
            observed_ts = float(item.get("observed_ts"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(observed_ts) or observed_ts > signal_ts:
            continue
        observed[name] = {
            "status": "OBSERVED",
            "value": value,
            "source": f"opportunity.pre_entry_features.{name}",
            "observed_ts": observed_ts,
        }
    return observed


def _regime_features_at_signal(
    opportunity: Mapping[str, Any], feature_snapshot: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Return only pre-entry, explicitly captured Phase-7 regime dimensions.

    Similar-looking values are deliberately not substituted: directional AI
    score gap is not exchange spread, candle volume is not book depth, and ATR
    is not realized volatility or volatility-of-volatility.
    """
    source_features = (
        feature_snapshot.get("source_features")
        if isinstance(feature_snapshot.get("source_features"), Mapping) else {}
    )
    market_context = (
        feature_snapshot.get("market_context")
        if isinstance(feature_snapshot.get("market_context"), Mapping) else {}
    )
    if not market_context and isinstance(source_features.get("market_context"), Mapping):
        market_context = source_features["market_context"]
    cycle = (
        feature_snapshot.get("cycle_3m_universe")
        if isinstance(feature_snapshot.get("cycle_3m_universe"), Mapping) else {}
    )
    trend = (
        market_context.get("trend_strength")
        if isinstance(market_context.get("trend_strength"), Mapping) else {}
    )
    structure = (
        market_context.get("market_structure")
        if isinstance(market_context.get("market_structure"), Mapping) else {}
    )
    book = (
        feature_snapshot.get("order_book")
        if isinstance(feature_snapshot.get("order_book"), Mapping) else {}
    )
    pre_entry = _validated_pre_entry_features(opportunity)
    features = {
        # Keep bucket observations under their canonical names.  In particular,
        # a spread bucket is not a measured spread in bps and a depth bucket is
        # not top-of-book bid/ask quantity.
        name: pre_entry.get(name, _observed_feature(None, ""))
        for name in _NORMALIZED_PRE_ENTRY_FEATURES
    }
    features.update({
        "realized_volatility": _first_feature(
            (feature_snapshot.get("realized_volatility"), "feature_snapshot.realized_volatility"),
            (source_features.get("realized_volatility"), "feature_snapshot.source_features.realized_volatility"),
        ),
        "volatility_of_volatility": _first_feature(
            (feature_snapshot.get("volatility_of_volatility"), "feature_snapshot.volatility_of_volatility"),
            (feature_snapshot.get("vol_of_vol"), "feature_snapshot.vol_of_vol"),
            (source_features.get("volatility_of_volatility"), "feature_snapshot.source_features.volatility_of_volatility"),
        ),
        "market_spread_bps": _first_feature(
            (feature_snapshot.get("market_spread_bps"), "feature_snapshot.market_spread_bps"),
            (feature_snapshot.get("spread_bps"), "feature_snapshot.spread_bps"),
            (book.get("spread_bps"), "feature_snapshot.order_book.spread_bps"),
        ),
        "bid_depth_qty": _first_feature(
            (feature_snapshot.get("bid_depth_qty"), "feature_snapshot.bid_depth_qty"),
            (book.get("bid_depth_qty"), "feature_snapshot.order_book.bid_depth_qty"),
            (book.get("bid_qty"), "feature_snapshot.order_book.bid_qty"),
        ),
        "ask_depth_qty": _first_feature(
            (feature_snapshot.get("ask_depth_qty"), "feature_snapshot.ask_depth_qty"),
            (book.get("ask_depth_qty"), "feature_snapshot.order_book.ask_depth_qty"),
            (book.get("ask_qty"), "feature_snapshot.order_book.ask_qty"),
        ),
        "liquidity": _first_feature(
            (feature_snapshot.get("liquidity"), "feature_snapshot.liquidity"),
            (feature_snapshot.get("liquidity_bucket"), "feature_snapshot.liquidity_bucket"),
            (source_features.get("liquidity"), "feature_snapshot.source_features.liquidity"),
        ),
        "adx": _first_feature(
            (feature_snapshot.get("adx"), "feature_snapshot.adx"),
            (cycle.get("adx14"), "feature_snapshot.cycle_3m_universe.adx14"),
            (trend.get("adx"), "feature_snapshot.market_context.trend_strength.adx"),
        ),
        "trend_strength": _first_feature(
            (trend.get("trend_score"), "feature_snapshot.market_context.trend_strength.trend_score"),
            (feature_snapshot.get("trend_strength"), "feature_snapshot.trend_strength"),
        ),
        "market_structure": _first_feature(
            (structure.get("structure_score"), "feature_snapshot.market_context.market_structure.structure_score"),
            (feature_snapshot.get("market_structure"), "feature_snapshot.market_structure"),
        ),
        "regime": pre_entry.get("regime") or _first_feature(
            (cycle.get("regime"), "feature_snapshot.cycle_3m_universe.regime"),
            (market_context.get("regime_label"), "feature_snapshot.market_context.regime_label"),
            (source_features.get("regime"), "feature_snapshot.source_features.regime"),
            (feature_snapshot.get("regime"), "feature_snapshot.regime"),
        ),
        "session": _first_feature(
            (cycle.get("session_utc"), "feature_snapshot.cycle_3m_universe.session_utc"),
            (source_features.get("session_bucket"), "feature_snapshot.source_features.session_bucket"),
            (feature_snapshot.get("session_bucket"), "feature_snapshot.session_bucket"),
            (feature_snapshot.get("session_utc"), "feature_snapshot.session_utc"),
        ),
        "signal_timestamp": _first_feature(
            (opportunity.get("signal_ts"), "opportunity.signal_ts"),
            (opportunity.get("shared_ai_call_ts_epoch"), "opportunity.shared_ai_call_ts_epoch"),
            (opportunity.get("timestamp"), "opportunity.timestamp"),
        ),
    })
    return features


def build_phase7_support_qualification(
    results: list[dict[str, Any]], config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fail-closed, lane-deduplicated cell-support receipt."""
    gate = dict(PHASE7_SUPPORT_GATE_V1)
    if config is not None:
        gate.update(dict(config))
    minimum = int(gate["minimum_independent_cohorts"])
    minimum_effective = int(gate.get("minimum_effective_cohorts", minimum))
    minimum_cell = int(gate["minimum_cohorts_per_regime_direction"])
    if minimum < 1 or minimum_effective < 1 or minimum_cell < 1:
        raise ValueError("PHASE7_SUPPORT_THRESHOLDS_MUST_BE_POSITIVE")
    required_dimensions = tuple(str(v) for v in gate["required_dimensions"])
    taxonomy, taxonomy_reasons = _validate_runtime_taxonomy(gate)
    projection_config = gate.get("regime_projection") or {}
    projected_targets = {
        str(value).upper() for value in projection_config.get("target_regimes") or []
    } if isinstance(projection_config, Mapping) else set()
    eligible_cells, eligible_registry, registry_reasons = _validate_eligible_cell_registry(
        gate, taxonomy, results
    )
    cells: list[tuple[str, str]] = []
    cell_config_reasons: list[str] = []
    for cell in eligible_cells:
        if not isinstance(cell, Mapping):
            cell_config_reasons.append("ELIGIBLE_RUNTIME_CELL_INVALID")
            continue
        pair = (str(cell.get("regime") or "").upper(), str(cell.get("direction") or "").upper())
        if (not all(pair)
                or pair[0] not in set(taxonomy.get("regimes", [])).union(projected_targets)
                or pair[1] not in taxonomy.get("directions", [])):
            cell_config_reasons.append("ELIGIBLE_RUNTIME_CELL_TAXONOMY_MISMATCH")
        elif pair not in cells:
            cells.append(pair)
    if not cells:
        cell_config_reasons.append("ELIGIBLE_RUNTIME_CELLS_MISSING")

    grouped: dict[str, list[dict[str, Any]]] = {}
    missing_identity_rows = 0
    for row in results:
        decision_id, identity_reason = _canonical_decision_identity(row)
        if identity_reason:
            missing_identity_rows += 1
            continue
        grouped.setdefault(str(decision_id), []).append(row)

    observed_counts = {name: 0 for name in required_dimensions}
    unknown_counts = {name: 0 for name in required_dimensions}
    inconsistent_counts = {name: 0 for name in required_dimensions}
    cell_counts = {f"{regime}|{direction}": 0 for regime, direction in cells}
    cell_clusters: dict[str, set[str]] = {name: set() for name in cell_counts}
    fully_observed_cohorts = 0
    taxonomy_mismatch_decisions = 0
    sibling_lane_rows_deduplicated = sum(max(0, len(rows) - 1) for rows in grouped.values())
    for decision_id, rows in grouped.items():
        cohort_complete = True
        for name in required_dimensions:
            observations = []
            for row in rows:
                item = ((row.get("regime_features_at_signal") or {}).get(name) or {})
                if item.get("status") == "OBSERVED" and item.get("source"):
                    observations.append((canonical_json(item.get("value")), str(item["source"])))
            distinct = set(observations)
            if len(observations) != len(rows):
                unknown_counts[name] += 1
                cohort_complete = False
            elif len(distinct) != 1:
                inconsistent_counts[name] += 1
                cohort_complete = False
            else:
                observed_counts[name] += 1
        directions = {str(row.get("side") or "").upper() for row in rows}
        regimes = {
            str((((row.get("regime_features_at_signal") or {}).get("regime") or {}).get("value")) or "").upper()
            for row in rows
        }
        if len(directions) != 1 or len(regimes) != 1:
            cohort_complete = False
        direction = next(iter(directions), "")
        regime = next(iter(regimes), "")
        if (direction not in taxonomy.get("directions", [])
                or regime not in set(taxonomy.get("regimes", [])).union(projected_targets)):
            taxonomy_mismatch_decisions += 1
            cohort_complete = False
        if cohort_complete:
            fully_observed_cohorts += 1
            cell_name = f"{regime}|{direction}"
            if cell_name in cell_counts:
                cell_counts[cell_name] += 1
                cluster_ids = {
                    str(row.get("dependence_cluster_id") or row.get("price_cluster_id") or decision_id)
                    for row in rows
                }
                if len(cluster_ids) == 1:
                    cell_clusters[cell_name].update(cluster_ids)

    dimension_gates = {
        name: (
            observed_counts[name] == len(grouped)
            and unknown_counts[name] == 0
            and inconsistent_counts[name] == 0
            and len(grouped) > 0
        )
        for name in required_dimensions
    }
    effective_cell_counts = {name: len(cell_clusters[name]) for name in cell_counts}
    effective_n = len({cluster for clusters in cell_clusters.values() for cluster in clusters})
    cell_gates = {
        name: cell_counts[name] >= minimum_cell and effective_cell_counts[name] >= minimum_cell
        for name in cell_counts
    }
    uses_projected_regimes = any(regime in {"BULL", "BEAR"} for regime, _ in cells)
    projection, projection_reasons = (
        _validate_projection(gate, set(grouped)) if uses_projected_regimes
        else ({"status": "NOT_USED"}, [])
    )
    gates = {
        "all_rows_have_canonical_independent_decision_identity": missing_identity_rows == 0,
        "frozen_runtime_taxonomy_valid": not taxonomy_reasons,
        "eligible_cells_match_frozen_taxonomy": not cell_config_reasons,
        "eligible_cells_bound_to_exact_signed_registry": not registry_reasons,
        "observed_regimes_match_frozen_taxonomy": taxonomy_mismatch_decisions == 0,
        "projected_regime_schema_signed_and_fold_fitted": not projection_reasons,
        "minimum_independent_cohorts": fully_observed_cohorts >= minimum,
        "minimum_cluster_adjusted_effective_cohorts": effective_n >= minimum_effective,
        "all_required_dimensions_observed_and_consistent": all(dimension_gates.values()),
        "every_eligible_regime_direction_cell_supported": bool(cell_gates) and all(cell_gates.values()),
    }
    reasons = list(dict.fromkeys(
        taxonomy_reasons + registry_reasons + cell_config_reasons + projection_reasons
    ))
    if missing_identity_rows:
        reasons.append("MISSING_CANONICAL_DECISION_IDENTITY")
    if taxonomy_mismatch_decisions:
        reasons.append("OBSERVED_RUNTIME_TAXONOMY_MISMATCH")
    if fully_observed_cohorts < minimum:
        reasons.append("INSUFFICIENT_INDEPENDENT_COHORTS")
    if effective_n < minimum_effective:
        reasons.append("INSUFFICIENT_CLUSTER_ADJUSTED_EFFECTIVE_COHORTS")
    if not all(dimension_gates.values()):
        reasons.append("REQUIRED_PHASE7_FEATURES_UNKNOWN_OR_INCONSISTENT")
    if not all(cell_gates.values()):
        reasons.append("INSUFFICIENT_REGIME_DIRECTION_COHORT_SUPPORT")
    eligible = all(gates.values())
    return {
        "schema": "phase7_regime_support_qualification_v2",
        "status": "SUPPORTED_FOR_PHASE7_RESEARCH" if eligible else "NOT_SUPPORTED",
        "qualification_allowed": eligible,
        "scope": "PHASE7_RESEARCH_FEATURE_SUPPORT_ONLY",
        "profitability_qualified": False,
        "live_trading_authorized": False,
        "config": gate,
        "row_count": len(results),
        "independent_cohort_count": len(grouped),
        "raw_independent_decision_n": len(grouped),
        "cluster_adjusted_effective_n": effective_n,
        "effective_n_method": "CONSERVATIVE_UNIQUE_DEPENDENCE_CLUSTER_COUNT",
        "sibling_lane_rows_deduplicated": sibling_lane_rows_deduplicated,
        "fully_observed_independent_cohort_count": fully_observed_cohorts,
        "missing_identity_rows": missing_identity_rows,
        "dimension_evidence": {
            name: {"observed_cohorts": observed_counts[name],
                   "unknown_cohorts": unknown_counts[name],
                   "inconsistent_cohorts": inconsistent_counts[name],
                   "gate_passed": dimension_gates[name]}
            for name in required_dimensions
        },
        "frozen_runtime_taxonomy": taxonomy,
        "eligible_cell_registry": eligible_registry,
        "eligible_regime_direction_cells": [f"{regime}|{direction}" for regime, direction in cells],
        "taxonomy_mismatch_decisions": taxonomy_mismatch_decisions,
        "regime_projection": projection,
        "regime_direction_cohorts": cell_counts,
        "regime_direction_raw_independent_n": cell_counts,
        "regime_direction_cluster_adjusted_effective_n": effective_cell_counts,
        "regime_direction_gates": cell_gates,
        "gates": gates,
        "reason_codes": reasons,
    }


def _load_envelope(root: Path, segment: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    ref = segment.get("segment_ref") if isinstance(segment.get("segment_ref"), Mapping) else {}
    digest = str(ref.get("sha256") or "").lower()
    relative = str(ref.get("relative_path") or "")
    try:
        target = (root.parent / relative).resolve()
        target.relative_to(root.parent.resolve())
    except (ValueError, OSError):
        return None, "UNKNOWN_TAPE_PATH_OUTSIDE_V3"
    canonical = (root / "market_segments" / digest[:2] / f"{digest}.json").resolve()
    if target != canonical or len(digest) != 64:
        return None, "UNKNOWN_TAPE_PATH_NOT_CANONICAL"
    try:
        payload = target.read_bytes()
    except OSError:
        return None, "UNKNOWN_TAPE_OBJECT_MISSING"
    if hashlib.sha256(payload).hexdigest() != digest:
        return None, "UNKNOWN_TAPE_CHECKSUM_MISMATCH"
    try:
        envelope = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "UNKNOWN_TAPE_ENVELOPE_INVALID"
    if not isinstance(envelope, dict) or envelope.get("schema") != "market_segment_v3":
        return None, "UNKNOWN_TAPE_ENVELOPE_SCHEMA_INVALID"
    return envelope, None


def _schedule(intent: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    schedule = intent.get("chase_schedule")
    if not isinstance(schedule, Mapping) or schedule.get("authoritative") is not True:
        return None
    raw = schedule.get("intervals")
    if not isinstance(raw, list) or not raw:
        return None
    result = []
    for index, interval in enumerate(raw):
        if not isinstance(interval, Mapping):
            return None
        start = _number(interval.get("start_ts"), interval.get("start"))
        end = _number(interval.get("end_ts"), interval.get("end"))
        limit = _number(interval.get("limit_price"), interval.get("price"))
        if start is None or end is None or limit is None:
            return None
        result.append({
            "bucket_id": str(interval.get("bucket_id") or interval.get("stage_id") or f"stage-{index}"),
            "start_ts": start, "end_ts": end, "limit_price": limit,
            "generation": schedule.get("generation_id") or intent.get("schedule_id"),
        })
    return result


def _unknown(binding: Mapping[str, Any], decision: Mapping[str, Any],
             opportunity: Mapping[str, Any], reasons: list[str]) -> dict[str, Any]:
    tape_ids = sorted(binding.get("tape_ids") or [])
    identity_values = [
        binding.get("epoch_id"), binding.get("opportunity_id"),
        binding.get("episode_id"),
    ]
    cohort = (
        stable_hash("cohort", {
            "epoch_id": identity_values[0], "opportunity_id": identity_values[1],
            "episode_id": identity_values[2], "tape_ids": tape_ids,
        })
        if all(str(value or "") for value in identity_values)
        else None
    )
    policy_spec = (
        decision.get("paper_policy_spec")
        if isinstance(decision.get("paper_policy_spec"), Mapping) else {}
    )
    exit_config = (
        policy_spec.get("exit_config")
        if isinstance(policy_spec.get("exit_config"), Mapping) else {}
    )
    feature_snapshot = (
        opportunity.get("feature_snapshot_at_signal")
        if isinstance(opportunity.get("feature_snapshot_at_signal"), Mapping) else {}
    )
    market_context = (
        feature_snapshot.get("market_context")
        if isinstance(feature_snapshot.get("market_context"), Mapping) else {}
    )
    entry_offset_pct = decision.get("entry_offset_pct")
    if entry_offset_pct is None and policy_spec.get("entry_offset_fraction") is not None:
        fraction = _number(policy_spec.get("entry_offset_fraction"))
        entry_offset_pct = fraction * 100.0 if fraction is not None else None
    ai_direction = str(opportunity.get("raw_direction") or "").upper() or None
    side_candidate = str(
        decision.get("direction") or decision.get("side")
        or decision.get("executed_direction") or ai_direction or ""
    ).upper() or None
    side = side_candidate if side_candidate in {"LONG", "SHORT"} else None
    regime_features = _regime_features_at_signal(opportunity, feature_snapshot)
    regime_feature_coverage = {
        "status": (
            "COMPLETE" if all(item["status"] == "OBSERVED" for item in regime_features.values())
            else "PARTIAL" if any(item["status"] == "OBSERVED" for item in regime_features.values())
            else "UNKNOWN"
        ),
        "observed_dimensions": sorted(
            key for key, item in regime_features.items() if item["status"] == "OBSERVED"
        ),
        "unknown_dimensions": sorted(
            key for key, item in regime_features.items() if item["status"] == "UNKNOWN"
        ),
    }
    return {
        "schema": SCHEMA,
        "epoch_id": binding.get("epoch_id"),
        "opportunity_id": binding.get("opportunity_id"),
        "episode_id": binding.get("episode_id"),
        "decision_id": binding.get("event_id"),
        "policy_id": binding.get("policy_id"),
        "policy_signature": binding.get("policy_signature"),
        "evidence_world": EVIDENCE_WORLD,
        "comparison_cohort_key": cohort,
        "lane": binding.get("research_lane"),
        "family": (
            decision.get("policy_family") or decision.get("family")
            or exit_config.get("family")
        ),
        "entry_offset_pct": entry_offset_pct,
        "chase_policy": (
            decision.get("chase_policy") or policy_spec.get("entry_limit_policy")
        ),
        "exit_family": (
            decision.get("exit_family") or exit_config.get("exit_profile_id")
            or exit_config.get("family")
        ),
        "regime": (
            decision.get("regime") or decision.get("market_regime")
            or market_context.get("regime_label")
        ),
        "side": side,
        "split": str(decision.get("split") or "").upper() or None,
        "ai_direction": ai_direction,
        "ai_decision": str(decision.get("raw_ai_decision") or "").upper() or None,
        "long_score": decision.get("long_score"),
        "short_score": decision.get("short_score"),
        "score_gap": decision.get("score_gap"),
        "classification": "UNKNOWN", "supported": False,
        "unknown_reason_codes": sorted(set(reasons)),
        "requested_qty": None, "available_qty": None, "raw_partial_qty": None,
        "rounded_executable_qty": None, "accumulated_qty": None, "filled_qty": None,
        "minimum_lot_decision": "UNKNOWN", "minimum_notional_decision": "UNKNOWN",
        "quantity_attempts": [], "gross_pnl_usd": None, "fees_usd": None,
        "slippage_usd": None, "fill_latency_sec": None,
        "price_concession_per_unit": None,
        "missed_entry_cost_usd": None,
        "missed_entry_cost_basis": "UNAVAILABLE_REQUIRES_DECLARED_MARK_HORIZON",
        "volatility_at_signal": {
            # Preserve the collector's native normalized fields individually.
            # Collapsing these to a single generic value made ATR/percentile
            # stratification impossible even when the opportunity retained
            # both measurements.
            "volatility_atr": feature_snapshot.get("volatility_atr"),
            "volatility_percentile": feature_snapshot.get("volatility_percentile"),
            "atr14": feature_snapshot.get("atr14"),
            "atr14_pct_3m": feature_snapshot.get("atr14_pct_3m"),
            "realized_volatility": feature_snapshot.get("realized_volatility"),
            "volatility_metric": (
                feature_snapshot.get("volatility_metric")
                or market_context.get("volatility_metric")
            ),
        },
        "regime_feature_schema": "phase7_regime_features_v1",
        "regime_features_at_signal": regime_features,
        "regime_feature_coverage": regime_feature_coverage,
        "net_pnl_usd": None,
        "terminal_outcome_status": "UNKNOWN",
        "terminal_outcome_reason_codes": ["UNKNOWN_TERMINAL_EXECUTION_NOT_EXACTLY_BOUND"],
        "profitability_supported": False,
        "pre_entry_path_status": (
            "COMPLETE" if binding.get("required_pre_entry_path_complete") is True else "UNKNOWN"
        ),
    }


def _bind_terminal_outcome(
    executions: list[Mapping[str, Any]], lifecycles: list[Mapping[str, Any]],
    *, classification: str, entry_slippage_usd: Any, evaluated_filled_qty: Any,
) -> dict[str, Any]:
    """Bind one observed terminal paper outcome without estimating missing costs."""
    if classification == "NO_FILL":
        return {"terminal_outcome_status": "NOT_APPLICABLE_NO_FILL",
                "terminal_outcome_reason_codes": [], "profitability_supported": True,
                "gross_pnl_usd": 0.0, "fees_usd": 0.0, "funding_usd": 0.0,
                "slippage_usd": 0.0, "net_pnl_usd": 0.0}
    terminal = [row for row in executions if row.get("close_ts") is not None]
    terminal_lifecycle = [row for row in lifecycles if row.get("terminal") is True]
    reasons: list[str] = []
    if len(terminal) != 1:
        reasons.append("UNKNOWN_TERMINAL_EXECUTION_NOT_UNIQUE")
    if len(terminal_lifecycle) != 1:
        reasons.append("UNKNOWN_TERMINAL_LIFECYCLE_NOT_UNIQUE")
    if reasons:
        return {"terminal_outcome_status": "UNKNOWN", "terminal_outcome_reason_codes": reasons,
                "profitability_supported": False, "net_pnl_usd": None}
    execution = terminal[0]
    gross = _number(execution.get("gross_pnl_usd"))
    trading = _number(execution.get("trading_fees_usd"))
    funding = _number(execution.get("funding_fees_usd"))
    exit_slippage = _number(execution.get("exit_slippage_usd"))
    entry_slippage = _number(entry_slippage_usd)
    observed_net = _number(execution.get("net_pnl_usd"))
    observed_qty = _number(execution.get("filled_qty"))
    evaluated_qty = _number(evaluated_filled_qty)
    for value, code in ((gross, "UNKNOWN_GROSS_PNL_MISSING"),
                        (trading, "UNKNOWN_TRADING_FEES_MISSING"),
                        (funding, "UNKNOWN_FUNDING_FEES_MISSING"),
                        (entry_slippage, "UNKNOWN_ENTRY_SLIPPAGE_MISSING"),
                        (exit_slippage, "UNKNOWN_EXIT_SLIPPAGE_MISSING"),
                        (observed_net, "UNKNOWN_NET_PNL_MISSING"),
                        (observed_qty, "UNKNOWN_TERMINAL_FILLED_QUANTITY_MISSING")):
        if value is None:
            reasons.append(code)
    if reasons:
        return {"terminal_outcome_status": "UNKNOWN", "terminal_outcome_reason_codes": reasons,
                "profitability_supported": False, "gross_pnl_usd": gross,
                "fees_usd": trading, "funding_usd": funding,
                "slippage_usd": None, "net_pnl_usd": None}
    if evaluated_qty is None or abs(float(observed_qty) - float(evaluated_qty)) > 1e-9:
        return {"terminal_outcome_status": "UNKNOWN",
                "terminal_outcome_reason_codes": ["UNKNOWN_TERMINAL_QUANTITY_MISMATCH"],
                "profitability_supported": False, "gross_pnl_usd": gross,
                "fees_usd": trading, "funding_usd": funding,
                "slippage_usd": None, "net_pnl_usd": None}
    total_slippage = float(entry_slippage) + float(exit_slippage)
    reconciled_net = float(gross) - float(trading) - float(funding) - total_slippage
    if abs(float(observed_net) - reconciled_net) > 1e-8:
        return {"terminal_outcome_status": "UNKNOWN",
                "terminal_outcome_reason_codes": ["UNKNOWN_TERMINAL_COST_RECONCILIATION_MISMATCH"],
                "profitability_supported": False, "gross_pnl_usd": gross,
                "fees_usd": trading, "funding_usd": funding,
                "slippage_usd": total_slippage, "net_pnl_usd": None}
    return {"terminal_outcome_status": "REALIZED_COST_COMPLETE",
            "terminal_outcome_reason_codes": [], "profitability_supported": True,
            "gross_pnl_usd": gross, "fees_usd": trading, "funding_usd": funding,
            "slippage_usd": total_slippage, "net_pnl_usd": observed_net,
            "exit_price": execution.get("exit_price"), "close_ts": execution.get("close_ts"),
            "exit_reason": execution.get("exit_reason")}


def build_v3_conservative_results(
    v3_root: str | Path, *, phase7_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate exactly-bound decisions and retain every UNKNOWN explicitly."""
    root = Path(v3_root).resolve()
    if root.name != "v3":
        raise ValueError("V3_EVALUATOR_ROOT_MUST_BE_V3")
    ledgers = {
        name: _read_jsonl(root / "ledgers" / f"{name}.jsonl")
        for name in ("decision", "opportunity", "order_intent", "market_segment", "execution", "lifecycle")
    }
    recovery_segments = _read_jsonl(
        root / "recovery_ledgers" / "market_segment.jsonl"
    )
    ledgers["market_segment"].extend(recovery_segments)
    bindings = build_v3_binding_index(root)["bindings"]
    decisions = {str(row.get("event_id") or ""): row for row in ledgers["decision"]}
    opportunities = {_identity(row): row for row in ledgers["opportunity"]}
    intents: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    executions: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    lifecycles: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    segments: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    envelope_cache: dict[str, tuple[dict[str, Any] | None, str | None]] = {}

    def load_envelope_cached(segment: Mapping[str, Any]):
        ref = segment.get("segment_ref") if isinstance(segment.get("segment_ref"), Mapping) else {}
        key = canonical_json(ref)
        if key not in envelope_cache:
            envelope_cache[key] = _load_envelope(root, segment)
        return envelope_cache[key]

    def verify_segment_cached(segment: Mapping[str, Any]):
        _envelope, error = load_envelope_cached(segment)
        if error:
            return None, [error]
        ref = segment.get("segment_ref") if isinstance(segment.get("segment_ref"), Mapping) else {}
        return str(ref.get("sha256") or "") or None, []
    for row in ledgers["order_intent"]:
        intents.setdefault(_policy_identity(row), []).append(row)
    for row in ledgers["execution"]:
        executions.setdefault(_policy_identity(row), []).append(row)
    for row in ledgers["lifecycle"]:
        lifecycles.setdefault(_policy_identity(row), []).append(row)
    for row in ledgers["market_segment"]:
        segments.setdefault(_identity(row), []).append(row)

    lifecycle_evidence_index = build_lifecycle_evidence_index(root)
    results = []
    for binding in bindings:
        decision = decisions.get(str(binding.get("event_id") or ""), {})
        identity = _identity(binding)
        reasons = list(binding.get("unknown_reason_codes") or [])
        lifecycle_evidence = join_lifecycle_evidence(lifecycle_evidence_index, binding)
        if lifecycle_evidence["status"] != "VERIFIED":
            reasons.extend(lifecycle_evidence["reason_codes"])
        matching_intents = authoritative_schedule_intents(
            intents.get((*identity, str(binding.get("policy_signature") or "")), [])
        )
        schedule_fingerprints = {
            (str(row.get("schedule_id") or ""), str(row.get("schedule_sha256") or ""))
            for row in matching_intents
        }
        if len(schedule_fingerprints) != 1:
            reasons.append("UNKNOWN_AUTHORITATIVE_INTENT_NOT_UNIQUE")
        intent = matching_intents[-1] if len(schedule_fingerprints) == 1 else {}
        schedule = _schedule(intent)
        if schedule is None:
            reasons.append("UNKNOWN_AUTHORITATIVE_SCHEDULE_UNUSABLE")
        direction = str(
            decision.get("direction") or decision.get("side")
            or opportunities.get(identity, {}).get("raw_direction") or ""
        ).upper()
        requested_qty = _number(intent.get("requested_qty"), intent.get("quantity"))
        constraints = intent.get("signed_quantity_constraints")
        if direction not in {"LONG", "SHORT"}:
            reasons.append("UNKNOWN_DIRECTION_MISSING_OR_INVALID")
        if requested_qty is None or requested_qty <= 0:
            reasons.append("UNKNOWN_REQUESTED_QUANTITY_MISSING")

        tape_rows: list[dict[str, Any]] = []
        tape_ids: list[str] = []
        selected_segments, _future_history = authoritative_future_path_segments(
            root, segments.get(identity, []), verifier=verify_segment_cached,
        )
        for segment in selected_segments:
            coverage = segment.get("coverage") if isinstance(segment.get("coverage"), Mapping) else {}
            role = segment_role(segment)
            if role not in {
                "ENTRY_PATH", "ENTRY_AND_EXIT_PATH", "FULL_LIFECYCLE",
                ALL_OPPORTUNITY_FUTURE_ROLE,
            }:
                continue
            if role == ALL_OPPORTUNITY_FUTURE_ROLE:
                if not complete_conservative_future_path(segment):
                    reasons.append("UNKNOWN_FUTURE_ENTRY_PATH_INCOMPLETE")
                    continue
            elif coverage.get("conservative_bbo_depth_eligible") is not True:
                reasons.append("UNKNOWN_ENTRY_SEGMENT_NOT_CONSERVATIVE_ELIGIBLE")
                continue
            envelope, error = load_envelope_cached(segment)
            if error:
                reasons.append(error)
                continue
            rows = envelope.get("rows") if isinstance(envelope.get("rows"), list) else []
            if not rows:
                reasons.append("UNKNOWN_ENTRY_SEGMENT_ROWS_MISSING")
                continue
            tape_rows.extend(row for row in rows if isinstance(row, dict))
            tape_ids.append(str(segment["segment_ref"]["sha256"]))

        if reasons:
            unknown = _unknown(binding, decision, opportunities.get(identity, {}), reasons)
            unknown["lifecycle_evidence"] = lifecycle_evidence
            unknown["qualification_evidence_collected"] = False
            results.append(unknown)
            continue
        receipt = evaluate_limit_fill(
            tape_rows, direction=direction, requested_qty=requested_qty,
            chase_schedule=schedule, aggressor_window_sec=1,
            symbol=str(intent.get("symbol") or "BTCUSD"), quantity_constraints=constraints,
        )
        if receipt.get("supported") is not True:
            unknown = _unknown(binding, decision, opportunities.get(identity, {}), [
                "UNKNOWN_CONSERVATIVE_EVALUATOR_" + str(reason)
                for reason in receipt.get("negative_reasons") or ["UNSPECIFIED"]
            ])
            unknown["lifecycle_evidence"] = lifecycle_evidence
            unknown["qualification_evidence_collected"] = True
            results.append(unknown)
            continue
        classification = str(receipt.get("final_classification") or "UNKNOWN")
        if classification not in {"FULL_FILL", "PARTIAL_FILL", "NO_FILL"}:
            unknown = _unknown(binding, decision, opportunities.get(identity, {}), ["UNKNOWN_EVALUATOR_CLASSIFICATION"])
            unknown["lifecycle_evidence"] = lifecycle_evidence
            unknown["qualification_evidence_collected"] = True
            results.append(unknown)
            continue
        cohort = stable_hash("cohort", {
            "epoch_id": identity[0], "opportunity_id": identity[1],
            "episode_id": identity[2], "tape_ids": sorted(tape_ids),
        })
        row = _unknown(binding, decision, opportunities.get(identity, {}), [])
        row.update({
            "comparison_cohort_key": cohort, "classification": classification,
            "supported": True, "unknown_reason_codes": [],
            "requested_qty": receipt.get("requested_qty"),
            "available_qty": receipt.get("visible_executable_qty"),
            "raw_partial_qty": receipt.get("raw_partial_qty"),
            "rounded_executable_qty": receipt.get("rounded_executable_qty"),
            "accumulated_qty": receipt.get("accumulated_qty"),
            "filled_qty": receipt.get("filled_qty"),
            "minimum_lot_decision": receipt.get("minimum_lot_decision"),
            "minimum_notional_decision": receipt.get("minimum_notional_decision"),
            "quantity_attempts": receipt.get("quantity_attempts") or [],
            # Preserve the content identity of the authoritative persisted
            # schedule envelope. The fill evaluator also hashes its normalized
            # interval list; expose that separately rather than replacing the
            # signed collector identity with a derived representation.
            "schedule_sha256": binding.get("schedule_sha256"),
            "evaluated_schedule_sha256": receipt.get("schedule_sha256"),
            "tape_ids": sorted(tape_ids),
            "fill_price": receipt.get("fill_price"), "evaluator_receipt": receipt,
            "fill_latency_sec": receipt.get("fill_latency_sec"),
            "price_concession_per_unit": receipt.get("price_concession_per_unit"),
            "slippage_usd": receipt.get("slippage_usd"),
            "missed_entry_cost_usd": receipt.get("missed_entry_cost_usd"),
            "missed_entry_cost_basis": receipt.get("missed_entry_cost_basis"),
            "lifecycle_evidence": lifecycle_evidence,
            "qualification_evidence_collected": True,
        })
        policy_key = (*identity, str(binding.get("policy_signature") or ""))
        if binding.get("required_pre_entry_path_complete") is not True:
            row.update({
                "terminal_outcome_status": "UNKNOWN",
                "terminal_outcome_reason_codes": [
                    "UNKNOWN_REQUIRED_PRE_ENTRY_BBO_DEPTH_TRADE_PATH_INCOMPLETE"
                ],
                "profitability_supported": False, "net_pnl_usd": None,
            })
        else:
            row.update(_bind_terminal_outcome(
                executions.get(policy_key, []), lifecycles.get(policy_key, []),
                classification=classification, entry_slippage_usd=receipt.get("slippage_usd"),
                evaluated_filled_qty=receipt.get("filled_qty"),
            ))
        results.append(row)
    results.sort(key=lambda row: tuple(str(row.get(field) or "") for field in (
        "opportunity_id", "episode_id", "decision_id", "policy_signature"
    )))
    counts = {name: sum(row["classification"] == name for row in results)
              for name in ("FULL_FILL", "PARTIAL_FILL", "NO_FILL", "UNKNOWN")}
    terminal_counts = {
        name: sum(row.get("terminal_outcome_status") == name for row in results)
        for name in ("REALIZED_COST_COMPLETE", "NOT_APPLICABLE_NO_FILL", "UNKNOWN")
    }
    feature_names = tuple(_regime_features_at_signal({}, {}).keys())
    observed_by_dimension = {
        name: sum(
            ((row.get("regime_features_at_signal") or {}).get(name) or {}).get("status")
            == "OBSERVED"
            for row in results
        )
        for name in feature_names
    }
    unknown_by_dimension = {
        name: len(results) - observed_by_dimension[name] for name in feature_names
    }
    phase7_support = build_phase7_support_qualification(results, phase7_config)
    lifecycle_status_counts = {
        status: sum((row.get("lifecycle_evidence") or {}).get("status") == status for row in results)
        for status in ("VERIFIED", "UNKNOWN")
    }
    lifecycle_reason_counts: Counter[str] = Counter(
        reason for row in results
        for reason in ((row.get("lifecycle_evidence") or {}).get("reason_codes") or [])
    )
    lifecycle_evidence_coverage = {
        "schema": "lifecycle_evidence_analyzer_coverage_v1",
        "episodes_total": len(results),
        "verified_episode_count": lifecycle_status_counts["VERIFIED"],
        "unknown_episode_count": lifecycle_status_counts["UNKNOWN"],
        "coverage_complete": bool(results) and lifecycle_status_counts["VERIFIED"] == len(results),
        "unknown_reason_counts": dict(sorted(lifecycle_reason_counts.items())),
        "bundle_index": {
            key: lifecycle_evidence_index[key]
            for key in (
                "schema", "manifest_count", "valid_unique_count", "invalid_count",
                "duplicate_identity_count", "defect_counts",
            )
        },
    }
    regime_feature_coverage = {
        "schema": "phase7_regime_feature_coverage_v1",
        "row_count": len(results),
        "dimensions": [
            {
                "name": name,
                "observed_rows": observed_by_dimension[name],
                "unknown_rows": unknown_by_dimension[name],
                "status": (
                    "OBSERVED" if results and unknown_by_dimension[name] == 0
                    else "PARTIAL" if observed_by_dimension[name] > 0
                    else "UNKNOWN"
                ),
            }
            for name in feature_names
        ],
        # This flag is only permission to publish supported Phase-7 segmented
        # research. It is not policy/profit/live qualification.
        "qualification_allowed": phase7_support["qualification_allowed"],
        "profitability_calculated": False,
    }
    return {"schema": SCHEMA, "row_count": len(results), "classification_counts": counts,
            "terminal_outcome_counts": terminal_counts,
            "results_sha256": hashlib.sha256(canonical_json(results).encode()).hexdigest(),
            "regime_feature_coverage": regime_feature_coverage,
            "phase7_support_qualification": phase7_support,
            "lifecycle_evidence_coverage": lifecycle_evidence_coverage,
            "results": results}


def persist_v3_conservative_results(canonical_root: str | Path, *, analyzer_revision: str) -> dict[str, Any]:
    root = Path(canonical_root).resolve()
    if root.name != "canonical-research-data":
        raise ValueError("POLICY_EVALUATOR_ROOT_NOT_CANONICAL")
    manifest = json.loads((root / "canonical_dataset_current.json").read_text(encoding="utf-8-sig"))
    generation = generation_identity(manifest, analyzer_revision=analyzer_revision)
    phase7_config = {
        "eligible_cell_registry": build_signed_eligible_cell_registry(generation),
    }
    report = build_v3_conservative_results(root / "v3", phase7_config=phase7_config)
    # Populate the disposable generation cache from these evaluator-produced
    # rows. UNKNOWN rows are retained so coverage failures are queryable and
    # cannot silently disappear from fill-rate denominators.
    from research.policy_evidence_library import PolicyEvidenceLibrary
    library = PolicyEvidenceLibrary(str(root), manifest, analyzer_revision=analyzer_revision)
    cache_rows = []
    cache_skip_reason_counts: dict[str, int] = {}
    for row in report["results"]:
        missing = [
            field for field in (
                "epoch_id", "opportunity_id", "episode_id", "decision_id", "policy_signature",
                "comparison_cohort_key",
            )
            if not str(row.get(field) or "")
        ]
        if missing:
            # The exhaustive artifact retains the original UNKNOWN row. The
            # relational query cache cannot safely key a row whose causal
            # identity is absent; skipping it is explicit and never fabricates
            # an opportunity ID from an episode or event ID.
            for field in missing:
                key = f"RESULT_IDENTITY_MISSING_{field.upper()}"
                cache_skip_reason_counts[key] = cache_skip_reason_counts.get(key, 0) + 1
            continue
        cache_rows.append(row)
    ingested = library.ingest(cache_rows)
    directory = root / "derived" / "policy-evidence" / generation["generation_key"]
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "conservative-results.jsonl.gz"
    fd, name = tempfile.mkstemp(prefix=".conservative-results.", suffix=".tmp", dir=directory)
    os.close(fd)
    temporary = Path(name)
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="conservative-results.jsonl", mode="wb", fileobj=raw, mtime=0) as zipped:
                for row in report["results"]:
                    zipped.write((canonical_json(row) + "\n").encode())
            raw.flush(); os.fsync(raw.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {key: value for key, value in report.items() if key != "results"} | {
        "generation": generation,
        "relative_path": destination.relative_to(root).as_posix(),
        "artifact_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "artifact_size_bytes": destination.stat().st_size,
        "cache_rows_ingested": ingested,
        "cache_rows_skipped_missing_identity": len(report["results"]) - len(cache_rows),
        "cache_skip_reason_counts": dict(sorted(cache_skip_reason_counts.items())),
    }
