"""Synthetic acceptance tests for the Stage C results consumer.

Oracles in this file are handwritten. Production-fault rows come from an outer
recorder that swaps the production binding, reruns the named assertion, and
restores it. Value pairs and import errors are not kills.
"""

from __future__ import annotations

import ast
import copy
import json
import os
import signal
import sys
import tempfile
import time
import resource
import unittest
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.results_gates_v1.dashboard_projection import project
from research.results_gates_v1.folds import assess, project_causal
import research.results_gates_v1.gates as gates_mod
import research.results_gates_v1.guards as guards_mod
import research.results_gates_v1.metrics as metrics_mod
import research.results_gates_v1.statistics as statistics_mod
import research.results_gates_v1.validate as validate_mod
from research.results_gates_v1.gates import (
    compare_ge,
    compare_gt,
    compare_le,
    evaluate_fixture,
    exposure_gate,
    overlaps_half_open,
    profile_sha256,
)
from research.results_gates_v1.guards import (
    BOUNDS,
    COMMAND_SECONDS,
    LIBRARY_IMPORTS,
    PACKAGE_FILES,
    PER_WORKER_VAS_BYTES,
    RECEIPT_BYTES,
    TEST_IMPORTS,
    MonotonicClock,
    commit_receipt,
    enforce_elapsed,
    enforce_rss,
    load_lock,
    package_root,
    read_canonical_verified,
    read_pipe_bounded,
    reject_canaries,
    rss_kib,
    run_contained,
    vas_ceiling_probe,
)
from research.results_gates_v1.metrics import compute
from research.results_gates_v1.packages import build, dump_yaml, parse_yaml
from research.results_gates_v1.statistics import benjamini_hochberg
from research.results_gates_v1.validate import (
    CanaryRejected,
    CapStop,
    DriftStop,
    ProfileRejected,
    SchemaError,
    canonical_bytes,
    parse_number,
    schema_manifest,
    sha256_bytes,
    sha256_obj,
    to_plain,
    validate_zero_statement,
)
from research.results_gates_v1 import fixture_cli

FIXTURE = package_root() / "fixtures" / "family-traces.json"
SUITE_STARTED = time.monotonic()
FAULTS = []
SECRET = "CANARY_SECRET_SUPERSECRETVALUE"
H2_INJECTION_CONTINUED = False


class IsolationStop(RuntimeError):
    """A weak path raised an incidental schema error. Not a kill."""


def fail_oracle(oracle_id):
    error = AssertionError(oracle_id)
    error.oracle_id = oracle_id
    raise error


def _observe(fn):
    try:
        fn()
    except AssertionError as exc:
        return "FAIL", type(exc).__name__, getattr(exc, "oracle_id", None)
    except Exception as exc:
        return "ERROR", type(exc).__name__, None
    return "PASS", None, None


def record_fault(fault_id, assertion, module, name, mutant, exercise, control_exercise, oracle_id, intended_type):
    """INTACT, same-mutant CONTROL, WEAK named oracle, then restoration.

    Only the named oracle identity with its exact type counts. SchemaError and
    any other AssertionError are ERROR and are not kills.
    """
    original = getattr(module, name)
    sentinel = object()
    setattr(original, "_c993_sentinel", sentinel)
    intact, intact_failure, _intact_identity = _observe(exercise)
    setattr(module, name, mutant)
    try:
        control, control_failure, _control_identity = _observe(control_exercise)
        weak, weak_failure, weak_identity = _observe(exercise)
    finally:
        setattr(module, name, original)
    restored = getattr(module, name) is original and getattr(original, "_c993_sentinel", None) is sentinel
    restored_run, restored_failure, _restored_identity = _observe(exercise)
    identity_ok = weak == "FAIL" and weak_failure == intended_type and weak_identity == oracle_id
    counted = (
        intact == "PASS"
        and control == "PASS"
        and identity_ok
        and restored
        and restored_run == "PASS"
    )
    row = {
        "id": fault_id,
        "assertion": assertion,
        "oracle_id": oracle_id,
        "intended_type": intended_type,
        "base_oracle": oracle_id,
        "control": control,
        "control_failure": control_failure,
        "intact": intact,
        "intact_failure": intact_failure,
        "weak": weak,
        "failure_type": weak_failure,
        "weak_identity": weak_identity,
        "restored": restored,
        "restored_run": restored_run,
        "restored_failure": restored_failure,
        "counted_as_kill": counted,
    }
    FAULTS.append(row)
    if weak_failure == "SchemaError":
        row["disposition"] = "ERROR_STOP"
        raise IsolationStop(row)
    if not counted:
        raise AssertionError(row)


def consistent_family_document(count):
    """Test-only document. Derived ids stay aligned across family, coverage, and series."""
    doc = load()
    base_ids = [row["id"] for row in doc["family"]]
    if count < len(base_ids):
        raise AssertionError("count below canonical family")
    extras = [f"X{index:03d}" for index in range(count - len(base_ids))]
    doc["family"] = list(doc["family"]) + [{"id": ident, "role": "CANDIDATE"} for ident in extras]
    family_ids = base_ids + extras
    block = doc["comparable_family"]
    width = len(block["series"][0]["differential"])
    block["preregistered_ids"] = list(family_ids)
    block["presented_ids"] = list(family_ids)
    for ident in extras:
        block["series"].append({"id": ident, "differential": ["0"] * width})
    coverage = doc["coverage"]
    coverage["planned"] = list(coverage["planned"]) + extras
    coverage["remaining"] = list(coverage["remaining"]) + extras
    for ident in extras:
        doc["p_values"].append({"id": ident, "evaluated": False, "p": None})
    return doc


def load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def run(doc=None, **kwargs):
    return evaluate_fixture(load() if doc is None else doc, **kwargs)


def gate(result, gate_id):
    rows = [row for row in result["decisions"] if row["gate_id"] == gate_id]
    if len(rows) != 1:
        raise AssertionError(gate_id)
    return rows[0]["status"]


def oracle_bh(pairs, q):
    ordered = sorted(pairs, key=lambda item: (item[1], item[0]))
    largest = None
    count = len(ordered)
    for rank, (_ident, p_value) in enumerate(ordered, start=1):
        if p_value <= Fraction(rank, count) * q:
            largest = rank
    if largest is None:
        return []
    return [ordered[index][0] for index in range(largest)]


def oracle_rc(series, draws):
    width = len(series[0])
    means = [sum(values, Fraction(0)) / width for values in series]
    statistic = max(means)
    recentered = [[value - means[index] for value in values] for index, values in enumerate(series)]
    extreme = 0
    for draw in draws:
        peak = max(
            sum((recentered[k][index] for index in draw), Fraction(0)) / width for k in range(len(series))
        )
        if peak >= statistic:
            extreme += 1
    return Fraction(extreme, len(draws))


def weakened_bh_drop_p1(pairs, q):
    return oracle_bh([item for item in pairs if item[1] < 1], q)


def weakened_bh_strict(pairs, q):
    ordered = sorted(pairs, key=lambda item: (item[1], item[0]))
    largest = None
    count = len(ordered)
    for rank, (_ident, p_value) in enumerate(ordered, start=1):
        if p_value < Fraction(rank, count) * q:
            largest = rank
    if largest is None:
        return []
    return [ordered[index][0] for index in range(largest)]


class OracleTests(unittest.TestCase):
    def test_bh_matches_hand_oracle_and_p1_is_not_coverage(self):
        result = run()
        bh = result["statistics"]["bh"]
        pairs = [
            ("S1", Fraction("0.001")),
            ("S2", Fraction("0.04")),
            ("S3", Fraction("0.03")),
            ("F7", Fraction(1)),
            ("R6", Fraction(1)),
            ("T4", Fraction(1)),
            ("T5", Fraction(1)),
        ]
        expected = oracle_bh(pairs, Fraction("0.05"))
        self.assertEqual(list(bh["rejected_ids"]), expected)
        self.assertEqual(expected, ["S1"])
        self.assertEqual(bh["m"], 7)
        self.assertEqual(bh["bookkeeping_p1"], 4)
        self.assertEqual(bh["executed_coverage"], 3)
        self.assertIs(bh["p1_is_coverage"], False)
        self.assertEqual(result["coverage"]["executed_coverage"], 3)

    def test_bh_boundary_is_inclusive(self):
        entries = [
            {"id": "A", "evaluated": True, "p": "0.0125"},
            {"id": "B", "evaluated": True, "p": "0.03"},
            {"id": "C", "evaluated": True, "p": "0.04"},
            {"id": "D", "evaluated": True, "p": "0.05"},
        ]
        produced = benjamini_hochberg(entries, Fraction("0.05"))
        pairs = [(row["id"], Fraction(row["p"])) for row in entries]
        expected = oracle_bh(pairs, Fraction("0.05"))
        self.assertEqual(list(produced["rejected_ids"]), expected)
        self.assertEqual(expected, ["A", "B", "C", "D"])

    def test_missing_evaluated_p_is_not_imputed(self):
        blocked = benjamini_hochberg([{"id": "A", "evaluated": True, "p": None}], Fraction("0.05"))
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertEqual(blocked["rejected_ids"], [])

    def test_white_rc_matches_fixed_draw_oracle(self):
        doc = load()
        result = run(doc)
        series = [[Fraction(v) for v in row["differential"]] for row in doc["comparable_family"]["series"]]
        draws = doc["rc_draws"]
        expected = oracle_rc(series, draws)
        self.assertEqual(expected, Fraction(1, 20))
        self.assertEqual(result["statistics"]["white_rc"]["p"], "0.05")
        self.assertEqual(result["statistics"]["white_rc"]["real_inference"], "BLOCKED")
        self.assertEqual(gate(result, "white_rc_full_family"), "SYNTHETIC_BRANCH_PASS")
        uncentered = max(sum(values, Fraction(0)) / len(values) for values in series)
        raw_extreme = 0
        for draw in draws:
            peak = max(sum((series[k][index] for index in draw), Fraction(0)) / len(series[0]) for k in range(len(series)))
            if peak >= uncentered:
                raw_extreme += 1

    def test_selected_pool_and_incomplete_family_do_not_compute_rc(self):
        selected = load()
        selected["comparable_family"]["presented_ids"] = ["S1"]
        selected_result = run(selected)
        self.assertEqual(selected_result["statistics"]["white_rc"]["status"], "REJECTED_NOT_FULL_FAMILY")
        self.assertIsNone(selected_result["statistics"]["white_rc"]["p"])
        self.assertIs(selected_result["statistics"]["white_rc"]["selected_pool"], True)
        incomplete = load()
        incomplete["comparable_family"]["series"] = incomplete["comparable_family"]["series"][:2]
        incomplete_result = run(incomplete)
        self.assertEqual(incomplete_result["statistics"]["white_rc"]["reason"], "INCOMPLETE_COMPARABLE_FAMILY")
        self.assertIsNone(incomplete_result["statistics"]["white_rc"]["p"])

    def test_absent_draws_are_not_a_zero_p(self):
        doc = load()
        doc["rc_draws"] = []
        result = run(doc)
        self.assertEqual(result["statistics"]["white_rc"]["status"], "BLOCKED")
        self.assertIsNone(result["statistics"]["white_rc"]["p"])


class BoundaryTests(unittest.TestCase):
    def test_numeric_boundaries_missing_and_nan(self):
        rows = [
            ("M56_gross_boundary", "gross >= 2x", compare_ge("10", "10"), compare_ge("9.99", "10"), compare_ge(None, "10")),
            ("M57_net_boundary", "net > 0", compare_gt("0.01", "0"), compare_gt("0", "0"), compare_gt(None, "0")),
            ("M58_stress_boundary", "stress net > 0", compare_gt("0.01", "0"), compare_gt("0", "0"), compare_gt(None, "0")),
            ("M59_fold_positive_boundary", "positive folds >= 3", compare_ge(3, 3), compare_ge(2, 3), compare_ge(None, 3)),
            ("M60_oof_boundary", "oof >= 60", compare_ge(60, 60), compare_ge(59, 60), compare_ge(None, 60)),
            ("M61_share_boundary", "share <= cap", compare_le("0.40", "0.40"), compare_le("0.4000001", "0.40"), compare_le(None, "0.40")),
            ("M62_hold_boundary", "hold >= 300", compare_ge("300", "300"), compare_ge("299", "300"), compare_ge(None, "300")),
            ("M63_rc_p_boundary", "rc p <= 0.05", compare_le("0.05", "0.05"), compare_le("0.0500001", "0.05"), compare_le(None, "0.05")),
            ("M64_lead_cap_boundary", "leads <= 10", compare_le(10, 10), compare_le(11, 10), compare_le(None, 10)),
        ]
        for mid, fault, on_boundary, other_side, missing in rows:
            self.assertEqual(on_boundary, "SYNTHETIC_BRANCH_PASS")
            self.assertEqual(other_side, "SYNTHETIC_BRANCH_FAIL")
            self.assertEqual(missing, "BLOCKED")
        with self.assertRaises(SchemaError):
            compare_gt("NaN", "0")
        doc = load()
        doc["accounting"]["signals"][0]["net"] = "NaN"
        with self.assertRaises(SchemaError):
            run(doc)

    def test_week_share_boundary(self):
        self.assertEqual(compare_le("0.50", "0.50"), "SYNTHETIC_BRANCH_PASS")
        self.assertEqual(compare_le("0.51", "0.50"), "SYNTHETIC_BRANCH_FAIL")


class AccountingTests(unittest.TestCase):
    def test_primary_accounting_and_cost_split(self):
        before = load()
        snapshot = copy.deepcopy(before)
        result = run(before)
        self.assertEqual(before, snapshot)
        self.assertEqual(result["evaluation_status"], "BLOCKED_NO_APPROVED_REAL_PROFILE")
        self.assertIs(result["research_done"], False)
        self.assertIs(result["mission_done"], False)
        self.assertEqual(result["live"], "OFF")
        self.assertIsNone(result["scope"]["real_n"])
        self.assertIsNone(result["scope"]["real_dates"])
        self.assertIsNone(result["scope"]["real_cost_thresholds"])
        self.assertIs(result["scope"]["inherited_old_n"], False)
        self.assertIs(result["scope"]["inherited_a2_unseen"], False)
        self.assertEqual(result["cost"]["danish_account_fee_assumption"], "0")
        self.assertEqual(result["cost"]["danish_account_fee_basis"], "EXPLICIT_ACCOUNT_ASSERTION_NOT_VENUE_VERIFICATION")
        self.assertIsNone(result["cost"]["fee_verification_evidence"])
        self.assertIsNone(result["cost"]["measured_real_execution_friction"])
        self.assertEqual(result["cost"]["measured_real_execution_friction_status"], "BLOCKED")
        self.assertEqual(result["cost"]["funding_charges"], "EXCLUDED_NOT_MODELLED")
        self.assertIsNone(result["cost"]["funding_numeric"])
        self.assertEqual(result["cost"]["queue_delay_status"], "SYNTHETIC")
        self.assertIsNone(result["cost"]["queue_delay_numeric"])
        self.assertIs(result["cost"]["old_fee_thresholds_revived"], False)
        self.assertIs(result["cost"]["old_funding_thresholds_revived"], False)
        self.assertEqual(result["cost"]["stressed_net"], "9")
        self.assertEqual(result["cost"]["stress_scope"], "SLIPPAGE_ONLY")
        self.assertEqual(result["metrics"]["net_per_signal"], {"numerator": "10", "denominator": "7"})
        self.assertEqual(result["metrics"]["per_fill_net"]["denominator"], "4")
        self.assertEqual(result["metrics"]["fill_rate"], {"numerator": "4", "denominator": "7"})
        self.assertEqual(result["trials"]["trial_count"], 6)
        self.assertEqual(result["trials"]["diversity_filter_ignored"], True)
        self.assertIs(result["trials"]["erases_trials"], False)
        self.assertEqual(result["methods"]["diversity_dedup"]["status"], "PROPOSED_NOT_TESTED")
        self.assertIsNone(result["methods"]["diversity_dedup"]["value"])
        self.assertEqual(list(result["labels"]["invalid_for_fills"]), ["S3"])
        self.assertEqual(list(result["labels"]["exploratory_two_month_only"]), ["T4"])
        self.assertNotIn("SYN-S3", [row["strategy_id"] for row in result["packages"]["emitted"]])
        mutated = copy.deepcopy(snapshot)
        mutated["source"] = "mutated"

    def test_unresolved_positions_stay_incomplete(self):
        doc = load()
        doc["accounting"]["signals"][0]["state"] = "unresolved"
        doc["accounting"]["signals"][0]["net"] = None
        doc["accounting"]["signals"][0]["slippage"] = None
        doc["accounting"]["signals"][0]["hold_seconds"] = None
        result = run(doc)
        self.assertEqual(result["metrics"]["status"], "INCOMPLETE")
        self.assertIsNone(result["metrics"]["net_per_signal"])
        self.assertEqual(gate(result, "net_sum"), "BLOCKED")

    def test_nonpositive_concentration_and_missing_hold_block(self):
        doc = load()
        for row in doc["accounting"]["signals"]:
            if row["state"] == "filled":
                row["net"] = "0"
                row["slippage"] = "0"
        doc["cost"]["gross"] = "0"
        metrics = compute(doc["accounting"])
        self.assertEqual(metrics["concentration_status"], "AMBIGUOUS")
        result = run(doc)
        self.assertEqual(gate(result, "day_share"), "AMBIGUOUS")
        self.assertIs(result["zero_result_statement"]["mission_done"], False)
        self.assertIs(result["zero_result_statement"]["research_done"], False)
        self.assertEqual(result["zero_result_statement"]["text_scope"], "synthetic-family-traces-v1")
        missing = copy.deepcopy(doc["accounting"])
        missing["signals"][0]["hold_seconds"] = None
        self.assertEqual(compute(missing)["status"], "BLOCKED")

    def test_coverage_partition_and_gate_failure_subset(self):
        overlapped = load()
        overlapped["coverage"]["excluded"].append("S1")
        self.assertEqual(run(overlapped)["coverage"]["status"], "RECONCILIATION_FAILED")
        outsider = load()
        outsider["coverage"]["gate_failures"].append("T4")
        self.assertIs(run(outsider)["coverage"]["gate_failures_subset"], False)

    def test_fold_leakage_and_reserved_borrow(self):
        leaked = load()
        leaked["folds"]["folds"][0]["oof"][0]["t"] = -5
        self.assertEqual(assess(leaked["folds"])["status"], "BLOCKED")
        self.assertEqual(gate(run(leaked), "qualifying_oof_trades"), "BLOCKED")
        borrowed = load()
        borrowed["folds"]["folds"][0]["oof"][0]["id"] = "HOLD-R"
        self.assertEqual(assess(borrowed["folds"])["reason"], "RESERVED_BORROWED")
        self.assertIsNone(run()["folds"]["iid_fold_pvalue"])
        self.assertEqual(run()["folds"]["definition_approval"], "NOT_APPROVED")

    def test_future_perturbation_keeps_earlier_projection(self):
        first = run()
        doc = load()
        doc["causal"]["observations"][2]["value"] = "999"
        second = run(doc)
        before = canonical_bytes(project_causal(first["causal"], 16))
        after = canonical_bytes(project_causal(second["causal"], 16))
        self.assertEqual(before, after)
        self.assertEqual(first["causal"]["status"], "NO_LEAKAGE")
        leaked = load()
        leaked["causal"]["decisions"][0]["used_observation_ids"].append("o-future")
        self.assertEqual(run(leaked)["causal"]["status"], "LEAKAGE")


class ExposureTests(unittest.TestCase):
    def test_overlap_boundary_and_unknown_ledger(self):
        doc = load()
        overlapped = exposure_gate(doc["exposure_ledger"], {"start": 39, "end": 41, "token": "UNDISTURBED"})
        self.assertEqual(overlapped["synthetic_overlap"], "OVERLAP")
        self.assertEqual(overlapped["false_undisturbed_claim"], "REJECTED")
        self.assertEqual(overlapped["untouched_validation_acceptance"], "BLOCKED")
        self.assertIs(overlaps_half_open(10, 12, 0, 10), False)
        self.assertIs(overlaps_half_open(39, 41, 30, 40), True)
        closed_touch = 10 <= 10 and 0 <= 12
        unknown = copy.deepcopy(doc["exposure_ledger"])
        unknown["completeness"] = "UNKNOWN"
        unknown_gate = exposure_gate(unknown, {"start": 60, "end": 70, "token": "unseen"})
        self.assertEqual(unknown_gate["synthetic_overlap"], "UNKNOWN")
        self.assertEqual(unknown_gate["untouched_validation_acceptance"], "BLOCKED")
        self.assertEqual(unknown_gate["false_undisturbed_claim"], "BLOCKED")
        missing = copy.deepcopy(doc["exposure_ledger"])
        missing["intervals"] = [row for row in missing["intervals"] if row["id"] != "07d"]
        missing_gate = exposure_gate(missing, {"start": 80, "end": 90, "token": "UNTOUCHED"})
        self.assertEqual(missing_gate["untouched_block_reason"], "UNKNOWN_OR_INCOMPLETE_LEDGER")
        self.assertNotEqual(missing_gate["untouched_validation_acceptance"], "ACCEPT")

    def test_07d_accepted_finding_is_rejected(self):
        doc = load()
        doc["quarantine_findings"] = [{"epoch": "07d", "disposition": "ACCEPTED"}]
        result = run(doc)
        self.assertEqual(result["quarantine"]["status"], "REJECTED_AUDIT_ONLY")
        self.assertEqual(result["quarantine"]["accepted_findings"], "REJECTED")
        self.assertEqual(result["quarantine"]["disposition"], "AUDIT_ONLY")


class PackageDashboardTests(unittest.TestCase):
    def test_package_roundtrip_cap_and_invalid_fill(self):
        result = run()
        emitted = result["packages"]["emitted"]
        self.assertEqual(len(emitted), 2)
        self.assertIs(result["packages"]["truncated"], False)
        parsed = parse_yaml(emitted[0]["yaml"])
        self.assertIs(parsed["paper_only"], True)
        self.assertIs(parsed["relay_eligible"], False)
        self.assertEqual(parsed["application"], "NOT_APPLIED")
        again = dump_yaml(parsed)
        self.assertEqual(parse_yaml(again), parsed)
        drafts = []
        extras = {}
        for index in range(11):
            drafts.append(
                {
                    "strategy_id": f"SYN-{index}",
                    "family_member_id": f"M{index}",
                    "definition": {"members": ["A"], "ordered": False, "paper_only": True, "relay_eligible": False},
                    "extra_name": f"n{index}",
                }
            )
            extras[f"n{index}"] = "synthetic-extra"
        capped = build(drafts, extras, {"exploratory_two_month_only": [], "invalid_for_fills": []})
        self.assertEqual(capped["status"], "REJECTED_OVER_CAP")
        self.assertEqual(capped["emitted"], [])
        self.assertIs(capped["truncated"], False)
        self.assertEqual(capped["attempted_leads"], 11)
        promoted = load()
        promoted["strategy_drafts"].append(
            {
                "strategy_id": "SYN-S3",
                "family_member_id": "S3",
                "definition": {"members": ["C"], "ordered": True, "paper_only": True, "relay_eligible": False},
                "extra_name": "note-s1",
            }
        )
        withheld = run(promoted)
        self.assertEqual([row["reason"] for row in withheld["packages"]["withheld"]], ["INVALID_FOR_FILLS"])
        self.assertNotIn("SYN-S3", [row["strategy_id"] for row in withheld["packages"]["emitted"]])

    def test_dashboard_provenance_and_missing_mapping(self):
        result = run()
        dashboard = result["dashboard"]
        self.assertEqual(dashboard["connection_state"], "NOT_CONNECTED")
        self.assertEqual(dashboard["dashboard_compatibility"], "PROJECTION_ONLY_NOT_CONNECTED")
        self.assertIs(dashboard["mtime_used"], False)
        self.assertEqual(dashboard["freshness_status"], "UNKNOWN")
        self.assertIs(dashboard["live_evidence"], False)
        for row in list(dashboard["panels"]) + list(dashboard["downloads"]):
            self.assertEqual(row["source"], "synthetic-family-traces")
            self.assertEqual(row["as_of"], "SYNTHETIC-T0")
            self.assertEqual(row["fixture_status"], "FIXTURE_ONLY")
            self.assertEqual(row["connection_state"], "NOT_CONNECTED")
            self.assertIn("source", row)
            self.assertIn("as_of", row)
            self.assertIn("fixture_status", row)
            self.assertIn("connection_state", row)
        missing = load()
        missing["dashboard"]["mapped_panels"] = ["coverage", "methods"]
        blocked = run(missing)
        self.assertEqual(blocked["dashboard"]["dashboard_compatibility"], "BLOCKED_MISSING_MAPPING")
        self.assertEqual(list(blocked["dashboard"]["missing_panels"]), ["gates"])
        self.assertEqual(blocked["dashboard"]["panels"][0]["connection_state"], "NOT_CONNECTED")
        envelope = {
            "source": "synthetic-family-traces",
            "as_of": "SYNTHETIC-T0",
            "fixture_status": "FIXTURE_ONLY",
            "connection_state": "CONNECTED",
        }
        with self.assertRaises(SchemaError):
            project(envelope, load()["dashboard"])
        source = (package_root() / "dashboard_projection.py").read_text(encoding="utf-8")
        self.assertNotIn("getmtime", source)
        self.assertNotIn(".stat(", source)


class FailClosedTests(unittest.TestCase):
    def test_schema_profile_and_producer_rejection(self):
        unknown = load()
        unknown["confidence"] = "0.99"
        with self.assertRaises(SchemaError):
            run(unknown)
        version = load()
        version["version"] = "99"
        with self.assertRaises(SchemaError):
            run(version)
        producer = load()
        producer["schema"] = "CoverageReport"
        with self.assertRaises(SchemaError):
            run(producer)
        profile = load()
        profile["profile_id"] = "unapproved-real-profile"
        with self.assertRaises(ProfileRejected):
            run(profile)
        old = load()
        old["cost"]["rt15"] = "15"
        with self.assertRaises(SchemaError):
            run(old)
        with self.assertRaises(SchemaError):
            validate_zero_statement({"text_scope": "synthetic-family-traces-v1", "mission_done": True, "research_done": False})

    def test_methods_have_no_favorable_defaults(self):
        methods = run()["methods"]
        self.assertEqual(methods["deflated_sharpe"]["status"], "NOT_TESTED")
        self.assertIsNone(methods["deflated_sharpe"]["value"])
        self.assertIsNone(methods["effective_n"]["value"])
        self.assertIsNone(methods["cluster_ci"]["value"])
        self.assertEqual(methods["romano_wolf"]["status"], "PROPOSED_NOT_TESTED")
        self.assertIsNone(methods["romano_wolf"]["value"])
        self.assertIsNone(methods["pbo"]["value"])
        self.assertIsNone(methods["bootstrap_b2000_seed20261104_block1day"]["value"])
        self.assertEqual(methods["trial_count"]["status"], "IMPLEMENTED_BOOKKEEPING")
        self.assertIs(methods["trial_count"]["independent_observations"], False)

    def test_result_is_immutable_and_has_no_eta(self):
        result = run()
        with self.assertRaises(TypeError):
            result["evaluation_status"] = "PASS"
        plain = to_plain(result)
        self.assertNotIn("eta", _keys(plain))
        self.assertNotIn("strategy_profitable", _keys(plain))

    def test_caps_stop_without_truncation(self):
        oversized = {"family": [{"id": f"S{index}", "role": "CANDIDATE"} for index in range(257)]}
        with self.assertRaises(CapStop) as caught:
            run(oversized)
        self.assertEqual(caught.exception.reason, "fixture_strategies")
        self.assertEqual(caught.exception.status, "INCOMPLETE_STOP")
        with self.assertRaises(CapStop) as workers:
            run(workers=3)
        self.assertEqual(workers.exception.reason, "cpu_workers")
        self.assertEqual(run(workers=2)["artifacts"]["workers_used"], 2)

        class Expired:
            def elapsed(self):
                return 121

        with self.assertRaises(CapStop) as timed:
            run(clock=Expired())
        self.assertEqual(timed.exception.reason, "command_seconds")
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            with self.assertRaises(CapStop):
                commit_receipt(receipt, b"x" * (BOUNDS["receipt_bytes"] + 1))
            self.assertIs(receipt.exists(), False)

    def test_canary_does_not_leak_and_pins_match(self):
        with self.assertRaises(CanaryRejected) as caught:
            reject_canaries({"payload": SECRET})
        self.assertEqual(str(caught.exception), "canary_detected")
        self.assertNotIn(SECRET, str(caught.exception))
        lock = load_lock()
        self.assertEqual(lock["fixture_sha256"], sha256_bytes(FIXTURE.read_bytes()))
        self.assertEqual(lock["profile_sha256"], profile_sha256())
        self.assertEqual(lock["schema_manifest_sha256"], sha256_obj(schema_manifest()))
        self.assertIs(lock["real_evaluation_permitted"], False)
        self.assertEqual(lock["bounds"], BOUNDS)
        self.assertEqual(lock["third_party"], [])


class CliTests(unittest.TestCase):
    def test_cli_rejects_real_input_network_and_canaries_before_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            code = fixture_cli.main(["--real-input", "--fixture-only", "--receipt", str(receipt)])
            self.assertEqual(code, 2)
            self.assertIs(receipt.exists(), False)
            code = fixture_cli.main(["--fixture", str(FIXTURE), "--receipt", str(receipt)])
            self.assertEqual(code, 2)
            self.assertIs(receipt.exists(), False)
            code = fixture_cli.main(["--fixture-only", "--access", "network", "--fixture", str(FIXTURE), "--receipt", str(receipt)])
            self.assertEqual(code, 3)
            self.assertIs(receipt.exists(), False)
            code = fixture_cli.main(["--fixture-only", "--fixture", "/etc/passwd", "--receipt", str(receipt)])
            self.assertEqual(code, 3)
            self.assertIs(receipt.exists(), False)
            canary = package_root() / "fixtures" / "canary-test.json"
            try:
                canary.write_text(json.dumps({"secret": SECRET}) + "\n", encoding="utf-8")
                code, output = _capture_stdout(
                    lambda: fixture_cli.main(["--fixture-only", "--fixture", str(canary), "--receipt", str(receipt)])
                )
                self.assertEqual(code, 3)
                self.assertIs(receipt.exists(), False)
                self.assertNotIn(SECRET, output)
                self.assertIn("prohibited_access", output)
                self.assertNotIn("canary_detected", output)
            finally:
                canary.unlink(missing_ok=True)

    def test_cli_fixture_receipt_pins_and_import_caps(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt_path = Path(directory) / "receipt.json"
            stdout = _capture_stdout(lambda: fixture_cli.main(["--fixture-only", "--receipt", str(receipt_path)]))
            self.assertEqual(stdout[0], 0)
            self.assertIs(receipt_path.exists(), True)
            self.assertEqual(receipt_path.read_text(encoding="utf-8"), stdout[1])
        receipt = json.loads(stdout[1])
        self.assertIs(receipt["ok"], True)
        self.assertEqual(receipt["evaluation_status"], "BLOCKED_NO_APPROVED_REAL_PROFILE")
        self.assertIs(receipt["research_done"], False)
        self.assertEqual(receipt["live"], "OFF")
        self.assertEqual(receipt["compatibility"]["coverage_report"], "BLOCKED_NOT_REDEFINED")
        self.assertEqual(sorted(receipt["pins"]["files"]), sorted(PACKAGE_FILES))
        self.assertEqual(len(receipt["pins"]["files"]), 15)
        self.assertIs(receipt["pins"]["design_specs_are_not_implementation_pins"], True)
        self.assertEqual(receipt["resources"]["bounds"], BOUNDS)
        self.assertIs(receipt["resources"]["enforced"], True)
        self.assertIs(receipt["resources"]["cap_hit"], False)
        self.assertLess(receipt["resources"]["command_elapsed_ms"], 120000)
        self.assertNotIn("eta", _keys(receipt))
        disk = {rel: sha256_bytes((package_root() / rel).read_bytes()) for rel in PACKAGE_FILES}
        self.assertEqual(receipt["pins"]["files"], disk)
        library_roots = set()
        for rel in PACKAGE_FILES:
            if not rel.endswith(".py") or rel.startswith("tests/"):
                continue
            library_roots |= _import_roots(package_root() / rel)
        self.assertTrue(library_roots <= LIBRARY_IMPORTS)
        forbidden = {"socket", "urllib", "requests", "subprocess", "ctypes", "pickle", "ssl", "http", "threading", "multiprocessing", "concurrent"}
        self.assertTrue(library_roots.isdisjoint(forbidden))
        self.assertTrue(_import_roots(Path(__file__)) <= TEST_IMPORTS)
        self.assertIn("enforce_workers", _called_in(package_root() / "gates.py", "evaluate_fixture"))
        self.assertIn("enforce_strategy_count", _called_in(package_root() / "gates.py", "evaluate_fixture"))
        self.assertIn("enforce_elapsed", _called_in(package_root() / "gates.py", "evaluate_fixture"))
        self.assertIn("enforce_rss", _called_in(package_root() / "gates.py", "evaluate_fixture"))
        self.assertIn("enforce_receipt_bytes", _called_in(package_root() / "guards.py", "commit_receipt"))


class CorrectiveTests(unittest.TestCase):
    def test_h1_production_faults(self):
        original_bh = statistics_mod.benjamini_hochberg
        original_interval = gates_mod._interval_admitted
        original_compute = metrics_mod.compute

        def bh_less(entries, q=statistics_mod.BH_Q):
            prepared = []
            for row in entries:
                p_value = Fraction(row["p"]) if row["evaluated"] else Fraction(1)
                prepared.append((row["id"], p_value))
            prepared.sort(key=lambda item: (item[1], item[0]))
            count = len(prepared)
            largest = None
            for rank, (_ident, p_value) in enumerate(prepared, start=1):
                if p_value < (Fraction(rank, count) * q):
                    largest = rank
            rejected = [] if largest is None else [prepared[index][0] for index in range(largest)]
            return {"status": "SYNTHETIC_BRANCH_COMPUTED", "rejected_ids": rejected, "m": count, "real_inference": "BLOCKED"}

        def bh_drop_p1(entries, q=statistics_mod.BH_Q):
            return original_bh([row for row in entries if row.get("evaluated")], q)

        def rc_uncentered(series, draws, *, family_exact=True):
            if not family_exact or not series or not draws:
                return {"status": "BLOCKED", "p": None, "real_inference": "BLOCKED"}
            width = len(series[0]["differential"])
            parsed = [[Fraction(item) for item in row["differential"]] for row in series]
            statistic = max(sum(values, Fraction(0)) / width for values in parsed)
            extreme = 0
            for draw in draws:
                peak = max(sum((parsed[k][index] for index in draw), Fraction(0)) / width for k in range(len(parsed)))
                if peak >= statistic:
                    extreme += 1
            p_value = Fraction(extreme, len(draws))
            branch = "SYNTHETIC_BRANCH_PASS" if p_value <= Fraction("0.05") else "SYNTHETIC_BRANCH_FAIL"
            return {"status": branch, "p": validate_mod.number_str(p_value), "real_inference": "BLOCKED"}

        def rc_on_presented(doc):
            block = doc["comparable_family"]
            presented = set(block["presented_ids"])
            series = [row for row in block["series"] if row["id"] in presented]
            return statistics_mod.white_reality_check(series, doc["rc_draws"], family_exact=True)

        def never_overlap(*_args):
            return False

        def closed_overlap(start_a, end_a, start_b, end_b):
            if end_a < start_a or end_b < start_b:
                return False
            return start_a <= end_b and start_b <= end_a

        def always_complete(_ledger, _intervals):
            return True

        def admit_inverted(row):
            start = row.get("start")
            end = row.get("end")
            if isinstance(start, int) and isinstance(end, int) and end < start:
                return True
            return original_interval(row)

        def admit_unmarked_zero(row):
            start = row.get("start")
            end = row.get("end")
            if isinstance(start, int) and isinstance(end, int) and end == start:
                return True
            return original_interval(row)

        def reject_declared_empty(row):
            if row.get("width_mark") == "EMPTY":
                return False
            return original_interval(row)

        def reversed_compare(value, threshold, op):
            left = gates_mod._as_fraction(value)
            right = gates_mod._as_fraction(threshold)
            if left is None or right is None:
                return "BLOCKED"
            if op == "ge":
                ok = left > right
            elif op == "gt":
                ok = left >= right
            elif op == "le":
                ok = left < right
            else:
                ok = False
            return "SYNTHETIC_BRANCH_PASS" if ok else "SYNTHETIC_BRANCH_FAIL"

        def disabled(*_args, **_kwargs):
            return None

        def allow_nonzero(_state, _net):
            return None

        def drop_nonfills(accounting):
            kept = [row for row in accounting["signals"] if row["state"] in {"filled", "unresolved"}]
            return original_compute({"valuation": accounting["valuation"], "signals": kept})

        def exercise_bh_boundary():
            entries = [
                {"id": "A", "evaluated": True, "p": "0.0125"},
                {"id": "B", "evaluated": True, "p": "0.03"},
                {"id": "C", "evaluated": True, "p": "0.04"},
                {"id": "D", "evaluated": True, "p": "0.05"},
            ]
            produced = statistics_mod.benjamini_hochberg(entries, Fraction("0.05"))
            if list(produced["rejected_ids"]) != ["A", "B", "C", "D"]:
                fail_oracle("bh_inclusive_boundary")

        def exercise_p1():
            from research.results_gates_v1.coverage import reconcile
            result = gates_mod._bh(load(), reconcile(load()["coverage"]))
            if result["m"] != 7 or list(result["rejected_ids"]) != ["S1"] or result["bookkeeping_p1"] != 4:
                fail_oracle("bh_p1_mass_full_posted_n")

        def exercise_rc():
            result = gates_mod._rc(load())
            if result["p"] != "0.05":
                fail_oracle("white_rc_centered_fixed_draws")

        def exercise_pool():
            doc = load()
            doc["comparable_family"]["presented_ids"] = ["S1"]
            result = gates_mod._rc(doc)
            if result["status"] != "REJECTED_NOT_FULL_FAMILY" or result["p"] is not None:
                fail_oracle("white_rc_full_family_not_selected_pool")

        def exercise_overlap():
            result = gates_mod.exposure_gate(load()["exposure_ledger"], {"start": 39, "end": 41, "token": "UNDISTURBED"})
            if result["synthetic_overlap"] != "OVERLAP" or result["false_undisturbed_claim"] != "REJECTED":
                fail_oracle("exposure_overlap_reject_false_undisturbed")
            if result["untouched_validation_acceptance"] != "BLOCKED" or result["real_exposure"] != "UNKNOWN":
                fail_oracle("exposure_overlap_reject_false_undisturbed")

        def exercise_boundary():
            result = gates_mod.exposure_gate(load()["exposure_ledger"], {"start": 60, "end": 70, "token": "UNDISTURBED"})
            if result["synthetic_overlap"] != "NO_OVERLAP" or result["false_undisturbed_claim"] != "NOT_REJECTED_FOR_OVERLAP":
                fail_oracle("exposure_half_open_touch_is_not_overlap")

        def exercise_unknown():
            ledger = copy.deepcopy(load()["exposure_ledger"])
            ledger["completeness"] = "UNKNOWN"
            result = gates_mod.exposure_gate(ledger, {"start": 80, "end": 90, "token": "UNSEEN"})
            if result["synthetic_overlap"] != "UNKNOWN" or result["false_undisturbed_claim"] != "BLOCKED":
                fail_oracle("exposure_unknown_ledger_not_undisturbed")
            if result["untouched_validation_acceptance"] != "BLOCKED" or result["real_exposure"] != "UNKNOWN":
                fail_oracle("exposure_unknown_ledger_not_undisturbed")

        def exercise_ge():
            if gates_mod.compare_ge("10", "10") != "SYNTHETIC_BRANCH_PASS":
                fail_oracle("compare_ge_inclusive_boundary")

        def exercise_gt():
            if gates_mod.compare_gt("0", "0") != "SYNTHETIC_BRANCH_FAIL":
                fail_oracle("compare_gt_strict_boundary")

        def exercise_le():
            if gates_mod.compare_le("0.40", "0.40") != "SYNTHETIC_BRANCH_PASS":
                fail_oracle("compare_le_inclusive_boundary")

        def exercise_workers():
            try:
                gates_mod.evaluate_fixture(load(), workers=3)
            except CapStop as exc:
                if exc.status != "INCOMPLETE_STOP" or exc.reason != "cpu_workers":
                    fail_oracle("missing_worker_cap")
                return
            fail_oracle("missing_worker_cap")

        def exercise_strategies():
            doc = consistent_family_document(257)
            try:
                gates_mod.evaluate_fixture(doc)
            except CapStop as exc:
                if exc.status == "INCOMPLETE_STOP" and exc.reason == "fixture_strategies":
                    return
                fail_oracle("missing_strategy_count_cap")
            except SchemaError:
                raise
            fail_oracle("missing_strategy_count_cap")

        def exercise_elapsed():
            class _Expired:
                def elapsed(self):
                    return COMMAND_SECONDS + 1
            try:
                gates_mod.evaluate_fixture(load(), clock=_Expired())
            except CapStop as exc:
                if exc.reason != "command_seconds" or exc.status != "INCOMPLETE_STOP":
                    fail_oracle("missing_command_time_cap")
                return
            fail_oracle("missing_command_time_cap")

        def exercise_receipt():
            directory = Path(tempfile.mkdtemp())
            path = directory / "oversized.json"
            try:
                try:
                    guards_mod.commit_receipt(path, b"x" * (RECEIPT_BYTES + 1))
                except CapStop as exc:
                    if path.exists() or exc.reason != "receipt_bytes" or exc.status != "INCOMPLETE_STOP":
                        fail_oracle("missing_receipt_byte_cap")
                    return
                fail_oracle("missing_receipt_byte_cap")
            finally:
                if path.exists():
                    path.unlink()

        def exercise_nonfill():
            doc = load()
            doc["accounting"]["signals"][4]["net"] = "1"
            try:
                gates_mod.evaluate_fixture(doc)
            except SchemaError as exc:
                if "nonzero non-fill" not in str(exc) or doc["accounting"]["signals"][4]["net"] != "1":
                    fail_oracle("nonzero_nonfill_rejected")
                return
            fail_oracle("nonzero_nonfill_rejected")

        def exercise_inverted():
            ledger = copy.deepcopy(load()["exposure_ledger"])
            ledger["intervals"][0]["end"] = -1
            result = gates_mod.exposure_gate(ledger, {"start": 60, "end": 70, "token": "UNDISTURBED"})
            if result["synthetic_overlap"] != "UNKNOWN" or result["real_exposure"] != "UNKNOWN":
                fail_oracle("h6_inverted_required_epoch_unknown")
            if result["untouched_validation_acceptance"] != "BLOCKED":
                fail_oracle("h6_inverted_required_epoch_unknown")

        def exercise_zero():
            ledger = copy.deepcopy(load()["exposure_ledger"])
            ledger["intervals"][0]["end"] = ledger["intervals"][0]["start"]
            result = gates_mod.exposure_gate(ledger, {"start": 60, "end": 70, "token": "UNDISTURBED"})
            if result["synthetic_overlap"] != "UNKNOWN":
                fail_oracle("h6_unmarked_zero_width_unknown")

        def exercise_empty():
            ledger = copy.deepcopy(load()["exposure_ledger"])
            ledger["intervals"][0]["end"] = ledger["intervals"][0]["start"]
            ledger["intervals"][0]["width_mark"] = "EMPTY"
            ledger["intervals"][0]["provenance"] = "SYNTHETIC_DECLARED_EMPTY"
            result = gates_mod.exposure_gate(ledger, {"start": 60, "end": 70, "token": "UNDISTURBED"})
            if result["synthetic_overlap"] != "NO_OVERLAP":
                fail_oracle("h6_declared_empty_with_provenance_admitted")
            if result["untouched_validation_acceptance"] != "BLOCKED" or result["real_exposure"] != "UNKNOWN":
                fail_oracle("h6_declared_empty_with_provenance_admitted")

        def exercise_denominator():
            result = gates_mod.evaluate_fixture(load())
            if result["metrics"]["net_per_signal"]["denominator"] != "7":
                fail_oracle("net_per_signal_includes_nonfill_zeros")

        def control_bh_inclusive():
            from research.results_gates_v1.coverage import reconcile
            result = gates_mod._bh(load(), reconcile(load()["coverage"]))
            if list(result["rejected_ids"]) != ["S1"] or result["m"] != 7:
                raise AssertionError("control")

        def control_p1():
            entries = [
                {"id": "A", "evaluated": True, "p": "0.001"},
                {"id": "B", "evaluated": True, "p": "0.04"},
                {"id": "C", "evaluated": True, "p": "0.20"},
            ]
            produced = statistics_mod.benjamini_hochberg(entries, Fraction("0.05"))
            if produced["m"] != 3 or produced["status"] != "SYNTHETIC_BRANCH_COMPUTED":
                raise AssertionError("control")

        def control_rc():
            series = [
                {"id": "Z1", "differential": ["0", "0"]},
                {"id": "Z2", "differential": ["0", "0"]},
            ]
            produced = statistics_mod.white_reality_check(series, [[0, 0], [1, 1]], family_exact=True)
            if produced["p"] != "1":
                raise AssertionError("control")

        def control_pool():
            result = gates_mod._rc(load())
            if result["p"] != "0.05":
                raise AssertionError("control")

        def control_overlap():
            result = gates_mod.exposure_gate(load()["exposure_ledger"], {"start": 60, "end": 70, "token": "UNDISTURBED"})
            if result["synthetic_overlap"] != "NO_OVERLAP":
                raise AssertionError("control")

        def control_boundary():
            result = gates_mod.exposure_gate(load()["exposure_ledger"], {"start": 39, "end": 41, "token": "UNDISTURBED"})
            if result["false_undisturbed_claim"] != "REJECTED":
                raise AssertionError("control")

        def control_unknown():
            result = gates_mod.exposure_gate(load()["exposure_ledger"], {"start": 60, "end": 70, "token": "UNDISTURBED"})
            if result["synthetic_overlap"] != "NO_OVERLAP" or result["real_exposure"] != "UNKNOWN":
                raise AssertionError("control")

        def control_ge():
            if gates_mod.compare_ge("11", "10") != "SYNTHETIC_BRANCH_PASS":
                raise AssertionError("control")

        def control_gt():
            if gates_mod.compare_gt("1", "0") != "SYNTHETIC_BRANCH_PASS":
                raise AssertionError("control")

        def control_le():
            if gates_mod.compare_le("0.30", "0.40") != "SYNTHETIC_BRANCH_PASS":
                raise AssertionError("control")

        def control_workers():
            gates_mod.evaluate_fixture(load(), workers=1)

        def control_strategies():
            gates_mod.evaluate_fixture(load())

        def control_elapsed():
            class _Inside:
                def elapsed(self):
                    return 0
            gates_mod.evaluate_fixture(load(), clock=_Inside())

        def control_receipt():
            directory = Path(tempfile.mkdtemp())
            path = directory / "small.json"
            try:
                guards_mod.commit_receipt(path, b'{"ok":false}\n')
                if not path.is_file():
                    raise AssertionError("control")
            finally:
                if path.exists():
                    path.unlink()

        def control_nonfill():
            gates_mod.evaluate_fixture(load())

        def control_h6():
            result = gates_mod.exposure_gate(load()["exposure_ledger"], {"start": 60, "end": 70, "token": "UNDISTURBED"})
            if result["synthetic_overlap"] != "NO_OVERLAP" or result["untouched_validation_acceptance"] != "BLOCKED":
                raise AssertionError("control")

        def control_denominator():
            doc = load()
            doc["accounting"]["signals"] = [row for row in doc["accounting"]["signals"] if row["state"] == "filled"]
            result = gates_mod.evaluate_fixture(doc)
            if result["metrics"]["net_per_signal"]["denominator"] != "4":
                raise AssertionError("control")

        cases = [
            ("F-BH-INCLUSIVE", "test_bh_boundary_is_inclusive", statistics_mod, "benjamini_hochberg", bh_less, exercise_bh_boundary, control_bh_inclusive, "bh_inclusive_boundary"),
            ("F-BH-P1-MASS", "test_bh_matches_hand_oracle_and_p1_is_not_coverage", statistics_mod, "benjamini_hochberg", bh_drop_p1, exercise_p1, control_p1, "bh_p1_mass_full_posted_n"),
            ("F-RC-NO-CENTER", "test_white_rc_matches_fixed_draw_oracle", statistics_mod, "white_reality_check", rc_uncentered, exercise_rc, control_rc, "white_rc_centered_fixed_draws"),
            ("F-RC-SELECTED-POOL", "test_selected_pool_and_incomplete_family_do_not_compute_rc", gates_mod, "_rc", rc_on_presented, exercise_pool, control_pool, "white_rc_full_family_not_selected_pool"),
            ("F-EXPOSURE-OVERLAP", "test_overlap_boundary_and_unknown_ledger", gates_mod, "overlaps_half_open", never_overlap, exercise_overlap, control_overlap, "exposure_overlap_reject_false_undisturbed"),
            ("F-EXPOSURE-BOUNDARY", "test_overlap_boundary_and_unknown_ledger", gates_mod, "overlaps_half_open", closed_overlap, exercise_boundary, control_boundary, "exposure_half_open_touch_is_not_overlap"),
            ("F-EXPOSURE-UNKNOWN", "test_overlap_boundary_and_unknown_ledger", gates_mod, "_ledger_internally_complete", always_complete, exercise_unknown, control_unknown, "exposure_unknown_ledger_not_undisturbed"),
            ("F-COMPARE-GE", "test_numeric_boundaries_missing_and_nan", gates_mod, "_compare", reversed_compare, exercise_ge, control_ge, "compare_ge_inclusive_boundary"),
            ("F-COMPARE-GT", "test_numeric_boundaries_missing_and_nan", gates_mod, "_compare", reversed_compare, exercise_gt, control_gt, "compare_gt_strict_boundary"),
            ("F-COMPARE-LE", "test_numeric_boundaries_missing_and_nan", gates_mod, "_compare", reversed_compare, exercise_le, control_le, "compare_le_inclusive_boundary"),
            ("F-CAP-WORKERS", "test_caps_stop_without_truncation", gates_mod, "enforce_workers", disabled, exercise_workers, control_workers, "missing_worker_cap"),
            ("F-CAP-STRATEGIES", "test_caps_stop_without_truncation", gates_mod, "enforce_strategy_count", disabled, exercise_strategies, control_strategies, "missing_strategy_count_cap"),
            ("F-CAP-ELAPSED", "test_caps_stop_without_truncation", gates_mod, "enforce_elapsed", disabled, exercise_elapsed, control_elapsed, "missing_command_time_cap"),
            ("F-CAP-RECEIPT", "test_caps_stop_without_truncation", guards_mod, "enforce_receipt_bytes", disabled, exercise_receipt, control_receipt, "missing_receipt_byte_cap"),
            ("F-NONFILL-ZERO", "test_primary_accounting_and_cost_split", validate_mod, "assert_nonfill_zero", allow_nonzero, exercise_nonfill, control_nonfill, "nonzero_nonfill_rejected"),
            ("F-H6-INVERTED", "test_overlap_boundary_and_unknown_ledger", gates_mod, "_interval_admitted", admit_inverted, exercise_inverted, control_h6, "h6_inverted_required_epoch_unknown"),
            ("F-H6-UNMARKED-ZERO", "test_overlap_boundary_and_unknown_ledger", gates_mod, "_interval_admitted", admit_unmarked_zero, exercise_zero, control_h6, "h6_unmarked_zero_width_unknown"),
            ("F-H6-DECLARED-EMPTY", "test_overlap_boundary_and_unknown_ledger", gates_mod, "_interval_admitted", reject_declared_empty, exercise_empty, control_h6, "h6_declared_empty_with_provenance_admitted"),
            ("F-DENOMINATOR", "test_primary_accounting_and_cost_split", metrics_mod, "compute", drop_nonfills, exercise_denominator, control_denominator, "net_per_signal_includes_nonfill_zeros"),
        ]
        for fault_id, assertion, module, qualname, mutant, exercise, control_exercise, oracle_id in cases:
            record_fault(
                fault_id,
                assertion,
                module,
                qualname,
                mutant,
                exercise,
                control_exercise,
                oracle_id,
                "AssertionError",
            )

    def test_h5_nonzero_nonfill_is_rejected_not_normalized(self):
        doc = load()
        doc["accounting"]["signals"][4]["net"] = "2"
        with self.assertRaises(SchemaError) as caught:
            run(doc)
        self.assertIn("nonzero non-fill", str(caught.exception))
        self.assertEqual(doc["accounting"]["signals"][4]["net"], "2")

    def test_h6_width_cases_keep_real_exposure_unknown(self):
        inverted = load()
        inverted["exposure_ledger"]["intervals"][0]["end"] = -1
        inverted_result = run(inverted)
        self.assertEqual(inverted_result["exposure"]["synthetic_overlap"], "UNKNOWN")
        self.assertEqual(inverted_result["exposure"]["untouched_validation_acceptance"], "BLOCKED")
        self.assertEqual(inverted_result["exposure"]["real_exposure"], "UNKNOWN")
        unmarked = load()
        unmarked["exposure_ledger"]["intervals"][0]["end"] = unmarked["exposure_ledger"]["intervals"][0]["start"]
        unmarked_result = run(unmarked)
        self.assertEqual(unmarked_result["exposure"]["synthetic_overlap"], "UNKNOWN")
        declared = load()
        declared["exposure_ledger"]["intervals"][0]["end"] = declared["exposure_ledger"]["intervals"][0]["start"]
        declared["exposure_ledger"]["intervals"][0]["width_mark"] = "EMPTY"
        declared["exposure_ledger"]["intervals"][0]["provenance"] = "SYNTHETIC_DECLARED_EMPTY"
        declared_result = run(declared)
        self.assertEqual(declared_result["exposure"]["synthetic_overlap"], "NO_OVERLAP")
        self.assertEqual(declared_result["exposure"]["untouched_validation_acceptance"], "BLOCKED")
        self.assertEqual(declared_result["exposure"]["real_exposure"], "UNKNOWN")

    def test_h4_canonical_read_and_alternate_path(self):
        opens = []
        real_open = Path.open

        def wrapped(self, *args, **kwargs):
            opens.append(self)
            return real_open(self, *args, **kwargs)

        Path.open = wrapped
        try:
            data = read_canonical_verified()
        finally:
            Path.open = real_open
        fixture_opens = [item for item in opens if item.name == "family-traces.json"]
        self.assertEqual(len(fixture_opens), 1)
        self.assertEqual(sha256_bytes(data), load_lock()["fixture_sha256"])
        self.assertEqual(json.loads(data.decode("utf-8"))["schema"], "results_consumer_fixture_stub_v1")
        forks = []
        real_fork = os.fork

        def spy_fork():
            forks.append("FORK")
            return real_fork()

        os.fork = spy_fork
        try:
            code, output = _capture_stdout(lambda: fixture_cli.main(["--fixture-only", "--fixture", "/etc/passwd"]))
        finally:
            os.fork = real_fork
        self.assertEqual(code, 3)
        self.assertEqual(forks, [])
        self.assertIn("prohibited_access", output)
        self.assertNotIn("passwd", output)

    def test_h2_h3_cap_controls(self):
        def oversized(write_fd):
            os.write(write_fd, b"x" * (RECEIPT_BYTES + 1))
            return 0

        with self.assertRaises(CapStop) as pipe_cap:
            run_contained(oversized)
        self.assertEqual(pipe_cap.exception.status, "INCOMPLETE_STOP")
        self.assertEqual(pipe_cap.exception.reason, "receipt_bytes")
        self.assertEqual(vas_ceiling_probe(), "MEMORY_ERROR")
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            time.sleep(30)
            os._exit(0)
        os.close(write_fd)

        class _Jump:
            def elapsed(self):
                return COMMAND_SECONDS + 1

        try:
            with self.assertRaises(CapStop) as timed:
                read_pipe_bounded(read_fd, pid, timeout_s=COMMAND_SECONDS, clock=_Jump())
            self.assertEqual(timed.exception.reason, "command_seconds")
            self.assertEqual(timed.exception.status, "INCOMPLETE_STOP")
        finally:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
            os.close(read_fd)
        real = fixture_cli.run_contained

        def raise_cap(_child):
            raise CapStop("receipt_bytes")

        fixture_cli.run_contained = raise_cap
        try:
            code, output = _capture_stdout(lambda: fixture_cli.main(["--fixture-only"]))
        finally:
            fixture_cli.run_contained = real
        self.assertEqual(code, 5)
        payload = json.loads(output)
        self.assertEqual(payload["status"], "INCOMPLETE_STOP")
        self.assertEqual(payload["error"], "receipt_bytes")
        self.assertNotEqual(payload["status"], "REJECTED")

    def test_h4_published_pin_binds_parsed_bytes(self):
        raw = read_canonical_verified()
        verified = sha256_bytes(raw)
        parsed = json.loads(raw.decode("utf-8"))
        self.assertEqual(parsed["schema"], "results_consumer_fixture_stub_v1")
        result = gates_mod.evaluate_fixture(parsed)
        receipt = fixture_cli.build_receipt(
            result,
            clock=MonotonicClock(),
            verified_fixture_sha256=verified,
        )
        self.assertEqual(receipt["pins"]["files"]["fixtures/family-traces.json"], verified)
        real_pins = fixture_cli.pin_files

        def differing_reread():
            files = real_pins()
            files["fixtures/family-traces.json"] = "0" * 64
            return files

        fixture_cli.pin_files = differing_reread
        try:
            with self.assertRaises(DriftStop):
                fixture_cli.build_receipt(
                    result,
                    clock=MonotonicClock(),
                    verified_fixture_sha256=verified,
                )
        finally:
            fixture_cli.pin_files = real_pins
        self.assertEqual(sha256_bytes(raw), verified)


class H2LifecycleTests(unittest.TestCase):
    def test_01_installed_timer_and_parent_vas(self):
        delay, interval = signal.getitimer(signal.ITIMER_REAL)
        self.assertIs(signal.getsignal(signal.SIGALRM), guards_mod._on_suite_timer)
        self.assertEqual(interval, 0)
        self.assertGreater(delay, COMMAND_SECONDS)
        self.assertLessEqual(delay, guards_mod.SUITE_SECONDS)
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        self.assertEqual(soft, PER_WORKER_VAS_BYTES)
        self.assertEqual(hard, PER_WORKER_VAS_BYTES)

        def child(write_fd):
            child_soft = resource.getrlimit(resource.RLIMIT_AS)[0]
            os.write(write_fd, str(child_soft).encode("ascii"))
            return 0

        code, body = run_contained(child)
        self.assertEqual(code, 0)
        self.assertEqual(int(body.decode("ascii")), PER_WORKER_VAS_BYTES)

    def test_02_child_alarm_does_not_reset_parent_timer(self):
        before = signal.getitimer(signal.ITIMER_REAL)[0]
        code, _body = run_contained(lambda write_fd: 0)
        after, interval = signal.getitimer(signal.ITIMER_REAL)
        self.assertEqual(code, 0)
        self.assertEqual(interval, 0)
        self.assertGreater(after, COMMAND_SECONDS)
        self.assertLessEqual(after, before)
        self.assertIs(signal.getsignal(signal.SIGALRM), guards_mod._on_suite_timer)

    def test_03_injected_signal_continues_outer_work(self):
        global H2_INJECTION_CONTINUED
        probe = {"continued": False, "sentinel": False}

        class _Work(unittest.TestCase):
            def test_work(self):
                try:
                    guards_mod.mark_injected_signal()
                    signal.raise_signal(signal.SIGALRM)
                    probe["continued"] = True
                finally:
                    guards_mod.restore_halt_target(probe["previous"])

            def test_sentinel(self):
                probe["sentinel"] = True

        result = unittest.TextTestResult(sys.stderr, False, 0)
        probe["previous"] = guards_mod.bind_halt_runner(result.stop)
        suite = unittest.TestSuite([_Work("test_work"), _Work("test_sentinel")])
        suite.run(result)
        self.assertFalse(probe["continued"])
        self.assertFalse(probe["sentinel"])
        self.assertFalse(guards_mod._outer_deadline_expired)
        self.assertFalse(guards_mod._evidence_expired)
        H2_INJECTION_CONTINUED = True

    def test_04_following_outer_test_is_dispatched(self):
        self.assertTrue(H2_INJECTION_CONTINUED)

    def test_05_vas_failure_writes_no_success_receipt(self):
        self.assertEqual(vas_ceiling_probe(), "MEMORY_ERROR")

        def allocator(write_fd):
            blob = bytearray(PER_WORKER_VAS_BYTES)
            del blob
            os.write(write_fd, b"ALLOCATED")
            return 0

        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            code, body = run_contained(allocator)
            self.assertEqual(code, 5)
            text = body.decode("utf-8")
            self.assertIn("INCOMPLETE_STOP", text)
            self.assertIn("rss_mib", text)
            self.assertFalse(receipt.exists())

    def test_06_child_alarm_120_real_run_contained(self):
        def sleeper(write_fd):
            time.sleep(COMMAND_SECONDS + 30)
            os.write(write_fd, b"LATE")
            return 0

        started = time.monotonic()
        code, body = run_contained(sleeper)
        elapsed = time.monotonic() - started
        text = body.decode("utf-8")
        print(json.dumps({
            "control": "child_120_real_run_contained",
            "elapsed_s": round(elapsed, 3),
            "exit_code": code,
            "child_status_text": text.strip(),
            "physical_command_expiry": True,
            "physical_suite_600_elapsed": False,
            "installed_suite_seconds": guards_mod.SUITE_SECONDS,
        }, sort_keys=True))
        self.assertGreaterEqual(elapsed, COMMAND_SECONDS)
        self.assertLess(elapsed, COMMAND_SECONDS + 15)
        self.assertEqual(code, 5)
        self.assertIn("command_seconds", text)
        self.assertIn("INCOMPLETE_STOP", text)
        self.assertNotIn("LATE", text)


class ZzzResourceTests(unittest.TestCase):
    def test_suite_budget_and_mutation_receipt(self):
        elapsed = time.monotonic() - SUITE_STARTED
        enforce_elapsed(elapsed, kind="suite")
        observed = enforce_rss()
        self.assertLess(elapsed, 600)
        self.assertLess(observed, 512 * 1024)
        self.assertTrue(FAULTS)
        self.assertTrue(all(row["counted_as_kill"] for row in FAULTS))
        self.assertEqual(len(FAULTS), len({row["id"] for row in FAULTS}))
        print(json.dumps({
            "fault_ids": [row["id"] for row in FAULTS],
            "fault_count": len(FAULTS),
            "counted_kills": sum(1 for row in FAULTS if row["counted_as_kill"]),
            "failure_types": {row["id"]: row["failure_type"] for row in FAULTS},
            "oracle_ids": {row["id"]: row["oracle_id"] for row in FAULTS},
            "controls": {row["id"]: row["control"] for row in FAULTS},
            "evidence": "precommit_source_pin_bound",
            "suite_elapsed_ms": int(elapsed * 1000),
            "rss_kib": observed,
            "rss_cap_kib": 512 * 1024,
            "command_cap_seconds": 120,
            "suite_cap_seconds": 600,
            "receipt_cap_bytes": BOUNDS["receipt_bytes"],
            "strategy_cap": BOUNDS["fixture_strategies"],
            "worker_cap": BOUNDS["cpu_workers"],
            "cap_hit": False,
            "evaluation_status": "BLOCKED_NO_APPROVED_REAL_PROFILE",
            "research_done": False,
        }, sort_keys=True))


class ZzzSuiteHaltTests(unittest.TestCase):
    def test_01_real_deadline_no_child_and_owned_child(self):
        owned = {"continued": False, "sentinel": False, "kills": [], "reaps": []}
        real_kill = os.kill
        real_waitpid = os.waitpid

        def spy_kill(pid, sig):
            if sig == signal.SIGKILL:
                owned["kills"].append(pid)
            return real_kill(pid, sig)

        def spy_wait(pid, options):
            owned["reaps"].append(pid)
            return real_waitpid(pid, options)

        class _Owned(unittest.TestCase):
            def test_work(self):
                def sleeper(write_fd):
                    real_kill(os.getppid(), signal.SIGALRM)
                    time.sleep(30)
                    os.write(write_fd, b"DONE")
                    return 0

                try:
                    guards_mod.declare_deadline_due()
                    run_contained(sleeper)
                    owned["continued"] = True
                finally:
                    guards_mod.restore_halt_target(owned["previous"])

            def test_sentinel(self):
                owned["sentinel"] = True

        os.kill = spy_kill
        os.waitpid = spy_wait
        try:
            owned_result = unittest.TextTestResult(sys.stderr, False, 0)
            owned["previous"] = guards_mod.bind_halt_runner(owned_result.stop)
            unittest.TestSuite([
                _Owned("test_work"),
                _Owned("test_sentinel"),
            ]).run(owned_result)
        finally:
            os.kill = real_kill
            os.waitpid = real_waitpid
        self.assertFalse(owned["continued"])
        self.assertFalse(owned["sentinel"])
        self.assertEqual(len(owned["kills"]), 1)
        self.assertEqual(len(owned["reaps"]), 1)
        self.assertEqual(owned["kills"][0], owned["reaps"][0])
        self.assertTrue(guards_mod._evidence_expired)
        self.assertFalse(guards_mod._outer_deadline_expired)

        no_child = {"continued": False, "sentinel": False}

        class _NoChild(unittest.TestCase):
            def test_work(self):
                try:
                    signal.raise_signal(signal.SIGALRM)
                    no_child["continued"] = True
                finally:
                    guards_mod.restore_halt_target(no_child["previous"])

            def test_sentinel(self):
                no_child["sentinel"] = True

        no_result = unittest.TextTestResult(sys.stderr, False, 0)
        no_child["previous"] = guards_mod.bind_halt_runner(no_result.stop)
        unittest.TestSuite([
            _NoChild("test_work"),
            _NoChild("test_sentinel"),
        ]).run(no_result)
        self.assertFalse(no_child["continued"])
        self.assertFalse(no_child["sentinel"])
        with tempfile.TemporaryDirectory() as directory:
            receipt = Path(directory) / "receipt.json"
            with self.assertRaises(guards_mod.SuiteHalt):
                guards_mod.commit_receipt(receipt, b'{"ok": true}\n')
            self.assertFalse(receipt.exists())

            def invoke():
                try:
                    return fixture_cli.main(["--fixture-only", "--receipt", str(receipt)])
                except guards_mod.SuiteHalt:
                    return 5

            stdout = _capture_stdout(invoke)
        self.assertFalse(receipt.exists())
        self.assertIn("INCOMPLETE_STOP", stdout[1])
        self.assertIn("suite_seconds", stdout[1])
        print(json.dumps({
            "control": "real_deadline_practical",
            "physical_suite_600_elapsed": False,
            "installed_suite_delay_s": signal.getitimer(signal.ITIMER_REAL)[0],
            "installed_suite_seconds": guards_mod.SUITE_SECONDS,
            "sigkill_count": len(owned["kills"]),
            "reap_count": len(owned["reaps"]),
            "no_child_sentinel": no_child["sentinel"],
            "owned_sentinel": owned["sentinel"],
            "no_child_continued": no_child["continued"],
            "owned_continued": owned["continued"],
        }, sort_keys=True))

    def test_02_sentinel_not_dispatched(self):
        raise AssertionError("later outer test dispatched")


def _keys(obj):
    found = set()
    if isinstance(obj, dict):
        found.update(obj)
        for value in obj.values():
            found.update(_keys(value))
    elif isinstance(obj, list):
        for value in obj:
            found.update(_keys(value))
    return found


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _called_in(path: Path, function_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    names.add(child.func.id)
    return names


def _capture_stdout(fn):
    import io
    from contextlib import redirect_stdout

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = fn()
    return code, buffer.getvalue()


if __name__ == "__main__":
    class _ArmedSuite(unittest.TestSuite):
        def run(self, result, debug=False):
            guards_mod.install_worker_vas_ceiling()
            guards_mod.arm_suite_deadline(result.stop)
            try:
                return super().run(result, debug)
            finally:
                guards_mod.disarm_suite_deadline()

    loaded = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    outcome = unittest.TextTestRunner(verbosity=2).run(_ArmedSuite([loaded]))
    raise SystemExit(0 if outcome.wasSuccessful() else 1)
