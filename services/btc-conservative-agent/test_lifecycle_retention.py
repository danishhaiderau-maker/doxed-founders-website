import live_copy_control as lcc
import live_copy_lifecycle as lcl
import live_copy_monitor as lcm


def _a(cid, ts):
    return {"correlation_id": cid, "trade_id": cid, "event": "ORDER_PLACED", "research_lane": "DISLOC",
            "entry_allowed": True, "created_at_ts": ts, "signal_at_ts": ts}


def _r(cid, ts):
    return {"report_id": f"r-{cid}", "correlation_id": cid, "account": "A", "type": "ORDER_FILLED",
            "fly_received_at_ts": ts, "event_at_ts": ts, "timeline": {"fill_at_ts": ts},
            "fill": {"price": 1.0, "qty": 1.0, "exchange_trade_id": f"x-{cid}"}}


def test_approvals_survive_restart_and_old_rows_drop(tmp_path):
    p = tmp_path / "ap.jsonl"
    led = lcc.ApprovalLedger(p, clock=lambda: 10 * 86400.0)
    led.append(_a("old", 1.0))
    led.append(_a("dsl-1", 10 * 86400.0 - 60))
    again = lcc.ApprovalLedger(p, clock=lambda: 10 * 86400.0 + 5)
    assert [r["correlation_id"] for r in again.rows()] == ["dsl-1"]
    assert again.loaded_rows == 1


def test_restart_no_longer_creates_unlinked(tmp_path):
    p = tmp_path / "ap.jsonl"
    lcc.ApprovalLedger(p, clock=lambda: 1000.0).append(_a("dsl-9", 900.0))
    rebooted = lcc.ApprovalLedger(p, clock=lambda: 2000.0)
    intents = lcl.build_intents(rebooted.rows(), [_r("dsl-9", 950.0)], {}, {}, now=2000.0)
    assert lcl.unlinked(intents, now=2000.0)["unlinked_count"] == 0
    f = lcl.retention_facts(rebooted.rows(), [], boot_ts=2000.0, now=2000.0)
    assert f["pre_restart_intents"] == 1 and f["retained_since_ts"] == 900.0


def test_unlinked_counts_report_only_intents():
    intents = lcl.build_intents([], [_r("ghost", 5000.0), _r("old-ghost", 10.0)], {}, {}, now=90000.0)
    u = lcl.unlinked(intents, now=90000.0)
    assert u["unlinked_count"] == 2 and u["unlinked_new_24h"] == 1
    assert set(u["unlinked_ids"]) == {"ghost", "old-ghost"}


def test_gap_facts_via_registered_ctx():
    lcm.set_gap_ctx_fn(lambda: {"approvals": [_a("dsl-1", 100.0)], "reports": [_r("ghost", 150.0)],
                                "process_started_ts": 200.0, "epoch_ts": 120.0})
    try:
        f = lcm.lifecycle_retention_facts(300.0)
        assert f["covers_epoch"] is True and f["pre_restart_intents"] == 1
        u = lcm.unlinked_facts(300.0)
        assert u == {"new_24h": 1, "total": 1, "ids": ["ghost"]}
    finally:
        lcm.set_gap_ctx_fn(None)


def test_lifecycle_payload_has_g6_g8_fields():
    out = lcl.build({"approvals": [_a("dsl-1", 100.0)], "reports": [_r("ghost", 150.0)],
                     "process_started_ts": 200.0}, now=300.0)
    for k in ("retained_since_ts", "pre_restart_intents", "unlinked_count", "unlinked_ids", "unlinked_new_24h"):
        assert k in out
