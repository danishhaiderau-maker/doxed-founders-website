import exit_latency_slo as els
import live_copy_monitor as lcm


def _c(cid, lane, ts):
    return {cid: [{"type": "ORDER_FILLED", "lane": lane, "sent_at_ts": ts - 60},
                  {"type": "POSITION_CLOSED", "lane": lane, "sent_at_ts": ts}]}


def test_disloc_lane_and_alert_over_3s():
    by = {**_c("dsl-a", "DISLOC", 1000.0 + 1.5), **_c("dsl-b", "DISLOC", 2000.0 + 4.2), **_c("gs1-x", "GS_01", 3005)}
    tw = {"dsl-a": {"exit_ts": 1000.0}, "dsl-b": {"exit_ts": 2000.0}, "gs1-x": {"exit_ts": 3000}}
    out = els.live_follow(by, tw, since=0)
    assert set(out["lanes"]) == {"DISLOC"}
    d = out["lanes"]["DISLOC"]
    assert d["n"] == 2 and d["breach_count"] == 1 and d["breaches_over_3s"][0]["trade_id"] == "dsl-b"
    assert out["alert"] is True and out["status"] == "RED"


def test_fast_exits_green():
    out = els.live_follow(_c("dsl-a", "DISLOC", 101.2), {"dsl-a": {"exit_ts": 100.0}}, since=0)
    assert out["status"] == "GREEN" and out["lanes"]["DISLOC"]["max_sec"] == 1.2


def test_endpoint_wires_live_follow():
    src = open("bot.py", encoding="utf-8").read()
    assert '"live_follow": live_follow' in src and "_els.live_follow(" in src


def test_monitor_surfaces_entitlement_and_pause():
    src = open("live_copy_monitor.py", encoding="utf-8").read()
    assert "ENTITLEMENT_INACTIVE" in src and "ACCOUNT_PAUSED" in src
