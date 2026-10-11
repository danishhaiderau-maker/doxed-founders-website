"""Tile 13 DISLOC: rule, trade-through fills, 5 s / gap-closed cancels, exits, summary and verdict."""
import cross_venue_tape as cvt
import disloc_engine as de
import paper_policy_family_disloc as pol


class Live(dict):
    pass


def _engine(enabled=True):
    rows, trades, touch = [], [], {"q": (100.0, 100.02, 0.0)}
    eng = pol.make_evaluator()
    eng.bind_runtime(enabled_fn=lambda: enabled, trades_fn=lambda: list(trades),
                     touch_fn=lambda: touch["q"], append_fn=rows.append)
    return eng, rows, trades, touch


def _feed(monkeypatch, eng, prems):
    """Drive the engine's feature input directly: {anchor: prem_bp}."""
    state = {}

    def fake(now, live, q, b):
        anchor = int(now) - 1
        prem = prems.get(anchor)
        then = prems.get(anchor - 300)
        return {"anchor_bucket_ts": anchor, "stale_reasons": [], "prem_bp": prem,
                "prem_d5_bp": None if prem is None or then is None else prem - then}
    monkeypatch.setattr(eng, "_features", fake)
    return state


def test_registry_identity_and_c1_rule():
    assert pol.POLICY_ID.startswith("DISLOC_C1_")
    r = pol.RULE
    assert (r.long_prem_max, r.long_d5_max) == (8.1815, -3.0159) and r.long_only and r.short_shadow_only
    assert r.entry_mode == "TAKER_MARKET" and not r.gap_closed_exit and r.hold_sec == 60 and r.stop_bp == 40.0
    assert r.side(8.1815, -3.0159) == "LONG" and r.side(14.0, 3.5) is None and r.side(10.0, -4.0) is None
    assert r.shadow_short(14.0, 3.5)


def test_taker_fill_at_real_ask_after_delay_then_time_exit(monkeypatch):
    eng, rows, trades, touch = _engine()
    prems = {t: 11.0 for t in range(0, 500)}
    prems.update({t: 7.0 for t in range(350, 500)})
    _feed(monkeypatch, eng, prems)
    touch["q"] = (100.0, 100.02, 351.0)
    clock = iter([351.2, 351.5])
    eng.clock = lambda: next(clock)
    touch_seq = iter([(100.0, 100.02, 351.0), (100.01, 100.03, 351.4)])
    eng.touch_fn = lambda: next(touch_seq, (100.05, 100.07, 413.0))
    eng.step(now=351.1, live=None, bfx_quotes={}, bfx_bbo_ts=None)
    order, filled = rows[-2], rows[-1]
    assert order["order_type"].startswith("MARKET_TAKER") and order["signal_ask"] == 100.02
    assert filled["event"] == "DISLOC_FILLED" and filled["fill_price"] == 100.03 and filled["signal_to_fill_s"] == 0.5
    assert filled["fill_bid"] == 100.01 and filled["fill_ask"] == 100.03
    eng.clock = lambda: 413.3
    eng.step(now=413.3, live=None, bfx_quotes={}, bfx_bbo_ts=None)
    closed = rows[-1]
    assert closed["event"] == "DISLOC_CLOSED" and closed["exit_reason"] == "TIME_60S"
    assert abs(closed["pnl_bp_vs_touch"] - (100.05 / 100.03 - 1) * 1e4) < 1e-3
    assert abs(closed["net_bp_taker_2p8"] - (closed["pnl_bp_fill_mid_to_exit_mid"] - 2.8)) < 1e-9
    s = de.summarize(rows, pol.SPEC and pol.EXIT and None)
    assert s["by_side"]["LONG"]["by_fill_delay"]["le_2s"]["closed"] == 1 and s["closed_trades"] == 1


def test_short_side_is_shadow_logged_never_traded(monkeypatch):
    eng, rows, trades, touch = _engine()
    prems = {t: 10.0 for t in range(0, 400)}
    prems.update({350: 14.0})
    _feed(monkeypatch, eng, prems)
    touch["q"] = (100.0, 100.02, 351.0)
    eng.step(now=351.1, live=None, bfx_quotes={}, bfx_bbo_ts=None)
    assert [r["event"] for r in rows] == ["DISLOC_SHADOW_SHORT_SIGNAL"] and eng.order is None and eng.position is None


def test_tile_off_never_orders(monkeypatch):
    eng, rows, trades, touch = _engine(enabled=False)
    prems = {t: 11.0 for t in range(0, 400)}
    prems.update({350: 7.0})
    _feed(monkeypatch, eng, prems)
    touch["q"] = (100.0, 100.02, 351.0)
    _, trigger, outcomes = eng.step(now=351.1, live=None, bfx_quotes={}, bfx_bbo_ts=None)
    assert trigger is None and outcomes == [] and not rows and eng.counters["skipped_tile_off"] == 1


def test_verdict_kill_and_promote():
    base = {"schema": de.SCHEMA, "event": "DISLOC_CLOSED"}
    bad = [{**base, "net_bp_taker_2p8": 0.1, "fill_ts": 3600 * i, "exit_ts": 3600 * i} for i in range(150)]
    assert de.summarize(bad)["pre_registered"]["verdict"] == "KILL"
    good = [{**base, "net_bp_taker_2p8": 2.5 + (i % 3), "fill_ts": 3600 * i, "exit_ts": 3600 * i} for i in range(260)]
    assert de.summarize(good)["pre_registered"]["verdict"] == "PROMOTE_CANDIDATE"


def test_real_feature_path_matches_audit_definition():
    eng, rows, trades, touch = _engine()
    start = 1000
    n = 400
    bn = [100.0] * n
    by = [100.0] * n
    live = {"schema": cvt.LIVE_SCHEMA, "written_ts": start + 360.5, "history_start_ts": start,
            "mids": {"binance": bn, "bybit": by},
            "venues": {"binance": {"last_bbo_ts": start + 360.4}, "bybit": {"last_bbo_ts": start + 360.4}}}
    # Bitfinex mid 100.11 (prem 11 bp) 300 s ago, 100.07 (prem 7 bp) at the anchor
    q_then = {start + 59: (100.10, 100.12)}
    eng.step(now=start + 60.5, live={**live, "written_ts": start + 60.5,
             "venues": {v: {"last_bbo_ts": start + 60.4} for v in ("binance", "bybit")}},
             bfx_quotes=q_then, bfx_bbo_ts=start + 60.4)
    feat = eng._features(start + 360.5, live, {start + 359: (100.06, 100.08)}, start + 360.4)
    assert abs(feat["prem_bp"] - 7.0) < 1e-6 and abs(feat["prem_d5_bp"] - (-4.0)) < 1e-6
    assert eng.rule.side(feat["prem_bp"], feat["prem_d5_bp"]) == "LONG"


def test_foi_variant_c3_bounds_long_only_and_derivatives():
    import paper_policy_family_disloc_foi as foi
    r = foi.RULE
    assert r.long_only and r.long_prem_max == 6.94 and (r.fund_lo, r.fund_hi, r.oi_d60_max) == (0.2096, 0.4705, -10.5573)
    assert r.side(6.94, None, 0.4705, -10.5573) == "LONG"
    assert r.side(6.95, None, 0.3, -11) is None and r.side(6.0, None, 0.2096, -11) is None
    assert r.side(6.0, None, 0.3, -10.0) is None and r.side(20.0, 9.0, 0.3, -11) is None
    eng = foi.make_evaluator()
    assert eng.variant == "foi" and eng.ID_PREFIX == "dsf" and eng.lane == "FAMILY_DISLOC_FOI_LONG_1M"
    der = lambda oi: {"derivatives": {"binance": {"funding_rate": 0.00003}, "bybit": {"funding_rate": 0.00003,
                      "open_interest": oi}, "okx": {"funding_rate": 0.00003, "open_interest": oi}}}
    eng._derivs(0.0, der(1000.0))
    out = eng._derivs(3600.0, der(998.0))
    assert abs(out["fund_bp"] - 0.3) < 1e-9 and abs(out["oi_d60_bp"] - (-20.0)) < 1e-6


def test_foi_scoring_only_fast_fills_and_uncapturable():
    base = {"schema": de.SCHEMA, "event": "DISLOC_CLOSED", "side": "LONG"}
    pre = {"score_max_signal_to_fill_s": 2.0, "uncapturable_p50_delay_above_s": 2.0,
           "early_kill_at_trades": 60, "early_kill_mean_at_or_below_bp": -1.0, "kill_at_trades": 150}
    rows = [{**base, "net_bp_taker_2p8": 5.0, "signal_to_fill_s": 1.0, "fill_ts": i * 3600, "exit_ts": i * 3600}
            for i in range(10)]
    rows += [{**base, "net_bp_taker_2p8": -9.0, "signal_to_fill_s": 4.0, "fill_ts": 1e6, "exit_ts": 1e6}]
    s = de.summarize(rows, pre)
    assert s["scored_trades"] == 10 and s["closed_trades"] == 11 and s["mean_net_bp_taker_2p8"] == 5.0
    slow = [{**base, "net_bp_taker_2p8": 1.0, "signal_to_fill_s": 3.0, "fill_ts": i, "exit_ts": i} for i in range(60)]
    assert de.summarize(slow, pre)["pre_registered"]["verdict"] == "RETIRE_UNCAPTURABLE"
    bad = [{**base, "net_bp_taker_2p8": -2.0, "signal_to_fill_s": 1.0, "fill_ts": i * 3600, "exit_ts": i}
           for i in range(60)]
    assert de.summarize(bad, pre)["pre_registered"]["verdict"] == "KILL_EARLY"
