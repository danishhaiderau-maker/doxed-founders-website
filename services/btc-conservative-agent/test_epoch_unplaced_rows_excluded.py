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


def _ledger_rows(tmp_path, name, rows):
    path = tmp_path / "v3" / "ledgers" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    return path


def test_open_v3_ledger_helpers_and_raw_open_control(tmp_path):
    """Raw open of a ledger with straddle/unstamped still RED; open_v3_ledger + admit clears it."""
    ok = seg(FLOOR + 10, FLOOR + 70, "ok")
    straddle = seg(FLOOR - 10, FLOOR + 50, "str")
    unstamped = {"record_id": "uns"}
    path = _ledger_rows(tmp_path, "opportunity", [ok, straddle, unstamped])
    mon = eg.StreamReadMonitor([str(tmp_path)]).install()
    eg.set_process_guard(eg.EpochGuard(MANIFEST, floor_ts=FLOOR))
    try:
        assert eg.v3_ledger_relpath(path) == "v3/ledgers/opportunity.jsonl"
        assert eg.admit_v3_row(path, ok) is True
        assert eg.admit_v3_row(path, straddle) is False
        assert eg.admit_v3_row(path, unstamped) is False
        with open(path, "rb") as handle:
            handle.read()
        raw = mon.report(MANIFEST, FLOOR)
        assert raw["straddle_rows_unguarded"] == 1 and raw["unstamped_rows_unguarded"] == 1
        mon.files.clear()
        admitted = []
        with eg.open_v3_ledger(path, "r", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if eg.admit_v3_row(path, row):
                    admitted.append(row["record_id"])
        guarded = mon.report(MANIFEST, FLOOR)
    finally:
        eg.set_process_guard(None)
        eg._ACTIVE_MONITOR = None
    assert admitted == ["ok"]
    assert guarded["straddle_rows_unguarded"] == 0 and guarded["unstamped_rows_unguarded"] == 0
    assert guarded["unguarded_stream_reads"] == {}


def test_fill_model_latency_reader_is_guarded(tmp_path):
    from research.fill_model import measure_decision_latency

    ok_opp = {"record_id": "ok", "episode_id": "e1", "epoch_id": EPOCH_ID,
              "signal_ts": FLOOR + 20, "segment_ref": {"start_ts": FLOOR + 10, "end_ts": FLOOR + 70}}
    bad_opp = {"record_id": "str", "episode_id": "e2", "epoch_id": EPOCH_ID,
               "signal_ts": FLOOR - 5, "segment_ref": {"start_ts": FLOOR - 10, "end_ts": FLOOR + 50}}
    _ledger_rows(tmp_path, "opportunity", [ok_opp, bad_opp, {"record_id": "uns", "episode_id": "e3"}])
    _ledger_rows(tmp_path, "order_intent", [])
    mon = eg.StreamReadMonitor([str(tmp_path)]).install()
    eg.set_process_guard(eg.EpochGuard(MANIFEST, floor_ts=FLOOR))
    try:
        measure_decision_latency(tmp_path / "v3" / "ledgers")
        rep = mon.report(MANIFEST, FLOOR)
    finally:
        eg.set_process_guard(None)
        eg._ACTIVE_MONITOR = None
    assert rep["straddle_rows_unguarded"] == 0 and rep["unstamped_rows_unguarded"] == 0
    assert rep["unguarded_stream_reads"] == {}


def test_entry_baseline_book_replay_opportunity_reader_is_guarded(tmp_path):
    from research.entry_baseline_book_replay import read_opportunities

    ok = seg(FLOOR + 10, FLOOR + 70, "ok")
    _ledger_rows(tmp_path, "opportunity", [ok, seg(FLOOR - 10, FLOOR + 50, "str"), {"record_id": "uns"}])
    mon = eg.StreamReadMonitor([str(tmp_path)]).install()
    eg.set_process_guard(eg.EpochGuard(MANIFEST, floor_ts=FLOOR))
    try:
        rows = read_opportunities(tmp_path)
        rep = mon.report(MANIFEST, FLOOR)
    finally:
        eg.set_process_guard(None)
        eg._ACTIVE_MONITOR = None
    assert [r["record_id"] for r in rows] == ["ok"]
    assert rep["straddle_rows_unguarded"] == 0 and rep["unstamped_rows_unguarded"] == 0
    assert rep["unguarded_stream_reads"] == {}
