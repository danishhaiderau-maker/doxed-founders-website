"""Purity audit: ledger rows with no top-level time are placed by nested *_ts windows (never admitted by the guard)."""
import json

import analyzer_epoch_guard as eg

EPOCH_ID = "ce-20261004-v31-freeze21b"
MANIFEST = {"epoch_id": EPOCH_ID, "started_at_ts": 1_791_000_000.0}
FLOOR = 1_792_263_671.2608788
REL = "v3/ledgers/signal_snapshot.jsonl"


def seg(start, end):
    return {"epoch_id": "epoch-x", "segment_ref": {"start_ts": start, "end_ts": end},
            "requested_start_ts": start, "coverage": {"observed_end_ts": end}}


def write(tmp_path, rows):
    p = tmp_path / "signal_snapshot.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def test_nested_ts_class_buckets():
    assert eg.nested_ts_class(seg(FLOOR + 10, FLOOR + 70), FLOOR) == eg.NESTED_OK
    assert eg.nested_ts_class(seg(FLOOR - 10, FLOOR + 50), FLOOR) == eg.NESTED_STRADDLE
    assert eg.nested_ts_class(seg(FLOOR - 100, FLOOR - 40), FLOOR) == eg.NESTED_PRE_EPOCH
    assert eg.nested_ts_class({"epoch_id": "x", "n": 3}, FLOOR) == eg.NESTED_UNSTAMPED


def test_counts_split_straddle_and_unstamped_from_real_pre_epoch(tmp_path):
    p = write(tmp_path, [seg(FLOOR + 10, FLOOR + 70), seg(FLOOR - 10, FLOOR + 50), seg(FLOOR - 100, FLOOR - 40),
                         {"epoch_id": "x"}])
    c = eg.file_epoch_row_counts(p, REL, MANIFEST, FLOOR)
    assert c == {"pre_epoch": 1, "straddle": 1, "unstamped": 1}  # the wholly-post row is not counted anywhere
    assert eg.file_pre_epoch_rows(p, REL, MANIFEST, FLOOR) == 1


def test_top_level_pre_epoch_time_still_counts_pre_epoch(tmp_path):
    p = write(tmp_path, [{"decision_ts": FLOOR - 5}, {"decision_ts": FLOOR + 5}])
    assert eg.file_epoch_row_counts(p, "v3/ledgers/decision.jsonl", MANIFEST, FLOOR)["pre_epoch"] == 1


def test_guard_admit_stays_fail_closed_for_unstamped_ledger_rows():
    g = eg.EpochGuard(MANIFEST, floor_ts=FLOOR)
    assert g.admit(REL, seg(FLOOR + 10, FLOOR + 70)) is False  # no top-level time: still never admitted/countable
    assert g.admit(REL, seg(FLOOR - 100, FLOOR - 40)) is False


def test_report_exposes_separate_counters_and_only_pre_epoch_is_admitted(tmp_path):
    p = write(tmp_path, [seg(FLOOR - 10, FLOOR + 50), {"epoch_id": "x"}])
    mon = eg.StreamReadMonitor([str(tmp_path)]) if hasattr(eg, "StreamReadMonitor") else None
    assert mon is not None
    mon.files[str(p)] = {"stream": REL, "epoch_filtered": 0, "inventory": 0, "raw": 1, "raw_sites": ["x:y:1"]}
    rep = mon.report(MANIFEST, FLOOR)
    assert rep["pre_epoch_rows_admitted"] == 0
    assert rep["straddle_rows_unguarded"] == 1 and rep["unstamped_rows_unguarded"] == 1
    assert rep["straddle_rows_unguarded_by_stream"] == {REL: 1}


def test_xvp_shadow_rows_keyed_by_anchor_bucket_and_evaluated_ts_are_placed_not_flagged(tmp_path):
    """Live case: xvp_shadow_signals rows carry anchor_bucket_ts/evaluated_ts (not in data_epoch.TS_KEYS), so every new
    row was UNDATED and counted as 'pre-epoch admitted' (grew with each row). They are placed by nested *_ts."""
    rows = [{"schema": "xvp_shadow_trigger_v1", "anchor_bucket_ts": FLOOR + 60 * i, "evaluated_ts": FLOOR + 60 * i + 1,
             "side": "LONG"} for i in range(1, 6)]
    p = write(tmp_path, rows)
    assert eg.file_epoch_row_counts(p, "xvp_shadow_signals.jsonl", MANIFEST, FLOOR) == {
        "pre_epoch": 0, "straddle": 0, "unstamped": 0}
    old = write(tmp_path, [{"schema": "xvp_shadow_trigger_v1", "anchor_bucket_ts": FLOOR - 900, "evaluated_ts": FLOOR - 899}])
    assert eg.file_epoch_row_counts(old, "xvp_shadow_signals.jsonl", MANIFEST, FLOOR)["pre_epoch"] == 1
