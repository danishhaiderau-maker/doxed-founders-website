"""Truthful agreement audit across execution-evidence worlds.

This module deliberately refuses fuzzy joins.  A row participates in a
cross-world comparison only when every causal identity is explicitly present.
Timestamp proximity, trade labels, prices, directions, and row order are not
identity and are never used as substitutes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from math import isfinite
from numbers import Real
from typing import Any, Iterable, Mapping


SCHEMA = "cross_world_evidence_report_v1"
WORLD_ORDER = (
    "IDEAL_TOUCH_DIAGNOSTIC",
    "CONSERVATIVE_BBO_DEPTH",
    "SHADOW_COUNTERFACTUAL",
    "OBSERVED_PAPER",
    "BITFINEX_COPY",
)
REQUIRED_CAUSAL_IDENTITIES = (
    "epoch_id",
    "opportunity_id",
    "policy_signature",
    "schedule_id",
    "tape_id",
    "fill_id",
)
COMPARISON_FIELDS = ("entry_observed", "direction", "pnl_sign")


def _clean(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    # pandas represents empty CSV cells as NaN.  Stringifying that value would
    # manufacture the shared identity "nan" and collapse unrelated legacy rows
    # into a bogus duplicate key.
    if isinstance(value, Real) and not isinstance(value, bool) and not isfinite(float(value)):
        return None
    text = str(value).strip()
    if text.casefold() in {"nan", "none", "null", "nat", "<na>"}:
        return None
    return text or None


def explicit_causal_identity(row: Mapping[str, Any]) -> tuple[dict[str, str | None], list[str]]:
    """Return only explicitly named identities; never derive or hash one."""
    nested = row.get("causal_identity")
    nested = nested if isinstance(nested, Mapping) else {}
    identity = {
        name: _clean(row.get(name)) or _clean(nested.get(name))
        for name in REQUIRED_CAUSAL_IDENTITIES
    }
    missing = [name for name, value in identity.items() if value is None]
    return identity, missing


def _normalise_fill_state(row: Mapping[str, Any]) -> str | None:
    raw = str(
        row.get("fill_state")
        or row.get("outcome")
        or row.get("entry_outcome")
        or row.get("status")
        or ""
    ).strip().upper()
    if raw in {"FILL", "FILLED", "FULL_FILL", "EXECUTED", "CLOSED"}:
        return "FILL"
    if raw in {"PARTIAL", "PARTIAL_FILL", "PARTIALLY_FILLED"}:
        return "PARTIAL_FILL"
    if raw in {"NO_FILL", "UNFILLED", "EXPIRED", "TTL_EXPIRED"}:
        return "NO_FILL"
    if raw in {"UNSUPPORTED", "UNKNOWN", "NOT_COMPUTABLE"}:
        return None
    if row.get("filled") is True or row.get("executed") is True:
        return "FILL"
    if row.get("filled") is False or row.get("executed") is False:
        return "NO_FILL"
    return None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _pnl_sign(row: Mapping[str, Any]) -> str | None:
    value = None
    for name in ("net_pnl_usd", "outcome_net_pnl_usd", "actual_bitfinex_realized_pnl_usd"):
        value = _number(row.get(name))
        if value is not None:
            break
    if value is None:
        return None
    if value > 0:
        return "PROFIT"
    if value < 0:
        return "LOSS"
    return "FLAT"


def _entry_observed(row: Mapping[str, Any], fill_state: str | None) -> bool | None:
    if row.get("touched_limit") is True or row.get("ideal_touch") is True:
        return True
    if row.get("touched_limit") is False or row.get("ideal_touch") is False:
        return False
    if fill_state in {"FILL", "PARTIAL_FILL"}:
        return True
    if fill_state == "NO_FILL":
        return False
    return None


def _comparison(row: Mapping[str, Any]) -> dict[str, Any]:
    direction = _clean(row.get("executed_direction") or row.get("final_direction") or row.get("direction"))
    fill_state = _normalise_fill_state(row)
    return {
        # A diagnostic touch is never relabelled as a fill.  The boolean only
        # answers the common cross-world question: did this world observe an
        # entry-enabling event?
        "entry_observed": _entry_observed(row, fill_state),
        "evidence_state": fill_state,
        "direction": direction.upper() if direction else None,
        "pnl_sign": _pnl_sign(row),
    }


def _key(identity: Mapping[str, str | None]) -> tuple[str, ...]:
    return tuple(str(identity[name]) for name in REQUIRED_CAUSAL_IDENTITIES)


def _compare(left: Mapping[str, Any], right: Mapping[str, Any]) -> tuple[str, list[str]]:
    left_values = _comparison(left)
    right_values = _comparison(right)
    comparable = [
        name for name in COMPARISON_FIELDS
        if left_values[name] is not None and right_values[name] is not None
    ]
    if not comparable:
        return "NOT_COMPUTABLE", []
    disagreements = [name for name in comparable if left_values[name] != right_values[name]]
    return ("DISAGREE" if disagreements else "AGREE"), disagreements


def build_cross_world_evidence_report(
    world_rows: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    generated_at: str,
    source_revision: str = "UNKNOWN",
    epoch_id: str | None = None,
    max_samples: int = 100,
) -> dict[str, Any]:
    """Build a fail-closed cross-world report from explicitly identified rows."""
    indexes: dict[str, dict[tuple[str, ...], Mapping[str, Any]]] = {}
    world_summary: dict[str, dict[str, Any]] = {}
    not_computable: list[dict[str, Any]] = []

    for world in WORLD_ORDER:
        rows = [row for row in (world_rows.get(world) or []) if isinstance(row, Mapping)]
        candidates: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
        missing_counts: Counter[str] = Counter()
        complete = 0
        for row_index, row in enumerate(rows):
            identity, missing = explicit_causal_identity(row)
            if missing:
                missing_counts.update(missing)
                if len(not_computable) < max_samples:
                    not_computable.append({
                        "world": world,
                        "row_index": row_index,
                        "status": "NOT_COMPUTABLE",
                        "reason": "MISSING_EXPLICIT_CAUSAL_IDENTITIES",
                        "missing_identities": missing,
                        "present_identity": {k: v for k, v in identity.items() if v is not None},
                    })
                continue
            complete += 1
            candidates[_key(identity)].append(row)

        duplicate_keys = {key: values for key, values in candidates.items() if len(values) > 1}
        for key, values in duplicate_keys.items():
            if len(not_computable) < max_samples:
                not_computable.append({
                    "world": world,
                    "status": "NOT_COMPUTABLE",
                    "reason": "AMBIGUOUS_DUPLICATE_CAUSAL_IDENTITY",
                    "causal_identity": dict(zip(REQUIRED_CAUSAL_IDENTITIES, key)),
                    "duplicate_rows": len(values),
                })
        indexes[world] = {
            key: values[0]
            for key, values in candidates.items()
            if len(values) == 1
        }
        world_summary[world] = {
            "status": (
                "NO_EVIDENCE"
                if not rows
                else "COMPUTABLE"
                if indexes[world]
                else "NOT_COMPUTABLE"
            ),
            "rows_observed": len(rows),
            "rows_with_complete_explicit_identity": complete,
            "unique_joinable_rows": len(indexes[world]),
            "ambiguous_duplicate_identity_rows": sum(len(v) for v in duplicate_keys.values()),
            "rows_not_computable": len(rows) - len(indexes[world]),
            "missing_identity_counts": dict(sorted(missing_counts.items())),
        }

    pairwise: list[dict[str, Any]] = []
    joined_rows: list[dict[str, Any]] = []
    joined_keys: set[tuple[str, ...]] = set()
    for left_world, right_world in combinations(WORLD_ORDER, 2):
        common = sorted(set(indexes[left_world]) & set(indexes[right_world]))
        counts = Counter()
        disagreements = Counter()
        for key in common:
            status, fields = _compare(indexes[left_world][key], indexes[right_world][key])
            counts[status] += 1
            disagreements.update(fields)
            joined_keys.add(key)
            if len(joined_rows) < max_samples:
                joined_rows.append({
                    "left_world": left_world,
                    "right_world": right_world,
                    "causal_identity": dict(zip(REQUIRED_CAUSAL_IDENTITIES, key)),
                    "status": status,
                    "disagreement_fields": fields,
                    "left": _comparison(indexes[left_world][key]),
                    "right": _comparison(indexes[right_world][key]),
                })
        compared = counts["AGREE"] + counts["DISAGREE"]
        pairwise.append({
            "left_world": left_world,
            "right_world": right_world,
            "status": "COMPUTABLE" if compared else "NOT_COMPUTABLE",
            "explicit_identity_matches": len(common),
            "computable_comparisons": compared,
            "agreement_count": counts["AGREE"],
            "disagreement_count": counts["DISAGREE"],
            "outcome_not_computable_count": counts["NOT_COMPUTABLE"],
            "disagreement_field_counts": dict(sorted(disagreements.items())),
        })

    computable = sum(row["computable_comparisons"] for row in pairwise)
    agreements = sum(row["agreement_count"] for row in pairwise)
    disagreements = sum(row["disagreement_count"] for row in pairwise)
    all_world_keys = set(indexes[WORLD_ORDER[0]])
    for world in WORLD_ORDER[1:]:
        all_world_keys &= set(indexes[world])
    return {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "source_revision": source_revision or "UNKNOWN",
        "epoch_id": epoch_id,
        "evidence_status": "DESCRIPTIVE_ONLY",
        "qualification_effect": "NONE",
        "live_policy_change_allowed": False,
        "join_contract": {
            "required_explicit_identities": list(REQUIRED_CAUSAL_IDENTITIES),
            "prohibited_fallbacks": [
                "trade_id_only", "timestamp_proximity", "price_similarity",
                "direction_similarity", "row_order", "derived_or_hashed_missing_identity",
            ],
            "missing_or_ambiguous_identity_result": "NOT_COMPUTABLE",
        },
        "worlds": world_summary,
        "join_summary": {
            "distinct_pairwise_joined_identities": len(joined_keys),
            "all_five_world_identity_matches": len(all_world_keys),
            "pairwise_computable_comparisons": computable,
            "pairwise_agreements": agreements,
            "pairwise_disagreements": disagreements,
            "status": "COMPUTABLE" if computable else "NOT_COMPUTABLE",
        },
        "pairwise": pairwise,
        "joined_rows": joined_rows,
        "not_computable": not_computable,
        "sample_limits": {
            "joined_rows": max_samples,
            "not_computable": max_samples,
        },
    }
