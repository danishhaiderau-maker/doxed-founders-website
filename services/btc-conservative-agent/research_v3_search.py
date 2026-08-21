"""Auditable hierarchical search accounting without Cartesian materialization."""
from __future__ import annotations

from math import prod
from typing import Any, Mapping

from research_v3_contract import SAFE_POLICY_GENOME_CONTRACT, canonical_hash


def build_search_plan(entry_dimensions: Mapping[str, list[Any]]) -> dict[str, Any]:
    protection = SAFE_POLICY_GENOME_CONTRACT["protection_axes"]
    portfolio = SAFE_POLICY_GENOME_CONTRACT["portfolio_axes"]
    entry_counts = {name: len(values) for name, values in entry_dimensions.items()}
    protection_counts = {name: len(values) for name, values in protection.items()}
    portfolio_counts = {name: len(values) for name, values in portfolio.items()}
    nominal = prod(entry_counts.values()) * prod(protection_counts.values()) * prod(portfolio_counts.values())
    plan = {
        "schema": "hierarchical_policy_search_plan_v3",
        "entry_dimensions": {key: list(value) for key, value in entry_dimensions.items()},
        "protection_dimensions": protection,
        "portfolio_dimensions": portfolio,
        "counts": {
            "entry_cartesian": prod(entry_counts.values()),
            "protection_cartesian": prod(protection_counts.values()),
            "portfolio_cartesian": prod(portfolio_counts.values()),
            "nominal_full_cartesian": nominal,
            "materialized_policy_rows": 0,
        },
        "stages": [
            {"id": "INTEGRITY", "action": "exclude incomplete, censored, ambiguous and identity-defective evidence"},
            {"id": "PATH_COMPILE", "action": "compile reusable first-touch, MFE, MAE, ATR and time frontiers per episode"},
            {"id": "AXIS_SCREEN", "action": "chronological training-only marginal screens by family"},
            {"id": "STABLE_NEIGHBORHOODS", "action": "retain positive parameter neighborhoods, never isolated peaks"},
            {"id": "SUCCESSIVE_HALVING", "action": "allocate replay budget to stable survivors and interactions"},
            {"id": "NESTED_WALK_FORWARD", "action": "purged and embargoed outer chronological folds"},
            {"id": "SEALED_OOS", "action": "evaluate one frozen shortlist exactly once"},
            {"id": "CONSERVATIVE_STRESS", "action": "BBO/depth, partial fill, funding, latency and stop-slippage worlds"},
            {"id": "RISK_PARETO", "action": "hard reject unsafe candidates, then profit versus drawdown/CVaR frontier"},
        ],
        "cache_key": ["episode_hash", "entry_spec_hash", "exit_spec_hash", "fill_model_hash", "cost_model_hash"],
        "warning": "Nominal Cartesian count is transparency only; it is never persisted or exhaustively winner-picked.",
    }
    plan["search_plan_signature"] = canonical_hash("v3-search", plan)
    return plan


def search_progress(plan: Mapping[str, Any], stage_receipts: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = sum(int(row.get("unique_policies_evaluated") or 0) for row in stage_receipts)
    episodes = max((int(row.get("independent_episodes") or 0) for row in stage_receipts), default=0)
    return {
        "schema": "hierarchical_policy_search_progress_v3",
        "search_plan_signature": plan.get("search_plan_signature"),
        "nominal_full_cartesian": (plan.get("counts") or {}).get("nominal_full_cartesian"),
        "unique_policies_evaluated": evaluated,
        "independent_episodes": episodes,
        "stage_receipts": list(stage_receipts),
        "exhaustive_materialization": False,
    }

