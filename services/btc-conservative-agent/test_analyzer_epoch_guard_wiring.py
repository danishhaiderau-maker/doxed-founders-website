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


# ---------------------------------------------------------------- #420 regression
def test_ops_ledgers_are_not_evidence_and_read_guarded_rows_are_not_admitted(data_root):
    _declare(data_root)
    pd.DataFrame({"trade_id": ["b"], "ts": [de.utc_iso(POST)]}).to_csv(data_root / engine.TRADES_FILE, index=False)
    _jsonl(data_root / "relay_outbox_quarantine.jsonl", [{"created_at_unix": PRE, "reason": "STALE_OWNER"}] * 22)
    _jsonl(data_root / "runtime_telemetry_1m.jsonl", [{"ts": PRE}] * 5)
    _jsonl(data_root / "retired_tile_boundary_receipts.jsonl", [{"ts": PRE, "data_epoch_id": "ce-old"}])
    _jsonl(data_root / "taker_signal_counterfactuals.jsonl",
           [{"signal_ts": PRE, "data_epoch_id": "ce-old"}, {"signal_ts": POST, "data_epoch_id": EPOCH}])
    _jsonl(data_root / "adaptive_entry_decisions.jsonl", [{"ts": PRE}, {"ts": POST, "data_epoch_id": EPOCH}])
    (data_root / engine.EXPIRED_ORDERS_FILE).write_text(
        f"time,trade_id,dir\n{de.utc_iso(PRE)},x,LONG\n{de.utc_iso(POST)},y,SHORT\n", encoding="utf-8")

    block = engine._epoch_receipt_block()
    assert block["pre_epoch_rows_admitted"] == 0 and block["pre_epoch_rows_admitted_by_stream"] == {}
    assert block["pre_epoch_rows_read_guarded_by_stream"] == {
        "adaptive_entry_decisions.jsonl": 1, "expired_orders_3factor.csv": 1, "taker_signal_counterfactuals.jsonl": 1}
    assert block["pre_epoch_rows_read_guarded"] == 3
    assert "relay_outbox_quarantine.jsonl" in block["non_evidence_streams"]
    receipt = build_generation_receipt(_manifest(), integrity={"report_status": "VALID"}, data_epoch=block)
    assert receipt["level"] == "GREEN"


def test_unguarded_evidence_stream_still_fails_purity(data_root):
    _declare(data_root)
    pd.DataFrame({"trade_id": ["b"], "ts": [de.utc_iso(POST)]}).to_csv(data_root / engine.TRADES_FILE, index=False)
    _jsonl(data_root / "post_exit_replay.jsonl", [{"ts": PRE, "data_epoch_id": "ce-old"}, {"ts": POST}])
    block = engine._epoch_receipt_block()
    assert block["pre_epoch_rows_admitted_by_stream"] == {"post_exit_replay.jsonl": 1}


def test_expired_orders_loader_is_epoch_filtered_before_usecols(data_root):
    _declare(data_root)
    path = data_root / engine.EXPIRED_ORDERS_FILE
    path.write_text(f"time,trade_id,dir\n{de.utc_iso(PRE)},x,LONG\n{de.utc_iso(POST)},y,SHORT\n", encoding="utf-8")
    frame = engine._load_expired_orders_csv(str(path), usecols=["trade_id"])
    assert list(frame["trade_id"]) == ["y"]


def test_adaptive_funnel_reads_only_epoch_rows(data_root, monkeypatch):
    import adaptive_entry_funnel as funnel

    _declare(data_root)
    _jsonl(data_root / "adaptive_entry_decisions.jsonl",
           [{"trade_id": "old", "ts": PRE}, {"trade_id": "new", "ts": POST, "data_epoch_id": EPOCH}])
    _jsonl(data_root / "taker_signal_counterfactuals.jsonl",
           [{"trade_id": "old", "signal_ts": PRE}, {"trade_id": "new", "signal_ts": POST, "data_epoch_id": EPOCH}])
    (data_root / engine.EXPIRED_ORDERS_FILE).write_text(
        f"time,trade_id,dir\n{de.utc_iso(PRE)},old,LONG\n{de.utc_iso(POST)},new,LONG\n", encoding="utf-8")
    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)
        return {"schema": "adaptive_entry_funnel_v1"}

    monkeypatch.setattr(funnel, "build_report", capture)
    funnel.build_report_from_paths(
        decisions_path=str(data_root / "adaptive_entry_decisions.jsonl"),
        counterfactual_path=str(data_root / "taker_signal_counterfactuals.jsonl"),
        trades_path=str(data_root / engine.TRADES_FILE), expired_path=str(data_root / engine.EXPIRED_ORDERS_FILE),
        current_version="v", admit=engine._epoch_admit)
    for key in ("decisions", "taker_counterfactuals", "expired"):
        assert [r["trade_id"] for r in seen[key]] == ["new"], key
    assert [r["trade_id"] for r in seen["trades"]] == ["b"]


# Every module that names a READ_GUARDED stream, and why it is safe. A new reader must filter through the
# analyzer epoch guard (or stay off the analyzer path) before it is added here (#420).
GUARDED_READERS = {
    "taker_signal_counterfactuals.jsonl": {
        "bot.py",                              # Fly writer
        "execution_markouts.py",               # constant only; analyzer reads via adaptive_entry_funnel(admit)
        "strategy_lab/streams.py",             # stream registry, no reads
        "strategy_lab/stream_studies.py",      # taker study keeps ts >= epoch_start only
    },
    "adaptive_entry_decisions.jsonl": {"bot.py", "adaptive_entry_funnel.py"},
    "expired_orders_3factor.csv": {
        "bot.py",                              # Fly writer
        "analyzer_research_engine_v62.py",     # _load_expired_orders_csv filters through the guard
        "research/tile_evidence_points.py",    # load_evidence_inputs(admit=_epoch_admit)
        "research/research_v3_report.py",      # skips rows the data_dir manifest does not class COMPATIBLE
        "research/research_dashboard.py",      # required-input name lists only
        "research_reset_inventory.py",         # reset planner (Fly)
        "data_retention_policy.py",            # retention planner
    },
}


def test_read_guarded_streams_have_only_guarded_readers():
    import pathlib
    import re

    root = pathlib.Path(engine.__file__).resolve().parent
    assert set(GUARDED_READERS) == set(de.READ_GUARDED_BASES)
    for stream, allowed in GUARDED_READERS.items():
        found = set()
        for path in root.rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if rel.startswith(("test_", "tests/")) or "/test_" in rel or rel == "data_epoch.py":
                continue
            if stream in path.read_text(encoding="utf-8", errors="replace"):
                found.add(rel)
        assert found <= allowed, f"unreviewed reader of {stream}: {sorted(found - allowed)}"
    source = (root / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    assert not re.search(r"(read_csv|open)\(\s*(_agent_data_path\()?EXPIRED_ORDERS_FILE", source)
