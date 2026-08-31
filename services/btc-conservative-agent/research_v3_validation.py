"""Purged chronological validation and uncertainty gates for V3 policies."""
from __future__ import annotations

import random
from collections import Counter
from functools import lru_cache
from typing import Any, Iterable

from research_v3_risk import drawdown_budget_gate, portfolio_risk_metrics
from research_v3_sealed_holdout import verify_evaluation_receipt


SUPPORTED_TERMINAL_STATES = {"FULL_FILL", "PARTIAL_FILL", "NO_FILL", "NO_TRADE", "REJECTED", "REALIZED_ZERO_PNL"}
EXECUTED_TERMINAL_STATES = {"FULL_FILL", "PARTIAL_FILL", "REALIZED_ZERO_PNL"}
REQUIRED_MEASURED_COST_FIELDS = ("trading_fees_usd", "funding_usd", "slippage_usd")


def _measured_cost_evidence(
    rows: list[dict[str, Any]], policy_id: str,
) -> tuple[bool, list[dict[str, Any]]]:
    """Require an attributable measured-cost receipt for every execution.

    A numeric zero is valid only when the receipt explicitly says it was
    measured.  Missing values are never interpreted as zero for qualification.
    """
    defects: list[dict[str, Any]] = []
    for row in rows:
        episode_id = str(row.get("episode_id") or "")
        outcome = (row.get("policy_outcomes") or {}).get(policy_id)
        if not isinstance(outcome, dict):
            continue
        state = str(outcome.get("outcome_state") or "UNSUPPORTED")
        if state not in EXECUTED_TERMINAL_STATES:
            continue
        receipt = outcome.get("cost_evidence")
        reasons: list[str] = []
        if not isinstance(receipt, dict):
            reasons.append("MEASURED_COST_RECEIPT_MISSING")
        else:
            if receipt.get("schema") != "measured_execution_cost_receipt_v1":
                reasons.append("MEASURED_COST_RECEIPT_SCHEMA_INVALID")
            if receipt.get("status") != "MEASURED":
                reasons.append("MEASURED_COST_STATUS_REQUIRED")
            receipt_ids = receipt.get("source_receipt_ids")
            if not isinstance(receipt_ids, list) or not any(str(value).strip() for value in receipt_ids):
                reasons.append("MEASURED_COST_SOURCE_RECEIPT_REQUIRED")
            for field in REQUIRED_MEASURED_COST_FIELDS:
                value = receipt.get(field)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    reasons.append(f"{field.upper()}_MEASUREMENT_MISSING")
                elif float(value) < 0:
                    reasons.append(f"{field.upper()}_MEASUREMENT_NEGATIVE")
        if reasons:
            defects.append({
                "episode_id": episode_id,
                "outcome_state": state,
                "reasons": reasons,
            })
    return not defects, defects


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


def validate_purged_walk_forward(
    episodes: list[dict[str, Any]],
    *,
    policy_id: str,
    outer_folds: int = 5,
    purge_sec: float = 7200,
    embargo_sec: float = 300,
    minimum_valid_folds: int = 3,
) -> dict[str, Any]:
    """Evaluate a frozen policy on purged chronological validation folds.

    This is intentionally a qualification gate, not a policy selector.  The
    supplied ``policy_id`` must already be frozen; each fold evaluates only its
    later validation block and never uses it to choose or alter the policy.
    Missing/unsupported outcomes invalidate their fold instead of becoming
    zero-PnL observations.
    """
    input_defects = []
    seen_episode_ids: set[str] = set()
    for row in episodes:
        episode_id = str(row.get("episode_id") or "").strip()
        try:
            signal_ts = float(row.get("signal_ts"))
        except (TypeError, ValueError):
            signal_ts = None
        try:
            required_end_ts = float(row.get("required_end_ts"))
        except (TypeError, ValueError):
            required_end_ts = None
        if not episode_id:
            input_defects.append("MISSING_EPISODE_ID")
        elif episode_id in seen_episode_ids:
            input_defects.append(f"DUPLICATE_EPISODE_ID:{episode_id}")
        else:
            seen_episode_ids.add(episode_id)
        if signal_ts is None or signal_ts < 0:
            input_defects.append(f"INVALID_SIGNAL_TS:{episode_id or 'UNKNOWN'}")
        if (
            required_end_ts is None
            or required_end_ts < 0
            or (signal_ts is not None and required_end_ts < signal_ts)
        ):
            input_defects.append(f"INVALID_REQUIRED_END_TS:{episode_id or 'UNKNOWN'}")

    folds = chronological_folds(
        episodes,
        outer_folds=outer_folds,
        purge_sec=purge_sec,
        embargo_sec=embargo_sec,
    )
    results = []
    pooled_values: list[float] = []
    for fold in folds:
        values, states, missing = _policy_values(fold["validation"], policy_id)
        complete = bool(fold["train"]) and bool(values) and not missing
        expectancy = (sum(values) / len(values)) if complete else None
        if complete:
            pooled_values.extend(values)
        results.append({
            "fold": fold["fold"],
            "train_episodes": len(fold["train"]),
            "validation_episodes": len(fold["validation"]),
            "validation_episodes_scored": len(values),
            "validation_outcome_states": dict(sorted(states.items())),
            "missing_or_unsupported_episode_ids": missing,
            "expectancy_usd_per_opportunity": (
                None if expectancy is None else round(expectancy, 8)
            ),
            "complete": complete,
            "positive_expectancy": bool(expectancy is not None and expectancy > 0),
        })
    complete_results = [row for row in results if row["complete"]]
    positive_results = [row for row in complete_results if row["positive_expectancy"]]
    pooled_expectancy = (
        sum(pooled_values) / len(pooled_values) if pooled_values else None
    )
    required = max(1, int(minimum_valid_folds))
    passed = bool(
        not input_defects
        and len(complete_results) >= required
        and len(complete_results) == len(results)
        and len(positive_results) == len(complete_results)
        and pooled_expectancy is not None
        and pooled_expectancy > 0
    )
    blockers = []
    if input_defects:
        blockers.append("INVALID_WALK_FORWARD_CAUSAL_IDENTITIES_OR_TIMESTAMPS")
    if len(complete_results) < required:
        blockers.append("INSUFFICIENT_COMPLETE_PURGED_FOLDS")
    if len(complete_results) != len(results):
        blockers.append("INCOMPLETE_PURGED_FOLD_EVIDENCE")
    if complete_results and len(positive_results) != len(complete_results):
        blockers.append("NON_POSITIVE_PURGED_FOLD")
    if pooled_expectancy is None or pooled_expectancy <= 0:
        blockers.append("NON_POSITIVE_POOLED_WALK_FORWARD_EXPECTANCY")
    return {
        "schema": "purged_walk_forward_validation_v1",
        "policy_id": policy_id,
        "policy_selection_semantics": "FROZEN_BEFORE_VALIDATION_NOT_SELECTED_ON_FOLDS",
        "outer_folds_requested": int(outer_folds),
        "folds_materialized": len(folds),
        "complete_folds": len(complete_results),
        "positive_folds": len(positive_results),
        "minimum_valid_folds": required,
        "purge_sec": float(purge_sec),
        "embargo_sec": float(embargo_sec),
        "pooled_validation_episodes": len(pooled_values),
        "pooled_expectancy_usd_per_opportunity": (
            None if pooled_expectancy is None else round(pooled_expectancy, 8)
        ),
        "folds": results,
        "input_defects": sorted(set(input_defects)),
        "blockers": blockers,
        "passed": passed,
    }


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


@lru_cache(maxsize=8192)
def _episode_block_bootstrap_cached(
    values: tuple[float, ...], samples: int, seed: int,
) -> tuple[int, float | None, float | None, float | None]:
    if not values:
        return 0, None, None, None
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
    return (
        samples,
        round(float(_percentile(means, 0.025)), 8),
        round(float(_percentile(means, 0.975)), 8),
        round(sum(value > 0 for value in means) / len(means), 8),
    )


def episode_block_bootstrap(values: list[float], *, samples: int = 1000, seed: int = 7) -> dict[str, Any]:
    # A 21k-policy grid contains many policies with exactly the same OOS return
    # vector.  Re-running an identical seeded bootstrap for each policy made a
    # scheduled analyzer generation take tens of minutes. Cache the immutable
    # evidence vector; this changes no samples, probabilities, or gates.
    result = _episode_block_bootstrap_cached(
        tuple(float(value) for value in values), int(samples), int(seed),
    )
    return {
        "samples": result[0],
        "mean_lcb95": result[1],
        "mean_ucb95": result[2],
        "probability_mean_positive": result[3],
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
    sealed_holdout: Any,
    liquidation_buffer_verified: bool = False,
    purged_walk_forward: dict[str, Any] | None = None,
    minimum_episodes: int = 100,
    minimum_regimes: int = 3,
) -> dict[str, Any]:
    values, states, missing = _policy_values(episodes, policy_id)
    measured_costs_pass, cost_evidence_defects = _measured_cost_evidence(
        episodes, policy_id,
    )
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
        "measured_costs_pass": measured_costs_pass,
        "drawdown_budget_pass": bool(budget["passed"]),
        "cvar_budget_pass": "CVAR95_BUDGET_FAILED" not in budget["reasons"],
        # A complete market path does not prove liquidation safety. Require an
        # explicit leverage/margin/liquidation-distance receipt.
        "liquidation_buffer_pass": bool(liquidation_buffer_verified),
        "purged_walk_forward_pass": bool(
            isinstance(purged_walk_forward, dict)
            and purged_walk_forward.get("passed") is True
        ),
        "oos_lcb_positive_pass": bool(bootstrap.get("mean_lcb95") is not None and bootstrap["mean_lcb95"] > 0),
        "neighborhood_stability_pass": bool(neighborhood_stable),
        "multiple_testing_pass": probability >= adjusted_required_probability,
        "regime_coverage_pass": len(regimes) >= int(minimum_regimes),
        "minimum_episode_pass": len(values) >= int(minimum_episodes),
        # A boolean is an assertion, not evidence. Only a content-addressed,
        # single-use evaluation receipt can satisfy this qualification gate.
        "sealed_holdout_pass": verify_evaluation_receipt(
            sealed_holdout, policy_id=policy_id,
        ),
    }
    return {
        "schema": "safe_policy_validation_v3",
        "policy_id": policy_id,
        "episodes_supplied": len(episodes),
        "episodes_scored": len(values),
        "evidence_status": (
            "AVAILABLE" if values else "INSUFFICIENT_EXECUTION_EVIDENCE"
        ),
        "missing_or_unsupported_episode_ids": missing,
        "measured_cost_evidence": {
            "schema": "measured_execution_cost_coverage_v1",
            "required_fields": list(REQUIRED_MEASURED_COST_FIELDS),
            "semantics": "EXPLICIT_MEASURED_ZERO_ALLOWED_MISSING_NEVER_DEFAULTED_TO_ZERO",
            "defects": cost_evidence_defects,
            "passed": measured_costs_pass,
        },
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
        "purged_walk_forward": purged_walk_forward or {
            "schema": "purged_walk_forward_validation_v1",
            "passed": False,
            "blockers": ["PURGED_WALK_FORWARD_NOT_SUPPLIED"],
        },
        "sealed_holdout": sealed_holdout if isinstance(sealed_holdout, dict) else {
            "schema": "sealed_holdout_evaluation_v1",
            "passed": False,
            "blockers": ["VALID_SEALED_HOLDOUT_RECEIPT_NOT_SUPPLIED"],
        },
        "gates": gates,
        "qualified": all(gates.values()),
    }
