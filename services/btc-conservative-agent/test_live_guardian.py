import live_guardian as g

NOW = 1_800_000_000.0
MIR = {"ok": True, "positions": [], "open_orders": [], "orphans": [], "balance": {"derivatives_available_usd": 206.79}}


def ch(**k):
    return {"correlation_id": "GS01-1", "lane": "FAMILY_GS01_XV", **k}


def codes(v):
    return {x["code"] for x in v}


def test_clean_flat_state_has_no_violation():
    assert g.evaluate([], MIR, {}, NOW) == []


def test_stop_missing_over_5s_after_fill_disarms_but_not_before():
    c = ch(fill_at_ts=NOW - 6)
    m = dict(MIR, positions=[{"id": 1}])
    assert "STOP_MISSING_GT_5S" in codes(g.evaluate([c], m, {}, NOW))
    assert "STOP_MISSING_GT_5S" not in codes(g.evaluate([ch(fill_at_ts=NOW - 4)], m, {}, NOW))
    assert "STOP_MISSING_GT_5S" not in codes(g.evaluate([ch(fill_at_ts=NOW - 60, stop_confirmed=True)], m, {}, NOW))


def test_orphan_must_persist_and_ledger_mismatch_respects_grace():
    st = {}
    m = dict(MIR, orphans=[{"id": 9}])
    assert g.evaluate([], m, st, NOW) == []
    assert "ORPHAN" in codes(g.evaluate([], m, st, NOW + 4))
    # exchange has a position, no open chain: mismatch; fresh fill inside the grace window is tolerated
    mp = dict(MIR, positions=[{"id": 1}])
    assert "LEDGER_MISMATCH" in codes(g.evaluate([], mp, {}, NOW))
    assert "LEDGER_MISMATCH" not in codes(g.evaluate([ch(fill_at_ts=NOW - 2, stop_confirmed=True)], mp, {}, NOW))


def test_daily_realised_loss_cap_one_dollar_and_new_day_resets_baseline():
    st = {}
    assert g.evaluate([], MIR, st, NOW) == []
    low = dict(MIR, balance={"derivatives_available_usd": 205.70})
    assert "DAILY_LOSS_CAP" in codes(g.evaluate([], low, st, NOW + 60))
    ok = dict(MIR, balance={"derivatives_available_usd": 206.20})
    assert "DAILY_LOSS_CAP" not in codes(g.evaluate([], ok, {"day": g.day_key(NOW), "day_baseline": 206.79, "last_flat_balance": 206.20, "last_flat_ts": NOW}, NOW + 60))
    st2 = {"day": "1999-01-01", "day_baseline": 300.0}
    assert "DAILY_LOSS_CAP" not in codes(g.evaluate([], low, st2, NOW)) and st2["day_baseline"] == 205.70


def test_balance_drift_when_flat_beyond_reported_pnl():
    st = {"day": g.day_key(NOW), "day_baseline": 206.79, "last_flat_balance": 206.79, "last_flat_ts": NOW - 100}
    drift = dict(MIR, balance={"derivatives_available_usd": 206.10})
    assert "BALANCE_DRIFT" in codes(g.evaluate([], drift, st, NOW))
    st = {"day": g.day_key(NOW), "day_baseline": 206.79, "last_flat_balance": 206.79, "last_flat_ts": NOW - 100}
    explained = [ch(closed=True, pnl_usd=-0.69, closed_at_ts=NOW - 50)]
    assert "BALANCE_DRIFT" not in codes(g.evaluate(explained, drift, st, NOW))


def test_blind_mirror_with_open_position_disarms_after_30s_only():
    st = {}
    c = ch(fill_at_ts=NOW - 100, stop_confirmed=True)
    assert g.evaluate([c], {"ok": False, "error": "x"}, st, NOW) == []
    assert "EXCHANGE_MIRROR_BLIND" in codes(g.evaluate([c], {"ok": False}, st, NOW + 31))


def test_bot_wiring_tick_disarms_on_violation_and_is_inert_when_output_off():
    import pathlib
    src = pathlib.Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    assert 'name="live-guardian"' in src and "_disarm_live_control(reason)" in src and "AUTO_DISARM:" in src
    assert "if not bool(_get_live_copy_output().snapshot().get(\"enabled\")):" in src   # inert while live output is OFF
    ns = {}
    start = src.index("def _live_guardian_tick")
    end = src.index("def live_guardian_loop")
    import time as _t, logging
    calls = []

    class Out:
        def __init__(self, on): self.on = on
        def snapshot(self): return {"enabled": self.on}

    def run(on):
        calls.clear()
        g_ = {"time": _t, "logger": logging.getLogger("t"), "_LIVE_GUARDIAN_STATE": {"enabled": True, "calc": {}, "disarms": [], "last_violations": []},
              "_get_live_copy_output": lambda: Out(on),
              "_tile_integrity_chains": lambda now: [{"correlation_id": "GS01-1", "lane": "FAMILY_GS01_XV", "fill_at_ts": now - 9}],
              "_live_guardian_mirror": lambda: {"ok": True, "positions": [{}], "open_orders": [], "orphans": [], "balance": {"derivatives_available_usd": 206.0}},
              "_disarm_live_control": lambda reason: calls.append(reason) or {"cancel": {"failed": []}}}
        exec(src[start:end], g_)
        return g_["_live_guardian_tick"]()
    assert run(False) == [] and calls == []
    v = run(True)
    assert any(x["code"] == "STOP_MISSING_GT_5S" for x in v) and calls and calls[0].startswith("AUTO_DISARM:STOP_MISSING_GT_5S")


# ---- drill: every trigger disarms through the bot tick, plus heartbeat, arm refusal, relay reach, no order calls --------------
import pathlib as _pl, time as _t, logging as _lg2

_SRC = _pl.Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def _tick_ns(on=True, chains=None, mirror=None, epoch=None):
    start, end = _SRC.index("def _live_guardian_tick"), _SRC.index("def _live_guardian_heartbeat_stale")
    calls = []

    class Out:
        def snapshot(self): return {"enabled": on}

    ns = {"time": _t, "logger": _lg2.getLogger("t"), "_LIVE_GUARDIAN_STATE": {"enabled": True, "calc": {}, "disarms": [], "last_violations": [], "heartbeat_ts": None},
          "_get_live_copy_output": lambda: Out(), "_tile_integrity_chains": lambda now: chains(now) if callable(chains) else (chains or []),
          "_live_guardian_mirror": lambda: mirror, "_offline_sim_epoch_floor": lambda: epoch,
          "_disarm_live_control": lambda reason: calls.append(reason) or {"cancel": {"failed": []}}}
    exec(src_cut(start, end), ns)
    return ns, calls


def src_cut(a, b):
    return _SRC[a:b]


def _mir(**k):
    d = {"ok": True, "positions": [], "open_orders": [], "orphans": [], "balance": {"derivatives_available_usd": 206.79}}
    d.update(k)
    return d


def test_drill_each_of_the_six_triggers_disarms_via_the_bot_tick_with_its_own_code():
    now = _t.time()
    P = [{"symbol": "x", "side": "LONG", "amount": 0.001}]
    cases = {
        "STOP_MISSING_GT_5S": (lambda n: [ch(fill_at_ts=n - 9)], _mir(positions=P), None),
        "ORPHAN": (lambda n: [], _mir(orphans=["1"]), None),
        "LEDGER_MISMATCH": (lambda n: [], _mir(positions=P), None),
        "DAILY_LOSS_CAP": (lambda n: [ch(fill_at_ts=n - 500, stop_confirmed=True, closed=True, pnl_usd=-1.2, closed_at_ts=n - 100, exit_at_ts=n - 100)], _mir(), None),
        "BALANCE_DRIFT": (lambda n: [], _mir(balance={"derivatives_available_usd": 205.9}), None),
    }
    for code, (chains, mirror, epoch) in cases.items():
        ns, calls = _tick_ns(True, chains, mirror, epoch)
        if code == "ORPHAN":
            ns["_LIVE_GUARDIAN_STATE"]["calc"]["orphan_since"] = now - 10
        if code == "BALANCE_DRIFT":
            ns["_LIVE_GUARDIAN_STATE"]["calc"].update(day=g.day_key(now), day_baseline=206.79, last_flat_balance=206.79, last_flat_ts=now - 100)
        v = ns["_live_guardian_tick"](now)
        assert code in {x["code"] for x in v} and calls and code in calls[0], code
    ns, calls = _tick_ns(True, lambda n: [ch(fill_at_ts=n - 99, stop_confirmed=True)], {"ok": False, "error": "stale"})
    ns["_LIVE_GUARDIAN_STATE"]["calc"]["blind_since"] = now - 40
    v = ns["_live_guardian_tick"](now)
    assert "EXCHANGE_MIRROR_BLIND" in {x["code"] for x in v} and calls   # a failed read with a position open, 30 s


def test_single_failed_read_is_blind_not_clean_and_does_not_disarm_alone():
    st = {}
    assert g.evaluate([ch(fill_at_ts=NOW - 99, stop_confirmed=True)], {"ok": False}, st, NOW) == [] and st["read_state"] == "BLIND"
    g.evaluate([], MIR, st, NOW + 1)
    assert st["read_state"] == "OK" and "blind_since" not in st


def test_pre_epoch_rows_never_trip_loss_cap_or_drift():
    old = ch(fill_at_ts=NOW - 5000, closed=True, pnl_usd=-5.0, closed_at_ts=NOW - 10, exit_at_ts=NOW - 10, stop_confirmed=True)
    assert "DAILY_LOSS_CAP" in codes(g.evaluate([old], MIR, {}, NOW))
    assert "DAILY_LOSS_CAP" not in codes(g.evaluate([old], MIR, {}, NOW, epoch_ts=NOW - 100))


def test_heartbeat_stale_dead_guardian_disarms_and_arming_refuses_while_stale():
    assert g.heartbeat_stale(None, NOW) and g.heartbeat_stale(NOW - 11, NOW) and not g.heartbeat_stale(NOW - 3, NOW)
    s, e = _SRC.index("def _live_guardian_heartbeat_stale"), _SRC.index("def live_guardian_loop")
    calls = []

    class Out:
        def snapshot(self): return {"enabled": True}

    import live_guardian as lg
    ns = {"time": _t, "logger": _lg2.getLogger("t"), "_LIVE_GUARDIAN_STATE": {"heartbeat_ts": _t.time() - 60, "disarms": []},
          "_get_live_copy_output": lambda: Out(), "_disarm_live_control": lambda r: calls.append(r)}
    exec("import live_guardian as _lg\n" + _SRC[s:e], ns)
    assert ns["_live_guardian_watchdog"]() is True and calls == ["AUTO_DISARM:GUARDIAN_STALE"]
    ns["_LIVE_GUARDIAN_STATE"]["heartbeat_ts"] = _t.time()
    assert ns["_live_guardian_watchdog"]() is False
    # arming refuses while stale (live output ON route and per-tile ON route) and the watchdog runs from the uptime thread
    assert _SRC.count('"LIVE_GUARDIAN_HEARTBEAT_STALE"') == 2 and "_live_guardian_watchdog(now)" in _SRC
    assert "heartbeat_age_sec" in _SRC


def test_disarm_reach_and_relay_gate_and_guardian_has_no_order_calls():
    dis = _SRC[_SRC.index("def _disarm_live_control"):_SRC.index("@app.route('/api/exchange_exposure_audit'")]
    assert "_get_live_copy_output().set(False" in dis and "reset_all_off" in dis and "_cancel_managed_live_pending_entries" in dis
    # the relay only receives intents Fly delivers: with output OFF every ENTRY event is withheld by the delivery check
    import live_copy_control as lcc
    ok, reason = lcc.delivery_check({"event": sorted(lcc.ENTRY_EVENTS)[0], "entry_allowed": True}, secret="", now=_t.time(), tile_row={}, output={"enabled": False})
    assert ok is False
    # no order calls anywhere in the guardian code (HM will grep): the cancel/flag reset happens only inside the existing _disarm_live_control
    guard_src = _pl.Path(__file__).with_name("live_guardian.py").read_text(encoding="utf-8") + _SRC[_SRC.index("# ---- Live guardian"):_SRC.index("def monitor_live_guardian_route")]
    for tok in ("place_order", "submit_order", "cancel_order", "close_position", "bitfinex_private", "requests.post", "_relay_http_session.post", "api_close_showcase_position", "market_close"):
        assert tok not in guard_src, tok


def test_with_output_off_exit_events_still_flow_to_the_relay_and_only_entries_are_withheld():
    """HM condition (a). The protective stop is placed by the relay executor itself at entry fill (executor-capability:
    ONCE_AT_ENTRY_FILL), so it never depends on a Fly event; Fly-delivered exit/continuation events must pass with output OFF."""
    import live_copy_control as lc
    SECRET = "s3cret"
    OFF, TILE_OFF = {"enabled": False}, {"bitfinex_live_orders": False}
    for ev in sorted(lc.CONTINUATION_EVENTS):          # POSITION_OPENED / REDUCED / CLOSED, ORDER_EXPIRED / CANCELLED
        a = lc.sign_approval({"event": ev, "trade_id": "t-1", "entry_allowed": False}, SECRET)
        ok, why = lc.delivery_check({"event": ev, "trade_id": "t-1", "live_copy_approval": a}, secret=SECRET, now=1.0, tile_row=TILE_OFF, output=OFF)
        assert ok is True and why is None, ev
    for ev in sorted(lc.ENTRY_EVENTS):                  # entries ARE withheld with output OFF
        a = lc.sign_approval({"event": ev, "trade_id": "t-1", "entry_allowed": True}, SECRET)
        ok, why = lc.delivery_check({"event": ev, "trade_id": "t-1", "live_copy_approval": a}, secret=SECRET, now=1.0, tile_row={"bitfinex_live_orders": True}, output=OFF)
        assert ok is False and why == lc.DENY_OUTPUT_OFF, ev
    # the bot's per-record filter only applies the tile delivery gate to entry events
    seg = _SRC[_SRC.index("allowed, reason = _live_copy.delivery_check("):][:700]
    assert "if allowed and event in _live_copy.ENTRY_EVENTS" in seg
