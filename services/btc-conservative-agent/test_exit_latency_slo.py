import exit_latency_slo as e


def _row(tid, trig, fill, reason="GS_STOP"):
    return {"trade_id": tid, "research_lane": "L", "exit_reason": reason,
            "event_timestamps": {"exit_trigger_ts": trig, "exit_fill_ts": fill}}


def test_slo_green_and_red_on_single_5s_breach():
    rows = [_row(str(i), 100.0 + i, 100.5 + i) for i in range(20)]
    ok = e.evaluate(rows)
    assert ok["status"] == "GREEN" and ok["p95_sec"] == 0.5 and ok["slo_met"]
    bad = e.evaluate(rows + [_row("x", 200.0, 205.6)])
    assert bad["status"] == "RED" and bad["breach_count"] == 1
    assert bad["breaches_over_5s"][0]["exit_fill_utc"].endswith("Z")


def test_forced_and_missing_trigger_excluded():
    out = e.evaluate([_row("a", 1.0, 30.0, "ADMIN_MANUAL_CLOSE"), _row("b", None, 5.0)],
                     forced={"ADMIN_MANUAL_CLOSE"})
    assert out["n"] == 0 and out["missing_trigger_ts"] == 1 and out["status"] == "NO_DATA"


def test_iso_ms():
    assert e.iso_ms(1791569364.3341) == "2026-10-09T18:09:24.334Z"
