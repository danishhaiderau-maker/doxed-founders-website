"""Forced/deploy closes are counted per deploy and flagged when un-stamped, without touching SLO percentiles."""
import exit_latency_slo as e

FORCED = ("ADMIN_MANUAL_CLOSE",)


def row(tid, reason, fill, trig=None):
    ev = {"exit_fill_ts": fill}
    if trig is not None:
        ev["exit_trigger_ts"] = trig
    return {"trade_id": tid, "exit_reason": reason, "event_timestamps": ev}


def test_forced_rows_counted_per_deploy_and_missing_trigger_flagged_but_out_of_slo():
    rows = [row("a", "ADMIN_MANUAL_CLOSE", 1000.0), row("b", "ADMIN_MANUAL_CLOSE", 1002.0),
            row("c", "ADMIN_MANUAL_CLOSE", 9000.0, trig=8999.9), row("o", "GS_THESIS_CUT", 5000.0, trig=4998.0)]
    out = e.evaluate(rows, since=0, forced=FORCED)
    assert out["n"] == 1 and out["missing_trigger_ts"] == 0 and out["p95_sec"] == 2.0  # SLO untouched
    f = out["forced"]
    assert f["forced_closes"] == 3 and f["forced_missing_trigger_ts"] == 2 and f["status"] == "AMBER"
    assert [d["n"] for d in f["per_deploy"]] == [2, 1]
    assert f["per_deploy"][0]["trade_ids_missing"] == ["a", "b"] and f["per_deploy"][1]["n_missing_trigger_ts"] == 0


def test_all_stamped_is_green_and_none_is_no_data_and_window_applies():
    ok = e.evaluate([row("a", "ADMIN_MANUAL_CLOSE", 1000.0, trig=999.9)], since=0, forced=FORCED)["forced"]
    assert ok["status"] == "GREEN" and ok["forced_missing_trigger_ts"] == 0
    assert e.evaluate([], since=0, forced=FORCED)["forced"]["status"] == "NO_DATA"
    old = e.evaluate([row("a", "ADMIN_MANUAL_CLOSE", 10.0)], since=500, forced=FORCED)["forced"]
    assert old["forced_closes"] == 0
