import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent


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

    proof = engine.build_missed_opportunity_proof_report(session={})
    row = proof["proofs"][0]
    assert proof["schema"] == "missed_opportunity_proof_v1"
    assert row["classification"] == "PROVEN_MISSED_PROFIT"
    assert row["coverage"]["status"] == "COMPLETE"
    assert row["conservative_touch"] is True
    assert row["qualification_eligible"] is False
    assert row["cost_assumption"]["fee_profile"] == "BITFINEX_ZERO"
    assert row["cost_assumption"]["explicit_costs_complete"] is True
    assert row["coverage"]["tape_receipt"] == "tape-sha256-1"

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


def test_gross_path_without_explicit_costs_cannot_be_proven(tmp_path, monkeypatch):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    _identity_ledgers(tmp_path)
    rows = _signed_rows()
    rows[-1]["tape_evidence"].pop("slippage_usd")
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", rows)

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
