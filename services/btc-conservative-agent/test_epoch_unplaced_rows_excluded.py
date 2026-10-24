"""Item 5: straddle/unstamped V3 ledger rows are EXCLUDED (never guessed in), listed exactly, and every analyzer
ledger reader opens them through a guarded read, so a generation reports 0 unguarded rows."""
import json
from pathlib import Path

import analyzer_epoch_guard as eg

EPOCH_ID = "ce-20261004-v31-freeze21b"
FLOOR = 1_792_263_671.0
MANIFEST = {"epoch_id": EPOCH_ID, "started_at_ts": FLOOR}


def seg(start, end, rid):
    return {"record_id": rid, "epoch_id": EPOCH_ID, "segment_ref": {"start_ts": start, "end_ts": end}}


def make_root(tmp_path):
    ledgers = tmp_path / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    (ledgers / "market_segment.jsonl").write_text("\n".join(json.dumps(r) for r in [
        seg(FLOOR + 10, FLOOR + 70, "ok-1"), seg(FLOOR - 10, FLOOR + 50, "str-1"), {"record_id": "uns-1"}]) + "\n")
    (ledgers / "decision.jsonl").write_text(json.dumps({"record_id": "uns-2", "epoch_id": EPOCH_ID}) + "\n")
    return tmp_path


def test_unplaced_list_names_each_row_and_is_deterministic(tmp_path):
    root = make_root(tmp_path)
    doc = eg.unplaced_rows_document(root, MANIFEST, FLOOR)
    assert doc["count"] == 3
    assert doc["by_stream"] == {"v3/ledgers/decision.jsonl": {"unstamped": 1},
                                "v3/ledgers/market_segment.jsonl": {"straddle": 1, "unstamped": 1}}
    ids = {(r["stream"], r["line_no"], r["row_id"], r["kind"]) for r in doc["rows"]}
    assert ("v3/ledgers/market_segment.jsonl", 2, "str-1", "straddle") in ids
    assert ("v3/ledgers/decision.jsonl", 1, "uns-2", "unstamped") in ids
    assert all(len(r["row_sha256"]) == 64 for r in doc["rows"])
    assert eg.unplaced_rows_document(root, MANIFEST, FLOOR)["list_sha256"] == doc["list_sha256"]
    assert (root / "v3" / "ledgers" / "decision.jsonl").exists()  # listing never deletes


def test_guard_never_admits_straddle_or_unstamped_rows():
    g = eg.EpochGuard(MANIFEST, floor_ts=FLOOR)
    rel = "v3/ledgers/market_segment.jsonl"
    assert g.admit_v3_ledger_row(rel, seg(FLOOR + 10, FLOOR + 70, "ok")) is True
    assert g.admit_v3_ledger_row(rel, seg(FLOOR - 10, FLOOR + 50, "s")) is False
    assert g.admit_v3_ledger_row(rel, {"record_id": "u"}) is False


def test_ledger_readers_are_guarded_and_exclude_unplaced_rows(tmp_path):
    root = make_root(tmp_path)
    from research.v3_policy_report_adapter import _iter_jsonl
    from research.research_v3_report import _iter_ledger
    mon = eg.StreamReadMonitor([str(root)]).install()
    eg.set_process_guard(eg.EpochGuard(MANIFEST, floor_ts=FLOOR))
    try:
        path = root / "v3" / "ledgers" / "market_segment.jsonl"
        got = [r.get("record_id") for r in _iter_jsonl(path)]
        got2 = [r.get("record_id") for r in _iter_ledger(path)]
        rep = mon.report(MANIFEST, FLOOR)
    finally:
        eg.set_process_guard(None)
        eg._ACTIVE_MONITOR = None
    assert got == ["ok-1"] and got2 == ["ok-1"]
    assert rep["straddle_rows_unguarded"] == 0 and rep["unstamped_rows_unguarded"] == 0
    assert rep["unguarded_stream_reads"] == {}
