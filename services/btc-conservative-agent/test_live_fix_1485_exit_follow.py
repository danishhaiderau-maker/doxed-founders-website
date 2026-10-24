import exit_latency_slo as els


def _c(cid, lane, ts):
    return {cid: [{"type": "ORDER_FILLED", "lane": lane, "sent_at_ts": ts - 60},
                  {"type": "POSITION_CLOSED", "lane": lane, "sent_at_ts": ts}]}


def test_prefix_filter_and_alert_over_3s():
    by = {**_c("gb1-a", "B1", 1000.0 + 1.5), **_c("gb1-b", "B1", 2000.0 + 4.2), **_c("gs1-x", "GS_01", 3005)}
    tw = {"gb1-a": {"exit_ts": 1000.0}, "gb1-b": {"exit_ts": 2000.0}, "gs1-x": {"exit_ts": 3000}}
    out = els.live_follow(by, tw, since=0, prefixes=("B1",))
    assert set(out["lanes"]) == {"B1"}
    d = out["lanes"]["B1"]
    assert d["n"] == 2 and d["amber_count"] == 1 and d["breach_count"] == 0
    assert d["ambers_over_3s"][0]["trade_id"] == "gb1-b"
    assert out["alert"] is True and out["status"] == "AMBER"


def test_live_exit_red_over_8s_names_top_hop():
    by = _c("gs1-slow", "GS_01", 1000.0 + 9.5)
    tw = {"gs1-slow": {"exit_ts": 1000.0}}
    hops = {"gs1-slow": {"paper_exit->executor_claim": 4.1, "claim->close_sent": 5.0, "close_sent->fill": 0.4}}
    out = els.live_follow(by, tw, since=0, prefixes=("GS_01",), hop_lags_by_corr=hops)
    d = out["lanes"]["GS_01"]
    assert out["status"] == "RED" and d["breach_count"] == 1
    assert d["breaches_over_8s"][0]["top_hop"] == "claim->close_sent"


def test_fast_exits_green():
    out = els.live_follow(_c("gs1-a", "GS_01", 101.2), {"gs1-a": {"exit_ts": 100.0}}, since=0)
    assert out["status"] == "GREEN" and out["lanes"]["GS_01"]["max_sec"] == 1.2


def test_disloc_retired_live_follow_is_empty_no_data():
    # Tile 6/7 (DISLOC / DISLOC-FOI) removed 23 Oct 2026: no default DISLOC filter, no
    # DISLOC normaliser, and the bot's view passes no journal -> stable NO_DATA shape.
    assert els.LIVE_FOLLOW_LANE_PREFIXES == ()
    assert not hasattr(els, "DISLOC_EXIT_LANES") and not hasattr(els, "paper_rows_from_disloc_ledger")
    out = els.live_follow({}, {}, since=0)
    assert out["schema"] == "live_exit_follow_v1" and out["lanes"] == {} and out["status"] == "NO_DATA"
    src = open("bot.py", encoding="utf-8").read()
    assert "_els.live_follow({}, {}, since=since)" in src


def test_endpoint_wires_live_follow():
    src = open("bot.py", encoding="utf-8").read()
    assert '"live_follow": live_follow' in src and "_els.live_follow(" in src
    assert "_exit_latency_closed_trade_rows" in src
    assert "live_on_lanes=inp.get(\"live_on_lanes\")" in src


def test_monitor_surfaces_entitlement_and_pause():
    src = open("live_copy_monitor.py", encoding="utf-8").read()
    assert "ENTITLEMENT_INACTIVE" in src and "ACCOUNT_PAUSED" in src
