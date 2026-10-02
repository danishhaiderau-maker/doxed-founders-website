import json

import pandas as pd
import pytest

import analyzer_research_engine_v62 as engine
import data_epoch as de
from research.generation_receipt import build_generation_receipt
from strategy_lab.export import generation_health

EPOCH = "ce-20261004-v31-clean"
START = 1_791_100_000.0
PRE, POST = START - 3600, START + 3600


@pytest.fixture
def data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BTC_DATA_EPOCH_DIR", str(tmp_path))
    monkeypatch.setattr(engine, "_EPOCH_GUARD", None)
    monkeypatch.setattr(engine, "_EPOCH_MANIFEST_DIR", None)
    pd.DataFrame({"trade_id": ["a", "b"], "ts": [de.utc_iso(PRE), de.utc_iso(POST)]}).to_csv(
        tmp_path / engine.TRADES_FILE, index=False)
    return tmp_path


def _declare(root):
    de.write_json_atomic(root / de.MANIFEST_NAME, de.new_manifest(EPOCH, started_at_ts=START))


def _jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _manifest():
    return {"generation_id": "g1", "required_report_status": {"core": {"available_in_generation": True}}}


def test_undeclared_epoch_is_inert(data_root):
    frame = pd.read_csv(data_root / engine.TRADES_FILE)
    assert engine._epoch_filter_frame(engine.TRADES_FILE, frame) is frame
    assert engine._epoch_admit("trade_outcome.jsonl", {"ts": PRE}) is True
    assert engine._epoch_receipt_block() is None
    receipt = build_generation_receipt(_manifest(), integrity={"report_status": "VALID"}, data_epoch=None)
    assert "data_epoch" not in receipt and receipt["level"] == "GREEN"


def test_declared_epoch_filters_loaders_and_reports_pre_epoch_rows(data_root):
    _declare(data_root)
    _jsonl(data_root / "trade_outcome.jsonl", [{"trade_id": "a", "ts": PRE}, {"trade_id": "b", "ts": POST}])
    _jsonl(data_root / "v3" / "ledgers" / "decision.jsonl",
           [{"decision_ts": PRE}, {"decision_ts": POST, "data_epoch_id": EPOCH}])

    trades = engine._epoch_filter_frame(engine.TRADES_FILE, pd.read_csv(data_root / engine.TRADES_FILE))
    assert list(trades["trade_id"]) == ["b"]
    assert [r["trade_id"] for r in engine._load_jsonl_rows("trade_outcome.jsonl")] == ["b"]
    assert engine._v2_data_start_ts() == pd.Timestamp(START, unit="s", tz="UTC")

    block = engine._epoch_receipt_block()
    assert block["epoch_id"] == EPOCH
    assert block["pre_epoch_rows_rejected"] == 2
    # Files still hold pre-epoch rows that direct readers could pick up: purity fails closed.
    assert block["pre_epoch_rows_admitted"] == 3
    assert block["pre_epoch_rows_admitted_by_stream"] == {
        "trade_outcome.jsonl": 1, "trades_3factor.csv": 1, "v3/ledgers/decision.jsonl": 1}
    receipt = build_generation_receipt(_manifest(), integrity={"report_status": "VALID"}, data_epoch=block)
    assert receipt["level"] == "RED" and receipt["data_epoch"]["epoch_id"] == EPOCH


def test_clean_epoch_data_root_is_pure(data_root):
    _declare(data_root)
    pd.DataFrame({"trade_id": ["b"], "ts": [de.utc_iso(POST)]}).to_csv(data_root / engine.TRADES_FILE, index=False)
    _jsonl(data_root / "signal_replay.jsonl.1", [{"trade_id": "b", "ts": POST, "data_epoch_id": EPOCH}])
    _jsonl(data_root / "market_context_1m.jsonl", [{"minute_ts": PRE}])

    block = engine._epoch_receipt_block()
    assert block["pre_epoch_rows_admitted"] == 0 and block["pre_epoch_rows_admitted_by_stream"] == {}
    receipt = build_generation_receipt(_manifest(), integrity={"report_status": "VALID"}, data_epoch=block)
    assert receipt["level"] == "GREEN"


def test_unproven_purity_is_red():
    block = {"declared": True, "epoch_id": None, "pre_epoch_rows_admitted": None, "error": "OSError: x"}
    receipt = build_generation_receipt(_manifest(), integrity={"report_status": "VALID"}, data_epoch=block)
    assert receipt["level"] == "RED"
    assert any("purity unproven" in r for r in receipt["reasons"])


def test_summary_generation_health_carries_data_epoch_only_when_declared(tmp_path):
    receipt = build_generation_receipt(_manifest(), integrity={"report_status": "VALID"})
    (tmp_path / "analyzer_generation_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    assert "data_epoch" not in generation_health(str(tmp_path))["generation_receipt"]

    block = {"declared": True, "epoch_id": EPOCH, "pre_epoch_rows_admitted": 0, "pre_epoch_rows_rejected": 7}
    receipt = build_generation_receipt(_manifest(), integrity={"report_status": "VALID"}, data_epoch=block)
    (tmp_path / "analyzer_generation_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    assert generation_health(str(tmp_path))["generation_receipt"]["data_epoch"]["epoch_id"] == EPOCH
