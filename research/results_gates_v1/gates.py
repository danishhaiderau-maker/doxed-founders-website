"""Synthetic-profile gate evaluation. Real evaluation cannot PASS.

Example numeric gates live only in the versioned synthetic profile. Real N,
dates and cost thresholds stay null. Unsupported methods stay untested.
"""

from __future__ import annotations

import json
from fractions import Fraction

from . import coverage, folds, metrics, packages, statistics
from .dashboard_projection import project
from .guards import (
    MonotonicClock,
    check_profile_pin,
    enforce_elapsed,
    enforce_rss,
    enforce_strategy_count,
    enforce_workers,
    reject_canaries,
)
from .validate import (
    EVALUATION_STATUS,
    UNKNOWNS,
    ProfileRejected,
    SchemaError,
    canonical_bytes,
    freeze,
    number_str,
    parse_number,
    sha256_bytes,
    sha256_obj,
    validate_output,
    validate_stub,
    validate_zero_statement,
)

PROFILE_ID = "synthetic-branch-profile-v1"
REQUIRED_EPOCHS = (
    "study1_train",
    "study1_test",
    "run_a",
    "run_a2",
    "07d",
    "spent_holdouts",
)
_UNDISTURBED = frozenset({"UNDISTURBED", "UNTOUCHED", "UNSEEN"})

_PROFILE = {
    "schema": "gate_method_profile_v1",
    "version": "1",
    "profile_id": PROFILE_ID,
    "approval": "SYNTHETIC_BRANCH_ONLY_NOT_REAL",
    "real_evaluation_permitted": False,
    "real_n": None,
    "real_dates": None,
    "real_cost_thresholds": None,
    "inherited_old_n": False,
    "inherited_a2_unseen": False,
    "example_gates": {
        "bh_q": "0.05",
        "white_rc_p_max": "0.05",
        "nested_folds_required_positive": 3,
        "nested_folds_total": 4,
        "qualifying_oof_trades_min": 60,
        "day_share_max": "0.40",
        "week_share_max": "0.50",
        "median_hold_seconds_min": "300",
        "max_packaged_leads": 10,
        "gross_vs_roundtrip_multiple": "2",
        "stress_slippage_only_multiple": "1.5",
        "net_sum_min_exclusive": "0",
        "stress_net_min_exclusive": "0",
    },
    "concentration": {
        "numerator": "sum_of_positive_net_in_bucket",
        "denominator": "sum_of_positive_net_all_buckets",
        "utc_day": "UTC_[00:00,24:00)_half_open",
        "utc_week": "ISO_week_Monday_00:00_UTC_half_open",
        "nonpositive_denominator": "AMBIGUOUS_BLOCK",
        "zero_bucket": "share_zero_if_denominator_positive",
        "negative_nets": "excluded_from_numerator_and_denominator",
        "synthetic_fixture_uses_labels": True,
    },
    "hold_median": {
        "population": "filled_resolved_only",
        "even_count": "mean_of_two_central_order_statistics",
        "unresolved": "INCOMPLETE",
        "missing_filled_hold": "BLOCKED",
    },
    "white_rc": {
        "differential": "strategy_pnl_minus_benchmark_pnl",
        "centering": "SUBTRACT_SAMPLE_MEAN",
        "draws": "FIXED_MANUFACTURED_ONLY",
        "family": "ENTIRE_PREREGISTERED_COMPARABLE_FAMILY",
        "real_inference": "BLOCKED",
    },
    "bh": {
        "q": "0.05",
        "comparison": "LESS_OR_EQUAL",
        "tie_break": "P_THEN_ID",
        "nonevaluated_p": "1",
        "p1_is_coverage": False,
    },
    "cost": {
        "danish_account_fee_assumption": "0",
        "danish_account_fee_basis": "EXPLICIT_ACCOUNT_ASSERTION_NOT_VENUE_VERIFICATION",
        "fee_verification_evidence": None,
        "measured_real_execution_friction": None,
        "measured_real_execution_friction_status": "BLOCKED",
        "funding_charges": "EXCLUDED_NOT_MODELLED",
        "funding_numeric": None,
        "queue_delay_fixtures": "SYNTHETIC",
        "stress": "SLIPPAGE_ONLY_NOT_SPREAD_LATENCY_OR_WHOLE_COST",
        "old_fee_thresholds_revived": False,
        "old_funding_thresholds_revived": False,
    },
    "exposure": {
        "interval": "half_open_[start,end)",
        "required_epochs": list(REQUIRED_EPOCHS),
        "epoch_07d": "AUDIT_ONLY",
        "unknown_or_incomplete": "BLOCK_UNTOUCHED_VALIDATION",
        "width_rule": "RK_INTERPRETATION_NOT_ORIGINAL_POSITIVE_WIDTH",
        "inverted": "INCOMPLETE_UNKNOWN",
        "unmarked_zero_width": "UNKNOWN",
        "declared_empty": "ACCEPTED_WITH_PROVENANCE",
    },
}


def synthetic_profile() -> dict:
    return json.loads(json.dumps(_PROFILE))


def profile_sha256() -> str:
    return sha256_obj(_PROFILE)


def compare_ge(value, threshold) -> str:
    return _compare(value, threshold, "ge")


def compare_gt(value, threshold) -> str:
    return _compare(value, threshold, "gt")


def compare_le(value, threshold) -> str:
    return _compare(value, threshold, "le")


def exposure_gate(ledger: dict, claim: dict) -> dict:
    intervals = {row["id"]: row for row in ledger["intervals"]}
    internally_complete = _ledger_internally_complete(ledger, intervals)
    token = str(claim.get("token", "")).upper()
    undisturbed = token in _UNDISTURBED
    claim_bounds = _claim_bounds(claim)
    if not internally_complete or claim_bounds is None:
        overlap = "UNKNOWN"
        false_claim = "BLOCKED" if undisturbed else "NOT_APPLICABLE"
        reason = "UNKNOWN_OR_INCOMPLETE_LEDGER"
    else:
        overlap = "OVERLAP" if _overlaps_any(claim_bounds, intervals.values()) else "NO_OVERLAP"
        if undisturbed and overlap == "OVERLAP":
            false_claim = "REJECTED"
        elif undisturbed:
            false_claim = "NOT_REJECTED_FOR_OVERLAP"
        else:
            false_claim = "NOT_APPLICABLE"
        reason = "REAL_DATE_RECEIPTS_ABSENT"
    return {
        "real_exposure": "UNKNOWN",
        "untouched_validation_acceptance": "BLOCKED",
        "untouched_block_reason": "UNKNOWN_OR_INCOMPLETE_LEDGER" if not internally_complete or claim_bounds is None else reason,
        "synthetic_overlap": overlap,
        "false_undisturbed_claim": false_claim,
        "interval": "half_open_[start,end)",
        "epoch_07d": "AUDIT_ONLY",
        "coordinate_system": ledger.get("coordinate_system"),
        "real_dates": None,
    }


def overlaps_half_open(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    if end_a <= start_a or end_b <= start_b:
        return False
    return start_a < end_b and start_b < end_a


def evaluate_fixture(doc: dict, *, workers: int = 1, clock=None):
    ticker = clock or MonotonicClock()
    enforce_workers(workers)
    reject_canaries(doc)
    enforce_elapsed(ticker.elapsed(), kind="command")
    peeked = _peek_family_count(doc)
    if peeked is not None:
        enforce_strategy_count(peeked)
    before = canonical_bytes(doc)
    validate_stub(doc)
    if doc["profile_id"] != PROFILE_ID:
        raise ProfileRejected("unapproved profile")
    check_profile_pin(profile_sha256(), doc["profile_id"])
    profile = synthetic_profile()
    example = profile["example_gates"]
    coverage_result = coverage.reconcile(doc["coverage"])
    trials = coverage.trial_accounting(doc["trials"], doc["coverage"])
    bh_result = _bh(doc, coverage_result)
    rc_result = _rc(doc)
    metric_result = metrics.compute(doc["accounting"])
    fold_result = folds.assess(doc["folds"])
    causal_result = folds.assess_causal(doc["causal"])
    exposure = exposure_gate(doc["exposure_ledger"], doc["exposure_claim"])
    quarantine = _quarantine(doc["quarantine_findings"])
    package_result = packages.build(doc["strategy_drafts"], doc["draft_extras"], doc["record_labels"])
    dashboard = project(doc, doc["dashboard"])
    cost = _cost(doc["cost"], metric_result, example)
    decisions = _decisions(
        example=example,
        coverage_result=coverage_result,
        trials=trials,
        bh_result=bh_result,
        rc_result=rc_result,
        metric_result=metric_result,
        fold_result=fold_result,
        causal_result=causal_result,
        exposure=exposure,
        quarantine=quarantine,
        package_result=package_result,
        cost=cost,
    )
    if canonical_bytes(doc) != before:
        raise SchemaError("input mutated")
    statement = None
    if metric_result["status"] == "COMPLETE" and metric_result["net_sum_fraction"] == 0:
        statement = {
            "text_scope": doc["scope"]["exact_scope"],
            "mission_done": False,
            "research_done": False,
        }
        validate_zero_statement(statement)
    observed_rss = enforce_rss()
    enforce_elapsed(ticker.elapsed(), kind="command")
    result = {
        "schema": "research_results_v1",
        "version": "1",
        "fixture_only": True,
        "compatibility_label": "FIXTURE-ONLY / COMPATIBILITY NOT ESTABLISHED",
        "evaluation_status": EVALUATION_STATUS,
        "research_done": False,
        "mission_done": False,
        "live": "OFF",
        "profile": {
            "profile_id": PROFILE_ID,
            "profile_sha256": profile_sha256(),
            "approval": profile["approval"],
            "real_evaluation_permitted": False,
        },
        "producer_compatibility": {
            "stage_a": "BLOCKED",
            "stage_b": "BLOCKED",
            "coverage_report": "BLOCKED_NOT_REDEFINED",
            "label": "FIXTURE-ONLY / COMPATIBILITY NOT ESTABLISHED",
        },
        "scope": {
            "exact_scope": doc["scope"]["exact_scope"],
            "real_n": None,
            "real_dates": None,
            "real_cost_thresholds": None,
            "inherited_old_n": False,
            "inherited_a2_unseen": False,
        },
        "decisions": decisions,
        "coverage": coverage_result,
        "trials": {
            "status": trials["status"],
            "trial_count": trials["trial_count"],
            "posted_n": coverage_result["planned_n"],
            "executed_coverage": coverage_result["executed_coverage"],
            "bookkeeping_p1": bh_result.get("bookkeeping_p1"),
            "diversity_dedup": "PROPOSED_NOT_TESTED",
            "erases_trials": False,
            "diversity_filter_ignored": trials["diversity_filter_ignored"],
        },
        "statistics": {"bh": bh_result, "white_rc": rc_result},
        "methods": statistics.method_block(),
        "metrics": _public_metrics(metric_result),
        "folds": folds.fold_summary(fold_result),
        "causal": causal_result,
        "exposure": exposure,
        "cost": cost["public"],
        "packages": package_result,
        "dashboard": dashboard,
        "labels": {
            "exploratory_two_month_only": list(doc["record_labels"]["exploratory_two_month_only"]),
            "invalid_for_fills": list(doc["record_labels"]["invalid_for_fills"]),
        },
        "quarantine": quarantine,
        "artifacts": {
            "immutable": True,
            "input_sha256": sha256_bytes(before),
            "results_sha256": None,
            "workers_used": workers,
            "worker_cap": 2,
            "rss_kib": observed_rss,
        },
        "unknowns": list(UNKNOWNS),
        "zero_result_statement": statement,
    }
    result["artifacts"]["results_sha256"] = _hash_without_self(result)
    validate_output(result)
    return freeze(result)


def _hash_without_self(result: dict) -> str:
    body = json.loads(json.dumps(result))
    body["artifacts"]["results_sha256"] = None
    return sha256_obj(body)


def _decisions(*, example, coverage_result, trials, bh_result, rc_result, metric_result, fold_result, causal_result, exposure, quarantine, package_result, cost) -> list[dict]:
    fold_ok = fold_result["status"] == "INTEGRITY_OK"
    return [
        _decision("danish_account_fee_assumption", "ASSERTED_NOT_VENUE_VERIFIED", "account assertion, not venue verification"),
        _decision("fee_verification_evidence", "BLOCKED_NO_EVIDENCE", "evidence is null"),
        _decision("measured_real_execution_friction", "BLOCKED", "measured friction is null"),
        _decision("funding_charges", "EXCLUDED_NOT_MODELLED", "excluded is not a measured zero"),
        _decision("queue_delay", "SYNTHETIC", "queue and delay fixtures are synthetic"),
        _decision("gross_vs_pinned_roundtrip", cost["gross_status"], "synthetic example multiple only"),
        _decision("net_sum", cost["net_status"], "strictly greater than zero"),
        _decision("stress_net_slippage_only", cost["stress_status"], "1.5x slippage only"),
        _decision(
            "nested_folds_positive",
            compare_ge(fold_result["positive_folds"], example["nested_folds_required_positive"]) if fold_ok else "BLOCKED",
            "synthetic example 3 of 4; real fold dates are not approved",
        ),
        _decision(
            "qualifying_oof_trades",
            compare_ge(fold_result["qualifying_oof"], example["qualifying_oof_trades_min"]) if fold_ok else "BLOCKED",
            "synthetic example count; real N is null",
        ),
        _decision("fold_definition_approval", "BLOCKED_NOT_APPROVED", "dated definitions are not approved"),
        _decision("bh_full_posted_n", bh_result["status"], "full posted N; p=1 is bookkeeping"),
        _decision("white_rc_full_family", rc_result["status"], "fixed draws only; real inference blocked"),
        _decision("day_share", _share_status(metric_result, "day_share_fraction", example["day_share_max"]), "predeclared positive-net share"),
        _decision("week_share", _share_status(metric_result, "week_share_fraction", example["week_share_max"]), "ISO week convention declared"),
        _decision("median_hold", _hold_status(metric_result, example["median_hold_seconds_min"]), "filled holds only"),
        _decision("packaged_leads", _lead_status(package_result, example["max_packaged_leads"]), "no truncation"),
        _decision("exposure_untouched_validation", exposure["untouched_validation_acceptance"], exposure["untouched_block_reason"]),
        _decision("exposure_false_undisturbed", exposure["false_undisturbed_claim"], exposure["synthetic_overlap"]),
        _decision("quarantine_07d", quarantine["status"], "07d is audit only"),
        _decision("coverage_reconciliation", coverage_result["status"], "planned equals evaluated plus excluded plus remaining"),
        _decision("trial_count", trials["status"], "attempted trials stay counted"),
        _decision("labels_preserved", "RECORDED", "exploratory and invalid-for-fills labels stay"),
        _decision("causal_leakage", causal_result["status"], "available time must not follow the decision"),
        _decision("evaluation", EVALUATION_STATUS, "no approved real gate profile"),
    ]


def _decision(gate_id: str, status: str, reason: str) -> dict:
    if status == "PASS":
        raise SchemaError("bare PASS is not a legal gate status")
    return {"gate_id": gate_id, "status": status, "reason": reason, "real_threshold": None}


def _cost(cost_doc: dict, metric_result: dict, example: dict) -> dict:
    public = {
        "danish_account_fee_assumption": "0",
        "danish_account_fee_basis": "EXPLICIT_ACCOUNT_ASSERTION_NOT_VENUE_VERIFICATION",
        "fee_verification_evidence": None,
        "measured_real_execution_friction": None,
        "measured_real_execution_friction_status": "BLOCKED",
        "funding_charges": "EXCLUDED_NOT_MODELLED",
        "funding_numeric": None,
        "queue_delay_status": cost_doc["queue_delay_status"],
        "queue_delay_numeric": None,
        "synthetic_roundtrip": None if cost_doc["synthetic_roundtrip"] is None else number_str(parse_number(cost_doc["synthetic_roundtrip"])),
        "gross": None if cost_doc["gross"] is None else number_str(parse_number(cost_doc["gross"])),
        "stressed_net": None,
        "stress_scope": "SLIPPAGE_ONLY",
        "old_fee_thresholds_revived": False,
        "old_funding_thresholds_revived": False,
        "real_cost_thresholds": None,
    }
    blocked = {
        "public": public,
        "gross_status": "BLOCKED",
        "net_status": "BLOCKED",
        "stress_status": "BLOCKED",
    }
    if metric_result["status"] != "COMPLETE":
        return blocked
    if cost_doc["gross"] is None or cost_doc["synthetic_roundtrip"] is None:
        net_status = compare_gt(metric_result["net_sum_fraction"], example["net_sum_min_exclusive"])
        blocked["net_status"] = net_status
        return blocked
    gross = parse_number(cost_doc["gross"])
    roundtrip = parse_number(cost_doc["synthetic_roundtrip"])
    fee = Fraction(0)
    slippage = metric_result["slippage_fraction"]
    expected = metric_result["net_sum_fraction"] + slippage + fee
    if gross != expected:
        return blocked
    multiple = parse_number(example["gross_vs_roundtrip_multiple"])
    stress_multiple = parse_number(example["stress_slippage_only_multiple"])
    stressed = gross - fee - (stress_multiple * slippage)
    public["stressed_net"] = number_str(stressed)
    return {
        "public": public,
        "gross_status": compare_ge(gross, multiple * roundtrip),
        "net_status": compare_gt(metric_result["net_sum_fraction"], example["net_sum_min_exclusive"]),
        "stress_status": compare_gt(stressed, example["stress_net_min_exclusive"]),
    }


def _bh(doc: dict, coverage_result: dict) -> dict:
    if coverage_result["status"] != "RECONCILED":
        return statistics.benjamini_hochberg([])
    universe = coverage.bh_universe(doc["coverage"])
    rows = {row["id"]: row for row in doc["p_values"]}
    if set(rows) != set(universe):
        blocked = statistics.benjamini_hochberg([])
        blocked["reason"] = "BH_ID_MISMATCH"
        return blocked
    evaluated = set(doc["coverage"]["evaluated"])
    entries = []
    for ident in universe:
        row = rows[ident]
        if row["evaluated"] != (ident in evaluated):
            blocked = statistics.benjamini_hochberg([])
            blocked["reason"] = "BH_EVALUATED_MISMATCH"
            return blocked
        entries.append({"id": ident, "evaluated": row["evaluated"], "p": row["p"]})
    return statistics.benjamini_hochberg(entries, parse_number(synthetic_profile()["example_gates"]["bh_q"]))


def _rc(doc: dict) -> dict:
    family_ids = {row["id"] for row in doc["family"]}
    block = doc["comparable_family"]
    preregistered = set(block["preregistered_ids"])
    presented = set(block["presented_ids"])
    series = block["series"]
    series_ids = {row["id"] for row in series}
    if not preregistered or family_ids != preregistered:
        return _rc_reject("REJECTED_NOT_FULL_FAMILY", selected=True)
    if series_ids != preregistered:
        return _rc_reject("BLOCKED", reason="INCOMPLETE_COMPARABLE_FAMILY", selected=False)
    if presented != preregistered:
        return _rc_reject("REJECTED_NOT_FULL_FAMILY", selected=True)
    if not doc["rc_draws"]:
        return statistics.white_reality_check(series, [], family_exact=True)
    return statistics.white_reality_check(series, doc["rc_draws"], family_exact=True)


def _rc_reject(status: str, *, selected: bool, reason: str | None = None) -> dict:
    return {
        "status": status,
        "reason": reason,
        "p": None,
        "real_inference": "BLOCKED",
        "selected_pool": selected,
    }


def _quarantine(findings: list[dict]) -> dict:
    claimed = any(row["epoch"] == "07d" and str(row["disposition"]).upper() == "ACCEPTED" for row in findings)
    return {
        "epoch": "07d",
        "disposition": "AUDIT_ONLY",
        "status": "REJECTED_AUDIT_ONLY" if claimed else "AUDIT_ONLY",
        "accepted_findings": "REJECTED" if claimed else "NONE",
    }


def _share_status(metric_result: dict, key: str, threshold: str) -> str:
    if metric_result["status"] != "COMPLETE":
        return "BLOCKED"
    if metric_result.get("concentration_status") == "AMBIGUOUS":
        return "AMBIGUOUS"
    return compare_le(metric_result.get(key), threshold)


def _hold_status(metric_result: dict, threshold: str) -> str:
    if metric_result["status"] != "COMPLETE" or metric_result.get("median_hold_fraction") is None:
        return "BLOCKED"
    return compare_ge(metric_result["median_hold_fraction"], threshold)


def _lead_status(package_result: dict, cap: int) -> str:
    if package_result["status"] == "REJECTED_OVER_CAP":
        return "REJECTED_OVER_CAP"
    if package_result["truncated"] is not False:
        raise SchemaError("truncation")
    return compare_le(package_result["attempted_leads"], cap)


def _compare(value, threshold, op: str) -> str:
    left = _as_fraction(value)
    right = _as_fraction(threshold)
    if left is None or right is None:
        return "BLOCKED"
    if op == "ge":
        ok = left >= right
    elif op == "gt":
        ok = left > right
    elif op == "le":
        ok = left <= right
    else:
        raise SchemaError("comparison")
    return "SYNTHETIC_BRANCH_PASS" if ok else "SYNTHETIC_BRANCH_FAIL"


def _as_fraction(value):
    if value is None:
        return None
    if isinstance(value, Fraction):
        return value
    return parse_number(value)


def _peek_family_count(doc) -> int | None:
    if isinstance(doc, dict) and isinstance(doc.get("family"), list):
        return len(doc["family"])
    return None


def _interval_admitted(row: dict) -> bool:
    """RK interpretation: inverted and unmarked zero-width epochs are not complete.

    A zero-width epoch is admitted only when width_mark is EMPTY and provenance
    is present. That is not an original positive-width rule.
    """
    start = row.get("start")
    end = row.get("end")
    if isinstance(start, bool) or isinstance(end, bool):
        return False
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    mark = row.get("width_mark", "NONE")
    provenance = row.get("provenance", None)
    if end < start:
        return False
    if end == start:
        return mark == "EMPTY" and isinstance(provenance, str) and provenance != ""
    if mark == "EMPTY":
        return False
    return True


def _ledger_internally_complete(ledger: dict, intervals: dict) -> bool:
    if ledger.get("completeness") != "COMPLETE" or ledger.get("real_dates") is not None:
        return False
    for ident in REQUIRED_EPOCHS:
        row = intervals.get(ident)
        if row is None or not _interval_admitted(row):
            return False
        if row.get("disposition") == "UNKNOWN":
            return False
        if ident == "07d" and row.get("disposition") != "AUDIT_ONLY":
            return False
    return True


def _claim_bounds(claim: dict) -> tuple[int, int] | None:
    start = claim.get("start")
    end = claim.get("end")
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    return start, end


def _overlaps_any(bounds: tuple[int, int], intervals) -> bool:
    start, end = bounds
    for row in intervals:
        if overlaps_half_open(start, end, row["start"], row["end"]):
            return True
    return False


def _public_metrics(metric_result: dict) -> dict:
    return {key: value for key, value in metric_result.items() if not key.endswith("_fraction")}
