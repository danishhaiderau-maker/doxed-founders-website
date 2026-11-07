"""Benjamini-Hochberg and White reality check on supplied fixed draws.

Real inference stays blocked. Romano-Wolf, PBO, dedup and the old bootstrap
seed block are not assigned values. DSR, effective n and cluster intervals
stay untested.
"""

from __future__ import annotations

from fractions import Fraction

from .validate import SchemaError, number_str, parse_number

BH_Q = Fraction("0.05")
RC_P_MAX = Fraction("0.05")


def benjamini_hochberg(entries: list[dict], q: Fraction = BH_Q) -> dict:
    """Full posted-N BH. Non-evaluated cells use p=1 as bookkeeping only."""
    if not entries:
        return _blocked("EMPTY")
    prepared = []
    bookkeeping = 0
    executed = 0
    for row in entries:
        ident = row["id"]
        if row["evaluated"]:
            if row.get("p") is None:
                return _blocked("MISSING_EVALUATED_P")
            try:
                p_value = parse_number(row["p"])
            except SchemaError:
                return _blocked("NONFINITE_P")
            if p_value < 0 or p_value > 1:
                return _blocked("P_OUT_OF_RANGE")
            executed += 1
        else:
            if row.get("p") not in (None,):
                return _blocked("NONEVALUATED_P_NOT_NULL")
            p_value = Fraction(1)
            bookkeeping += 1
        prepared.append((ident, p_value))
    prepared.sort(key=lambda item: (item[1], item[0]))
    m = len(prepared)
    largest = None
    for rank, (_ident, p_value) in enumerate(prepared, start=1):
        threshold = Fraction(rank, m) * q
        if p_value <= threshold:
            largest = rank
    rejected = [] if largest is None else [prepared[index][0] for index in range(largest)]
    return {
        "status": "SYNTHETIC_BRANCH_COMPUTED",
        "real_inference": "BLOCKED",
        "q": number_str(q),
        "m": m,
        "rejected_ids": rejected,
        "bookkeeping_p1": bookkeeping,
        "executed_coverage": executed,
        "p1_is_coverage": False,
        "comparison": "LESS_OR_EQUAL",
        "tie_break": "P_THEN_ID",
    }


def white_reality_check(series: list[dict], draws: list[list[int]], *, family_exact: bool) -> dict:
    """White RC on the whole comparable family using manufactured draws only.

    Differential convention (synthetic profile, not a real preregistration):
    strategy_pnl - benchmark_pnl. Recentering subtracts each series mean.
    No RNG, block length, or repetition count is invented here.
    """
    if not family_exact:
        return {
            "status": "REJECTED_NOT_FULL_FAMILY",
            "p": None,
            "real_inference": "BLOCKED",
            "draws_used": 0,
        }
    if not series:
        return _rc_blocked("INCOMPLETE_COMPARABLE_FAMILY")
    if not draws:
        return _rc_blocked("DRAWS_ABSENT")
    width = len(series[0]["differential"])
    parsed = []
    for row in series:
        values = [parse_number(item) for item in row["differential"]]
        if len(values) != width:
            return _rc_blocked("MISALIGNED")
        parsed.append(values)
    for draw in draws:
        if len(draw) != width or any(index < 0 or index >= width for index in draw):
            return _rc_blocked("DRAW_INVALID")
    means = [sum(values, Fraction(0)) / width for values in parsed]
    statistic = max(means)
    recentered = [[value - means[index] for value in values] for index, values in enumerate(parsed)]
    extreme = 0
    for draw in draws:
        peak = max(sum((recentered[k][index] for index in draw), Fraction(0)) / width for k in range(len(parsed)))
        if peak >= statistic:
            extreme += 1
    p_value = Fraction(extreme, len(draws))
    branch = "SYNTHETIC_BRANCH_PASS" if p_value <= RC_P_MAX else "SYNTHETIC_BRANCH_FAIL"
    return {
        "status": branch,
        "p": number_str(p_value),
        "extreme": extreme,
        "draws": len(draws),
        "V": number_str(statistic),
        "real_inference": "BLOCKED",
        "centering": "SUBTRACT_SAMPLE_MEAN",
        "differential": "strategy_pnl_minus_benchmark_pnl",
        "benchmark_preregistration": "NOT_APPROVED",
        "block_bootstrap": "NOT_USED",
        "rng": "NOT_USED",
        "selected_pool": False,
    }


def method_block() -> dict:
    retained_untested = {
        "requirement": "RETAINED",
        "status": "NOT_TESTED",
        "value": None,
    }
    proposed = {"status": "PROPOSED_NOT_TESTED", "value": None}
    return {
        "nested_walk_forward": {
            "requirement": "RETAINED",
            "dated_definitions": "NOT_APPROVED",
            "status": "NOT_TESTED",
            "iid_fold_pvalue": None,
        },
        "deflated_sharpe": dict(retained_untested),
        "effective_n": dict(retained_untested),
        "cluster_ci": dict(retained_untested),
        "trial_count": {
            "requirement": "RETAINED",
            "status": "IMPLEMENTED_BOOKKEEPING",
            "independent_observations": False,
        },
        "romano_wolf": dict(proposed),
        "pbo": dict(proposed),
        "diversity_dedup": {
            "status": "PROPOSED_NOT_TESTED",
            "value": None,
            "erases_trials": False,
        },
        "bootstrap_b2000_seed20261104_block1day": dict(proposed),
        "white_rc": {"real_inference": "BLOCKED", "fixed_draws_only": True},
        "bh": {"real_inference": "BLOCKED", "full_posted_n": True},
    }


def _blocked(reason: str) -> dict:
    return {
        "status": "BLOCKED",
        "reason": reason,
        "real_inference": "BLOCKED",
        "rejected_ids": [],
        "m": None,
        "bookkeeping_p1": None,
        "executed_coverage": None,
        "p1_is_coverage": False,
    }


def _rc_blocked(reason: str) -> dict:
    return {
        "status": "BLOCKED",
        "reason": reason,
        "p": None,
        "real_inference": "BLOCKED",
        "selected_pool": False,
    }
