import live_guardian as g

NOW = 1_800_000_000.0
MIR = {"ok": True, "positions": [], "open_orders": [], "orphans": [], "balance": {"derivatives_available_usd": 206.79}}


FF: list = []   # force-flat stub calls (C2)

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


def test_balance_loss_is_reconcile_only_never_a_cap_and_stale_baseline_ages_out():
    """RK ruling 07:57 (R7/R4): the balance-based daily cap is gone; the balance loss is recorded, never gated."""
    st = {}
    assert g.evaluate([], MIR, st, NOW) == []
    low = dict(MIR, balance={"derivatives_available_usd": 205.70})
    assert g.evaluate([], low, st, NOW + 60) == []
    rec = st["balance_reconcile"]
    assert rec["gating"] is False and rec["balance_loss_usd"] == 1.09 and rec["d3_loss_usd"] == 0.0 and rec["warning"] is True
    st2 = {"day_baseline": 300.0, "day_baseline_ts": NOW - 90000}      # a baseline older than 24 h has aged out
    assert g.evaluate([], low, st2, NOW) == [] and st2["day_baseline"] == 205.70


def test_balance_drift_when_flat_is_a_logged_warning_not_a_disarm():
    st = {"day": g.day_key(NOW), "day_baseline": 206.79, "last_flat_balance": 206.79, "last_flat_ts": NOW - 100}
    drift = dict(MIR, balance={"derivatives_available_usd": 206.10})
    assert g.evaluate([], drift, st, NOW) == []
    assert st["balance_reconcile"]["drift_usd"] == -0.69 and st["balance_reconcile"]["warning"] is True
    st = {"day": g.day_key(NOW), "day_baseline": 206.79, "last_flat_balance": 206.79, "last_flat_ts": NOW - 100}
    explained = [ch(closed=True, pnl_usd=-0.69, closed_at_ts=NOW - 50)]
    v = g.evaluate(explained, drift, st, NOW)
    assert "BALANCE_DRIFT" not in codes(v) and abs(st["balance_reconcile"]["drift_usd"]) < 1e-9


def test_large_balance_drop_alone_eg_funding_triggers_nothing():
    """RK ruling 07:57: a large Derivatives balance drop with no trade (funding, transfer) must never block, disarm, force-flat
    or change countability; it is only recorded as a non-gating balance-vs-D3 reconcile warning."""
    def bk(t):   # a fresh full-depth book (age 0.5 s)
        return {"s": t - 1.5, "b": [[100000.0, 1.0]], "a": [[100010.0, 1.0]]}
    st = {}
    assert g.evaluate([], MIR, st, NOW, book=bk(NOW)) == []
    for i, bal in enumerate((150.0, 120.0, 50.0)):
        t = NOW + 60 * (i + 1)
        v = g.evaluate([], dict(MIR, balance={"derivatives_available_usd": bal}), st, t, book=bk(t))
        assert v == [], v
        dis, amber = g.split_violations(v)
        assert dis == [] and amber == [] and g.position_flatten_violations(v) == []
    rec = st["balance_reconcile"]
    assert rec["gating"] is False and rec["warning"] is True and rec["balance_loss_usd"] == 156.79 and rec["gap_usd"] == 156.79
    assert st["ledger_loss_usd"] == 0.0


def test_guardian_daily_cap_counts_open_position_marked_to_book_via_shared_d3():
    """RK D3 (07:57): the guardian's cap = trade_realised_pnl_usd + the open position marked to the book (full-size walk,
    book <= 2 s, else OPEN_MARK_UNREADABLE); the same live_tile_gate.d3_open_mark_loss as admit_entry's daily_cap_entry_block."""
    import live_tile_gate as ltg
    pos = {"symbol": "tBTCF0:USTF0", "side": "LONG", "amount": 0.001, "base_price": 100000.0}
    m = dict(MIR, positions=[pos])
    open_c = ch(correlation_id="GS01-9", fill_at_ts=NOW - 60, stop_confirmed=True, tile="GS01")
    calls = []
    real = ltg.open_mark_to_book_loss

    def fake(p, book, now):
        calls.append(p)
        return 0.20, {"book_age_s": 0.5}
    ltg.open_mark_to_book_loss = fake
    try:
        st = {}
        v = g.evaluate([open_c], m, st, NOW, book={"ts": NOW})
        assert {ltg.VIOL_TILE_LOSS_CAP, ltg.VIOL_TOTAL_LOSS_CAP} <= codes(v), v
        dis, amber = g.split_violations([x for x in v if x["code"] in (ltg.VIOL_TILE_LOSS_CAP, ltg.VIOL_TOTAL_LOSS_CAP)])
        assert dis == [] and amber, "D3 cap is an AMBER entry block, never a disarm"
        assert st["d3"]["open_mark_loss"] == 0.2 and calls
        ltg.open_mark_to_book_loss = lambda p, book, now: (0.20, {"book_age_s": 3.0})   # book older than 2 s => block
        v = g.evaluate([open_c], m, {}, NOW, book={"ts": NOW - 3})
        assert "OPEN_MARK_UNREADABLE" in codes(v) and ltg.VIOL_TOTAL_LOSS_CAP not in codes(v)
        ltg.open_mark_to_book_loss = lambda p, book, now: (None, {"error": "no book"})
        assert "OPEN_MARK_UNREADABLE" in codes(g.evaluate([open_c], m, {}, NOW, book=None))
        ltg.open_mark_to_book_loss = lambda p, book, now: (0.01, {"book_age_s": 0.5})    # under both caps
        assert not ({ltg.VIOL_TILE_LOSS_CAP, ltg.VIOL_TOTAL_LOSS_CAP, "OPEN_MARK_UNREADABLE"} & codes(g.evaluate([open_c], m, {}, NOW, book={"ts": NOW})))
    finally:
        ltg.open_mark_to_book_loss = real
    # real full-size walk: LONG 0.001 @ 100500 exits into bid 100000 => $0.50 open loss => tile + total cap (AMBER)
    real_pos = dict(pos, base_price=100500.0)
    st = {}
    v = g.evaluate([open_c], dict(MIR, positions=[real_pos]), st, NOW, book={"s": NOW - 1.5, "b": [[100000.0, 1.0]], "a": [[100010.0, 1.0]]})
    assert {ltg.VIOL_TILE_LOSS_CAP, ltg.VIOL_TOTAL_LOSS_CAP} <= codes(v) and st["d3"]["open_mark_loss"] == 0.5, (v, st.get("d3"))
    import inspect
    assert "d3_open_mark_loss(" in inspect.getsource(ltg.daily_cap_entry_block)
    assert "d3_cap_violations(" in inspect.getsource(g.evaluate)
    src = inspect.getsource(g.evaluate)
    tail = src[src.index("RK ruling 07:57"):]
    assert "out.append" not in tail and "out.extend" not in tail, "the balance reconcile must never append a violation"


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
              "_get_live_copy_output": lambda: Out(on), "_offline_sim_epoch_floor": lambda: None,
              "_tile_integrity_chains": lambda now: [{"correlation_id": "GS01-1", "lane": "FAMILY_GS01_XV", "fill_at_ts": now - 9}],
              "_live_guardian_mirror": lambda: {"ok": True, "positions": [{}], "open_orders": [], "orphans": [], "balance": {"derivatives_available_usd": 206.0}},
              "_disarm_live_control": lambda reason: calls.append(reason) or {"cancel": {"failed": []}},
              "_live_guardian_exposed": lambda chains: False, "_live_guardian_record_protection": lambda now, viol: None, "_live_guardian_persist": lambda: None,
"_live_guardian_force_flat": lambda reason, now=None, **kw: FF.append(reason) or {"requested": True, "reason": reason}}
        exec(src[start:end], g_)
        return g_["_live_guardian_tick"]()
    assert run(False) == [] and calls == []
    v = run(True)
    assert any(x["code"] == "STOP_MISSING_GT_5S" for x in v) and calls and calls[0].startswith("AUTO_DISARM:") and "STOP_MISSING_GT_5S" in calls[0]


# ---- drill: every trigger disarms through the bot tick, plus heartbeat, arm refusal, relay reach, no order calls --------------
import pathlib as _pl, time as _t, logging as _lg2

_SRC = _pl.Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def _tick_ns(on=True, chains=None, mirror=None, epoch=None):
    start, end = _SRC.index("def _live_guardian_tick"), _SRC.index("def _live_guardian_heartbeat_stale")
    calls = []

    class Out:
        def snapshot(self): return {"enabled": on}

    ns = {"time": _t, "_LIVE_GUARDIAN_ERROR_LIMIT": 3, "logger": _lg2.getLogger("t"), "_LIVE_GUARDIAN_STATE": {"enabled": True, "calc": {}, "disarms": [], "last_violations": [], "heartbeat_ts": None},
          "_get_live_copy_output": lambda: Out(), "_tile_integrity_chains": lambda now: chains(now) if callable(chains) else (chains or []),
          "_live_guardian_mirror": lambda: mirror, "_offline_sim_epoch_floor": lambda: epoch,
          "_disarm_live_control": lambda reason: calls.append(reason) or {"cancel": {"failed": []}},
              "_live_guardian_exposed": lambda chains: False, "_live_guardian_record_protection": lambda now, viol: None, "_live_guardian_persist": lambda: None,
"_live_guardian_force_flat": lambda reason, now=None, **kw: FF.append(reason) or {"requested": True, "reason": reason}}
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
    }
    for code, (chains, mirror, epoch) in cases.items():
        ns, calls = _tick_ns(True, chains, mirror, epoch)
        if code == "ORPHAN":
            ns["_LIVE_GUARDIAN_STATE"]["calc"]["orphan_since"] = now - 10
        v = ns["_live_guardian_tick"](now)
        assert code in {x["code"] for x in v} and calls and code in calls[0], code
    # AMEND4 E1 (L12): a daily-loss-cap breach is an AMBER entry block only - never a disarm, never a force-flat
    ns, calls = _tick_ns(True, lambda n: [ch(fill_at_ts=n - 500, stop_confirmed=True, closed=True, pnl_usd=-1.2, closed_at_ts=n - 100,
                                             exit_at_ts=n - 100)], _mir(), None)
    ns["_live_guardian_tick"](now)
    assert calls == [] and "DAILY_LOSS_CAP" in (ns["_LIVE_GUARDIAN_STATE"]["amber"] or {}).get("codes", [])
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
    ns = {"time": _t, "_LIVE_GUARDIAN_ERROR_LIMIT": 3, "logger": _lg2.getLogger("t"), "_LIVE_GUARDIAN_STATE": {"heartbeat_ts": _t.time() - 60, "disarms": []},
          "_get_live_copy_output": lambda: Out(), "_disarm_live_control": lambda r: calls.append(r),
          "_live_guardian_force_flat": lambda reason, now=None, **kw: FF.append(reason) or {"requested": True}}
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


import datetime as _dtm


def test_rolling_window_does_not_reset_at_the_utc_or_sydney_day_boundary():
    """HM: day_key used the UTC day (rolls at 11:00 AEDT). Loss cap and baseline now use a rolling 24 h window."""
    ts = _dtm.datetime(2026, 10, 19, 23, 59, 0, tzinfo=_dtm.timezone.utc).timestamp()      # one minute before the UTC rollover
    st = {}
    assert g.evaluate([], MIR, st, ts) == []
    low = dict(MIR, balance={"derivatives_available_usd": 205.70})
    after = ts + 120                                                                       # two minutes later, new UTC day
    assert g.evaluate([], low, st, after) == [] and st["balance_reconcile"]["balance_loss_usd"] == 1.09, "baseline must survive the UTC midnight (reconcile only, RK 07:57)"
    loss = [ch(closed=True, pnl_usd=-1.2, fill_at_ts=ts - 900, closed_at_ts=ts - 30)]
    assert "DAILY_LOSS_CAP" in codes(g.evaluate(loss, MIR, {}, after)), "ledger loss must survive the UTC midnight"
    assert "DAILY_LOSS_CAP" not in codes(g.evaluate([ch(closed=True, pnl_usd=-1.2, fill_at_ts=ts - 1000, closed_at_ts=ts - 90000)], MIR, {}, after)), "older than 24 h ages out"
    assert g.day_key(_dtm.datetime(2026, 10, 19, 13, 0, tzinfo=_dtm.timezone.utc).timestamp()) == "2026-10-20"   # Sydney day, AEDT


def test_ledger_loss_cap_fires_even_when_the_exchange_read_is_blind_and_blind_since_resets_when_flat():
    loss = [ch(closed=True, pnl_usd=-1.5, fill_at_ts=NOW - 900, closed_at_ts=NOW - 60)]
    assert "DAILY_LOSS_CAP" in codes(g.evaluate(loss, {"ok": False, "error": "x"}, {}, NOW))
    st = {}
    g.evaluate([], {"ok": False}, st, NOW)
    assert "blind_since" not in st and st["read_state"] == "BLIND"            # nothing exposed: no accumulation
    g.evaluate([ch(fill_at_ts=NOW - 1, stop_confirmed=True)], {"ok": False}, st, NOW + 1)
    assert st["blind_since"] == NOW + 1
    g.evaluate([], {"ok": False}, st, NOW + 100)
    assert "blind_since" not in st                                               # exposure ended, blind clock cleared


def _loop_ns(on, tick_raises):
    start, end = _SRC.index("def _live_guardian_tick"), _SRC.index("def _live_guardian_heartbeat_stale")
    ns, calls = _tick_ns(on, lambda n: (_ for _ in ()).throw(RuntimeError("boom")) if tick_raises else [], _mir())
    return ns, calls


def test_guardian_evaluation_error_does_not_stamp_heartbeat_and_disarms_after_three_consecutive_failures():
    ns, calls = _loop_ns(True, True)
    st = ns["_LIVE_GUARDIAN_STATE"]
    ok_ns, _ = _loop_ns(True, False)
    ok_ns["_live_guardian_tick"](NOW)
    assert ok_ns["_LIVE_GUARDIAN_STATE"]["heartbeat_ts"] == NOW and ok_ns["_LIVE_GUARDIAN_STATE"]["error_streak"] == 0
    for i in range(2):
        try:
            ns["_live_guardian_tick"](NOW + i)
            raise AssertionError("tick must raise")
        except RuntimeError as exc:
            assert ns["_live_guardian_on_error"](exc, NOW + i) is False
    assert st["heartbeat_ts"] is None and st["error_streak"] == 2 and not calls     # never stamped, no disarm yet
    try:
        ns["_live_guardian_tick"](NOW + 3)
    except RuntimeError as exc:
        assert ns["_live_guardian_on_error"](exc, NOW + 3) is True
    assert calls == ["AUTO_DISARM:GUARDIAN_ERROR"] and st["disarms"][-1]["reason"] == "AUTO_DISARM:GUARDIAN_ERROR"
    off_ns, off_calls = _loop_ns(False, True)
    for i in range(5):
        off_ns["_live_guardian_on_error"](RuntimeError("x"), NOW + i)
    assert off_calls == []                                                          # output OFF: nothing to disarm
    assert "_live_guardian_on_error(exc)" in _SRC[_SRC.index("def live_guardian_loop"):][:600]


# ---- HM re-read fixes (#784) -------------------------------------------------------------------------------------------
def test_closed_row_without_close_time_still_counts_in_loss_cap():
    c = ch(fill_at_ts=NOW - 500, stop_confirmed=True, closed=True, pnl_usd=-1.5)   # no closed_at_ts / exit_at_ts
    assert "DAILY_LOSS_CAP" in codes(g.evaluate([c], MIR, {}, NOW))
    assert g.close_ts(c, NOW) == NOW


def test_failed_disarm_counts_as_error_and_skips_heartbeat():
    now = _t.time()
    ns, calls = _tick_ns(True, lambda n: [ch(fill_at_ts=n - 9)], _mir(positions=[{"id": 1}]))
    def boom(reason): raise RuntimeError("disarm down")
    ns["_disarm_live_control"] = boom
    st = ns["_LIVE_GUARDIAN_STATE"]
    st["heartbeat_ts"] = None
    v = ns["_live_guardian_tick"](now)
    assert v and st["heartbeat_ts"] is None and st["error_streak"] == 1 and "disarm down" in st["last_error"]["error"]
    assert st["disarms"][-1]["error"]
    ns["_live_guardian_tick"](now + 1); ns["_live_guardian_tick"](now + 2)
    assert st["error_streak"] == 3 and st["heartbeat_ts"] is None


def test_off_tick_keeps_flat_hist_and_drops_transient_markers():
    now = _t.time()
    ns, _ = _tick_ns(False, [], _mir())
    st = ns["_LIVE_GUARDIAN_STATE"]
    st["calc"] = {"flat_hist": [[now - 60, 210.0]], "day_baseline": 210.0, "orphan_since": now - 5, "last_flat_balance": 210.0, "last_flat_ts": now - 5, "blind_since": now - 9}
    ns["_live_guardian_tick"](now)
    assert st["calc"] == {"flat_hist": [[now - 60, 210.0]], "day_baseline": 210.0}


def test_missing_epoch_floor_fails_loudly_and_payload_trims_flat_hist():
    ns, _ = _tick_ns(True, [], _mir())
    del ns["_offline_sim_epoch_floor"]
    try:
        ns["_live_guardian_tick"](_t.time())
        raise AssertionError("must raise")
    except RuntimeError as exc:
        assert "epoch floor" in str(exc)
    assert "flat_hist_n" in _SRC[_SRC.index("def monitor_live_guardian_route"):][:4000]
    assert '"flat_hist"' in _SRC[_SRC.index("def monitor_live_guardian_route"):][:4000]   # excluded, only the count is emitted


def test_long_off_gap_drops_stale_baseline_no_false_loss_cap():
    st = {"flat_hist": [], "day_baseline": 210.0, "day_baseline_ts": NOW - 30 * 3600}
    v = g.evaluate([], dict(MIR, balance={"derivatives_available_usd": 205.0}), st, NOW)
    assert "DAILY_LOSS_CAP" not in codes(v) and st["day_baseline"] == 205.0 and st["day_baseline_ts"] == NOW
    # a fresh (<24 h) baseline still counts in the (non-gating, RK 07:57) reconcile
    st2 = {"flat_hist": [], "day_baseline": 210.0, "day_baseline_ts": NOW - 3600}
    assert g.evaluate([], dict(MIR, balance={"derivatives_available_usd": 205.0}), st2, NOW) == [] and st2["balance_reconcile"]["balance_loss_usd"] == 5.0


def test_baseline_ts_written_with_peak_time():
    st = {}
    g.evaluate([], MIR, st, NOW)
    assert st["day_baseline_ts"] == NOW
    g.evaluate([], dict(MIR, balance={"derivatives_available_usd": 206.5}), st, NOW + 60)
    assert st["day_baseline"] == 206.79 and st["day_baseline_ts"] == NOW


def test_closed_row_without_close_time_does_not_create_false_drift():
    c = ch(fill_at_ts=NOW - 500, stop_confirmed=True, closed=True, pnl_usd=-0.3)   # no close time
    st = {"last_flat_balance": 206.79, "last_flat_ts": NOW - 30}
    for i in range(3):
        v = g.evaluate([c], dict(MIR, balance={"derivatives_available_usd": 206.79}), st, NOW + i * 30)
        assert "BALANCE_DRIFT" not in codes(v) and not st["balance_reconcile"]["drift_usd"], i
    assert st["ledger_loss_usd"] == 0.3        # still counted in the loss cap


def test_live_guardian_route_returns_a_response():
    """Regression (19 Oct): the #784/#786 union merge dropped the route's return line -> HTTP 500."""
    import ast
    tree = ast.parse(_SRC)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "monitor_live_guardian_route")
    assert isinstance(fn.body[-1], ast.Return) and "_monitor_response" in ast.unparse(fn.body[-1])
    for n in tree.body:   # every monitor route in bot.py ends with a return
        if isinstance(n, ast.FunctionDef) and n.name.startswith("monitor_") and n.name.endswith("_route") and any(
                isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "route" for d in n.decorator_list):
            assert isinstance(n.body[-1], (ast.Return, ast.If, ast.Try, ast.With)), n.name


# ------------------------------------------------- two-tile live gate: every live tile, tile attribution (24 Oct)
import live_tile_gate as _ltg

GS6_LANE = "FAMILY_GS06_COMMITTED_FADE_ATR_TP"


def _tiles(v, code):
    return {x.get("tile") for x in v if x["code"] == code}


def test_guardian_covers_every_live_tile_not_only_gs01():
    assert g.LANE_NEEDLE == ""
    c = {"correlation_id": "gs6-1", "lane": GS6_LANE, "fill_at_ts": NOW - 6}
    v = g.evaluate([c], dict(MIR, positions=[{"id": 1}]), {}, NOW, lane_needle="")
    assert "STOP_MISSING_GT_5S" in codes(v) and _tiles(v, "STOP_MISSING_GT_5S") == {"gs6"}


def test_mutex_breach_two_open_live_positions():
    cs = [{"correlation_id": "gs1-1", "lane": "FAMILY_GS01_XV_PREMIUM_ATR_TP", "fill_at_ts": NOW - 60, "stop_confirmed": True},
          {"correlation_id": "gs6-1", "lane": GS6_LANE, "fill_at_ts": NOW - 60, "stop_confirmed": True}]
    v = g.evaluate(cs, dict(MIR, positions=[{"id": 1}, {"id": 2}]), {}, NOW, lane_needle="")
    assert "MUTEX_BREACH" in codes(v)
    assert "MUTEX_BREACH" not in codes(g.evaluate(cs[:1], dict(MIR, positions=[{"id": 1}]), {}, NOW, lane_needle=""))


def test_stop_placement_failed_and_slow_stop_disarm_with_tile():
    c = {"correlation_id": "gs6-1", "lane": GS6_LANE, "fill_at_ts": NOW - 60, "stop_confirmed": True,
         "errors": [{"type": "STOP_FAILED"}]}
    v = g.evaluate([c], dict(MIR, positions=[{"id": 1}]), {}, NOW, lane_needle="")
    assert _tiles(v, "STOP_PLACEMENT_FAILED") == {"gs6"}
    slow = {"correlation_id": "gs1-1", "lane": "FAMILY_GS01_XV_PREMIUM_ATR_TP", "fill_at_ts": NOW - 60,
            "stop_confirmed": True, "stop_confirmed_at_ts": NOW - 54}
    v = g.evaluate([slow], dict(MIR, positions=[{"id": 1}]), {}, NOW, lane_needle="")
    assert _tiles(v, "FILL_TO_STOP_GT_5S") == {"gs1"}
    fast = dict(slow, stop_confirmed_at_ts=NOW - 57)
    assert "FILL_TO_STOP_GT_5S" not in codes(g.evaluate([fast], dict(MIR, positions=[{"id": 1}]), {}, NOW, lane_needle=""))


def test_tile_key_mismatch_entry_order_without_its_tile_chain_after_grace():
    order = {"id": 7, "type": "LIMIT", "reduce_only": False, "cid": _ltg.encode_cid("gs6", 12345)}
    m = dict(MIR, open_orders=[order])
    st = {}
    assert "TILE_KEY_MISMATCH" not in codes(g.evaluate([], m, st, NOW, lane_needle=""))     # inside grace
    v = g.evaluate([], m, st, NOW + g.LEDGER_GRACE_SEC, lane_needle="")
    assert _tiles(v, "TILE_KEY_MISMATCH") == {"gs6"}
    # matching non-closed chain of that tile (however old its order): no mismatch, state cleared
    pend = {"correlation_id": "gs6-1", "lane": GS6_LANE, "order_sent_at_ts": NOW - 900}
    st2 = {}
    for t in (NOW, NOW + 30):
        assert "TILE_KEY_MISMATCH" not in codes(g.evaluate([pend], m, st2, t, lane_needle=""))
    # legacy (non tile-keyed) cids and reduce-only stops are never attributed by cid (HM C6: only reduce_only is skipped)
    legacy = dict(MIR, open_orders=[{"id": 8, "type": "LIMIT", "cid": 12345}, {"id": 9, "type": "STOP", "reduce_only": True, "cid": _ltg.encode_cid("gs1", 1)}])
    st3 = {}
    g.evaluate([], legacy, st3, NOW, lane_needle="")
    assert "TILE_KEY_MISMATCH" not in codes(g.evaluate([], legacy, st3, NOW + 30, lane_needle=""))


# ---- HM G6 daily realised loss caps (prereg AMEND3 D3: $0.05 per tile, $0.10 total, UTC day) ----
import live_tile_gate as ltg  # noqa: E402

DAY0 = 1_800_000_000.0 - (1_800_000_000.0 % 86400)      # 00:00 UTC of NOW's day


def lc(tid, pnl, at):
    return {"correlation_id": tid, "trade_id": tid, "closed": True, "pnl_usd": pnl, "closed_at_ts": at}


def test_g6_caps_are_pinned_to_the_prereg_values():
    assert ltg.DAILY_LOSS_CAP_PER_TILE_USD == 0.05 and ltg.DAILY_LOSS_CAP_TOTAL_USD == 0.10
    assert "632dcf347ce061a3f81d0afd73a4820d0406f4b714e11d5daed00485183efde9" in ltg.DAILY_LOSS_CAP_SOURCE


def test_g6_per_tile_cap_trips_at_five_cents_and_names_the_tile():
    v = g.evaluate([lc("gs1-a", -0.03, NOW - 10), lc("gs1-b", -0.02, NOW - 5)], MIR, {}, NOW)
    tile = [x for x in v if x["code"] == ltg.VIOL_TILE_LOSS_CAP]
    assert tile and tile[0]["tile"] == "gs1"
    assert ltg.VIOL_TOTAL_LOSS_CAP not in codes(v)
    assert not ltg.daily_loss_violations([lc("gs1-a", -0.049, NOW - 10)], NOW)


def test_g6_total_cap_trips_across_tiles_when_no_single_tile_does():
    rows = [lc("gs1-a", -0.04, NOW - 10), lc("gs6-a", -0.04, NOW - 9), lc("gb1-a", -0.02, NOW - 8)]
    v = ltg.daily_loss_violations(rows, NOW)
    assert codes(v) == {ltg.VIOL_TOTAL_LOSS_CAP}


def test_g6_uses_the_utc_day_and_wins_offset_losses_on_the_same_day():
    # yesterday's loss does not count today; a same-day win offsets a same-day loss
    assert not ltg.daily_loss_violations([lc("gs1-a", -0.09, DAY0 - 1)], NOW)
    assert not ltg.daily_loss_violations([lc("gs1-a", -0.06, NOW - 20), lc("gs1-b", 0.02, NOW - 10)], NOW)
    # a closed row with pnl but no close time counts as today (conservative)
    assert ltg.daily_loss_violations([{"correlation_id": "gs1-x", "closed": True, "pnl_usd": -0.05}], NOW)


def test_g6_unattributed_loss_counts_towards_total_and_errors_fail_safe():
    v = ltg.daily_loss_violations([lc("zzz-a", -0.10, NOW - 1)], NOW)
    assert codes(v) == {ltg.VIOL_TOTAL_LOSS_CAP}
    v = ltg.daily_loss_violations([None], NOW)          # malformed input -> violation, never silently passes
    assert v and v[0]["code"].endswith("_ERROR")


def test_g6_is_checked_even_when_the_exchange_mirror_is_blind():
    v = g.evaluate([lc("gs1-a", -0.05, NOW - 3)], {"ok": False, "error": "x"}, {}, NOW)
    assert ltg.VIOL_TILE_LOSS_CAP in codes(v)


# ------------------------------------------------------------------ HM R2 C2 / RK force-flat (bounded retries, RED, no auto re-arm)
def _ff_ns():
    import collections, logging, threading, time as _tt
    from collections import deque
    src = _SRC[_SRC.index("LIVE_GUARDIAN_FORCE_FLAT_CONFIRMATION ="):_SRC.index("def _live_copy_stale_exit_check")]
    ns = {"time": _tt, "threading": threading, "deque": deque, "collections": collections, "logger": logging.getLogger("t"), "_LIVE_GUARDIAN_STATE": {},
          "_BOT_ADMIN_TOKEN": "x", "LIVE_COPY_OPS_BASE": "http://relay", "LIVE_COPY_OPS_USER_ID": "u", "requests": None,
          "_live_guardian_persist": lambda: True, "_disarm_live_control": lambda reason: None}
    exec(src, ns)
    return ns


def test_c2_force_flat_ok_only_on_flat_no_stops_and_paused():
    ns = _ff_ns()
    rec = {"reason": "MUTEX_BREACH"}
    seq = [{"ok": False, "verdict": "NOT_FLAT"}, {"ok": True, "verdict": "FLAT_NO_STOPS", "paused": True}]
    ns["_live_guardian_force_flat_worker"](rec, post=lambda r: seq.pop(0), sleep=lambda s: None)
    assert rec["ok"] and rec["readback"] == "FLAT_NO_STOPS" and rec["attempts"] == 2
    assert "red_alert" not in ns["_LIVE_GUARDIAN_STATE"] and ns["_live_guardian_arm_block"]() is None


def test_c2_force_flat_unconfirmed_after_bounded_retries_is_red_and_blocks_arming():
    ns = _ff_ns()
    calls, sleeps = [], []
    ff = ns["_live_guardian_force_flat"]("STOP_PLACEMENT_FAILED", now=1.0,
                                        spawn=lambda fn: None)   # spawned, not yet done -> arming refuses (in progress)
    assert ns["_live_guardian_arm_block"]() == "LIVE_GUARDIAN_FORCE_FLAT_IN_PROGRESS"
    assert ns["_LIVE_GUARDIAN_STATE"]["auto_disarm_latched"]["reason"] == "STOP_PLACEMENT_FAILED"
    ns["_live_guardian_force_flat_worker"](ff, post=lambda r: calls.append(r) or {"ok": False, "verdict": "FLAT_WITH_ORDERS"},
                                           sleep=sleeps.append)
    assert len(calls) == len(ns["_LIVE_GUARDIAN_FORCE_FLAT_RETRY_SEC"]) == 3 and sleeps == [5.0, 15.0]
    red = ns["_LIVE_GUARDIAN_STATE"]["red_alert"]
    assert red["code"] == "FORCE_FLAT_UNCONFIRMED" and red["readback"] == "FLAT_WITH_ORDERS"
    assert ns["_live_guardian_arm_block"]() == "LIVE_GUARDIAN_FORCE_FLAT_RED"
    assert ns["_live_guardian_force_flat_check"]()["status"] == "RED"
    # a post that raises is also unconfirmed (never treated as flat)
    ns2 = _ff_ns()
    ns2["_live_guardian_force_flat_worker"]({"reason": "x"}, post=lambda r: 1 / 0, sleep=lambda s: None)
    assert ns2["_LIVE_GUARDIAN_STATE"]["red_alert"]["readback"] == "UNCONFIRMED"


def test_c2_wiring_red_alert_surfaces_arming_refuses_and_stale_exit_never_rearms():
    assert "_live_guardian_force_flat_check()" in _SRC[_SRC.index("def _system_health_fly_self_checks"):]
    assert _SRC.count("_live_guardian_arm_block()") >= 3   # live output ON route + per-tile ON route
    stale = _SRC[_SRC.index("def _live_copy_stale_exit_check"):]
    stale = stale[:stale.index("\ndef ")]
    assert stale.index('_LIVE_GUARDIAN_STATE.get("auto_disarm_latched")') < stale.index("_BOT_ADMIN_TOKEN:")   # before the rearm post
    tick = _SRC[_SRC.index("def _live_guardian_tick"):_SRC.index("def live_guardian_loop")]
    assert "_live_guardian_force_flat(" in tick
    helper = _SRC[_SRC.index("def _live_guardian_force_flat_post"):_SRC.index("def _live_guardian_force_flat_worker")]
    assert "/force-flat-disarm" in helper and "rearm" not in helper and "logger" not in helper   # never logs the token
    on = _SRC[_SRC.index('reason=_onoff_reason("OPERATOR_ON", data))'):][:200]
    assert '_LIVE_GUARDIAN_STATE.pop("auto_disarm_latched", None)' in on


def test_rk0225_untagged_non_reduce_only_order_is_red_immediately():
    import live_tile_gate as ltg
    tagged = (0b101 << 28) | (2 << 21) | 77     # gs1
    gs1 = ch(correlation_id="gs1-1", order_sent_at_ts=NOW - 1)
    for o in ({"id": 1, "cid": 987654, "reduce_only": False}, {"id": 2, "cid": None, "reduce_only": False},
              {"id": 3, "cid": 987654, "reduce_only": None}, {"id": 4, "cid": (0b101 << 28) | (6 << 21) | 1, "reduce_only": False}):
        v = g.evaluate([gs1], dict(MIR, open_orders=[o]), {}, NOW)
        assert "UNTAGGED_ORDER" in codes(v), o
        assert "UNTAGGED_ORDER" in g.RED_CODES
    # reduce-only closing orders (stops) may be untagged; a tagged entry of a live tile is fine
    assert "UNTAGGED_ORDER" not in codes(g.evaluate([gs1], dict(MIR, open_orders=[{"id": 5, "cid": 987654, "reduce_only": True}]), {}, NOW))
    assert ltg.decode_cid(tagged) == "gs1"
    assert "UNTAGGED_ORDER" not in codes(g.evaluate([gs1], dict(MIR, open_orders=[{"id": 6, "cid": tagged, "reduce_only": False}]), {}, NOW))


def test_rk0225_untagged_position_is_red_after_grace_tagged_is_not():
    mp = dict(MIR, positions=[{"id": 1, "amount": 0.001}])
    st = {}
    assert "UNTAGGED_POSITION" not in codes(g.evaluate([], mp, st, NOW))
    assert "UNTAGGED_POSITION" in codes(g.evaluate([], mp, st, NOW + g.LEDGER_GRACE_SEC))
    # an untagged (no tile key) open chain does not explain the position either
    st2 = {}
    legacy = ch(correlation_id="LEGACY-1", lane="LEGACY", fill_at_ts=NOW - 100, stop_confirmed=True)
    g.evaluate([legacy], mp, st2, NOW)
    assert "UNTAGGED_POSITION" in codes(g.evaluate([legacy], mp, st2, NOW + 11))
    # a tile-keyed open live chain explains it; flat clears the timer
    st3 = {}
    gs1 = ch(correlation_id="gs1-1", fill_at_ts=NOW - 100, stop_confirmed=True)
    g.evaluate([gs1], mp, st3, NOW)
    assert "UNTAGGED_POSITION" not in codes(g.evaluate([gs1], mp, st3, NOW + 11))
    g.evaluate([], mp, st, NOW + 12)
    g.evaluate([], MIR, st, NOW + 13)
    assert "untagged_pos_since" not in st


def test_rk0225_tick_latches_red_for_untagged_codes_before_disarm_and_force_flat():
    tick = _SRC[_SRC.index("def _live_guardian_tick"):_SRC.index("def live_guardian_loop")]
    red = tick.index('_lg.latch_red(_LIVE_GUARDIAN_STATE, {')
    assert "RED_CODES" in tick and red < tick.index("_disarm_live_control(reason)") < tick.index("_live_guardian_force_flat(reason, now, disarmed=True)")


def test_rk3_evidence_read_timeout_is_stamped_e6_read_timeout():
    """RK binding ruling (3) (#852 R3): the relay mirror's error_code E6_READ_TIMEOUT becomes the STOP_FAILURE cause."""
    c = ch(fill_at_ts=NOW - 100, stop_confirmed=True)
    timed_out = {"ok": False, "error": "Bitfinex v2/auth/r/positions: authenticated request total deadline exceeded after 2000ms",
                 "error_code": "E6_READ_TIMEOUT"}
    st = {}
    assert g.evaluate([c], timed_out, st, NOW, book=None) == []
    out = g.evaluate([c], timed_out, st, NOW + 11, book=None)
    sf = [v for v in out if v["code"] == g._ltg_caps.STOP_FAILURE_FLATTEN]
    assert sf and sf[0]["cause"] == "E6_READ_TIMEOUT"
    blind = [v for v in g.evaluate([c], timed_out, st, NOW + 31, book=None) if v["code"] == "EXCHANGE_MIRROR_BLIND"]
    assert blind and blind[0]["cause"] == "E6_READ_TIMEOUT"
    # any other failed read keeps the generic cause
    st2 = {}
    g.evaluate([c], {"ok": False, "error": "HTTP 500", "error_code": "EXCHANGE_READ_FAILED"}, st2, NOW, book=None)
    sf2 = [v for v in g.evaluate([c], {"ok": False, "error": "HTTP 500"}, st2, NOW + 11, book=None)
           if v["code"] == g._ltg_caps.STOP_FAILURE_FLATTEN]
    assert sf2 and sf2[0]["cause"] == "EXCHANGE_READ_FAILED"
