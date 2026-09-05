from copy import deepcopy
import hashlib
import json

import pytest

from research.declared_shadow_model import validate_contract
from research.declared_shadow_scenario_input import SCHEMA, SOURCE_FIELDS, bind_declared_shadow_scenario


GEN = {"manifest_entry_hash": "a" * 64, "epoch_id": "epoch-test", "source_revision": "b" * 40,
       "deployed_revision": "b" * 40, "tile_config_signature": "c" * 64,
       "analyzer_revision": "d" * 40, "evaluator_version": "test-v1", "generation_key": "first-generation"}


def scenario():
    return {"schema": SCHEMA, "source_identity": {key: GEN[key] for key in SOURCE_FIELDS},
            "declared_at_ts": 100, "evidence_basis": "DECLARED_SIMULATION",
            "model_id": "explicit-fee-funding-scenario", "provenance": "TEST_EXPLICIT_ASSUMPTIONS",
            "fee_rates": {"entry": .002, "exit": .002},
            "funding": {"treatment": "CONSTANT_ENTRY_NOTIONAL_RATE", "rate_per_hour": .0001},
            "latency": {"treatment": "PRESERVE_BASELINE_TIMING", "additional_latency_sec": 0}}


def bind(value=None, *, generation=None, first=101):
    raw = json.dumps(scenario() if value is None else value).encode()
    return bind_declared_shadow_scenario(raw, expected_sha256=hashlib.sha256(raw).hexdigest(),
        expected_generation=GEN if generation is None else generation, first_signal_ts=first)


def test_explicit_costs_preserved_and_contract_bound_to_actual_generation():
    source = scenario()
    before = deepcopy(source)
    result = bind(source)
    assert result["status"] == "DECLARED_MODEL_READY"
    contract = validate_contract(result["contract"], GEN)
    assert contract["fee_rates"] == source["fee_rates"]
    assert contract["funding"] == source["funding"]
    assert contract["source_config_sha256"] == result["scenario_input_sha256"]
    assert source == before
    assert not contract["qualification_eligible"] and not result["live_arming_authorized"]


def test_new_publication_can_rebind_same_explicit_scenario_not_a_new_source_epoch():
    first = bind()
    updated = dict(GEN, generation_key="second-generation", manifest_entry_hash="e" * 64)
    second = bind(generation=updated)
    assert second["status"] == "DECLARED_MODEL_READY"
    assert second["contract"]["signature"] != first["contract"]["signature"]
    assert second["scenario_input_sha256"] == first["scenario_input_sha256"]
    assert bind(generation=dict(GEN, epoch_id="new-epoch"))["status"] == "UNKNOWN"


@pytest.mark.parametrize("field", ["fee_rates", "funding", "latency", "declared_at_ts", "source_identity", "provenance"])
def test_missing_input_is_unknown_not_a_default(field):
    value = scenario()
    del value[field]
    result = bind(value)
    assert result["status"] == "UNKNOWN" and result["contract"] is None


@pytest.mark.parametrize("defect", ["epoch", "revision", "config", "postsignal", "badfee", "nonzero_latency", "funding_treatment"])
def test_mismatch_or_unsupported_assumptions_fail_closed(defect):
    value = scenario()
    if defect == "epoch": value["source_identity"]["epoch_id"] = "wrong"
    if defect == "revision": value["source_identity"]["source_revision"] = "f" * 40
    if defect == "config": value["source_identity"]["tile_config_signature"] = "f" * 64
    if defect == "postsignal": value["declared_at_ts"] = 102
    if defect == "badfee": value["fee_rates"]["entry"] = -1
    if defect == "nonzero_latency": value["latency"]["additional_latency_sec"] = 1
    if defect == "funding_treatment": value["funding"] = {"treatment": "UNKNOWN"}
    assert bind(value)["status"] == "UNKNOWN"


@pytest.mark.parametrize("first", [None, True, float("nan"), float("inf"), -1])
def test_missing_causal_cohort_time_rejected(first):
    assert bind(first=first)["status"] == "UNKNOWN"


def test_pinned_hash_and_duplicate_fields_rejected():
    raw = json.dumps(scenario()).encode()
    assert bind_declared_shadow_scenario(raw, expected_sha256="0" * 64,
        expected_generation=GEN, first_signal_ts=101)["status"] == "UNKNOWN"
    duplicate = b'{"schema":"first","schema":"second"}'
    result = bind_declared_shadow_scenario(duplicate, expected_sha256=hashlib.sha256(duplicate).hexdigest(),
        expected_generation=GEN, first_signal_ts=101)
    assert result["reason_codes"] == ["SCENARIO_INPUT_DUPLICATE_KEY"]


def test_runtime_admission_declaration_not_silently_treated_as_terminal_economics():
    from test_runtime_baseline_declaration import RuntimeDeclarationTests
    from research.runtime_baseline_declaration import build_runtime_baseline_declaration
    captured = build_runtime_baseline_declaration(**RuntimeDeclarationTests().inputs())["declaration"]
    assert captured is not None and "funding" not in captured
    assert bind(captured)["status"] == "UNKNOWN"
