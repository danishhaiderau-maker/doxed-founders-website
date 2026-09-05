"""Separate collector receipts must reach the normal evaluator read path."""
import hashlib
import json

import pytest

from research.policy_evidence_evaluator import build_v3_conservative_results
from research_dynamic_entry_policy import DEFAULT_CAUSAL_FEATURES
from test_policy_evidence_evaluator import _fixture, _write


def _separate_receipt_fixture(tmp_path):
    v3 = _fixture(tmp_path)
    path = v3 / "ledgers/opportunity.jsonl"
    opportunity = json.loads(path.read_text().strip())
    opportunity["signal_ts"] = 10.0
    _write(path, [opportunity])
    receipt = {
        "epoch_id": "epoch-1", "opportunity_id": "opp-1", "episode_id": "ep-1",
        "receipt_schema": "pre_entry_features_v1",
        "availability_boundary": "PRE_DECISION_ONLY", "captured_at_ts": 9.0,
        "features": {name: {"value": "LOW", "observed_ts": 8.0}
                     for name in DEFAULT_CAUSAL_FEATURES},
    }
    receipt["features"]["regime"]["value"] = "BULL"
    receipt["features"]["direction"]["value"] = "LONG"
    _write(v3 / "ledgers/pre_entry_features.jsonl", [receipt])
    return v3, receipt


def _ledger_hashes(v3):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (v3 / "ledgers").glob("*.jsonl")}


def test_normal_read_joins_separate_receipt_without_mutating_raw_evidence(tmp_path):
    v3, _ = _separate_receipt_fixture(tmp_path)
    before = _ledger_hashes(v3)
    result = build_v3_conservative_results(v3)
    row = result["results"][0]
    assert _ledger_hashes(v3) == before
    assert row["classification"] == "FULL_FILL"
    assert row["profitability_supported"] is False  # Features cannot supply missing exits/costs.
    for name in DEFAULT_CAUSAL_FEATURES:
        observed = row["regime_features_at_signal"][name]
        assert observed["status"] == "OBSERVED"
        assert observed["observed_ts"] == 8.0
        assert observed["source"] == f"opportunity.pre_entry_features.{name}"


@pytest.mark.parametrize("field", ["epoch_id", "opportunity_id", "episode_id"])
def test_foreign_identity_cannot_supply_causal_features(tmp_path, field):
    v3, receipt = _separate_receipt_fixture(tmp_path)
    receipt[field] = "foreign"
    _write(v3 / "ledgers/pre_entry_features.jsonl", [receipt])
    row = build_v3_conservative_results(v3)["results"][0]
    assert row["regime_features_at_signal"]["depth_bucket"]["status"] == "UNKNOWN"


@pytest.mark.parametrize("field", ["epoch_id", "opportunity_id", "episode_id"])
def test_missing_receipt_identity_cannot_join(tmp_path, field):
    v3, receipt = _separate_receipt_fixture(tmp_path)
    receipt.pop(field)
    _write(v3 / "ledgers/pre_entry_features.jsonl", [receipt])
    row = build_v3_conservative_results(v3)["results"][0]
    assert row["regime_features_at_signal"]["depth_bucket"]["status"] == "UNKNOWN"


def test_same_episode_in_another_epoch_cannot_contaminate_valid_receipt(tmp_path):
    v3, receipt = _separate_receipt_fixture(tmp_path)
    foreign = {**receipt, "epoch_id": "foreign", "captured_at_ts": 100.0}
    _write(v3 / "ledgers/pre_entry_features.jsonl", [foreign, receipt])
    row = build_v3_conservative_results(v3)["results"][0]
    assert row["regime_features_at_signal"]["depth_bucket"]["observed_ts"] == 8.0


@pytest.mark.parametrize("field", ["source_revision", "deployed_revision", "tile_config_signature", "config_signature"])
def test_conflicting_receipt_provenance_is_not_used(tmp_path, field):
    v3, receipt = _separate_receipt_fixture(tmp_path)
    path = v3 / "ledgers/opportunity.jsonl"
    opportunity = json.loads(path.read_text().strip())
    opportunity[field] = "original"
    _write(path, [opportunity])
    receipt[field] = "foreign"
    _write(v3 / "ledgers/pre_entry_features.jsonl", [receipt])
    row = build_v3_conservative_results(v3)["results"][0]
    assert row["regime_features_at_signal"]["depth_bucket"]["status"] == "UNKNOWN"


def test_matching_receipt_provenance_remains_usable(tmp_path):
    v3, receipt = _separate_receipt_fixture(tmp_path)
    provenance = {field: "original" for field in (
        "source_revision", "deployed_revision", "tile_config_signature", "config_signature")}
    path = v3 / "ledgers/opportunity.jsonl"
    opportunity = json.loads(path.read_text().strip())
    _write(path, [{**opportunity, **provenance}])
    _write(v3 / "ledgers/pre_entry_features.jsonl", [{**receipt, **provenance}])
    row = build_v3_conservative_results(v3)["results"][0]
    assert row["regime_features_at_signal"]["depth_bucket"]["observed_ts"] == 8.0


@pytest.mark.parametrize("defect", ["duplicate", "post_capture", "post_observation", "missing_timestamp", "missing_file"])
def test_invalid_receipt_never_fabricates_usable_feature(tmp_path, defect):
    v3, receipt = _separate_receipt_fixture(tmp_path)
    rows = [receipt]
    if defect == "duplicate":
        rows.append(dict(receipt))
    elif defect == "post_capture":
        receipt["captured_at_ts"] = 11.0
    elif defect == "post_observation":
        receipt["features"]["depth_bucket"]["observed_ts"] = 11.0
    elif defect == "missing_timestamp":
        receipt["features"]["depth_bucket"].pop("observed_ts")
    else:
        rows = []
    _write(v3 / "ledgers/pre_entry_features.jsonl", rows)
    if defect == "missing_file":
        (v3 / "ledgers/pre_entry_features.jsonl").unlink()
    before = _ledger_hashes(v3)
    row = build_v3_conservative_results(v3)["results"][0]
    assert _ledger_hashes(v3) == before
    assert row["regime_features_at_signal"]["depth_bucket"]["status"] == "UNKNOWN"
    assert "observed_ts" not in row["regime_features_at_signal"]["depth_bucket"]
    assert row["classification"] == "FULL_FILL"  # Entry tape support is independent.


def test_explicit_capture_boundary_is_preserved_for_scalar_features(tmp_path):
    v3, receipt = _separate_receipt_fixture(tmp_path)
    receipt["features"]["depth_bucket"] = "DEEP"
    _write(v3 / "ledgers/pre_entry_features.jsonl", [receipt])
    observed = build_v3_conservative_results(v3)["results"][0]["regime_features_at_signal"]["depth_bucket"]
    assert observed["value"] == "DEEP"
    assert observed["observed_ts"] == 9.0


def test_observed_terminal_preserves_dynamic_fields_from_real_separate_receipt(tmp_path):
    v3, receipt = _separate_receipt_fixture(tmp_path)
    receipt["bucket_definition_signature"] = "recorded-taxonomy-v7"
    _write(v3 / "ledgers/pre_entry_features.jsonl", [receipt])
    path = v3 / "ledgers/opportunity.jsonl"
    opportunity = json.loads(path.read_text().strip())
    opportunity.update(market="BITFINEX", symbol="BTCUSD")
    _write(path, [opportunity])
    identity = {"epoch_id": "epoch-1", "opportunity_id": "opp-1", "episode_id": "ep-1",
                "policy_signature": "sig-1"}
    _write(v3 / "ledgers/execution.jsonl", [{
        **identity, "close_ts": 20, "gross_pnl_usd": 5.0,
        "trading_fees_usd": .5, "funding_fees_usd": .25, "exit_slippage_usd": .25,
        "filled_qty": 1.0, "net_pnl_usd": 4.0,
        "execution_model": "OBSERVED_PAPER_MATCHES", "cost_model_id": "MEASURED_COSTS",
    }])
    _write(v3 / "ledgers/lifecycle.jsonl", [{**identity, "terminal": True}])
    before = _ledger_hashes(v3)
    row = build_v3_conservative_results(v3)["results"][0]
    assert _ledger_hashes(v3) == before
    assert row["terminal_outcome_status"] == "REALIZED_COST_COMPLETE"
    assert row["market"] == "BITFINEX" and row["symbol"] == "BTCUSD"
    assert row["signal_ts"] == 10.0
    assert row["required_end_ts"] == row["lifecycle_evidence"]["completion"]["horizon_complete_ts"] == 18000
    assert row["required_end_ts_basis"] == "VERIFIED_LIFECYCLE_COMPLETION_HORIZON"
    assert row["pre_entry_features"]["depth_bucket"] == {"value": "LOW", "observed_ts": 8.0}
    assert row["bucket_definition_signature"] == "recorded-taxonomy-v7"
    assert row["pre_entry_feature_receipt_sha256"]


@pytest.mark.parametrize("defect", ["absent", "conflicting", "duplicate", "foreign_identity"])
def test_taxonomy_is_never_replaced_by_current_or_opportunity_default(tmp_path, defect):
    v3, receipt = _separate_receipt_fixture(tmp_path)
    path = v3 / "ledgers/opportunity.jsonl"
    opportunity = json.loads(path.read_text().strip())
    opportunity["bucket_definition_signature"] = "opportunity-default"
    _write(path, [opportunity])
    rows = [receipt]
    if defect != "absent":
        receipt["bucket_definition_signature"] = "other-recorded-taxonomy"
    if defect == "duplicate":
        rows *= 2
    if defect == "foreign_identity":
        receipt["epoch_id"] = "foreign"
    _write(v3 / "ledgers/pre_entry_features.jsonl", rows)
    row = build_v3_conservative_results(v3)["results"][0]
    assert row["bucket_definition_signature"] is None


@pytest.mark.parametrize("field,value", [("market", "OTHER"), ("symbol", "ETHUSD"), ("signal_ts", 11.0)])
def test_conflicting_recorded_dynamic_identity_is_not_selected_last(tmp_path, field, value):
    v3, _ = _separate_receipt_fixture(tmp_path)
    path = v3 / "ledgers/opportunity.jsonl"
    opportunity = json.loads(path.read_text().strip())
    opportunity.update(market="BITFINEX", symbol="BTCUSD")
    _write(path, [opportunity])
    path = v3 / "ledgers/decision.jsonl"
    decision = json.loads(path.read_text().strip())
    decision[field] = value
    _write(path, [decision])
    row = build_v3_conservative_results(v3)["results"][0]
    assert row[field] is None
    assert f"DYNAMIC_RECORDED_FIELD_INVALID_OR_CONFLICTING:{field}" in row["dynamic_input_blockers"]


def test_missing_horizon_is_not_fabricated_from_signal_or_two_hour_default(tmp_path, monkeypatch):
    from research import policy_evidence_evaluator as evaluator
    v3, _ = _separate_receipt_fixture(tmp_path)
    monkeypatch.setattr(evaluator, "join_lifecycle_evidence", lambda *_: {"status": "UNKNOWN", "reason_codes": ["MISSING"]})
    row = build_v3_conservative_results(v3)["results"][0]
    assert row["signal_ts"] == 10.0
    assert row["required_end_ts"] is None and row["required_end_ts_basis"] is None


def test_partial_feature_join_preserves_only_actual_valid_observations(tmp_path):
    v3, receipt = _separate_receipt_fixture(tmp_path)
    receipt["bucket_definition_signature"] = "recorded-taxonomy"
    receipt["features"]["depth_bucket"]["observed_ts"] = 11
    _write(v3 / "ledgers/pre_entry_features.jsonl", [receipt])
    row = build_v3_conservative_results(v3)["results"][0]
    assert "depth_bucket" not in row["pre_entry_features"]
    assert row["pre_entry_features"]["regime"] == {"value": "BULL", "observed_ts": 8.0}
    assert row["bucket_definition_signature"] is None
