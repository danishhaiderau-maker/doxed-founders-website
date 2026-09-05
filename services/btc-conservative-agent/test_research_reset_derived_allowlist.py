import ast
from pathlib import Path

import pytest

from research_reset_inventory import (
    ANALYZER_REPORT_FILES, POLICY_CACHE_FILES, plan_research_reset,
)
from research_reset_execution import execute_research_reset
from test_research_reset_inventory import proof, put


def test_declared_report_allowlist_exactly_matches_engine_without_import():
    source = Path(__file__).with_name("analyzer_research_engine_v62.py")
    values = {}
    for node in ast.parse(source.read_text(encoding="utf-8-sig")).body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = (tuple(values[x.id] if isinstance(x, ast.Name) else ast.literal_eval(x)
                           for x in node.value.elts) if isinstance(node.value, ast.Tuple)
                     else ast.literal_eval(node.value))
        except (ValueError, KeyError, TypeError):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                values[target.id] = value
    assert ANALYZER_REPORT_FILES == frozenset(values["ANALYZER_JSON_REPORT_FILES"])


def test_exact_four_cache_outputs_and_declared_reports_require_epoch_proof(tmp_path):
    names = [f"derived/policy-evidence/generation-{'a'*64}/{name}" for name in POLICY_CACHE_FILES]
    names += ["analyzer/" + name for name in ANALYZER_REPORT_FILES]
    for name in names:
        put(tmp_path, name)
    unproved = plan_research_reset(tmp_path)
    assert unproved["complete"] and not unproved["targets"]
    assert all(x["reason"] == "EPOCH_RECOVERY_BOUNDARY_PROOF_REQUIRED" for x in unproved["retained"])
    planned = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert {x["path"] for x in planned["targets"]} == set(names)


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_cache_sidecars_retain_database(tmp_path, suffix):
    name = f"derived/policy-evidence/generation-{'a'*64}/results.sqlite"
    put(tmp_path, name)
    put(tmp_path, name + suffix)
    planned = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert not planned["targets"]
    row = next(x for x in planned["retained"] if x["path"] == name)
    assert row["reason"] == "POLICY_CACHE_SQLITE_SIDECARS_REQUIRE_OWNER_RESET"


def test_lookalikes_source_accounting_recovery_and_migration_remain_retained(tmp_path):
    names = ["derived/policy-evidence/generation-short/results.sqlite",
        f"derived/policy-evidence/generation-{'a'*64}/other.sqlite",
        f"derived/policy-evidence/generation-{'a'*64}/nested/results.sqlite",
        f"derived/policy-evidence/generation-{'a'*64}/source.py",
        "analyzer/unknown_report.json", "analyzer/nested/ai_calibration_report.json",
        "analyzer/config.json", "analyzer/paper_state.json", "analyzer/recovery.json",
        "analyzer/trades_3factor.csv", "analyzer/analyzer_crash.log", "analyzer/source.py",
        "migration/legacy-mirror-preservation-old/files/order_multiverse.jsonl"]
    for name in names:
        put(tmp_path, name)
    planned = plan_research_reset(tmp_path, proof=proof(tmp_path))
    assert planned["complete"] and not planned["targets"]
    assert {x["path"] for x in planned["retained"]} == set(names)


def test_derived_plan_executes_only_fixture_paths_and_retains_control(tmp_path):
    names = [f"derived/policy-evidence/generation-{'a'*64}/{name}" for name in POLICY_CACHE_FILES]
    names += ["analyzer/ai_calibration_report.json"]
    for name in names:
        put(tmp_path, name)
    control = put(tmp_path, "analyzer/paper_state.json")
    result = execute_research_reset(runtime_root=tmp_path, proof=proof(tmp_path),
        quiescent=True, recovery_states={"fixture_readers": "NOT_PRESENT"},
        receipt_path=tmp_path / "reset_receipt.json")
    assert result["status"] == "COMPLETE"
    assert control.exists()
    assert not any((tmp_path / name).exists() for name in names)
