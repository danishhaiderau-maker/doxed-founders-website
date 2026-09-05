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
