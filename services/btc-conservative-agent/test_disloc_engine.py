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


class Book:
    """book_at(t): latest 1 s row whose second has closed by t (s + 1 <= t); records every read."""

    def __init__(self, rows, now_ref):
        self.rows, self.now_ref, self.reads = rows, now_ref, []

    def __call__(self, t):
        self.reads.append((t, self.now_ref[0]))
        ok = [s for s in self.rows if s + 1 <= t]
        return {"s": max(ok), **self.rows[max(ok)]} if ok else None


def _row(bid, ask, sz=1.0):
    return {"b": [[bid, sz], [bid - 0.5, sz]], "a": [[ask, sz], [ask + 0.5, sz]]}


def _book_engine(monkeypatch, book_rows, prem_flip=350, delay=(1.5, "default_until_n10")):
    eng, rows, trades, touch = _engine()
    prems = {t: 11.0 for t in range(0, 600)}
    prems.update({t: 7.0 for t in range(prem_flip, 600)})
    _feed(monkeypatch, eng, prems)
    touch["q"] = (100.0, 100.02, 351.0)
    now = [0.0]
    book = Book(book_rows, now)
    import paper_fill
    monkeypatch.setattr(paper_fill, "book10l_at", book)
    eng.bind_runtime(enabled_fn=lambda: True, trades_fn=lambda: [], touch_fn=lambda: touch["q"],
                     append_fn=rows.append, delay_fn=lambda lane, leg: delay)

    def step(t):
        now[0] = t
        eng.clock = lambda: t
        eng.step(now=t, live=None, bfx_quotes={}, bfx_bbo_ts=t)
    return eng, rows, book, step, touch


def test_stepb_entry_waits_for_delay_and_never_reads_future_book(monkeypatch):
    # (1) no look-ahead: nothing is priced at signal time; every book read is <= the engine clock
    rows_ = {s: _row(100.0, 100.02) for s in range(340, 420)}
    rows_[351] = _row(100.01, 100.03)
    eng, rows, book, step, _ = _book_engine(monkeypatch, rows_)
    step(351.1)
    assert rows[-1]["event"] == "DISLOC_ORDER" and eng.position is None
    assert rows[-1]["fill_due_ts"] == 352.5 and not book.reads
    step(352.0)
    assert eng.position is None
    step(352.6)
    filled = rows[-1]
    assert filled["event"] == "DISLOC_FILLED" and filled["fill_price"] == 100.03
    assert filled["signal_to_fill_s"] == 1.5 and filled["book_s"] == 351
    assert all(t <= now + 1e-9 for t, now in book.reads)


def test_stepb_ioc_cap_no_fill_goes_to_shadow_ledger(monkeypatch):
    # (2) delayed ask 100.02 * (1 + 5 bp) = 100.07 cap; book jumped to 100.20 -> NO_FILL + shadow row
    rows_ = {s: _row(100.0, 100.02) for s in range(340, 352)}
    rows_.update({s: _row(100.18, 100.20) for s in range(352, 420)})
    rows_[351] = _row(100.18, 100.20)
    eng, rows, book, step, _ = _book_engine(monkeypatch, rows_)
    step(351.1)
    step(352.6)
    ev = [r["event"] for r in rows]
    assert ev[-2:] == ["DISLOC_CANCELLED", "DISLOC_SHADOW_NO_FILL"]
    assert rows[-2]["cancel_reason"] == "NO_FILL_IOC_CAP" and eng.position is None
    sh = rows[-1]
    assert sh["shadow_only"] and sh["uncapped_fill_price"] == 100.20 and abs(sh["ioc_cap_px"] - 100.02 * 1.0005) < 1e-9


def test_stepb_ioc_cap_partial_inside_cap(monkeypatch):
    rows_ = {s: _row(100.0, 100.02) for s in range(340, 420)}
    rows_[351] = {"b": [[100.0, 1.0]], "a": [[100.05, 0.0001], [100.5, 5.0]]}
    eng, rows, book, step, _ = _book_engine(monkeypatch, rows_)
    step(351.1)
    step(352.6)
    f = rows[-1]
    assert f["event"] == "DISLOC_FILLED" and f["fill_price"] == 100.05 and f["fill_status"] == "PARTIAL"
    assert f["filled_qty"] == 0.0001


def test_stepb_exit_uses_same_delay_rule(monkeypatch):
    # (3) TIME_60S triggers at 412.6 on signal-time data; priced from the book at trigger + exit delay
    rows_ = {s: _row(100.0, 100.02) for s in range(340, 420)}
    rows_[351] = _row(100.01, 100.03)
    rows_.update({s: _row(100.10, 100.12) for s in range(413, 420)})
    eng, rows, book, step, touch = _book_engine(monkeypatch, rows_, delay=(1.2, "lane_p50_n12"))
    step(351.1)
    step(352.4)
    assert eng.position is not None
    touch["q"] = (100.04, 100.06, 412.4)
    step(412.5)
    assert eng.position["exit_pending"]["due_ts"] == 412.5 + 1.2 and rows[-1]["event"] == "DISLOC_FILLED"
    step(413.0)
    assert eng.position is not None  # not due yet
    step(413.8)
    c = rows[-1]
    assert c["event"] == "DISLOC_CLOSED" and c["exit_reason"] == "TIME_60S"
    assert c["exit_price"] == 100.0 and c["exit_book_s"] == 412
    assert c["exit_delay_s"] == 1.2 and c["exit_delay_source"] == "lane_p50_n12"
    assert c["exit_trigger_ts"] == 412.5 and abs(c["exit_ts"] - 413.7) < 1e-9
    assert all(t <= now + 1e-9 for t, now in book.reads)


def test_stepb_rows_carry_books_and_delay(monkeypatch):
    # (4) signal_book, fill_book, delay_s, delay_source on every fill row
    rows_ = {s: _row(100.0, 100.02) for s in range(340, 420)}
    eng, rows, book, step, _ = _book_engine(monkeypatch, rows_, delay=(None, None))
    step(351.1)
    step(352.6)
    f = rows[-1]
    for k in ("signal_book", "fill_book", "delay_s", "delay_source", "depth_snapshot_signal", "depth_snapshot_fill"):
        assert f.get(k) is not None, k
    assert f["delay_source"] == "default_until_n10" and f["delay_s"] == 1.5
    assert f["signal_book"]["s"] == 350 and f["fill_book"]["s"] == 351


def test_stepb_no_book_cancels_and_never_uses_touch(monkeypatch):
    eng, rows, book, step, _ = _book_engine(monkeypatch, {})
    step(351.1)
    step(352.6)
    assert rows[-1]["event"] == "DISLOC_CANCELLED" and rows[-1]["cancel_reason"] == "NO_BOOK_AT_FILL"


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


# ---- HM 13:27 step-B block items -------------------------------------------------------------
def _opened(monkeypatch, rows_, delay=(1.2, "lane_p50_n12")):
    eng, rows, book, step, touch = _book_engine(monkeypatch, rows_, delay=delay)
    step(351.1)            # signal: no book read at all
    assert not book.reads
    step(352.4)            # entry priced at 351 + 1.2
    assert eng.position is not None
    return eng, rows, book, step, touch


def _no_future_reads(book):
    assert book.reads and all(t <= now + 1e-9 for t, now in book.reads)


def test_stepb_signal_evaluation_never_reads_the_book(monkeypatch):
    eng, rows, book, step, _ = _book_engine(monkeypatch, {s: _row(100.0, 100.02) for s in range(300, 420)})
    for t in (349.1, 350.1, 351.1):
        step(t)
    assert rows[-1]["event"] == "DISLOC_ORDER" and not book.reads


def test_stepb_stop_exit_priced_after_delay_no_look_ahead(monkeypatch):
    rows_ = {s: _row(100.0, 100.02) for s in range(340, 420)}
    rows_.update({s: _row(99.40, 99.42) for s in range(361, 420)})
    eng, rows, book, step, touch = _opened(monkeypatch, rows_)
    stop = eng.position["stop_px"]
    touch["q"] = (stop - 0.01, stop + 0.01, 361.0)
    step(361.5)
    ex = eng.position["exit_pending"]
    assert ex["reason"] == "STOP" and ex["due_ts"] == 361.5 + 1.2
    step(362.0)
    assert eng.position is not None
    step(362.8)
    c = rows[-1]
    assert c["event"] == "DISLOC_CLOSED" and c["exit_reason"] == "STOP" and c["exit_price"] == 99.40
    assert c["exit_book_s"] == 361 and "stop_px" not in c   # real book, no clamp to the stop price
    _no_future_reads(book)


def test_stepb_gap_closed_exit_no_look_ahead(monkeypatch):
    rows_ = {s: _row(100.0, 100.02) for s in range(340, 420)}
    eng, rows, book, step, touch = _opened(monkeypatch, rows_)
    monkeypatch.setattr(eng.rule, "gap_closed_exit", True, raising=False)
    monkeypatch.setattr(eng.rule, "gap_closed", lambda side, prem: True)
    step(355.0)
    assert eng.position["exit_pending"]["reason"] == "GAP_CLOSED"
    step(356.3)
    assert rows[-1]["event"] == "DISLOC_CLOSED" and rows[-1]["exit_reason"] == "GAP_CLOSED"
    _no_future_reads(book)


def test_stepb_time_exit_no_look_ahead(monkeypatch):
    rows_ = {s: _row(100.0, 100.02) for s in range(340, 430)}
    eng, rows, book, step, touch = _opened(monkeypatch, rows_)
    step(412.5)
    assert eng.position["exit_pending"]["reason"] == "TIME_60S"
    step(413.8)
    assert rows[-1]["exit_reason"] == "TIME_60S"
    _no_future_reads(book)


def test_stepb_partial_exit_closes_only_filled_qty(monkeypatch):
    rows_ = {s: _row(100.0, 100.02, sz=5.0) for s in range(340, 430)}
    eng, rows, book, step, touch = _opened(monkeypatch, rows_)
    qty = eng.position["qty"]
    for s in range(412, 414):   # thin bid: only 40% of the position can exit
        rows_[s] = {"b": [[100.0, qty * 0.4]], "a": [[100.02, 5.0]]}
    step(412.5)
    step(413.8)
    assert rows[-1]["event"] == "DISLOC_PARTIAL_EXIT" and eng.position is not None
    assert abs(eng.position["open_qty"] - qty * 0.6) < 1e-9
    step(415.1)               # remainder priced at 413.8 + 1.2 on the restored book
    c = rows[-1]
    assert c["event"] == "DISLOC_CLOSED" and c["exit_pieces"] == 2 and abs(c["exit_filled_qty"] - qty) < 1e-9
    assert c["exit_status"] == "FILLED_IN_PARTS"
    _no_future_reads(book)


def test_stepb_unpriceable_exit_forced_after_10s(monkeypatch):
    rows_ = {s: _row(100.0, 100.02) for s in range(340, 400)}  # book stops at 399
    eng, rows, book, step, touch = _opened(monkeypatch, rows_)
    step(412.5)
    step(413.8)
    assert eng.position is not None
    step(413.7 + de.EXIT_NO_BOOK_FORCE_SEC + 0.2)
    c = rows[-1]
    assert c["event"] == "DISLOC_CLOSED" and c["exit_status"] == "FORCED_NO_BOOK"
    assert c["pnl_bp_vs_touch"] is None and c["fresh_data_excluded"] == "FORCED_NO_BOOK"
    import paper_fill
    assert paper_fill.row_fresh_eligible(c) is False and de.row_net_usd(c) is None


def test_stepb_pnl_sized_on_filled_qty():
    row = {"pnl_bp_vs_touch": 10.0, "requested_qty": 0.0002, "qty": 0.0001, "exit_filled_qty": 0.0001}
    full = {"pnl_bp_vs_touch": 10.0, "requested_qty": 0.0002, "qty": 0.0002, "exit_filled_qty": 0.0002}
    assert abs(de.row_net_usd(row) - de.row_net_usd(full) / 2) < 1e-12
