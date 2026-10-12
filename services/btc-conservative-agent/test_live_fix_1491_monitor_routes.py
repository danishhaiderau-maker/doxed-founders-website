import live_monitor_routes as lmr
import live_copy_monitor as lcm
import monitor_checks as mc
import runtime_telemetry as rt


def _reports(tid="dsl-1-1"):
    tl = {"fly_signal_at_ts": 100.0, "fly_intent_emitted_at_ts": 100.1, "railway_received_at_ts": 100.3,
          "order_sent_at_ts": 101.0, "exchange_ack_at_ts": 101.3, "fill_at_ts": 101.4,
          "stop_placed_at_ts": 101.9, "stop_confirmed_at_ts": 102.2}
    return [
        {"type": "ORDER_FILLED", "correlation_id": tid, "lane": "DISLOC", "account": "acct-1",
         "timeline": tl, "fill": {"price": 100.05}, "event_at_ts": 101.5, "fly_received_at_ts": 101.6},
        {"type": "POSITION_CLOSED", "correlation_id": tid, "lane": "DISLOC", "account": "acct-1",
         "timeline": {**tl, "exit_sent_at_ts": 160.2, "exit_ack_at_ts": 160.5},
         "close": {"price": 100.9}, "event_at_ts": 160.6, "fly_received_at_ts": 160.8},
    ]


def test_timeline_hops_and_slippage():
    tw = {"dsl-1-1": {"side": "LONG", "entry_price": 100.0, "entry_ts": 100.05, "signal_ts": 100.0,
                      "exit_price": 101.0, "exit_ts": 160.05, "exit_reason": "TIME_60S"}}
    out = lmr.timeline(_reports(), [], tw)
    r = out["rows"][0]
    assert r["entry"]["signal_to_send_ms"] == 1000
    assert r["entry"]["fill_to_stop_placed_ms"] == 500
    assert r["exit"]["paper_exit_to_live_exit_ms"] == 450
    assert r["entry"]["slippage_bp_vs_paper"] == 5.0
    assert r["exit"]["slippage_bp_vs_paper"] == 9.9
    assert out["rolling"]["exit_paper_to_live_ms"]["p95"] == 450


def test_pause_history_alerts_self_pause():
    acc = [{"account": "acct-1", "pause_history": [
        {"at": "2026-10-12T09:29:24Z", "actor": "SYSTEM", "code": "TERMINAL_CLOSE_CLAIM_REFUSED"}]}]
    from datetime import datetime
    now = datetime.fromisoformat("2026-10-12T09:40:00+00:00").timestamp()
    out = lmr.pause_history(acc, now=now)
    assert out["status"] == "RED" and out["self_pauses_1h"] == 1
    assert lmr.pause_history(acc, now=now + 7200)["status"] == "GREEN"


def test_protection_state_flags_zero_and_double():
    pos = [{"side": "LONG", "amount": 0.00029}]
    stop = {"id": "1", "type": "STOP", "reduce_only": True, "oco": False}
    ok = lmr.protection_state([{"account": "a", "exchange_mirror": {"positions": pos, "open_orders": [stop], "read_age_s": 2}}])
    assert ok["status"] == "GREEN" and ok["positions"][0]["protective_count"] == 1
    dup = lmr.protection_state([{"account": "a", "exchange_mirror": {"positions": pos, "open_orders": [stop, {**stop, "id": "2"}]}}])
    assert dup["status"] == "RED" and "DUPLICATE_PROTECTION:a" in dup["causes"]
    none = lmr.protection_state([{"account": "a", "exchange_mirror": {"positions": pos, "open_orders": []}}])
    assert "UNPROTECTED:a" in none["causes"]


def test_clock_route_and_check():
    out = lmr.clock({"offset_ms": 992, "status": "DRIFT"}, _reports(), since=0)
    assert out["status"] == "RED" and out["one_way_bounds"]["fly_intent_to_railway_received"]["min_ms"] == 200
    assert mc.clock_skew({"offset_ms": 992, "synchronized": False}, live_on=True)["status"] == "RED"
    assert mc.clock_skew({"offset_ms": 992, "synchronized": False}, live_on=False)["alert"] is True
    assert mc.clock_skew({"offset_ms": 20, "synchronized": True}, live_on=True)["status"] == "GREEN"


def test_slew_uses_adjtime_and_never_raises():
    calls = []

    class Libc:
        def adjtime(self, tv, old):
            calls.append(tv)
            return 0
    out = rt.slew_clock(992.0, 40.0, enabled=True, _libc=Libc())
    assert out["applied_ms"] == -992.0 and calls
    assert rt.slew_clock(5.0, 40.0, enabled=True, _libc=Libc())["applied_ms"] is None
    assert rt.slew_clock(992.0, 900.0, enabled=True, _libc=Libc())["error"] == "SAMPLE_NOT_TRUSTED_FOR_SLEW"


def _summary(website, last):
    return lcm.summary(output={"enabled": True}, force_paper_mode=False, relay_stack_mode="",
                       switch_rows=[{"lane": "DISLOC", "bitfinex_live_orders": True}], gaps=[], lags={},
                       executor_last_report_ts=last, website=website, rejects_1h=0, unsigned_rejects_1h=0, now=1000.0)


def test_heartbeat_lag_with_fresh_report_is_not_unreachable():
    web = {"reachable": True, "armed_accounts": ["a"], "executor_heartbeat_age_sec": 180, "accounts": []}
    out = _summary(web, 950.0)
    assert "EXECUTOR_UNREACHABLE" not in out["causes"] and "EXECUTOR_HEARTBEAT_LAGGING" in out["causes"]
    assert "EXECUTOR_UNREACHABLE" in _summary(web, 500.0)["causes"]
