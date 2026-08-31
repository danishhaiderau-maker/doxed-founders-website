"""Fail-closed, Pareto-aware ranking for Safe Policy Genome candidates."""
from __future__ import annotations

from typing import Any, Iterable


REQUIRED_GATES = (
    "integrity_pass",
    "complete_paths_pass",
    "conservative_execution_pass",
    "drawdown_budget_pass",
    "cvar_budget_pass",
    "liquidation_buffer_pass",
    "purged_walk_forward_pass",
    "oos_lcb_positive_pass",
    "neighborhood_stability_pass",
    "multiple_testing_pass",
    "regime_coverage_pass",
    "minimum_episode_pass",
    "sealed_holdout_pass",
)


def _eligible(row: dict[str, Any]) -> tuple[bool, list[str]]:
    gates = row.get("gates") if isinstance(row.get("gates"), dict) else {}
    blockers = [name for name in REQUIRED_GATES if gates.get(name) is not True]
    return not blockers, blockers


def rank_safe_policies(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Rank only fully safe candidates; never let raw PnL bypass risk gates."""
    assessed = []
    for source in rows:
        row = dict(source)
        eligible, blockers = _eligible(row)
        row["ranking_eligible"] = eligible
        row["ranking_blockers"] = blockers
        assessed.append(row)
    survivors = [row for row in assessed if row["ranking_eligible"]]
    survivors.sort(key=lambda row: (
        -float(row.get("sealed_oos_net_usd") or 0),
        abs(float(row.get("max_drawdown_usd") or 0)),
        abs(float(row.get("cvar95_usd") or 0)),
        -float(row.get("expectancy_lcb_usd") or 0),
        str(row.get("policy_signature") or row.get("policy_id") or ""),
    ))
    for index, row in enumerate(survivors, 1):
        row["safe_rank"] = index
        row["rank_basis"] = "PROFIT_DESC_THEN_DRAWDOWN_ASC_THEN_CVAR_ASC_AMONG_ALL_GATES_PASSING"
    return {
        "schema": "safe_policy_ranking_v1",
        "qualification": "QUALIFIED" if survivors else "NO_SAFE_QUALIFIED_POLICY",
        "required_gates": list(REQUIRED_GATES),
        "policies_assessed": len(assessed),
        "policies_qualified": len(survivors),
        "number_one": survivors[0] if survivors else None,
        "ranked": survivors,
        "blocked": [row for row in assessed if not row["ranking_eligible"]],
    }
