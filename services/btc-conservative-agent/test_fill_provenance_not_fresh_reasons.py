"""Per-reason not_fresh breakdown + quarantine counter on /api/monitor/fill-provenance (HM 19 Oct, queue item 6)."""
import monitor_fill_provenance as mfp

SNAP = {"s": 1, "b": [[1, 1]], "a": [[2, 1]]}
LANE = "FAMILY_GSB1_CVD_DIV_REGIME"


def _row(tid, *, fresh=False, epoch="fse-1", exit_status="PAPER_FILL", exit_snap=True, quarantined=False, all_book=True):
    p = {"fresh_eligible": fresh, "fill_model": "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY", "fill_model_version": 1,
         "fresh_epoch_id": epoch, "exit_status": exit_status, "all_legs_book10l": all_book,
         "depth_snapshot_signal": SNAP, "depth_snapshot_fill": SNAP}
    if exit_snap:
        p["exit_depth_snapshot_fill"] = SNAP
    return {"trade_id": tid, "status": "CLOSED", "lane": LANE, "identity_quarantined": quarantined, "pricing": p}


def test_primary_reason_per_not_fresh_row_and_secondary_writer_flags():
    rows = [
        _row("ok", fresh=True),
        _row("q", quarantined=True),
        _row("f1", exit_status="FORCED_NO_BOOK"),
        _row("f2", exit_status="PAPER_FILL_FORCED"),
        _row("b1", epoch=""),
        _row("b2", epoch="", exit_snap=False),                 # blank epoch wins; the missing snapshot is a flag
        _row("m1", exit_snap=False),
        _row("l1", all_book=False),
        _row("u1"),
    ]
    out = mfp.build({LANE: rows}, {LANE: "B1"}, [], None, {})
    t = out["per_tile"][0]
    assert t["fresh_eligible"] == 1 and t["not_fresh"] == 8 and t["identity_quarantined"] == 1
    assert t["not_fresh_reasons"] == {
        "IDENTITY_QUARANTINED": 1, "FORCED_EXIT:FORCED_NO_BOOK": 1, "FORCED_EXIT:PAPER_FILL_FORCED": 1,
        "BLANK_FRESH_EPOCH_ID": 2, "MISSING_EXIT_FILL_SNAPSHOT": 1, "LEG_NOT_BOOK10L": 1,
        "FRESH_ELIGIBLE_FALSE_UNEXPLAINED": 1}
    assert sum(t["not_fresh_reasons"].values()) == t["not_fresh"]
    assert t["writer_flags"] == {"blank_fresh_epoch_id": 2, "missing_exit_fill_snapshot": 2}
