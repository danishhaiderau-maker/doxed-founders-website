"""Purged chronological validation and uncertainty gates for V3 policies."""
from __future__ import annotations

import random
from collections import Counter
from typing import Any, Iterable

from research_v3_risk import drawdown_budget_gate, portfolio_risk_metrics


SUPPORTED_TERMINAL_STATES = {"FULL_FILL", "PARTIAL_FILL", "NO_FILL", "NO_TRADE", "REJECTED", "REALIZED_ZERO_PNL"}


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def chronological_folds(episodes: Iterable[dict[str, Any]], *, outer_folds: int = 5, purge_sec: float = 7200, embargo_sec: float = 300) -> list[dict[str, Any]]:
    rows = sorted((dict(row) for row in episodes), key=lambda row: float(row.get("signal_ts") or 0))
    if outer_folds < 2 or len(rows) < outer_folds + 1:
        return []
    block = max(1, len(rows) // (outer_folds + 1))
    folds = []
    for index in range(1, outer_folds + 1):
        validation_start = index * block
        validation_end = len(rows) if index == outer_folds else min(len(rows), (index + 1) * block)
        validation = rows[validation_start:validation_end]
        if not validation:
            continue
        boundary = float(validation[0].get("signal_ts") or 0)
        train = [
            row for row in rows[:validation_start]
            if float(row.get("required_end_ts") or row.get("signal_ts") or 0) < boundary - float(purge_sec) - float(embargo_sec)
        ]
        folds.append({
            "fold": index,
            "train_episode_ids": [row.get("episode_id") for row in train],
            "validation_episode_ids": [row.get("episode_id") for row in validation],
            "train": train,
            "validation": validation,
            "validation_start_ts": boundary,
            "purge_sec": float(purge_sec),
            "embargo_sec": float(embargo_sec),
        })
    return folds


def _policy_values(rows: list[dict[str, Any]], policy_id: str) -> tuple[list[float], Counter, list[str]]:
    values, states, missing = [], Counter(), []
    for row in rows:
        outcome = (row.get("policy_outcomes") or {}).get(policy_id)
        if not isinstance(outcome, dict):
            missing.append(str(row.get("episode_id") or ""))
            continue
        state = str(outcome.get("outcome_state") or "UNSUPPORTED")
        states[state] += 1
        if state not in SUPPORTED_TERMINAL_STATES:
            missing.append(str(row.get("episode_id") or ""))
            continue
        if state in {"NO_FILL", "NO_TRADE", "REJECTED"}:
            # These are valid opportunity outcomes but not fabricated $0
            # executions. They count in coverage/EV denominators explicitly.
            values.append(0.0)
            continue
        if outcome.get("net_pnl_usd") is None:
            missing.append(str(row.get("episode_id") or ""))
            continue
        values.append(float(outcome["net_pnl_usd"]))
    return values, states, missing


def episode_block_bootstrap(values: list[float], *, samples: int = 1000, seed: int = 7) -> dict[str, Any]:
    if not values:
        return {"samples": 0, "mean_lcb95": None, "mean_ucb95": None, "probability_mean_positive": None}
    rng = random.Random(seed)
    means = []
    value_count = len(values)
    for _ in range(samples):
        # ``statistics.mean`` converts every float to an exact Fraction. That
        # is needlessly expensive inside thousands of policy bootstraps and
        # previously made a single analyzer generation exceed its 30-minute
        # publication contract. Inputs are already normalized floats and the
        # result is rounded to 8 decimals, so a direct floating-point sum is
        # deterministic for this seeded, fixed-order draw.
        total = 0.0
        for _index in range(value_count):
            total += float(values[rng.randrange(value_count)])
        means.append(total / value_count)
    return {
        "samples": samples,
        "mean_lcb95": round(float(_percentile(means, 0.025)), 8),
        "mean_ucb95": round(float(_percentile(means, 0.975)), 8),
        "probability_mean_positive": round(sum(value > 0 for value in means) / len(means), 8),
    }


def validate_policy(
    episodes: list[dict[str, Any]],
    *,
    policy_id: str,
    starting_equity_usd: float,
    max_drawdown_usd: float,
    max_drawdown_pct: float,
    min_cvar95_usd: float,
    policies_tested: int,
    conservative_execution: bool,
    neighborhood_stable: bool,
    sealed_holdout: bool,
    liquidation_buffer_verified: bool = False,
    minimum_episodes: int = 100,
    minimum_regimes: int = 3,
) -> dict[str, Any]:
    values, states, missing = _policy_values(episodes, policy_id)
    risk = portfolio_risk_metrics(values, starting_equity_usd=starting_equity_usd)
    # The opportunity-level EV denominator includes explicit non-executions,
    # but the report must never rename them as realized zero-PnL trades.
    risk["realized_zero_executions"] = int(states.get("REALIZED_ZERO_PNL", 0))
    risk["non_execution_zero_contributions"] = int(
        states.get("NO_FILL", 0) + states.get("NO_TRADE", 0) + states.get("REJECTED", 0)
    )
    risk.pop("realized_zero", None)
    budget = drawdown_budget_gate(risk, max_drawdown_usd=max_drawdown_usd, max_drawdown_pct=max_drawdown_pct, min_cvar95_usd=min_cvar95_usd)
    bootstrap = episode_block_bootstrap(values)
    regimes = {str(row.get("regime") or "UNKNOWN") for row in episodes if str(row.get("regime") or "UNKNOWN") != "UNKNOWN"}
    adjusted_required_probability = 1.0 - (0.05 / max(1, int(policies_tested)))
    probability = float(bootstrap.get("probability_mean_positive") or 0)
    gates = {
        "integrity_pass": not missing,
        "complete_paths_pass": not missing,
        "conservative_execution_pass": bool(conservative_execution),
        "drawdown_budget_pass": bool(budget["passed"]),
        "cvar_budget_pass": "CVAR95_BUDGET_FAILED" not in budget["reasons"],
        # A complete market path does not prove liquidation safety. Require an
        # explicit leverage/margin/liquidation-distance receipt.
        "liquidation_buffer_pass": bool(liquidation_buffer_verified),
        "oos_lcb_positive_pass": bool(bootstrap.get("mean_lcb95") is not None and bootstrap["mean_lcb95"] > 0),
        "neighborhood_stability_pass": bool(neighborhood_stable),
        "multiple_testing_pass": probability >= adjusted_required_probability,
        "regime_coverage_pass": len(regimes) >= int(minimum_regimes),
        "minimum_episode_pass": len(values) >= int(minimum_episodes),
        "sealed_holdout_pass": bool(sealed_holdout),
    }
    return {
        "schema": "safe_policy_validation_v3",
        "policy_id": policy_id,
        "episodes_supplied": len(episodes),
        "episodes_scored": len(values),
        "missing_or_unsupported_episode_ids": missing,
        "outcome_states": dict(sorted(states.items())),
        "regimes": sorted(regimes),
        "risk": risk,
        "drawdown_budget": budget,
        "bootstrap": bootstrap,
        "multiple_testing": {
            "policies_tested": int(policies_tested),
            "method": "BONFERRONI_FAMILYWISE_BOOTSTRAP_SCREEN",
            "required_probability_positive": round(adjusted_required_probability, 10),
        },
        "gates": gates,
        "qualified": all(gates.values()),
    }
