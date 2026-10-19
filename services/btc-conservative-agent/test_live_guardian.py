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
