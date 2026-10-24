import live_copy_lifecycle as lcl

NOW = 1_800_000_000.0


# RK (24 Oct): a healthy relay mirror carries a readable live_fences view (test_live_fences_health.py covers the bad cases)
LIVE_FENCES_OK = {"schema": "relay_live_fences_v1", "ok": True, "blocked": False, "exit_fill": [], "exit_fill_cycle": [], "other": [],
                  "exit_fill_fence_write_failures": [], "boot_reconcile": None}


def _web(status="ACTIVE", armed=True, action="STARTED", mirror=None):
    if isinstance(mirror, dict) and "live_fences" not in mirror:
        mirror = {**mirror, "live_fences": LIVE_FENCES_OK}
    return {"reachable": True, "armed_accounts": ["acct-a"] if armed else [], "server_ts": NOW + 0.2,
            "fetched_at_ts": NOW,
            "accounts": [{"account": "acct-a", "armed": armed, "status": status, "relay_mode": "LIVE" if armed else "PAUSED",
                          "executor_healthy": True, "executor_status": "RUNNING",
                          "last_transition": {"action": action, "actor": "SYSTEM", "reason": "POSITION_MISMATCH",
                                              "at": "2026-10-11T06:22:00Z"},
                          "exchange_mirror": mirror}]}


def _ctx(**kw):
    base = {"output": {"enabled": True}, "force_paper_mode": False, "relay_stack_mode": "",
            "switch_rows": [{"lane": "GS01", "bitfinex_live_orders": True}], "website": _web(),
            "approvals": [], "reports": [], "paper_twins": {}, "deliveries": {}}
    base.update(kw)
    return base


def test_paused_while_output_on_is_red_with_reason():
    out = lcl.build(_ctx(website=_web(status="PAUSED", armed=False, action="SAFETY_PAUSED")), now=NOW)
    codes = out["alert_codes"]
    assert "ACCOUNT_PAUSED_WHILE_OUTPUT_ON" in codes
    a = next(x for x in out["alerts"] if x["code"] == "ACCOUNT_PAUSED_WHILE_OUTPUT_ON")
    assert a["paused_reason"] == "POSITION_MISMATCH" and a["last_change"]["actor"] == "SYSTEM"
    assert out["arming"]["effective_live"] is False and "NO_ACCOUNT_ARMED" in out["arming"]["blockers"]
    assert out["verdict"] == "RED"


def test_mismatch_paused_but_started_and_stale_relay_mode():
    # Unexplained PAUSED+STARTED (reason=POSITION_MISMATCH) stays a real RED mismatch.
    out = lcl.build(_ctx(website=_web(status="PAUSED", armed=False, action="STARTED")), now=NOW)
    assert any(m["code"] == "PAUSED_BUT_LAST_CHANGE_STARTED" for m in out["arming"]["mismatches"])
    assert out["arming"]["verdict"] == "RED"
    assert "ARMING_SOURCES_MISMATCH" in out["alert_codes"]
    out2 = lcl.build(_ctx(relay_stack_mode="source_research_only"), now=NOW)
    assert any(m["code"] == "RELAY_MODE_STALE_VS_ACCOUNT" for m in out2["arming"]["mismatches"])
    assert "ARMING_SOURCES_MISMATCH" in out2["alert_codes"]


def test_paused_but_started_during_deploy_is_expected_not_money_risk():
    # LIVE-FIX-1928: intentional pause/deploy race (USER_REQUEST_START) must not
    # paint ARMING_SOURCES_MISMATCH / money_risk RED. ACCOUNT_PAUSED_WHILE_OUTPUT_ON
    # still fires when output+tiles are ON (capability truth).
    # healthy flat relay mirror (with live_fences): a MISSING mirror is non-GREEN by RK 24 Oct (test_live_fences_health.py)
    web = _web(status="PAUSED", armed=False, action="STARTED", mirror={"ok": True, "flat": True, "read_age_s": 1.0})
    web["accounts"][0]["last_transition"]["reason"] = "USER_REQUEST_START"
    web["accounts"][0]["last_transition"]["actor"] = "USER"
    out = lcl.build(_ctx(website=web), now=NOW)
    assert not any(m["code"] == "PAUSED_BUT_LAST_CHANGE_STARTED" for m in out["arming"]["mismatches"])
    assert any(m["code"] == "PAUSED_BUT_LAST_CHANGE_STARTED" for m in out["arming"]["expected_mismatches"])
    assert out["arming"]["verdict"] == "GREEN"
    assert "ARMING_SOURCES_MISMATCH" not in out["alert_codes"]
    assert "ARMING_EXPECTED_MISMATCH" in out["alert_codes"]
    exp = next(a for a in out["alerts"] if a["code"] == "ARMING_EXPECTED_MISMATCH")
    assert exp["severity"] == "INFO" and exp["alert_class"] == "paused_window"
    assert "ARMING_EXPECTED_MISMATCH" in out["expected_skip_codes"]
    assert "ARMING_SOURCES_MISMATCH" not in out["money_risk_codes"]
    # Deploy maintenance: output OFF -> no ACCOUNT_PAUSED_WHILE_OUTPUT_ON either;
    # money_risk stays GREEN from arming race alone.
    out_off = lcl.build(_ctx(website=web, output={"enabled": False},
                             switch_rows=[{"lane": "DISLOC", "bitfinex_live_orders": False}]), now=NOW)
    assert out_off["arming"]["verdict"] == "GREEN"
    assert out_off["money_risk_verdict"] == "GREEN"
    assert "ARMING_SOURCES_MISMATCH" not in out_off["money_risk_codes"]


def test_intent_without_decision_then_skipped_and_full_trade_drift():
    appr = [{"correlation_id": "gs1-x", "trade_id": "gs1-x", "event": "ORDER_PLACED", "entry_allowed": True,
             "signal_at_ts": NOW - 100, "created_at_ts": NOW - 99, "research_lane": "GS01"}]
    out = lcl.build(_ctx(approvals=appr), now=NOW)
    assert "INTENT_NO_EXECUTOR_DECISION" in out["alert_codes"]
    rep = [{"report_id": "r1", "type": "INTENT_SKIPPED", "correlation_id": "gs1-x", "account": "acct-a",
            "event_at_ts": NOW - 98, "timeline": {}, "decision": {"decision": "SKIPPED", "reason": "instance-status=PAUSED",
                                                                   "decided_at_ts": NOW - 98}}]
    out = lcl.build(_ctx(approvals=appr, reports=rep), now=NOW)
    assert "INTENT_NO_EXECUTOR_DECISION" not in out["alert_codes"]
    nof = next(a for a in out["alerts"] if a["code"] == "NO_ORDER_FOR_INTENT")
    assert nof["severity"] == "AMBER" and nof["executor_reason"] == "instance-status=PAUSED"
    tl = {"railway_received_at_ts": NOW - 98.5, "order_sent_at_ts": NOW - 98, "exchange_ack_at_ts": NOW - 97.8,
          "fill_at_ts": NOW - 97}
    rep = [
        {"report_id": "p", "type": "ORDER_PLACED", "correlation_id": "gs1-x", "account": "acct-a", "timeline": tl,
         "order": {"type": "LIMIT", "side": "LONG", "qty": 0.0003, "price": 100.0}, "event_at_ts": NOW - 98},
        {"report_id": "f", "type": "ORDER_FILLED", "correlation_id": "gs1-x", "account": "acct-a", "timeline": tl,
         "fill": {"price": 100.0, "qty": 0.0003}, "event_at_ts": NOW - 97, "fly_received_at_ts": NOW - 95},
        {"report_id": "f", "type": "ORDER_FILLED", "correlation_id": "gs1-x", "account": "acct-a", "timeline": tl,
         "fill": {"price": 100.0, "qty": 0.0003}, "event_at_ts": NOW - 97},
        {"report_id": "s", "type": "STOP_CONFIRMED", "correlation_id": "gs1-x", "account": "acct-a",
         "timeline": {**tl, "stop_confirmed_at_ts": NOW - 96}, "stop": {"exchange_order_id": "9", "price": 99.6}},
        {"report_id": "c", "type": "POSITION_CLOSED", "correlation_id": "gs1-x", "account": "acct-a", "timeline": tl,
         "close": {"price": 100.2, "reason": "TP"}, "event_at_ts": NOW - 10},
    ]
    twins = {"gs1-x": {"side": "LONG", "entry_price": 100.0, "qty": 0.0003, "exit_price": 100.1,
                       "exit_ts": NOW - 12, "exit_reason": "TP"}}
    out = lcl.build(_ctx(approvals=appr, reports=rep, paper_twins=twins), now=NOW)
    a = out["intents"][0]["accounts"][0]
    assert len(a["fills"]) == 1 and a["stop_count"] == 1 and a["open"] is False
    assert a["drift"]["entry_price_bp"] == 0.0 and a["drift"]["exit_price_bp"] == 9.99
    assert a["drift"]["exit_reason_match"] is True and a["exit"]["trigger_ts"] == NOW - 12
    assert "EXIT_DRIFT_BP" in out["alert_codes"]


def test_exchange_mirror_orphans_and_stale_read_and_expiry():
    m = {"ok": True, "flat": True, "positions": [], "open_orders": [{"id": "1", "type": "STOP"}],
         "stops": [{"id": "1"}], "orphans": ["1"], "read_age_s": 400}
    out = lcl.build(_ctx(website=_web(mirror=m)), now=NOW)
    assert {"ORPHAN_ORDER_WHEN_FLAT", "EXCHANGE_READ_STALE"} <= set(out["alert_codes"])
    old = [{"correlation_id": "old", "event": "ORDER_PLACED", "entry_allowed": True, "created_at_ts": NOW - 7 * 3600}]
    out = lcl.build(_ctx(approvals=old), now=NOW)
    assert out["intents_expired"] == 1 and "INTENT_NO_EXECUTOR_DECISION" not in out["alert_codes"]
    assert out["clock"]["fly_to_website_offset_ms"] == 200.0


def test_research_only_stack_mode_is_not_a_mismatch():
    out = lcl.build(_ctx(relay_stack_mode="research_only"), now=NOW)
    codes = [m.get("code") for m in out["arming"]["mismatches"]]
    assert "RELAY_MODE_STALE_VS_ACCOUNT" not in codes


def test_showcase_mirror_exit_matches_any_paper_reason():
    import inspect
    src = inspect.getsource(lcl)
    assert '"SHOWCASE_MIRROR", str(twin.get("exit_reason")' in src


# LIVE-FIX-1715: closed-trade parity must not paint the top-level verdict RED.
_FLAT = {"ok": True, "flat": True, "positions": [], "open_orders": [], "stops": [], "orphans": [], "read_age_s": 10}


def _closed_trade(paper_reason="TIME_60S", live_reason="MANUAL_OR_EXCHANGE_CLOSE", live_exit=99.949):
    appr = [{"correlation_id": "dsl-1", "trade_id": "dsl-1", "event": "ORDER_PLACED", "entry_allowed": True,
             "signal_at_ts": NOW - 100, "created_at_ts": NOW - 99, "research_lane": "DISLOC"}]
    tl = {"railway_received_at_ts": NOW - 98.5, "executor_received_at_ts": NOW - 98.4,
          "executor_decided_at_ts": NOW - 98.3, "order_sent_at_ts": NOW - 98,
          "exchange_ack_at_ts": NOW - 97.8, "fill_at_ts": NOW - 97}
    rep = [
        {"report_id": "d", "type": "INTENT_SUBMITTED", "correlation_id": "dsl-1", "account": "acct-a", "timeline": tl,
         "decision": {"decision": "SUBMITTED", "reason": "ok", "decided_at_ts": NOW - 98.3}, "event_at_ts": NOW - 98.3,
         "fly_received_at_ts": NOW - 98},
        {"report_id": "p", "type": "ORDER_PLACED", "correlation_id": "dsl-1", "account": "acct-a", "timeline": tl,
         "order": {"type": "LIMIT", "side": "LONG", "qty": 0.0003, "price": 100.0}, "event_at_ts": NOW - 98,
         "fly_received_at_ts": NOW - 97},
        {"report_id": "f", "type": "ORDER_FILLED", "correlation_id": "dsl-1", "account": "acct-a", "timeline": tl,
         "fill": {"price": 100.0, "qty": 0.0003}, "event_at_ts": NOW - 97, "fly_received_at_ts": NOW - 95},
        {"report_id": "s", "type": "STOP_CONFIRMED", "correlation_id": "dsl-1", "account": "acct-a",
         "timeline": {**tl, "stop_confirmed_at_ts": NOW - 96}, "stop": {"exchange_order_id": "9", "price": 99.6},
         "fly_received_at_ts": NOW - 95},
        {"report_id": "c", "type": "POSITION_CLOSED", "correlation_id": "dsl-1", "account": "acct-a", "timeline": tl,
         "close": {"price": live_exit, "reason": live_reason}, "event_at_ts": NOW - 10, "fly_received_at_ts": NOW - 9},
    ]
    twins = {"dsl-1": {"side": "LONG", "entry_price": 100.0, "qty": 0.0003, "exit_price": 100.0,
                       "exit_ts": NOW - 12, "exit_reason": paper_reason}}
    return {"approvals": appr, "reports": rep, "paper_twins": twins}


def test_flat_closed_exit_reason_mismatch_and_small_drift_is_not_red():
    out = lcl.build(_ctx(website=_web(mirror=dict(_FLAT)), **_closed_trade()), now=NOW)
    assert out["arming"]["effective_live"] is True
    assert {"EXIT_REASON_MISMATCH", "EXIT_DRIFT_BP"} <= set(out["alert_codes"])  # still visible to HM
    a = out["intents"][0]["accounts"][0]
    assert a["open"] is False and a["drift"]["exit_reason_match"] is False and abs(a["drift"]["exit_price_bp"]) < 10
    for al in out["alerts"]:
        if al["code"] in ("EXIT_REASON_MISMATCH", "EXIT_DRIFT_BP"):
            assert al["severity"] == "AMBER" and al["alert_class"] == "historical_parity"
    assert out["money_risk_verdict"] == "GREEN" and out["verdict"] == "GREEN"
    assert out["parity_verdict"] == "AMBER"
    assert set(out["parity_codes"]) == {"EXIT_REASON_MISMATCH", "EXIT_DRIFT_BP"}


def test_closed_large_exit_drift_is_money_risk_red():
    out = lcl.build(_ctx(website=_web(mirror=dict(_FLAT)), **_closed_trade(live_reason="TIME_60S", live_exit=99.8)),
                    now=NOW)
    d = next(x for x in out["alerts"] if x["code"] == "EXIT_DRIFT_BP")
    assert d["severity"] == "RED" and d["alert_class"] == "money_risk"
    assert out["verdict"] == "RED"


def test_open_orphan_or_unprotected_position_still_red_with_closed_parity_noise():
    m = dict(_FLAT, orphans=["1"], open_orders=[{"id": "1", "type": "STOP"}])
    out = lcl.build(_ctx(website=_web(mirror=m), **_closed_trade()), now=NOW)
    assert "ORPHAN_ORDER_WHEN_FLAT" in out["money_risk_codes"]
    assert out["verdict"] == "RED" and out["money_risk_verdict"] == "RED"
    m = dict(_FLAT, flat=False, positions=[{"qty": 0.0003, "stops": 0}])
    out = lcl.build(_ctx(website=_web(mirror=m), **_closed_trade()), now=NOW)
    assert "POSITION_WITHOUT_EXCHANGE_STOP" in out["alert_codes"] and out["verdict"] == "RED"


def test_monitor_summary_closed_price_drift_is_parity_not_verdict():
    import live_copy_monitor as lcm
    kw = dict(output={"enabled": True}, force_paper_mode=False, relay_stack_mode="",
              switch_rows=[{"lane": "DISLOC", "bitfinex_live_orders": True}], lags={},
              executor_last_report_ts=NOW, rejects_1h=0, unsigned_rejects_1h=0, now=NOW,
              website={"reachable": True, "armed_accounts": ["acct-a"], "executor_heartbeat_age_sec": 1})
    closed = {"code": "PRICE_DRIFT_VS_PAPER", "severity": "AMBER", "closed": True, "drift_bp": 5.01}
    s = lcm.summary(gaps=[closed], **kw)
    assert s["verdict"] == "GREEN" and s["parity_verdict"] == "AMBER" and "PRICE_DRIFT_VS_PAPER" in s["causes"]
    s = lcm.summary(gaps=[dict(closed, closed=False)], **kw)  # open copy: still counts
    assert s["verdict"] == "AMBER"
    s = lcm.summary(gaps=[dict(closed, severity="RED", drift_bp=20.0)], **kw)  # >= RED bp: money risk
    assert s["verdict"] == "RED"
    s = lcm.summary(gaps=[closed, {"code": "MISSING_STOP", "severity": "RED"}], **kw)
    assert s["verdict"] == "RED"


def test_api_summary_verdict_follows_money_risk():
    import live_copy_monitor_api as api
    ctx = _ctx(website=dict(_web(mirror=dict(_FLAT)), executor_heartbeat_age_sec=1), **_closed_trade())
    from flask import Flask
    with Flask(__name__).test_request_context("/api/monitor/live/summary"):
        out = api._summary(ctx, api._analysis(ctx, NOW), NOW)
    assert "EXIT_REASON_MISMATCH" in out["causes"]
    assert any(a["code"] == "EXIT_REASON_MISMATCH" for a in out["lifecycle_alerts"])
    assert out["verdict"] != "RED" and out["parity_verdict"] == "AMBER"
    assert "EXIT_REASON_MISMATCH" in out["parity_causes"]


# LIVE-FIX-1816: missed copies that carry no money risk must not paint RED.
def _iso(ts):
    import datetime as dt
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat().replace("+00:00", "Z")


_MISSED = {"NO_ORDER_FOR_INTENT", "INTENT_NO_EXECUTOR_DECISION"}


def _missed_intent(cid="dsl-9", paper_closed=True, events=("ORDER_PLACED", "POSITION_OPENED", "POSITION_CLOSED")):
    appr = [{"correlation_id": cid, "trade_id": cid, "event": ev, "entry_allowed": ev == "ORDER_PLACED",
             "signal_at_ts": NOW - 100, "created_at_ts": NOW - 99 + i, "research_lane": "DISLOC"}
            for i, ev in enumerate(events)]
    twin = {"side": "LONG", "entry_price": 100.0, "qty": 0.0003,
            "exit_price": 100.1 if paper_closed else None, "exit_ts": NOW - 39 if paper_closed else None,
            "exit_reason": "TIME_60S" if paper_closed else None}
    return {"approvals": appr, "paper_twins": {cid: twin}, "deliveries": {cid: NOW - 98.9},
            "protection": {"executor_capability": {"received_at_ts": NOW - 5}}}  # executor heartbeat fresh


def _web_rearmed(mirror, armed_at=NOW - 50):
    w = _web(mirror=mirror)
    w["accounts"][0]["armed_at"] = _iso(armed_at)
    w["accounts"][0]["last_transition"] = {"action": "STARTED", "actor": "USER", "reason": "USER_REQUEST_START",
                                           "at": _iso(armed_at)}
    w["executor_heartbeat_age_sec"] = 1
    return w


def test_intent_emitted_while_paused_is_expected_skip_not_money_risk():
    trans = [{"account": "acct-a", "action": "SAFETY_PAUSED", "at": _iso(NOW - 200)}]
    for closed in (True, False):  # paused at emit: an expected skip whether the paper is open or closed
        out = lcl.build(_ctx(website=_web_rearmed(dict(_FLAT)), account_transitions=trans,
                             **_missed_intent(paper_closed=closed, events=("ORDER_PLACED",) if not closed else
                                              ("ORDER_PLACED", "POSITION_OPENED", "POSITION_CLOSED"))), now=NOW)
        missed = [a for a in out["alerts"] if a["code"] in _MISSED]
        assert {a["code"] for a in missed} == _MISSED  # still visible
        assert all(a["alert_class"] == "paused_window" and a["severity"] == "INFO" and a["armed_at_emit"] is False
                   for a in missed)
        assert out["money_risk_verdict"] == "GREEN" and out["verdict"] == "GREEN"
        assert out["expected_skip_count"] == 1 and out["expected_skips"][0]["correlation_id"] == "dsl-9"
        assert out["missed_copy_count"] == 0


def test_missed_intent_paper_closed_exchange_flat_is_historical_missed_copy():
    out = lcl.build(_ctx(website=_web(mirror=dict(_FLAT)), **_missed_intent()), now=NOW)
    missed = [a for a in out["alerts"] if a["code"] in _MISSED]
    assert {a["code"] for a in missed} == _MISSED
    assert all(a["alert_class"] == "historical_missed_copy" and a["severity"] == "AMBER"
               and a["armed_at_emit"] is True and a["paper_closed"] and a["exchange_flat"] for a in missed)
    assert out["money_risk_verdict"] == "GREEN" and out["verdict"] == "GREEN"
    assert out["missed_copy_verdict"] == "AMBER" and out["missed_copy_count"] == 1
    assert set(out["missed_copies"][0]["codes"]) == _MISSED
    assert set(out["missed_copy_codes"]) == _MISSED and not (set(out["money_risk_codes"]) & _MISSED)
    # an exchange position owned by another OPEN tracked live copy is accounted for
    other = _closed_trade()
    other["reports"] = [r for r in other["reports"] if r["type"] != "POSITION_CLOSED"]
    other["paper_twins"]["dsl-1"].update(exit_price=None, exit_ts=None, exit_reason=None)
    m = dict(_FLAT, flat=False, positions=[{"amount": 0.0003, "stops": 1}])
    ctx = _missed_intent()
    ctx["approvals"] += other["approvals"]
    ctx["paper_twins"].update(other["paper_twins"])
    out = lcl.build(_ctx(website=_web(mirror=m), reports=other["reports"], **ctx), now=NOW)
    assert all(a["alert_class"] == "historical_missed_copy" for a in out["alerts"] if a["code"] in _MISSED
               and a["correlation_id"] == "dsl-9")
    assert out["money_risk_verdict"] == "GREEN"


def test_missed_intent_while_armed_with_open_paper_or_unexplained_exposure_stays_red():
    # armed + paper still open
    out = lcl.build(_ctx(website=_web(mirror=dict(_FLAT)),
                         **_missed_intent(paper_closed=False, events=("ORDER_PLACED", "POSITION_OPENED"))), now=NOW)
    missed = [a for a in out["alerts"] if a["code"] in _MISSED]
    assert missed and all(a["severity"] == "RED" and a["alert_class"] == "money_risk" for a in missed)
    assert out["money_risk_verdict"] == "RED" and out["verdict"] == "RED" and out["missed_copy_count"] == 0
    # armed + paper closed but the exchange holds exposure no open copy explains
    m = dict(_FLAT, flat=False, positions=[{"amount": 0.0003, "stops": 1}])
    out = lcl.build(_ctx(website=_web(mirror=m), **_missed_intent()), now=NOW)
    assert all(a["severity"] == "RED" and a["exchange_accounted"] is False
               for a in out["alerts"] if a["code"] in _MISSED)
    assert out["verdict"] == "RED"
    # armed + paper closed but the exchange read is stale/unknown
    out = lcl.build(_ctx(website=_web(mirror=dict(_FLAT, read_age_s=400)), **_missed_intent()), now=NOW)
    assert out["verdict"] == "RED"
    # no paper twin and no terminal event: unknown paper state stays RED
    ctx = _missed_intent(events=("ORDER_PLACED",))
    ctx["paper_twins"] = {}
    out = lcl.build(_ctx(website=_web(mirror=dict(_FLAT)), **ctx), now=NOW)
    assert out["verdict"] == "RED"


def test_api_summary_moves_historical_missed_copies_to_own_bucket():
    import live_copy_monitor_api as api
    ctx = _ctx(website=dict(_web(mirror=dict(_FLAT)), executor_heartbeat_age_sec=1), **_missed_intent())
    from flask import Flask
    with Flask(__name__).test_request_context("/api/monitor/live/summary"):
        out = api._summary(ctx, api._analysis(ctx, NOW), NOW)
    assert {"NO_ORDER_FOR_INTENT", "INTENT_NO_ACCOUNT_ORDER"} <= set(out["causes"])  # still listed
    assert out["verdict"] != "RED"
    assert out["missed_copy_verdict"] == "AMBER" and out["missed_copy_count"] == 1
    assert {"NO_ORDER_FOR_INTENT", "INTENT_NO_ACCOUNT_ORDER"} <= set(out["missed_copy_causes"])
    assert out["missed_copies"][0]["correlation_id"] == "dsl-9"
    for k in ("parity_verdict", "parity_causes", "expected_skips", "expected_skip_count", "lifecycle_alerts"):
        assert k in out
    # same intent with the paper still open: real money risk
    ctx = _ctx(website=dict(_web(mirror=dict(_FLAT)), executor_heartbeat_age_sec=1),
               **_missed_intent(paper_closed=False, events=("ORDER_PLACED", "POSITION_OPENED")))
    with Flask(__name__).test_request_context("/api/monitor/live/summary"):
        out = api._summary(ctx, api._analysis(ctx, NOW), NOW)
    assert out["verdict"] == "RED" and out["missed_copy_count"] == 0


def test_monitor_summary_missed_copy_classes_do_not_move_verdict_unless_red():
    import live_copy_monitor as lcm
    kw = dict(output={"enabled": True}, force_paper_mode=False, relay_stack_mode="",
              switch_rows=[{"lane": "DISLOC", "bitfinex_live_orders": True}], lags={},
              executor_last_report_ts=NOW, rejects_1h=0, unsigned_rejects_1h=0, now=NOW,
              website={"reachable": True, "armed_accounts": ["acct-a"], "executor_heartbeat_age_sec": 1})
    g = {"code": "INTENT_NO_ACCOUNT_ORDER", "severity": "AMBER", "alert_class": "historical_missed_copy"}
    s = lcm.summary(gaps=[g, {"code": "ACCOUNT_DID_NOT_COPY", "severity": "INFO", "alert_class": "paused_window"}],
                    **kw)
    assert s["verdict"] == "GREEN" and s["missed_copy_verdict"] == "AMBER"
    assert s["missed_copy_causes"] == ["INTENT_NO_ACCOUNT_ORDER"] and s["expected_skip_causes"] == ["ACCOUNT_DID_NOT_COPY"]
    s = lcm.summary(gaps=[dict(g, severity="RED")], **kw)
    assert s["verdict"] == "RED"
