"""Real pinned loader and actual publication callsite, without running all reports."""
import ast
import hashlib
import json
from pathlib import Path

import pytest

from research.shadow_model_input import (PATH_ENV, HASH_ENV, load_shadow_model_input,
    first_signal_ts_from_baseline, assert_publication_shadow_model_input)
from research.declared_shadow_model import validate_contract
from test_declared_shadow_scenario_input import GEN, scenario


def pin(tmp_path, monkeypatch, value=None):
    path = tmp_path / "explicit-scenario.json"
    raw = json.dumps(scenario() if value is None else value).encode()
    path.write_bytes(raw)
    monkeypatch.setenv(PATH_ENV, str(path))
    monkeypatch.setenv(HASH_ENV, hashlib.sha256(raw).hexdigest())
    return path, load_shadow_model_input()


def publication_call(source_input, baseline):
    tree = ast.parse(Path(__file__).with_name("analyzer_research_engine_v62.py").read_text(encoding="utf-8-sig"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "write_report_manifest")
    assignment = next(node for node in ast.walk(function)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name) and node.value.func.id == "_write_conservative_shadow_report")
    calls = []
    def writer(*args, **kwargs):
        calls.append(kwargs)
        return {"test": "callsite"}, Path("unused")
    namespace = dict(_write_conservative_shadow_report=writer, shadow_model_input=source_input,
        baseline_replay=baseline, baseline_replay_error=None, policy_cycle_error=None,
        policy_data_dir="unused", policy_report_dir="unused",
        first_signal_ts_from_baseline=first_signal_ts_from_baseline)
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), "actual-publication-callsite", "exec"), namespace)
    return calls


def test_normal_pinned_scenario_reaches_actual_publication_bound_to_this_generation(tmp_path, monkeypatch):
    _, source = pin(tmp_path, monkeypatch)
    baseline = {"generation": GEN, "episode_receipts": [
        {"signal_ts": 102, "results": [{"outcome_state": "FULL_FILL"}]},
        {"signal_ts": 101, "results": [{"outcome_state": "UNKNOWN"}]}]}
    calls = publication_call(source, baseline)
    contract = validate_contract(calls[0]["research_model"], GEN)
    assert contract["first_cohort_signal_ts"] == 101
    assert contract["fee_rates"] == scenario()["fee_rates"]
    assert contract["funding"] == scenario()["funding"]
    assert not contract["qualification_eligible"]
    assert_publication_shadow_model_input({"analysis_provenance": {"shadow_model_input": source.provenance()}})


def test_actual_callsite_does_not_drop_unknown_early_opportunity(tmp_path, monkeypatch):
    _, source = pin(tmp_path, monkeypatch)
    baseline = {"generation": GEN, "episode_receipts": [
        {"signal_ts": 101, "results": [{"outcome_state": "FULL_FILL"}]},
        {"signal_ts": 99, "results": [{"outcome_state": "UNKNOWN"}]}]}
    with pytest.raises(ValueError, match="POST_SIGNAL_DECLARATION"):
        publication_call(source, baseline)


@pytest.mark.parametrize("defect", ["missing_time", "empty_cohort", "bad_epoch", "missing_funding"])
def test_actual_callsite_missing_input_never_produces_contract(tmp_path, monkeypatch, defect):
    value = scenario()
    if defect == "missing_funding": del value["funding"]
    _, source = pin(tmp_path, monkeypatch, value)
    baseline = {"generation": GEN, "episode_receipts": [{"signal_ts": 101}]}
    if defect == "missing_time": baseline["episode_receipts"].append({"results": []})
    if defect == "empty_cohort": baseline["episode_receipts"] = []
    if defect == "bad_epoch": baseline["generation"] = dict(GEN, epoch_id="new-source-epoch")
    with pytest.raises(ValueError): publication_call(source, baseline)


def test_same_pin_rebinds_publication_but_mutation_cannot_publish(tmp_path, monkeypatch):
    path, source = pin(tmp_path, monkeypatch)
    newer = dict(GEN, manifest_entry_hash="f" * 64, generation_key="new-generation")
    baseline = {"generation": newer, "episode_receipts": [{"signal_ts": 101}]}
    assert publication_call(source, baseline)[0]["research_model"]["generation"] == newer
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="INPUT_CHANGED"): source.assert_unchanged()


@pytest.mark.parametrize("bad", [None, True, float("nan"), "101"])
def test_invalid_time_in_any_episode_does_not_shrink_comparison_cohort(bad):
    assert first_signal_ts_from_baseline({"episode_receipts": [{"signal_ts": 101}, {"signal_ts": bad}]}) is None
