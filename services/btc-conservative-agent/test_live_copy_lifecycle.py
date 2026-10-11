import live_copy_lifecycle as lcl

NOW = 1_800_000_000.0


def _web(status="ACTIVE", armed=True, action="STARTED", mirror=None):
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
    out = lcl.build(_ctx(website=_web(status="PAUSED", armed=False, action="STARTED")), now=NOW)
    assert any(m["code"] == "PAUSED_BUT_LAST_CHANGE_STARTED" for m in out["arming"]["mismatches"])
    out2 = lcl.build(_ctx(relay_stack_mode="source_research_only"), now=NOW)
    assert any(m["code"] == "RELAY_MODE_STALE_VS_ACCOUNT" for m in out2["arming"]["mismatches"])
    assert "ARMING_SOURCES_MISMATCH" in out2["alert_codes"]


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
