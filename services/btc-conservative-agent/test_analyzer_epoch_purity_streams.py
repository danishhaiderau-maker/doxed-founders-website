"""Epoch purity per retained stream: retained pre-epoch rows stay on disk and never reach a result.

Every stream the final-e reset retained with pre-epoch rows is exercised through
the guarded loaders and through a raw open; the read monitor must account the
raw open's pre-epoch rows as admitted and the guarded read as clean.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd
import pytest

import analyzer_epoch_guard as eg
import data_epoch as de

EPOCH = "ce-20261004-v31-final-e"
OLD = "ce-20261004-v31-final"
START = 1_791_078_018.0
PRE, POST = START - 600, START + 600

RETAINED_STREAMS = (
    "adaptive_entry_decisions.jsonl", "ai_confidence_calibration.jsonl", "counterfactual.jsonl",
    "expired_orders_3factor.csv", "golden_stack_rejections.jsonl", "order_multiverse.jsonl",
    "order_multiverse_entry_grid.jsonl", "post_exit_replay.jsonl", "pre_entry_evidence_handoffs.jsonl",
    "relay_outbox_quarantine.jsonl", "retired_tile_boundary_receipts.jsonl", "runtime_telemetry_1m.jsonl",
    "shadow_outcome.jsonl", "signal_replay.jsonl", "signal_snapshot.jsonl",
    "taker_signal_counterfactuals.jsonl", "xvp_shadow_signals.jsonl",
)


@pytest.fixture
def guard(tmp_path):
    guard = eg.EpochGuard(de.new_manifest(EPOCH, started_at_ts=START))
    eg.set_process_guard(guard)
    monitor = eg.StreamReadMonitor([tmp_path]).install()
    yield guard, monitor
    eg.set_process_guard(None)
    eg._ACTIVE_MONITOR = None


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "trade_id", "reason"])
        writer.writeheader()
        writer.writerows(rows)


def _mixed(path: Path):
    """Two current rows (stamped, unstamped post-epoch) and two pre-epoch rows (foreign stamp, early ts)."""
    if path.name.endswith(".csv"):
        _write_csv(path, [
            {"timestamp": de.utc_iso(PRE), "trade_id": "old", "reason": "TTL_EXPIRED"},
            {"timestamp": de.utc_iso(POST), "trade_id": "cur", "reason": "TTL_EXPIRED"},
            {"timestamp": de.utc_iso(PRE - 5), "trade_id": "old2", "reason": "TTL_EXPIRED"},
            {"timestamp": de.utc_iso(POST + 5), "trade_id": "cur2", "reason": "TTL_EXPIRED"},
        ])
    else:
        _write_jsonl(path, [
            {"trade_id": "old", "ts": POST, "data_epoch_id": OLD},
            {"trade_id": "cur", "ts": POST, "data_epoch_id": EPOCH},
            {"trade_id": "old2", "ts": PRE},
            {"trade_id": "cur2", "ts": POST + 5},
        ])
    return path


def _ids(rows):
    return sorted(r["trade_id"] for r in rows)


def _engine():
    """The service analyzer, never the fail-closed ``research/`` stub another suite may put first on sys.path."""
    import importlib
    import sys

    here = Path(__file__).resolve().parent
    loaded = sys.modules.get("analyzer_research_engine_v62")
    if loaded is not None and Path(getattr(loaded, "__file__", "") or "").resolve().parent != here:
        del sys.modules["analyzer_research_engine_v62"]
    if sys.path[:1] != [str(here)]:
        sys.path.insert(0, str(here))
    return importlib.import_module("analyzer_research_engine_v62")


@pytest.mark.parametrize("stream", RETAINED_STREAMS)
def test_retained_stream_guarded_read_admits_only_current_epoch(tmp_path, guard, stream):
    g, monitor = guard
    path = _mixed(tmp_path / stream)
    assert eg.file_pre_epoch_rows(path, stream, g.manifest) == 2
    if stream.endswith(".csv"):
        with eg.guarded_open(path, newline="", encoding="utf-8") as handle:
            rows = eg.epoch_csv_rows(csv.DictReader(handle), path)
    else:
        rows = [json.loads(line) for line in eg.epoch_lines(path, encoding="utf-8")]
    assert _ids(rows) == ["cur", "cur2"]
    report = monitor.report(g.manifest)
    assert report["pre_epoch_rows_admitted"] == 0 and report["unguarded_stream_reads"] == {}
    assert report["epoch_filtered_stream_opens"] == {stream: 1}
    assert path.read_bytes().count(b"old") == 2  # retained file untouched


@pytest.mark.parametrize("stream", RETAINED_STREAMS)
def test_retained_stream_raw_read_counts_every_pre_epoch_row_as_admitted(tmp_path, guard, stream):
    g, monitor = guard
    path = _mixed(tmp_path / stream)
    with open(path, encoding="utf-8") as handle:
        handle.read()
    report = monitor.report(g.manifest)
    assert report["pre_epoch_rows_admitted"] == 2
    assert report["pre_epoch_rows_admitted_by_stream"] == {stream: 2}
    site = report["unguarded_stream_reads"][stream]["sites"][0]
    assert site.startswith("test_analyzer_epoch_purity_streams.py:")


def test_monitor_ignores_writes_inventory_and_epoch_independent_streams(tmp_path, guard):
    g, monitor = guard
    path = _mixed(tmp_path / "counterfactual.jsonl")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n")
    with eg.guarded_read("counterfactual.jsonl", "inventory"):
        open(path, "rb").close()
    _write_jsonl(tmp_path / "market_context_1m.jsonl", [{"minute_ts": PRE}])
    open(tmp_path / "market_context_1m.jsonl", "rb").close()
    _write_jsonl(tmp_path / "v3" / "receipts" / "r.jsonl", [{"ts": PRE}])
    open(tmp_path / "v3" / "receipts" / "r.jsonl", "rb").close()
    report = monitor.report(g.manifest)
    assert report["pre_epoch_rows_admitted"] == 0 and report["unguarded_stream_reads"] == {}
    assert report["inventory_stream_opens"] == {"counterfactual.jsonl": 1}


def test_suspended_epoch_lines_generator_does_not_launder_other_opens(tmp_path, guard):
    g, monitor = guard
    lines = eg.epoch_lines(_mixed(tmp_path / "signal_replay.jsonl"), encoding="utf-8")
    next(lines)
    with open(_mixed(tmp_path / "shadow_outcome.jsonl"), encoding="utf-8") as handle:
        handle.read()
    lines.close()
    report = monitor.report(g.manifest)
    assert report["pre_epoch_rows_admitted_by_stream"] == {"shadow_outcome.jsonl": 2}


def test_stream_names_cover_rotations_and_ledgers(tmp_path):
    assert eg.stream_name(tmp_path / "signal_replay.jsonl.3") == "signal_replay.jsonl"
    assert eg.stream_name(tmp_path / "v3" / "ledgers" / "decision.jsonl") == "v3/ledgers/decision.jsonl"
    assert eg.stream_relpath(tmp_path, tmp_path / "v3" / "ledgers" / "decision.jsonl.2") == "v3/ledgers/decision.jsonl"
    assert eg.stream_relpath(tmp_path, tmp_path / "market_microstructure_1s.jsonl") is None
    assert eg.stream_relpath(tmp_path, tmp_path / "x.validation.json") is None
    assert eg.stream_relpath(tmp_path, tmp_path / "canonical_dataset_manifest.jsonl") is None


# ------------------------------------------------------------------ analyzer readers

def test_adaptive_entry_funnel_readers(tmp_path, guard):
    import adaptive_entry_funnel as aef

    assert _ids(aef._read_jsonl(str(_mixed(tmp_path / "adaptive_entry_decisions.jsonl")))) == ["cur", "cur2"]
    assert _ids(aef._read_csv(str(_mixed(tmp_path / "expired_orders_3factor.csv")))) == ["cur", "cur2"]
    assert guard[1].report(guard[0].manifest)["pre_epoch_rows_admitted"] == 0


def test_multiverse_collection_health_lines(tmp_path, guard):
    import multiverse_collection_health as mch

    lines = list(mch._lines(str(_mixed(tmp_path / "order_multiverse.jsonl"))))
    assert _ids(json.loads(line) for line in lines) == ["cur", "cur2"]
    assert guard[1].report(guard[0].manifest)["pre_epoch_rows_admitted"] == 0


def test_stream_studies_iter_json(tmp_path, guard):
    from strategy_lab import stream_studies

    assert _ids(stream_studies._iter_json(str(_mixed(tmp_path / "post_exit_replay.jsonl")))) == ["cur", "cur2"]
    assert stream_studies.content_signature(str(tmp_path / "post_exit_replay.jsonl"))
    report = guard[1].report(guard[0].manifest)
    assert report["pre_epoch_rows_admitted"] == 0 and report["inventory_stream_opens"] == {"post_exit_replay.jsonl": 1}


def test_data_health_readers(tmp_path, guard):
    from research import data_health_report as dhr

    assert _ids(dhr._iter_json([str(_mixed(tmp_path / "counterfactual.jsonl"))])) == ["cur", "cur2"]
    _mixed(tmp_path / "signal_replay.jsonl")
    assert dhr.signal_replay_health(str(tmp_path))["distinct_trades"] == 2
    assert guard[1].report(guard[0].manifest)["pre_epoch_rows_admitted"] == 0


def test_tile_evidence_and_input_blocker_readers(tmp_path, guard):
    from research import input_blockers, tile_evidence_points

    assert _ids(tile_evidence_points._read_csv(str(_mixed(tmp_path / "expired_orders_3factor.csv")))) == ["cur", "cur2"]
    assert _ids(tile_evidence_points._read_jsonl(str(_mixed(tmp_path / "shadow_outcome.jsonl")))) == ["cur", "cur2"]
    assert _ids(input_blockers._read_jsonl(_mixed(tmp_path / "counterfactual.jsonl"))) == ["cur", "cur2"]
    assert guard[1].report(guard[0].manifest)["pre_epoch_rows_admitted"] == 0


def test_research_completeness_shadow_rows(tmp_path, guard):
    import research_completeness_report as rcr

    _mixed(tmp_path / "shadow_outcome.jsonl")
    rows, receipt = rcr.load_shadow_rows(tmp_path)
    assert _ids(rows) == ["cur", "cur2"] and receipt["files"]["shadow_outcome.jsonl"] == 2
    assert guard[1].report(guard[0].manifest)["pre_epoch_rows_admitted"] == 0


def test_engine_loaders_and_receipt_with_monitor(tmp_path, guard, monkeypatch):
    engine = _engine()

    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BTC_DATA_EPOCH_DIR", str(tmp_path))
    monkeypatch.setattr(engine, "_EPOCH_GUARD", None)
    monkeypatch.setattr(engine, "_EPOCH_MANIFEST_DIR", None)
    de.write_json_atomic(tmp_path / de.MANIFEST_NAME, de.new_manifest(EPOCH, started_at_ts=START))
    pd.DataFrame({"trade_id": ["b"], "ts": [de.utc_iso(POST)]}).to_csv(tmp_path / engine.TRADES_FILE, index=False)
    _mixed(tmp_path / "counterfactual.jsonl")
    _mixed(tmp_path / "expired_orders_3factor.csv")
    _mixed(tmp_path / "xvp_shadow_signals.jsonl")

    assert _ids(engine._load_jsonl_rows("counterfactual.jsonl")) == ["cur", "cur2"]
    assert sorted(engine.robust_read_csv("expired_orders_3factor.csv")["trade_id"]) == ["cur", "cur2"]
    assert engine._file_time_span(str(tmp_path / "counterfactual.jsonl"))[2] == 2
    block = engine._epoch_receipt_block()
    assert block["pre_epoch_rows_admitted"] == 0 and block["pre_epoch_rows_admitted_by_stream"] == {}
    assert block["pre_epoch_rows_retained_by_stream"] == {
        "counterfactual.jsonl": 2, "expired_orders_3factor.csv": 2, "xvp_shadow_signals.jsonl": 2}
    assert block["read_monitor"]["active"] is True

    with open(tmp_path / "xvp_shadow_signals.jsonl", encoding="utf-8") as handle:
        handle.read()
    block = engine._epoch_receipt_block()
    assert block["pre_epoch_rows_admitted_by_stream"] == {"xvp_shadow_signals.jsonl": 2}


def test_engine_trade_readers_admit_only_current_epoch(tmp_path, guard, monkeypatch):
    engine = _engine()

    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BTC_DATA_EPOCH_DIR", str(tmp_path))
    monkeypatch.setenv("RESEARCH_HISTORICAL_BUNDLE_GLOB", str(tmp_path / "no-bundles-*.zip"))
    monkeypatch.setattr(engine, "_EPOCH_GUARD", None)
    monkeypatch.setattr(engine, "_EPOCH_MANIFEST_DIR", None)
    trades = tmp_path / "trades_3factor.csv"
    monkeypatch.setattr(engine, "TRADES_FILE", str(trades))
    de.write_json_atomic(tmp_path / de.MANIFEST_NAME, de.new_manifest(EPOCH, started_at_ts=START))
    pd.DataFrame({"trade_id": ["old", "cur", "old2"], "net_pnl_usd": [1.0, 2.0, 3.0],
                  "ts": [de.utc_iso(PRE), de.utc_iso(POST), de.utc_iso(PRE - 5)]}).to_csv(trades, index=False)

    assert engine._load_executed_trade_ids() == {"cur"}
    cohort = engine.historical_trade_cohort_report()
    assert json.dumps(cohort, default=str).count('"old') == 0
    block = engine._epoch_receipt_block()
    assert block["pre_epoch_rows_admitted"] == 0, block["read_monitor"]["unguarded_stream_reads"]
    assert block["pre_epoch_rows_retained_by_stream"] == {"trades_3factor.csv": 2}


def test_provenance_journal_is_non_evidence_for_the_purity_audit():
    import data_epoch as de

    assert de.non_evidence("canonical_dataset_manifest.jsonl")
    assert "canonical_dataset_manifest.jsonl" in de.NON_EVIDENCE_BASES
    assert not de.non_evidence("v3/canonical_dataset_manifest.jsonl")
