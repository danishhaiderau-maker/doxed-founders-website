import exit_latency_slo as els


def _c(cid, lane, ts):
    return {cid: [{"type": "ORDER_FILLED", "lane": lane, "sent_at_ts": ts - 60},
                  {"type": "POSITION_CLOSED", "lane": lane, "sent_at_ts": ts}]}


def test_disloc_lane_and_alert_over_3s():
    by = {**_c("dsl-a", "DISLOC", 1000.0 + 1.5), **_c("dsl-b", "DISLOC", 2000.0 + 4.2), **_c("gs1-x", "GS_01", 3005)}
    tw = {"dsl-a": {"exit_ts": 1000.0}, "dsl-b": {"exit_ts": 2000.0}, "gs1-x": {"exit_ts": 3000}}
    out = els.live_follow(by, tw, since=0)
    assert set(out["lanes"]) == {"FAMILY_DISLOC_BFX_DISLOCATION_1M"}
    d = out["lanes"]["FAMILY_DISLOC_BFX_DISLOCATION_1M"]
    assert d["n"] == 2 and d["amber_count"] == 1 and d["breach_count"] == 0
    assert d["ambers_over_3s"][0]["trade_id"] == "dsl-b"
    assert out["alert"] is True and out["status"] == "AMBER"


def test_live_exit_red_over_8s_names_top_hop():
    by = _c("dsl-slow", "FAMILY_DISLOC_BFX_DISLOCATION_1M", 1000.0 + 9.5)
    tw = {"dsl-slow": {"exit_ts": 1000.0}}
    hops = {"dsl-slow": {"paper_exit->executor_claim": 4.1, "claim->close_sent": 5.0, "close_sent->fill": 0.4}}
    out = els.live_follow(by, tw, since=0, hop_lags_by_corr=hops)
    d = out["lanes"]["FAMILY_DISLOC_BFX_DISLOCATION_1M"]
    assert out["status"] == "RED" and d["breach_count"] == 1
    assert d["breaches_over_8s"][0]["top_hop"] == "claim->close_sent"


def test_fast_exits_green():
    out = els.live_follow(_c("dsl-a", "DISLOC", 101.2), {"dsl-a": {"exit_ts": 100.0}}, since=0)
    lane = "FAMILY_DISLOC_BFX_DISLOCATION_1M"
    assert out["status"] == "GREEN" and out["lanes"][lane]["max_sec"] == 1.2


def test_foi_short_code_normalises():
    out = els.live_follow(_c("dsf-1", "DISLOC-FOI", 105.0), {"dsf-1": {"exit_ts": 100.0}}, since=0)
    assert "FAMILY_DISLOC_FOI_LONG_1M" in out["lanes"]


def test_endpoint_wires_live_follow():
    src = open("bot.py", encoding="utf-8").read()
    assert '"live_follow": live_follow' in src and "_els.live_follow(" in src
    assert "_exit_latency_closed_trade_rows" in src
    assert "live_on_lanes=inp.get(\"live_on_lanes\")" in src


def test_monitor_surfaces_entitlement_and_pause():
    src = open("live_copy_monitor.py", encoding="utf-8").read()
    assert "ENTITLEMENT_INACTIVE" in src and "ACCOUNT_PAUSED" in src
