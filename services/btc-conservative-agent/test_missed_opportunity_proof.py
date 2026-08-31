import importlib.util
import hashlib
import json
from pathlib import Path

import pytest

from research.quantity_execution import build_signed_quantity_constraints


ROOT = Path(__file__).resolve().parent


SIGNED_QUANTITY_CONSTRAINTS = build_signed_quantity_constraints(
    symbol="tBTCF0:USTF0",
    quantity_step="0.00000001",
    quantity_precision=8,
    min_lot="0.00000001",
    min_notional="0.000001",
    captured_at="2026-08-30T00:00:00+00:00",
    source_revision="test-revision",
    source="TEST_FIXTURE",
)


def _load_engine():
    spec = importlib.util.spec_from_file_location(
        "analyzer_missed_proof_test",
        ROOT / "analyzer_research_engine_v62.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _signed_rows(complete=True):
    rows = []
    schedule = [0, 60, 120, 240, 420, 600]
    prices = [99.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    for index, (due, ask) in enumerate(zip(schedule, prices)):
        if not complete and index == 4:
            continue
        rows.append({
            "schema": "compressed_chase_shadow_v1",
            "event": "STAGE",
            "execution_class": "SHADOW_ONLY",
            "places_order": False,
            "relay_eligible": False,
            "trade_id": "trade-1",
            "direction": "LONG",
            "shared_ai_call_id": "scan-1",
            "opportunity_id": "opportunity:episode-1",
            "episode_id": "episode-1",
            "epoch_id": "epoch-1",
            "policy_id": "SHADOW_COMPRESSED_CHASE_0_1_2_4_7_10_EXP13_V1",
            "policy_signature": "sig-1",
            "schedule_generation_id": "schedule-gen-1",
            "identity_complete": True,
            "missing_identity_fields": [],
            "stage_index": index,
            "stage_due_sec": due,
            "observed_ts": 1000 + due,
            "scheduled_due_ts": 1000 + due,
            "observed_delay_sec": 0.0,
            "virtual_limit_price": 100.0,
            "reference_price": 100.0,
            "bbo": {"bid": ask - 1, "ask": ask, "last": ask - 0.5},
            "bbo_fresh": True,
            "bbo_valid": True,
            "coverage_status": "OBSERVED",
            "eligible_at_stage": True,
            "schedule_seconds": schedule,
            "terminal_expiry_sec": 780,
            "expires_ts": 1780.0,
            "tape_window_start_ts": 1000.0,
            "tape_window_end_ts": 1780.0,
            "requested_qty": 1.0,
            "entry_fee_rate": 0.0,
            "exit_fee_rate": 0.0,
            "fee_profile": "BITFINEX_ZERO",
            "slippage_model": "SIGNED_BBO_DEPTH_EXPLICIT_FEES_V1",
            "signed_quantity_constraints": SIGNED_QUANTITY_CONSTRAINTS,
        })
    expiry = {
        **rows[0],
        "event": "EXPIRED",
        "stage_index": None,
        "stage_due_sec": None,
        "observed_ts": 1780,
        "bbo": {"bid": 109.0, "ask": 110.0, "last": 109.5},
        "tape_evidence": {
            "receipt_id": "tape-sha256-1",
            "coverage_status": "COMPLETE",
            "missing_seconds": 0,
            "timeframe": "1s",
            "start_ts": 1000,
            "end_ts": 1780,
            "conservative_execution_supported": True,
            "fill_status": "FULL_FILL",
            "entry_execution_price": 99.0,
            "quantity": 1.0,
            "fee_usd": 0.0,
            "slippage_usd": 0.25,
            "slippage_model": "BBO_PLUS_EXPLICIT_SLIPPAGE",
            "points": [
                {"bbo": {"bid": 98.0, "ask": 99.0}},
                {"bbo": {"bid": 104.0, "ask": 105.0}},
                {"bbo": {"bid": 109.0, "ask": 110.0}},
            ],
        },
    }
    rows.append(expiry)
    return rows


def _canonical_tape_row(bucket, *, bid, ask, qty=5.0):
    row = {
        "schema": "market_microstructure_1s_v1",
        "symbol": "tBTCF0:USTF0",
        "bucket_ts": bucket,
        "fresh": True,
        "valid_bbo": True,
        "bid": bid,
        "ask": ask,
        "bid_qty": qty,
        "ask_qty": qty,
        "last": (bid + ask) / 2.0,
    }
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
    row["row_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return row


def _write_complete_canonical_tape(root):
    tape = []
    for bucket in range(1000, 1780):
        if bucket == 1000:
            bid, ask = 98.0, 99.0
        elif bucket == 1779:
            bid, ask = 109.0, 110.0
        else:
            bid, ask = 104.0, 105.0
        tape.append(_canonical_tape_row(bucket, bid=bid, ask=ask))
    _write_jsonl(root / "market_microstructure_1s.jsonl", tape)


def _identity_ledgers(root):
    ledgers = root / "v3" / "ledgers"
    _write_jsonl(ledgers / "opportunity.jsonl", [{
        "opportunity_id": "opportunity:episode-1",
        "episode_id": "episode-1",
        "shared_ai_call_id": "scan-1",
        "epoch_id": "epoch-1",
        "signal_ts": 1000,
        "raw_direction": "LONG",
        "feature_snapshot_at_signal": {"regime": "TREND", "adx": 31.5},
    }])
    _write_jsonl(ledgers / "decision.jsonl", [{
        "episode_id": "episode-1",
        "shared_ai_call_id": "scan-1",
        "scores": {"long": 8, "short": 2, "confidence": 60},
        "contraindications": ["RESISTANCE_NEARBY"],
    }])
    _write_jsonl(ledgers / "execution.jsonl", [])


def test_complete_signed_schedule_can_prove_missed_profit(tmp_path, monkeypatch):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _identity_ledgers(tmp_path)
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", _signed_rows())
    _write_complete_canonical_tape(tmp_path)

    proof = engine.build_missed_opportunity_proof_report(session={})
    row = proof["proofs"][0]
    assert proof["schema"] == "missed_opportunity_proof_v1"
    assert row["classification"] == "PROVEN_MISSED_PROFIT"
    assert row["coverage"]["status"] == "COMPLETE"
    assert row["conservative_touch"] is True
    assert row["qualification_eligible"] is False
    assert row["cost_assumption"]["fee_profile"] == "BITFINEX_ZERO"
    assert row["cost_assumption"]["explicit_costs_complete"] is True
    assert row["coverage"]["tape_receipt"].startswith("tape-join-sha256-")
    assert row["coverage"]["tape_receipt"] != "tape-sha256-1"
    assert row["conservative_entry_price"] == 100.0
    assert row["coverage"]["raw_partial_quantity"] == 1.0
    assert row["coverage"]["rounded_executable_quantity"] == 1.0
    assert row["coverage"]["accumulated_quantity"] == 1.0
    assert row["coverage"]["minimum_lot_decision"] == "PASS"
    assert row["coverage"]["minimum_notional_decision"] == "PASS"
    assert row["coverage"]["signed_quantity_constraints"]["payload_sha256"] == (
        SIGNED_QUANTITY_CONSTRAINTS["payload_sha256"]
    )

    lab = engine.chase_policy_lab_report(session={}, proof_payload=proof)
    assert lab["schema"] == "chase_policy_lab_v1"
    assert lab["leader_label"] == "INSUFFICIENT_EVIDENCE"
    assert lab["top_schedule"]["checkpoint_seconds"] == [0, 60, 120, 240, 420, 600]
    assert lab["top_schedule"]["terminal_expiry_sec"] == 780
    assert lab["top_schedule"]["executed"]["pnl_usd"] is None


def test_arm_receipt_is_not_counted_as_schedule_or_fill(tmp_path, monkeypatch):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _identity_ledgers(tmp_path)
    rows = [{
        "schema": "compressed_chase_arm_receipt_v1",
        "ts": "2026-08-29T00:00:00+00:00",
        "shared_ai_call_id": "scan-unsupported",
        "status": "UNSUPPORTED",
        "reason": "MISSING_SIGNAL_PRICE",
        "shadow_only": True,
        "places_order": False,
    }, *_signed_rows()]
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", rows)

    proof = engine.build_missed_opportunity_proof_report(session={})
    assert proof["proof_count"] == 1
    assert [row["shared_ai_call_id"] for row in proof["proofs"]] == ["scan-1"]
    assert proof["arm_receipt_count"] == 1
    assert proof["arm_status_counts"] == {"UNSUPPORTED": 1}
    assert proof["arm_reason_counts"] == {"MISSING_SIGNAL_PRICE": 1}


def test_missing_checkpoint_fails_closed(tmp_path, monkeypatch):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _identity_ledgers(tmp_path)
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", _signed_rows(complete=False))

    row = engine.build_missed_opportunity_proof_report(session={})["proofs"][0]
    assert row["classification"] == "INSUFFICIENT_EVIDENCE"
    assert row["coverage"]["status"] == "INSUFFICIENT"


def test_checkpoint_only_rows_cannot_be_proven(tmp_path, monkeypatch):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _identity_ledgers(tmp_path)
    rows = _signed_rows()
    rows[-1].pop("tape_evidence")
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", rows)

    row = engine.build_missed_opportunity_proof_report(session={})["proofs"][0]
    assert row["classification"] == "INSUFFICIENT_EVIDENCE"
    assert row["conservative_touch"] is True
    assert row["net_terminal_return_pct"] is None
    assert row["coverage"]["tape_status"] in {"UNAVAILABLE", "INSUFFICIENT"}

    lab = engine.chase_policy_lab_report(session={}, proof_payload={
        "proofs": [row], "empty_reason": None,
    })
    assert lab["all_schedule_count"] == 1
    assert lab["top_schedule"]["unsupported"] == 1
    assert lab["top_schedule"]["supported"] == 0


def test_overdue_observed_checkpoint_is_not_accepted(tmp_path, monkeypatch):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _identity_ledgers(tmp_path)
    rows = _signed_rows()
    rows[2]["observed_ts"] += 16
    rows[2]["observed_delay_sec"] = 16.0
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", rows)

    row = engine.build_missed_opportunity_proof_report(session={})["proofs"][0]
    assert row["classification"] == "INSUFFICIENT_EVIDENCE"
    assert row["coverage"]["status"] == "INSUFFICIENT"


def test_joined_tape_derives_costs_only_from_signed_runtime_basis():
    engine = _load_engine()
    first = {
        "requested_qty": 2.0,
        "entry_fee_rate": 0.001,
        "exit_fee_rate": 0.002,
        "fee_profile": "TEST_EXPLICIT",
        "slippage_model": "SIGNED_BBO_DEPTH_EXPLICIT_FEES_V1",
        "signed_quantity_constraints": SIGNED_QUANTITY_CONSTRAINTS,
        "tape_window_start_ts": 1000.0,
        "tape_evidence_path": "market_microstructure_1s.jsonl",
    }
    touch = {"observed_ts": 1000.0, "virtual_limit_price": 100.0}
    expiry = {"observed_ts": 1001.0, "tape_window_end_ts": 1001.0}
    tape = {
        1000: {"fresh": True, "valid_bbo": True, "bid": 98.0, "ask": 99.0,
               "bid_qty": 5.0, "ask_qty": 5.0, "row_sha256": "a"},
        1001: {"fresh": True, "valid_bbo": True, "bid": 109.0, "ask": 110.0,
               "bid_qty": 5.0, "ask_qty": 5.0, "row_sha256": "b"},
    }
    joined = engine._joined_tape_evidence(first, expiry, touch, "LONG", tape)
    assert joined["coverage_status"] == "COMPLETE"
    assert joined["conservative_execution_supported"] is True
    assert joined["quantity"] == 2.0
    assert joined["notional_usd"] == 198.0
    assert joined["fee_usd"] == pytest.approx(0.198 + 0.436)
    assert joined["slippage_usd"] == 0.0
    assert joined["slippage_model"] == "SIGNED_BBO_DEPTH_EXPLICIT_FEES_V1"


def _compressed_join_inputs(*, available_qty=5.0):
    first = {
        "signal_ts": 1000.0, "expires_ts": 1003.0,
        "tape_window_start_ts": 1000.0, "tape_window_end_ts": 1003.0,
        "requested_qty": 2.0, "entry_fee_rate": 0.001,
        "exit_fee_rate": 0.002, "fee_profile": "TEST_EXPLICIT",
        "slippage_model": "SIGNED_BBO_DEPTH_EXPLICIT_FEES_V1",
        "signed_quantity_constraints": SIGNED_QUANTITY_CONSTRAINTS,
    }
    stages = {0: {
        "identity_complete": True, "eligible_at_stage": True,
        "bbo_fresh": True, "bbo_valid": True, "coverage_status": "OBSERVED",
        "observed_ts": 1000.0, "scheduled_due_ts": 1000.0,
        "observed_delay_sec": 0.0, "virtual_limit_price": 100.0,
        "stage_index": 0,
    }}
    expiry = {"observed_ts": 1004.0, "tape_window_end_ts": 1003.0}
    tape = {
        second: {
            "schema": "market_microstructure_1s_v1", "symbol": "tBTCF0:USTF0",
            "bucket_ts": second, "fresh": True, "valid_bbo": True,
            "bid": 100.0 + second - 1000,
            "ask": 101.0 + second - 1000, "bid_qty": available_qty,
            "ask_qty": available_qty, "row_sha256": str(second),
        }
        for second in range(1000, 1004)
    }
    return first, expiry, stages, tape


def test_complete_path_can_prove_true_no_fill():
    engine = _load_engine()
    first, expiry, stages, tape = _compressed_join_inputs()
    joined = engine._joined_compressed_chase_tape_evidence(
        first, expiry, stages, "LONG", tape,
    )
    assert joined["coverage_status"] == "COMPLETE"
    assert joined["fill_status"] == "NO_FILL"
    assert joined["accepted_quantity"] == 0.0
    assert joined["touch_ts"] is None
    assert joined["outcome_code"] == "TRUE_NO_FILL_BBO_NEVER_CROSSED_LIMIT"


def test_one_second_path_detects_touch_between_checkpoints_and_partial_fill():
    engine = _load_engine()
    first, expiry, stages, tape = _compressed_join_inputs(available_qty=0.75)
    tape[1001]["ask"] = 99.0
    joined = engine._joined_compressed_chase_tape_evidence(
        first, expiry, stages, "LONG", tape,
    )
    assert joined["coverage_status"] == "COMPLETE"
    assert joined["fill_status"] == "PARTIAL_FILL"
    assert joined["touch_ts"] == 1001
    assert joined["requested_quantity"] == 2.0
    assert joined["available_quantity"] == 0.75
    assert joined["accepted_quantity"] == 0.75
    assert joined["raw_partial_quantity"] == 0.75
    assert joined["rounded_executable_quantity"] == 0.75
    assert joined["accumulated_quantity"] == 0.75
    assert joined["minimum_lot_decision"] == "PASS"
    assert joined["minimum_notional_decision"] == "PASS"
    assert joined["signed_quantity_constraints"]["payload_sha256"] == (
        SIGNED_QUANTITY_CONSTRAINTS["payload_sha256"]
    )
    assert joined["outcome_code"] == "PARTIAL_FILL_AVAILABLE_DEPTH"
    assert joined["entry_execution_price"] == 100.0


def test_missing_tape_second_is_unknown_not_no_fill():
    engine = _load_engine()
    first, expiry, stages, tape = _compressed_join_inputs()
    tape.pop(1002)
    joined = engine._joined_compressed_chase_tape_evidence(
        first, expiry, stages, "LONG", tape,
    )
    assert joined["coverage_status"] == "INSUFFICIENT"
    assert joined["fill_status"] == "UNKNOWN_UNVERIFIABLE"
    assert joined["missing_seconds"] == 1
    assert joined["outcome_code"] == "UNKNOWN_MISSING_OR_CONFLICTING_1S_TAPE"


def test_missing_signed_quantity_constraints_are_unknown_not_fill_or_no_fill():
    engine = _load_engine()
    first, expiry, stages, tape = _compressed_join_inputs()
    first.pop("signed_quantity_constraints")
    tape[1001]["ask"] = 99.0

    joined = engine._joined_compressed_chase_tape_evidence(
        first, expiry, stages, "LONG", tape,
    )

    assert joined["coverage_status"] == "COMPLETE"
    assert joined["fill_status"] == "UNKNOWN_UNVERIFIABLE"
    assert joined["conservative_execution_supported"] is False
    assert joined["signed_quantity_constraints"] is None
    assert joined["quantity_constraint_reasons"] == [
        "SIGNED_QUANTITY_CONSTRAINTS_MISSING"
    ]
    assert joined["outcome_code"] == "UNKNOWN_SIGNED_QUANTITY_CONSTRAINTS_MISSING"


def test_ineligible_stage_is_not_counted_as_true_no_fill():
    engine = _load_engine()
    first, expiry, stages, tape = _compressed_join_inputs()
    stages[0]["eligible_at_stage"] = False
    tape[1001]["ask"] = 99.0
    assert engine._compressed_stage_observation_supported(stages[0]) is True
    joined = engine._joined_compressed_chase_tape_evidence(
        first, expiry, stages, "LONG", tape,
    )
    assert joined["fill_status"] == "INELIGIBLE"
    assert joined["fill_status"] != "NO_FILL"
    assert joined["conservative_execution_supported"] is False
    assert joined["touch_ts"] is None
    assert joined["outcome_code"] == "INELIGIBLE_NO_ENTRY_AT_ANY_STAGE"


def test_invalid_direction_is_unknown_not_no_fill():
    engine = _load_engine()
    first, expiry, stages, tape = _compressed_join_inputs()
    joined = engine._joined_compressed_chase_tape_evidence(
        first, expiry, stages, "SIDEWAYS", tape,
    )
    assert joined["fill_status"] == "UNKNOWN_UNVERIFIABLE"
    assert joined["outcome_code"] == "UNKNOWN_INVALID_DIRECTION"


def test_expiry_bucket_is_excluded_from_tape_window():
    engine = _load_engine()
    first, expiry, stages, tape = _compressed_join_inputs()
    tape.pop(1003)
    joined = engine._joined_compressed_chase_tape_evidence(
        first, expiry, stages, "LONG", tape,
    )
    assert joined["coverage_status"] == "COMPLETE"
    assert joined["expected_seconds"] == 3
    assert joined["terminal_execution_price"] == tape[1002]["bid"]


def test_short_path_uses_bid_touch_and_declared_limit():
    engine = _load_engine()
    first, expiry, stages, tape = _compressed_join_inputs()
    stages[0]["virtual_limit_price"] = 102.0
    tape[1001]["bid"] = 103.0
    joined = engine._joined_compressed_chase_tape_evidence(
        first, expiry, stages, "SHORT", tape,
    )
    assert joined["fill_status"] == "FULL_FILL"
    assert joined["touch_ts"] == 1001
    assert joined["entry_execution_price"] == 102.0


def _tape_row(bucket, *, symbol="tBTCF0:USTF0", row_hash=None, ask=101.0):
    row = {
        "schema": "market_microstructure_1s_v1", "symbol": symbol,
        "bucket_ts": bucket, "fresh": True, "valid_bbo": True,
        "bid": ask - 1.0, "ask": ask, "bid_qty": 5.0, "ask_qty": 5.0,
    }
    canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False)
    row["row_sha256"] = row_hash or hashlib.sha256(canonical.encode()).hexdigest()
    return row


def test_tape_loader_joins_numeric_rotations(tmp_path, monkeypatch):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _write_jsonl(tmp_path / "market_microstructure_1s.jsonl", [_tape_row(1002)])
    _write_jsonl(tmp_path / "market_microstructure_1s.jsonl.1", [_tape_row(1001)])
    _write_jsonl(tmp_path / "market_microstructure_1s.jsonl.2", [_tape_row(1000)])
    (tmp_path / "market_microstructure_1s.jsonl.validation.json").write_text("{}")
    assert sorted(engine._one_second_tape_by_bucket()) == [1000, 1001, 1002]


def test_tape_loader_rejects_wrong_symbol_and_conflicting_duplicate(tmp_path, monkeypatch):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _write_jsonl(tmp_path / "market_microstructure_1s.jsonl", [
        _tape_row(1000, symbol="tETHF0:USTF0"),
        _tape_row(1001, ask=101.0),
    ])
    _write_jsonl(tmp_path / "market_microstructure_1s.jsonl.1", [
        _tape_row(1001, ask=102.0),
    ])
    rows = engine._one_second_tape_by_bucket()
    assert 1000 not in rows
    assert rows[1001]["duplicate_conflict"] is True
    assert rows[1001]["fresh"] is False


def test_tape_loader_recomputes_row_hash(tmp_path, monkeypatch):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _write_jsonl(
        tmp_path / "market_microstructure_1s.jsonl",
        [_tape_row(1000, row_hash="forged")],
    )
    row = engine._one_second_tape_by_bucket()[1000]
    assert row["row_integrity_failure"] is True
    assert row["fresh"] is False


@pytest.mark.parametrize("bucket_ts", ["not-a-timestamp", float("nan"), 1000.5])
def test_tape_loader_skips_malformed_nonfinite_or_noninteger_bucket_without_crash(
    tmp_path, monkeypatch, bucket_ts,
):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    row = _tape_row(1000)
    row["bucket_ts"] = bucket_ts
    canonical = json.dumps(
        {key: value for key, value in row.items() if key != "row_sha256"},
        sort_keys=True, separators=(",", ":"), allow_nan=True,
    )
    row["row_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    _write_jsonl(tmp_path / "market_microstructure_1s.jsonl", [row])

    assert engine._one_second_tape_by_bucket() == {}


def test_embedded_terminal_tape_cannot_bypass_canonical_tape_join(
    tmp_path, monkeypatch,
):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _identity_ledgers(tmp_path)
    rows = _signed_rows()
    assert rows[-1]["tape_evidence"]["fill_status"] == "FULL_FILL"
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", rows)

    report = engine.build_missed_opportunity_proof_report(session={})
    result = report["proofs"][0]

    assert result["classification"] == "INSUFFICIENT_EVIDENCE"
    assert result["conservative_fill_status"] == "UNKNOWN_UNVERIFIABLE"
    assert result["execution_outcome"] == "UNKNOWN"
    assert report["execution_outcome_contract"] == [
        "FULL_FILL", "PARTIAL_FILL", "NO_FILL", "UNKNOWN",
    ]
    assert report["execution_outcome_counts"] == {
        "FULL_FILL": 0, "PARTIAL_FILL": 0, "NO_FILL": 0, "UNKNOWN": 1,
    }
    assert result["coverage"]["tape_receipt"].startswith("tape-join-sha256-")
    assert result["coverage"]["tape_receipt"] != "tape-sha256-1"
    assert result["coverage"]["tape_status"] == "INSUFFICIENT"
    assert "UNKNOWN_MISSING_OR_CONFLICTING_1S_TAPE" in result["coverage"]["rejection_codes"]


def test_report_missing_signed_quantity_constraints_remains_unknown(tmp_path, monkeypatch):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _identity_ledgers(tmp_path)
    rows = _signed_rows()
    for item in rows:
        item.pop("signed_quantity_constraints", None)
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", rows)
    _write_complete_canonical_tape(tmp_path)

    result = engine.build_missed_opportunity_proof_report(session={})["proofs"][0]

    assert result["classification"] == "INSUFFICIENT_EVIDENCE"
    assert result["conservative_fill_status"] == "UNKNOWN_UNVERIFIABLE"
    assert result["execution_outcome"] == "UNKNOWN"
    assert result["coverage"]["status"] == "INSUFFICIENT"
    assert result["coverage"]["signed_quantity_constraints"] is None
    assert result["coverage"]["quantity_constraint_reasons"] == [
        "SIGNED_QUANTITY_CONSTRAINTS_MISSING"
    ]
    assert "UNKNOWN_SIGNED_QUANTITY_CONSTRAINTS_MISSING" in (
        result["coverage"]["rejection_codes"]
    )


def test_execution_outcome_contract_keeps_all_noncanonical_states_unknown():
    engine = _load_engine()

    assert engine._EXECUTION_OUTCOME_CLASSES == (
        "FULL_FILL", "PARTIAL_FILL", "NO_FILL", "UNKNOWN",
    )
    assert engine._normalized_execution_outcome("FULL_FILL") == "FULL_FILL"
    assert engine._normalized_execution_outcome("PARTIAL_FILL") == "PARTIAL_FILL"
    assert engine._normalized_execution_outcome("NO_FILL") == "NO_FILL"
    for status in (None, "", "UNKNOWN_UNVERIFIABLE", "INELIGIBLE", "UNSUPPORTED"):
        assert engine._normalized_execution_outcome(status) == "UNKNOWN"


@pytest.mark.parametrize("mutation,expected_code", [
    ("duplicate", "DUPLICATE_STAGE_INDEX"),
    ("arbitrary", "STAGE_INDEX_SET_MISMATCH"),
])
def test_duplicate_or_arbitrary_stage_indexes_fail_closed(
    tmp_path, monkeypatch, mutation, expected_code,
):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _identity_ledgers(tmp_path)
    rows = _signed_rows()
    if mutation == "duplicate":
        rows.insert(1, dict(rows[0]))
    else:
        rows[5]["stage_index"] = 99
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", rows)
    result = engine.build_missed_opportunity_proof_report(session={})["proofs"][0]
    assert result["classification"] == "INSUFFICIENT_EVIDENCE"
    assert expected_code in result["coverage"]["rejection_codes"]


def test_gross_path_without_explicit_costs_cannot_be_proven(tmp_path, monkeypatch):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _identity_ledgers(tmp_path)
    rows = _signed_rows()
    for item in rows:
        item.pop("entry_fee_rate", None)
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", rows)
    _write_complete_canonical_tape(tmp_path)

    row = engine.build_missed_opportunity_proof_report(session={})["proofs"][0]
    assert row["gross_terminal_return_pct"] > 0
    assert row["net_terminal_return_pct"] is None
    assert row["classification"] == "INSUFFICIENT_EVIDENCE"
    assert row["cost_assumption"]["explicit_costs_complete"] is False


@pytest.mark.parametrize("missing", [
    "quantity", "fee_usd", "slippage_usd", "receipt_id", "shared_ai_call_id",
])
def test_observed_schedule_proof_fails_closed_when_required_evidence_is_missing(
    tmp_path, monkeypatch, missing,
):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _identity_ledgers(tmp_path)
    rows = _signed_rows()
    if missing == "shared_ai_call_id":
        for row in rows:
            row[missing] = ""
    else:
        rows[-1]["tape_evidence"].pop(missing)
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", rows)

    row = engine.build_missed_opportunity_proof_report(session={})["proofs"][0]
    assert row["classification"] == "INSUFFICIENT_EVIDENCE"


def test_empty_source_is_truthful_and_not_zero(tmp_path, monkeypatch):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    proof = engine.build_missed_opportunity_proof_report(session={})
    assert proof["proof_count"] == 0
    assert proof["empty_reason"].startswith("SOURCE_EMPTY_OR_UNAVAILABLE")
    assert proof["qualification_eligible"] is False


def test_missed_opportunity_heatmap_writes_strict_json_for_nan_shadow_pnl(
    tmp_path, monkeypatch,
):
    engine = _load_engine()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(engine, "MISSED_OPPORTUNITY_HEATMAP_FILE", str(tmp_path / "heatmap.json"))
    monkeypatch.setattr(
        engine,
        "_load_jsonl_rows",
        lambda path: [{
            "event": "APPROVE_NOT_TRADED",
            "block_reason": "POST_AI_GATE",
            "trade_id": "trade-nan",
            "shadow_pnl_usd": float("nan"),
        }] if path == engine.LANE_OPPORTUNITY_CAPTURE_FILE else [],
    )

    payload = engine.missed_opportunity_heatmap_report(session={})

    assert payload["heatmap"][0]["shadow_pnl_total_usd"] == 0.0
    assert json.loads((tmp_path / "heatmap.json").read_text(encoding="utf-8")) == payload
