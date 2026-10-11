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


def test_registry_identity_and_rule_thresholds():
    assert pol.POLICY_ID.startswith("DISLOC_")
    r = pol.RULE
    assert (r.long_prem_max, r.long_d5_max, r.short_prem_min, r.short_d5_min) == (8.1769, -3.0183, 13.0163, 2.9749)
    assert r.ttl_sec == 5.0 and r.hold_sec == 60 and r.stop_bp == 40.0
    assert r.side(8.0, -4.0) == "LONG" and r.side(14.0, 3.5) == "SHORT" and r.side(10.0, -4.0) is None
    assert r.gap_closed("LONG", 8.5) and not r.gap_closed("LONG", 8.0)
    assert r.gap_closed("SHORT", 13.0) and not r.gap_closed("SHORT", 13.5)


def test_ttl_cancel_when_no_trade_through(monkeypatch):
    eng, rows, trades, touch = _engine()
    prems = {t: 11.0 for t in range(0, 400)}
    prems.update({t: 7.0 for t in range(350, 400)})  # 300 s ago 11 -> d5 = -4
    _feed(monkeypatch, eng, prems)
    eng.clock = lambda: 351.2
    touch["q"] = (100.0, 100.02, 351.0)
    eng.step(now=351.1, live=None, bfx_quotes={}, bfx_bbo_ts=None)
    assert eng.order and eng.order["limit_price"] == 100.0 and eng.order["side"] == "LONG"
    trades.append({"p": 100.0, "v": 1, "S": "Sell", "received_ts": 352.0})  # at limit: no fill
    for t in range(352, 358):
        eng.step(now=t + 0.3, live=None, bfx_quotes={}, bfx_bbo_ts=None)
    assert rows[-1]["event"] == "DISLOC_CANCELLED" and rows[-1]["cancel_reason"] == "TTL_5S"


def test_gap_closed_cancel(monkeypatch):
    eng, rows, trades, touch = _engine()
    prems = {t: 11.0 for t in range(0, 400)}
    prems.update({350: 7.0, 351: 9.0})
    _feed(monkeypatch, eng, prems)
    eng.clock = lambda: 351.2
    touch["q"] = (100.0, 100.02, 351.0)
    eng.step(now=351.1, live=None, bfx_quotes={}, bfx_bbo_ts=None)
    eng.step(now=352.3, live=None, bfx_quotes={}, bfx_bbo_ts=None)
    assert rows[-1]["cancel_reason"] == "GAP_CLOSED_BEFORE_FILL"


def test_trade_through_fill_then_time_exit_and_summary(monkeypatch):
    eng, rows, trades, touch = _engine()
    prems = {t: 11.0 for t in range(0, 500)}
    prems.update({t: 7.0 for t in range(350, 500)})
    _feed(monkeypatch, eng, prems)
    eng.clock = lambda: 351.2
    touch["q"] = (100.0, 100.02, 351.0)
    eng.step(now=351.1, live=None, bfx_quotes={}, bfx_bbo_ts=None)
    trades.append({"p": 99.99, "v": 1, "S": "Sell", "received_ts": 353.0})
    touch["q"] = (100.01, 100.03, 353.0)
    eng.step(now=353.3, live=None, bfx_quotes={}, bfx_bbo_ts=None)
    assert eng.position and rows[-1]["event"] == "DISLOC_FILLED"
    assert rows[-1]["order_to_fill_s"] == 1.8 and rows[-1]["signal_to_fill_s"] == 2.0
    touch["q"] = (100.05, 100.07, 413.0)
    eng.step(now=413.3, live=None, bfx_quotes={}, bfx_bbo_ts=None)
    closed = rows[-1]
    assert closed["event"] == "DISLOC_CLOSED" and closed["exit_reason"] == "TIME_60S"
    assert abs(closed["pnl_bp_vs_touch"] - 5.0) < 1e-6
    assert abs(closed["net_bp_taker_2p8"] - (closed["pnl_bp_fill_mid_to_exit_mid"] - 2.8)) < 1e-9
    s = de.summarize(rows, {"kill_at_trades": 150})
    assert s["by_side"]["LONG"]["by_fill_delay"]["le_2s"]["closed"] == 1
    assert s["by_fill_delay"]["2_to_5s"]["closed"] == 0 and s["by_side"]["SHORT"]["tag"].startswith("SHORT_REFUTED")
    assert s["closed_trades"] == 1 and s["fill_rate"] == 1.0 and s["pre_registered"]["verdict"] == "COLLECTING"


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
