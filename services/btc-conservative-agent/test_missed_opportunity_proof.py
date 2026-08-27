import importlib.util
import json
from pathlib import Path


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
            "virtual_limit_price": 100.0,
            "reference_price": 100.0,
            "bbo": {"bid": ask - 1, "ask": ask, "last": ask - 0.5},
            "bbo_fresh": True,
            "bbo_valid": True,
            "coverage_status": "COMPLETE",
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
    assert row["net_terminal_return_pct"] is None
    assert row["coverage"]["tape_status"] in {"UNAVAILABLE", "INSUFFICIENT"}


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


def test_empty_source_is_truthful_and_not_zero(tmp_path, monkeypatch):
    engine = _load_engine()
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    proof = engine.build_missed_opportunity_proof_report(session={})
    assert proof["proof_count"] == 0
    assert proof["empty_reason"].startswith("SOURCE_EMPTY_OR_UNAVAILABLE")
    assert proof["qualification_eligible"] is False
