"""Tests for lifecycle telemetry and health mismatch alerts."""
from __future__ import annotations

import pytest

from lifecycle_telemetry import (
    LifecycleTelemetry,
    STAGE_SIGNAL,
    STAGE_ORDER,
    STAGE_FILL,
    STAGE_OPEN,
    STAGE_CLOSE,
    LATENCY_FILL_LATENCY,
)
from health_mismatch_alerts import (
    evaluate_alerts,
    RULE_PAPER_REAL_DIVERGENCE,
    RULE_SWITCH_INCONSISTENT,
    RULE_FEED_STALE,
    RULE_LIFECYCLE_STALL,
)


def test_lifecycle_stages_and_latencies():
    t = LifecycleTelemetry()
    t.begin("T1", ts=0.0)
    t.stage("T1", STAGE_SIGNAL, ts=0.1)
    t.stage("T1", STAGE_ORDER, ts=0.2)
    t.stage("T1", STAGE_FILL, ts=0.5)
    t.stage("T1", STAGE_OPEN, ts=0.6)
    t.stage("T1", STAGE_CLOSE, ts=10.6)
    row = t.get("T1")
    assert row["latencies"][LATENCY_FILL_LATENCY] == pytest.approx(0.3)
    assert row["stages"][STAGE_CLOSE] == 10.6


def test_lifecycle_out_of_order_refused():
    t = LifecycleTelemetry()
    t.begin("T1", ts=0.0)
    t.stage("T1", STAGE_ORDER, ts=1.0)
    with pytest.raises(ValueError):
        t.stage("T1", STAGE_SIGNAL, ts=2.0)


def test_lifecycle_unknown_stage_refused():
    t = LifecycleTelemetry()
    with pytest.raises(ValueError):
        t.stage("T1", "BOGUS")


def test_paper_vs_real_price():
    t = LifecycleTelemetry()
    t.paper_vs_real("T1", paper_price=100.0, real_price=101.0)
    pv = t.get("T1")["paper_vs_real"]
    assert pv["diff_usd"] == pytest.approx(-1.0)
    assert pv["diff_pct"] == pytest.approx(-1.0 / 101.0)


def test_alerts_healthy_when_clean():
    res = evaluate_alerts(
        match_report={"diverged": [], "unmatched": []},
        switch_status={"rows": []},
        global_arm={"live_armed": False, "bitfinex_live_enabled": False},
        telemetry={"stalled_trades": [], "feed": {"stale": False, "freshness_sec": 1.0}},
    )
    assert res["healthy"] is True


def test_alerts_divergence():
    res = evaluate_alerts(
        match_report={"diverged": [{"trade_id": "T1"}], "unmatched": []},
        switch_status={"rows": []},
        global_arm={"live_armed": False, "bitfinex_live_enabled": False},
        telemetry={"stalled_trades": [], "feed": {}},
    )
    assert any(a["rule"] == RULE_PAPER_REAL_DIVERGENCE for a in res["alerts"])


def test_alerts_switch_inconsistent():
    res = evaluate_alerts(
        match_report={"diverged": [], "unmatched": []},
        switch_status={"rows": [{"lane": "X", "bitfinex_live_orders": True, "last_denial": []}]},
        global_arm={"live_armed": False, "bitfinex_live_enabled": False},
        telemetry={"stalled_trades": [], "feed": {}},
    )
    assert any(a["rule"] == RULE_SWITCH_INCONSISTENT and a["severity"] == "CRITICAL"
               for a in res["alerts"])


def test_alerts_feed_stale():
    res = evaluate_alerts(
        match_report={"diverged": [], "unmatched": []},
        switch_status={"rows": []},
        global_arm={},
        telemetry={"stalled_trades": [], "feed": {"stale": True, "freshness_sec": 60.0}},
    )
    assert any(a["rule"] == RULE_FEED_STALE for a in res["alerts"])


def test_alerts_lifecycle_stall():
    res = evaluate_alerts(
        match_report={"diverged": [], "unmatched": []},
        switch_status={"rows": []},
        global_arm={},
        telemetry={"stalled_trades": [{"trade_id": "T1"}], "feed": {}},
    )
    assert any(a["rule"] == RULE_LIFECYCLE_STALL for a in res["alerts"])


def test_unmatched_while_armed_is_critical():
    res = evaluate_alerts(
        match_report={"diverged": [], "unmatched": [{"trade_id": "T1"}]},
        switch_status={"rows": []},
        global_arm={"live_armed": True, "bitfinex_live_enabled": True},
        telemetry={"stalled_trades": [], "feed": {}},
    )
    assert any(a["severity"] == "CRITICAL" for a in res["alerts"])
