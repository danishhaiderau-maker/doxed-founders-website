"""Disjoint coverage partition and attempted-trial bookkeeping.

p=1 cells are multiplicity bookkeeping. They are not executed coverage.
Diversity filters do not erase trials.
"""

from __future__ import annotations


def reconcile(coverage: dict) -> dict:
    planned = coverage["planned"]
    evaluated = coverage["evaluated"]
    excluded = coverage["excluded"]
    remaining = coverage["remaining"]
    failed = coverage["failed_attempts"]
    gate_failures = coverage["gate_failures"]
    sets = {
        "planned": set(planned),
        "evaluated": set(evaluated),
        "excluded": set(excluded),
        "remaining": set(remaining),
        "failed_attempts": set(failed),
        "gate_failures": set(gate_failures),
    }
    partition = sets["evaluated"] | sets["excluded"] | sets["remaining"]
    disjoint = (
        sets["evaluated"].isdisjoint(sets["excluded"])
        and sets["evaluated"].isdisjoint(sets["remaining"])
        and sets["excluded"].isdisjoint(sets["remaining"])
    )
    partition_ok = partition == sets["planned"]
    failed_separate = sets["failed_attempts"].isdisjoint(sets["planned"])
    failures_subset = sets["gate_failures"] <= sets["evaluated"]
    ok = disjoint and partition_ok and failed_separate and failures_subset
    return {
        "status": "RECONCILED" if ok else "RECONCILIATION_FAILED",
        "planned_n": len(planned),
        "evaluated_n": len(evaluated),
        "excluded_n": len(excluded),
        "remaining_n": len(remaining),
        "failed_attempt_n": len(failed),
        "gate_failure_n": len(gate_failures),
        "disjoint": disjoint,
        "partition_ok": partition_ok,
        "failed_attempts_separate": failed_separate,
        "gate_failures_subset": failures_subset,
        "executed_coverage": len(evaluated),
        "p1_bookkeeping_not_coverage": True,
    }


def trial_accounting(trials: dict, coverage: dict) -> dict:
    rows = trials["rows"]
    attempted = [row["id"] for row in rows]
    attempted_set = set(attempted)
    remaining = set(coverage["remaining"])
    failed = set(coverage["failed_attempts"])
    evaluated = set(coverage["evaluated"])
    excluded = set(coverage["excluded"])
    planned = set(coverage["planned"])
    must_attempt = (planned - remaining) | failed
    complete = attempted_set == must_attempt and attempted_set.isdisjoint(remaining)
    diversity = trials["diversity_filter_ids"]
    diversity_ignored = True
    if diversity is not None and set(diversity) != attempted_set:
        diversity_ignored = True
    return {
        "status": "COUNTED" if complete else "BLOCKED_TRIAL_SET",
        "trial_count": len(attempted),
        "attempted_ids": attempted,
        "diversity_filter_ignored": diversity_ignored,
        "diversity_filter_count": None if diversity is None else len(diversity),
        "erases_trials": False,
        "includes_remaining": False,
        "evaluated_covered": evaluated <= attempted_set,
        "failed_covered": failed <= attempted_set,
        "excluded_covered": excluded <= attempted_set,
        "kinds": sorted({row["kind"] for row in rows}),
    }


def bh_universe(coverage: dict) -> list[str]:
    """Posted N plus separate failed attempts. Order is sorted for stability."""
    return sorted(set(coverage["planned"]) | set(coverage["failed_attempts"]))
