from copy import deepcopy
import hashlib
import json

import pytest

from research.baseline_execution_context import build_baseline_execution_context
from research.baseline_execution_context import VerifiedLedgerRowIndex
from research.policy_evidence_schema import canonical_json, stable_hash
from research.declared_shadow_model import _baseline_context


def _sha(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _envelope(name, row, pins):
    raw = canonical_json(row).encode()
    pins[name] = hashlib.sha256(raw).hexdigest()
    return {"source_id": name, "raw_bytes": raw, "row": row, "row_sha256": _sha(row)}


def _fixture(*, partial=True, mode="FIXED_QUANTITY"):
    from test_conservative_shadow_terminal import _inputs
    values = _inputs()
    gen = values["generation"]
    identity = {"epoch_id": gen["epoch_id"], "episode_id": "episode", "opportunity_id": "opportunity",
                "shared_ai_call_id": "shared", "event_id": "event", "direction": "LONG", "symbol": "BTCUSD",
                "baseline_id": "NO_CHASE_LIMIT", "baseline_policy_signature": "target-baseline"}
    entry = {**values["entry_receipt"], "requested_qty": 1, "schedule_sha256": "a" * 64,
             "filled_qty": .4 if partial else 1,
             "final_classification": "PARTIAL_FILL" if partial else "FULL_FILL"}
    stage = {**identity, "schema": "compressed_chase_shadow_v1", "event": "STAGE", "stage_index": 0,
             "identity_complete": True, "missing_identity_fields": [],
             "policy_signature": "source-compressed-policy", "signal_ts": 9, "observed_ts": 9,
             "event_source_revision": gen["source_revision"], "event_config_signature": gen["tile_config_signature"],
             "requested_qty": 1, "requested_margin_usd": 10, "leverage": 10, "virtual_limit_price": 100,
             "signed_quantity_constraints": entry["quantity_constraints"]}
    sizing = {**identity, "schema": "baseline_sizing_authorization_v1", "generation": gen,
              "source_stage_zero_row_sha256": _sha(stage), "source_policy_signature": stage["policy_signature"],
              "sizing_mode": mode, "declared_at_ts": 9,
              "quantity_basis_price": 100, "baseline_schedule_sha256": entry["schedule_sha256"],
              "coverage_policy": {"sampling_interval_sec": 1, "first_sample_offset_sec": 1,
                                  "required_horizon_end_ts": 14}}
    atr = {**identity, "schema": "baseline_fill_atr_observation_v1", "generation": gen,
           "atr_basis": "EXPLICIT_AT_FILL_OBSERVATION", "atr_pct": 1,
           "observed_ts": 10, "available_at_ts": 10, "provenance": "OBSERVED_3M_ATR14_AT_BUCKET_10"}
    segment = {**identity, "schema": "market_segment_v3", "rows": [
        {**row, "symbol": "BTCUSD", "buy_vwap": row["ask"], "sell_vwap": row["bid"]}
        for row in values["future_path_rows"]]}
    pins = {}
    return {"generation": gen, "identity": identity, "entry_receipt": entry,
            "stage_zero_evidence": [_envelope("stage", stage, pins)],
            "sizing_authorization": _envelope("sizing", sizing, pins),
            "atr_evidence": _envelope("atr", atr, pins),
            "coverage_evidence": _envelope("coverage", segment, pins), "pinned_sources": pins}


def _replace(values, field, mutate):
    env = values[field][0] if field == "stage_zero_evidence" else values[field]
    row = deepcopy(env["row"])
    mutate(row)
    changed = _envelope(env["source_id"], row, values["pinned_sources"])
    if field == "stage_zero_evidence":
        values[field] = [changed]
        _replace(values, "sizing_authorization", lambda sizing: sizing.update(source_stage_zero_row_sha256=_sha(row)))
    else:
        values[field] = changed


@pytest.mark.parametrize("partial", [True, False])
@pytest.mark.parametrize("mode", ["FIXED_QUANTITY", "FIXED_MARGIN"])
def test_signed_context_preserves_requested_quantity_and_partial_position_margin(partial, mode):
    values = _fixture(partial=partial, mode=mode)
    before = deepcopy(values)
    result = build_baseline_execution_context(**values)
    assert result["status"] == "SUPPORTED", result
    context = result["context"]
    assert values == before
    assert context["requested_qty"] == "1"
    assert float(context["margin_usd"]) == (4 if partial else 10)
    assert context["requested_margin_usd"] == "10"
    assert context["entry_receipt_sha256"] == _sha(values["entry_receipt"])
    assert len(context["source_evidence_sha256"]) == 4
    assert not {"fees_usd", "funding_usd", "trading_fees_usd", "latency_cost_usd"} & context.keys()
    adapted = _baseline_context({"conservative_receipt": values["entry_receipt"], "execution_model_context": context}, values["generation"])
    assert adapted == context
    assert result["live_arming_authorized"] is False


@pytest.mark.parametrize("field,change,reason", [
    ("stage_zero_evidence", {"episode_id": "other"}, "IDENTITY_MISMATCH"),
    ("stage_zero_evidence", {"stage_index": 1}, "STAGE_ZERO_INVALID"),
    ("stage_zero_evidence", {"identity_complete": False}, "STAGE_ZERO_INVALID"),
    ("stage_zero_evidence", {"event_source_revision": "old"}, "STAGE_ZERO_INVALID"),
    ("stage_zero_evidence", {"requested_qty": 2}, "CROSS_POLICY_QUANTITY_FORBIDDEN"),
    ("sizing_authorization", {"baseline_policy_signature": "another"}, "IDENTITY_MISMATCH"),
    ("sizing_authorization", {"source_policy_signature": "another"}, "SIZING_AUTHORIZATION_INVALID"),
    ("sizing_authorization", {"sizing_mode": "ASSUME_SAME_MARGIN"}, "SIZING_MODE_UNSUPPORTED"),
    ("sizing_authorization", {"declared_at_ts": 11}, "SIZING_NOT_CAUSAL"),
    ("atr_evidence", {"atr_basis": "CAUSAL_CLOSED_BAR_DERIVED"}, "OBSERVED_ATR_REQUIRED"),
    ("atr_evidence", {"atr_basis": "SIGNAL_TIME_3M_ATR14"}, "OBSERVED_ATR_REQUIRED"),
    ("atr_evidence", {"observed_ts": 11}, "ATR_NOT_EXACT_CAUSAL_FILL"),
    ("atr_evidence", {"available_at_ts": 11}, "ATR_NOT_EXACT_CAUSAL_FILL"),
    ("atr_evidence", {"atr_pct": None}, "NUMBER_INVALID"),
    ("atr_evidence", {"symbol": "ETHUSD"}, "OBSERVED_ATR_REQUIRED"),
    ("atr_evidence", {"atr_pct": float("nan")}, "NUMBER_INVALID"),
])
def test_source_bound_but_invalid_evidence_stays_unknown(field, change, reason):
    values = _fixture()
    _replace(values, field, lambda row: row.update(change))
    result = build_baseline_execution_context(**values)
    assert result["status"] == "UNKNOWN"
    assert result["context"] is None
    assert reason in result["reason_codes"][0]


def test_fixed_margin_must_explain_original_quantity_at_declared_limit():
    values = _fixture(mode="FIXED_MARGIN")
    _replace(values, "sizing_authorization", lambda row: row.update(quantity_basis_price=200))
    result = build_baseline_execution_context(**values)
    assert result["reason_codes"] == ["BASELINE_CONTEXT_FIXED_MARGIN_QUANTITY_MISMATCH"]


def test_fixed_margin_requires_exact_baseline_schedule_binding():
    values = _fixture(mode="FIXED_MARGIN")
    _replace(values, "sizing_authorization", lambda row: row.update(baseline_schedule_sha256="b" * 64))
    assert build_baseline_execution_context(**values)["reason_codes"] == ["BASELINE_CONTEXT_FIXED_MARGIN_SCHEDULE_UNBOUND"]


@pytest.mark.parametrize("mutation", [
    lambda row: row["rows"].pop(1),
    lambda row: row["rows"].append(deepcopy(row["rows"][-1])),
    lambda row: row["rows"].reverse(),
    lambda row: row["rows"][0].update(fresh=False),
    lambda row: row["rows"][0].update(ask_qty=0),
    lambda row: row["rows"][0].pop("trade_count"),
])
def test_coverage_is_verified_from_actual_path_not_claimed_counts(mutation):
    values = _fixture()
    _replace(values, "coverage_evidence", mutation)
    assert build_baseline_execution_context(**values)["status"] == "UNKNOWN"


def test_source_pin_and_row_membership_are_independently_checked():
    values = _fixture()
    values["pinned_sources"]["atr"] = "0" * 64
    assert build_baseline_execution_context(**values)["reason_codes"] == ["BASELINE_CONTEXT_SOURCE_HASH_MISMATCH"]
    values = _fixture()
    envelope = values["atr_evidence"]
    envelope["row"] = {**envelope["row"], "atr_pct": 9}
    envelope["row_sha256"] = _sha(envelope["row"])
    assert build_baseline_execution_context(**values)["reason_codes"] == ["BASELINE_CONTEXT_ROW_NOT_IN_PINNED_SOURCE"]


def test_conflicting_duplicate_stage_zero_cannot_be_selected_favorably():
    values = _fixture()
    other = {**values["stage_zero_evidence"][0]["row"], "leverage": 20}
    values["stage_zero_evidence"].append(_envelope("other-stage", other, values["pinned_sources"]))
    assert build_baseline_execution_context(**values)["reason_codes"] == ["BASELINE_CONTEXT_STAGE_ZERO_CONFLICT"]


def test_identical_duplicate_stage_zero_is_deduplicated_and_jsonl_membership_supported():
    values = _fixture()
    stage = values["stage_zero_evidence"][0]
    stage["raw_bytes"] = b'{"unrelated":true}\n' + canonical_json(stage["row"]).encode() + b"\n"
    values["pinned_sources"]["stage"] = hashlib.sha256(stage["raw_bytes"]).hexdigest()
    values["stage_zero_evidence"].append(deepcopy(stage))
    assert build_baseline_execution_context(**values)["status"] == "SUPPORTED"


def test_context_signature_changes_with_exact_entry_receipt_and_source_generation():
    values = _fixture()
    original = build_baseline_execution_context(**values)["context"]
    values["entry_receipt"]["fill_latency_sec"] = 0
    newer = build_baseline_execution_context(**values)["context"]
    assert original["signature"] != newer["signature"]
    assert newer["entry_receipt_sha256"] != original["entry_receipt_sha256"]
    values["generation"] = {**values["generation"], "generation_key": "different"}
    assert build_baseline_execution_context(**values)["status"] == "UNKNOWN"


def test_partial_context_margin_is_accepted_by_existing_terminal_quantity_accounting():
    from test_conservative_shadow_terminal import _inputs, _rebind
    from research.conservative_shadow_terminal import evaluate_shadow_terminal
    inputs = _fixture(partial=True)
    context = build_baseline_execution_context(**inputs)["context"]
    terminal = _inputs()
    terminal["entry_receipt"] = inputs["entry_receipt"]
    for field in ("position_context_id", "atr_pct_at_fill", "leverage", "margin_usd"):
        terminal["position_context"][field] = context[field]
    _rebind(terminal)
    result = evaluate_shadow_terminal(**terminal)
    assert result["status"] == "COMPLETE", result


def test_missing_envelope_or_source_limit_is_explicit_unknown():
    values = _fixture()
    values["atr_evidence"] = None
    assert build_baseline_execution_context(**values)["status"] == "UNKNOWN"
    values = _fixture()
    values["atr_evidence"]["raw_bytes"] = b"x" * (16 * 1024 * 1024 + 1)
    assert build_baseline_execution_context(**values)["reason_codes"] == ["BASELINE_CONTEXT_SOURCE_PIN_OR_LIMIT_INVALID"]


def test_large_jsonl_index_keeps_bounded_memory_and_attests_only_verified_rows(tmp_path):
    import tracemalloc
    values = _fixture()
    stage = values["stage_zero_evidence"][0]["row"]
    path = tmp_path / "stage"
    filler = (canonical_json({"schema": "unrelated_stage", "payload": "x" * 32_768}) + "\n").encode()
    with path.open("wb") as handle:
        for _ in range(560):
            handle.write(filler)
        handle.write((canonical_json(stage) + "\n").encode())
    assert path.stat().st_size > 16 * 1024 * 1024
    with path.open("rb") as handle:
        source_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    values["pinned_sources"]["stage"] = source_hash
    tracemalloc.start()
    try:
        with VerifiedLedgerRowIndex() as index:
            count = index.add_source(tmp_path, "stage", expected_sha=source_hash,
                                     expected_size=path.stat().st_size, stage_only=True)
            assert count == 1
            proof = index.envelope("stage", stage)
            assert "raw_bytes" not in proof
            values["stage_zero_evidence"] = [proof]
            result = build_baseline_execution_context(**values)
            assert result["status"] == "SUPPORTED", result
            assert result["context"]["verified_ledger_row_membership"][0]["source_sha256"] == source_hash
            assert index.envelope("stage", {**stage, "requested_qty": 2}) is None
            # Caller cannot change a proven row or replace attestation with
            # a dictionary claiming that verification happened.
            values["stage_zero_evidence"] = [{**proof, "row": {**stage, "requested_qty": 2}}]
            assert build_baseline_execution_context(**values)["reason_codes"] == ["BASELINE_CONTEXT_STREAM_ROW_PROOF_INVALID"]
            values["stage_zero_evidence"] = [{**proof, "verified_row_proof": {"source_sha256": source_hash}}]
            assert build_baseline_execution_context(**values)["reason_codes"] == ["BASELINE_CONTEXT_STREAM_ROW_PROOF_INVALID"]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024


def test_wrong_full_stream_hash_rolls_back_all_membership_and_cleans_index(tmp_path):
    from pathlib import Path
    stage = _fixture()["stage_zero_evidence"][0]["row"]
    raw = (canonical_json(stage) + "\n").encode()
    (tmp_path / "source").write_bytes(raw)
    with VerifiedLedgerRowIndex() as index:
        temporary = Path(index._temporary.name)
        with pytest.raises(ValueError, match="SOURCE_HASH_MISMATCH"):
            index.add_source(tmp_path, "source", expected_sha="0" * 64, expected_size=len(raw))
        assert index.envelope("source", stage) is None
        assert index.sources == {}
    assert not temporary.exists()


def test_stream_row_size_bound_does_not_read_unbounded_line(tmp_path):
    from research.baseline_execution_context import MAX_LEDGER_ROW_BYTES
    raw = b"x" * (MAX_LEDGER_ROW_BYTES + 10)
    (tmp_path / "source").write_bytes(raw)
    with VerifiedLedgerRowIndex() as index:
        with pytest.raises(ValueError, match="SOURCE_ROW_SIZE_LIMIT"):
            index.add_source(tmp_path, "source", expected_sha=hashlib.sha256(raw).hexdigest(), expected_size=len(raw))
