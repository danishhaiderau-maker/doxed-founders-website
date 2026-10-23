"""REALISM_V2 F2/F3: the shared trigger helper (quote_trigger) + the F3 entry_trigger_ref_* stamp."""
import ast
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import quote_trigger as qt  # noqa: E402
import trigger_measure as tm  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
T = 1792395270


def _book(s, bid, ask):
    return {"s": s, "b": [[bid, 1.0], [bid - 5, 1.0]], "a": [[ask, 1.0], [ask + 5, 1.0]]}


def test_source_is_always_book_and_ticker_env_refused():
    before = qt.COUNTERS["ticker_refused"]
    assert qt.trigger_price_source("GS01_X", env={}) == "book"
    assert qt.trigger_price_source("GS01_X", env={"TRIGGER_PRICE_SOURCE": "ticker"}) == "book"
    env = {"TRIGGER_PRICE_SOURCE": "GS01_X=book, B1_Y=ticker, *=ticker"}
    assert qt.trigger_price_source("gs01_x", env=env) == "book"
    assert qt.trigger_price_source("B1_Y", env=env) == "book"
    assert qt.trigger_price_source("OTHER", env=env) == "book"
    assert qt.COUNTERS["ticker_refused"] > before
    assert qt.DEFAULT_SOURCE == "book" and qt.SOURCES == ("book",)


def test_any_ticker_source_is_refused():
    import pytest
    for src in ("ticker", "TICKER", "ws_ticker", "rest", "", None):
        with pytest.raises(qt.TickerSourceRefused):
            qt.selected_quote("LONG", "entry", T, src, book=_book(T, 100.0, 101.0))
    with pytest.raises(TypeError):
        qt.selected_quote("LONG", "entry", T, "book", ticker_px=101.0)  # no ticker argument exists any more
    src = open(os.path.join(HERE, "quote_trigger.py"), encoding="utf-8").read()
    assert "ticker_px" not in src and "ticker_ts" not in src


def test_no_ticker_in_bot_trigger_paths():
    src = open(os.path.join(HERE, "bot.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fns = {n.name: ast.get_source_segment(src, n) for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name in ("_realism_family_exit", "_stamp_trigger_basis", "_exchange_stop_trigger", "_realism_level_stops",
                 "_paper_entry_quote_gate"):
        body = fns[name].lower()
        for bad in ("ticker_px", "ticker_ts", "basis_ticker", "ticker_book_stale", "bbo_ts", "state.get(\"bid",
                    "state.get(\"ask", "now) - 30"):
            assert bad not in body, (name, bad)


def test_never_pool_across_sources():
    assert qt.pool_key({}) == "ticker" and qt.pool_key({"trigger_price_source": "book"}) == "book"
    assert qt.pool_key({"trigger_price_source": "BOOK"}) == "book"


def test_exec_side():
    b = _book(T, 100.0, 101.0)
    assert qt.exec_px("LONG", "exit", b) == 100.0 and qt.exec_px("SHORT", "exit", b) == 101.0
    assert qt.exec_px("LONG", "entry", b) == 101.0 and qt.exec_px("SHORT", "entry", b) == 100.0


def test_level_reached_at_or_through():
    assert qt.level_reached("LONG", qt.KIND_STOP, 100.0, 100.0)
    assert qt.level_reached("LONG", qt.KIND_STOP, 100.0, 99.0)
    assert not qt.level_reached("LONG", qt.KIND_STOP, 100.0, 100.5)
    assert qt.level_reached("SHORT", qt.KIND_STOP, 100.0, 100.0)
    assert qt.level_reached("LONG", qt.KIND_TARGET, 105.0, 105.0)
    assert not qt.level_reached("SHORT", qt.KIND_TARGET, 95.0, 96.0)


def test_stops_trigger_on_first_real_trade_only():
    rows = [_book(T, 100.5, 101.0), _book(T + 1, 99.9, 100.4)]  # bid touches 100 when row T+1 completes (T+2)
    trades = [{"ts": T + 1.4, "px": 99.95, "amt": -0.1}]          # first print through 100
    hit = qt.first_level_trigger("LONG", qt.KIND_STOP, 100.0, T, T + 3, book_rows=rows, trades=trades)
    assert hit["trigger_basis"] == "LAST_TRADE" and hit["trigger_ts"] == T + 1.4
    # RK 17:24 BINDING (1): a book touch alone never triggers a stop.
    assert qt.first_level_trigger("LONG", qt.KIND_STOP, 100.0, T, T + 3, book_rows=rows, trades=[]) is None


def test_targets_trigger_on_executable_book_side_only():
    rows = [_book(T, 104.0, 104.5), _book(T + 1, 105.0, 105.5)]
    trades = [{"ts": T + 0.5, "px": 106.0, "amt": 0.1}]
    hit = qt.first_level_trigger("LONG", qt.KIND_TARGET, 105.0, T - 1, T + 3, book_rows=rows, trades=trades)
    assert hit["trigger_basis"] == "BOOK_TOP" and hit["trigger_ts"] == T + 2


def test_exit_events_chronological_dedup_no_compression():
    rows = [_book(T + i, 100.0 + (i % 3), 101.0 + (i % 3)) for i in range(200)]
    rows[150] = _book(T + 150, 90.0, 91.0)
    ev = qt.exit_events("LONG", T - 1, T + 300, book_rows=rows, include_trades=False, max_events=16)
    assert len(ev) > 64 and [e["ts"] for e in ev] == sorted(e["ts"] for e in ev)  # max_events ignored
    assert any(e["px"] == 90.0 for e in ev)
    same = qt.exit_events("LONG", T - 1, T + 5, book_rows=[_book(T, 100, 101), _book(T + 1, 100, 101)])
    assert len(same) == 1


def test_stale_threshold_2s_book():
    assert qt.STALE_S == 2.0
    b = _book(T, 100.0, 101.0)
    q = qt.selected_quote("LONG", "entry", T + 2.0, "book", book=b)
    assert not q["quote_stale"] and q["quote_age_s"] == 2.0
    q = qt.selected_quote("LONG", "entry", T + 2.01, "book", book=b)
    assert q["quote_stale"] and q["quote_reason"] == qt.QUOTE_STALE
    assert qt.entry_quote_gate("LONG", T + 1.5, "book", book=b) is None
    assert qt.entry_quote_gate("LONG", T + 2.5, "book", book=b) == qt.QUOTE_STALE
    assert qt.entry_quote_gate("LONG", T + 0.5, "book", book=None) == qt.QUOTE_STALE


def test_dual_stamp_has_book_and_trade_views_no_ticker():
    d = qt.dual_stamp("LONG", "exit", T + 1.5, book=_book(T, 100.0, 101.0), last_trade={"ts": T + 1.2, "px": 100.2})
    assert set(d) == set(qt.DUAL_STAMP_KEYS) and not any("ticker" in k for k in d)
    assert d["dual_book_px"] == 100.0 and d["dual_book_age_s"] == 1.5
    assert d["dual_last_trade_px"] == 100.2 and d["dual_last_trade_age_s"] == 0.3


def _book_at_from(rows):
    by_s = {int(r["s"]): r for r in rows}

    def book_at(t):
        cands = [s for s in by_s if s + 1 <= t + 1e-9]
        return by_s[max(cands)] if cands else None
    return book_at


def test_book_rows_window_pages_full_window_no_skip():
    rows = [_book(T + i, 100.0, 101.0) for i in range(600)]  # 10 minutes: older code skipped all but the last 30 s
    page = qt.book_rows_window(T, T + 600, _book_at_from(rows))
    assert len(page["rows"]) == 600 and page["gap_seconds"] == [] and page["checked_to"] == T + 600


def test_book_rows_window_stamps_gaps_and_waits_for_late_rows():
    rows = [_book(T + i, 100.0, 101.0) for i in range(10) if i not in (3, 4)]
    page = qt.book_rows_window(T, T + 10, _book_at_from(rows))
    assert page["gap_seconds"] == [T + 3, T + 4] and len(page["rows"]) == 8
    # the newest second (T + 9) is missing but still within the 2 s grace: stop before it, re-read next pass
    late = [r for r in rows if r["s"] != T + 9]
    page2 = qt.book_rows_window(T, T + 10, _book_at_from(late))
    assert page2["checked_to"] == T + 9 and T + 9 not in page2["gap_seconds"]
    page3 = qt.book_rows_window(page2["checked_to"], T + 10, _book_at_from(rows))  # the late row lands
    assert [r["s"] for r in page3["rows"]] == [T + 9]


def test_trigger_stamp_has_trade_id_receive_window_version():
    trades = [{"ts": T + 1.4, "px": 99.95, "amt": -0.1, "id": 777, "recv_s": T + 2}]
    hit = qt.first_level_trigger("LONG", qt.KIND_STOP, 100.0, T, T + 3, trades=trades)
    assert hit["trigger_trade_id"] == 777 and hit["trigger_trade_recv_s"] == T + 2
    assert (hit["trigger_window_t0"], hit["trigger_window_t1"]) == (T, T + 3)
    assert hit["trigger_version"] == qt.TRIGGER_VERSION and hit["stop_basis_unverified"] is True


def test_stop_never_triggers_on_book_touch_only_comparison_column():
    rows = [_book(T, 99.9, 100.4)]               # book top through the stop at T + 1, no trade through
    assert qt.first_level_trigger("LONG", qt.KIND_STOP, 100.0, T, T + 3, book_rows=rows,
                                  trades=[{"ts": T + 1.5, "px": 100.2, "amt": -1}]) is None
    hit = qt.first_level_trigger("LONG", qt.KIND_STOP, 100.0, T, T + 3, book_rows=rows,
                                 trades=[{"ts": T + 2.5, "px": 100.0, "amt": -1, "id": 9}])  # at the level counts
    assert hit["trigger_basis"] == "LAST_TRADE" and hit["trigger_ts"] == T + 2.5
    assert hit["stop_trigger_label"] == "LAST_TRADE_DOC_INFERRED" and hit["stop_trigger_basis"] == "trades"
    assert hit["stop_book_touch_ts"] == T + 1 and hit["stop_book_touch_px"] == 99.9 and hit["stop_book_touch_s"] == T
    assert qt.STOP_BASES == ("trades",)


def test_tie_trade_and_book_row_same_instant_trade_first():
    rows = [_book(T, 105.2, 105.5)]  # ready at T + 1
    ev = qt.exit_events("LONG", T, T + 2, book_rows=rows, trades=[{"ts": T + 1.0, "px": 105.1, "amt": 1}])
    assert [e["basis"] for e in ev] == ["LAST_TRADE", "BOOK_TOP"]


def test_no_event_merge_above_64():
    rows = [_book(T + i, 100.0 + (i % 2), 101.0 + (i % 2)) for i in range(500)]
    ev = qt.exit_events("LONG", T, T + 600, book_rows=rows, include_trades=False)
    assert len(ev) == 500  # every row in order, no compression


def test_book_exit_price_is_book_side_and_stale_has_no_price():
    b = _book(T, 100.0, 101.0)
    x = qt.book_exit_price("LONG", T + 2.5, lambda t: b)
    assert x["px"] == 100.0 and x["price_age_s"] == 1.5 and not x["stale"]
    assert qt.book_exit_price("SHORT", T + 2.5, lambda t: b)["px"] == 101.0
    y = qt.book_exit_price("LONG", T + 3.5, lambda t: b)
    assert y["px"] is None and y["flag"] == qt.STALE_BOOK and y["last_book_px"] == 100.0


def test_mark_tail_reads_real_bfxci_rows(tmp_path):
    p = tmp_path / "d.jsonl"
    p.write_text("\n".join(json.dumps({"s": T + i, "mark": 100.0 + i, "fund_cur": 0.0}) for i in range(5)) + "\n")
    tail = qt.MarkTail(str(p))
    assert [r["mark"] for r in tail.between(T + 1, T + 3)] == [101.0, 102.0]


def test_live_tp_be_trigger_same_rule_as_paper():
    b = _book(T, 105.0, 105.5)
    live = qt.live_tp_be_trigger("LONG", 105.0, T + 1.2, b)
    paper = qt.first_level_trigger("LONG", qt.KIND_TARGET, 105.0, T, T + 2, book_rows=[b],
                                   trades=[{"ts": T + 0.5, "px": 106.0, "amt": 1}])
    assert live["trigger_basis"] == paper["trigger_basis"] == "BOOK_TOP" and live["trigger_ts"] == paper["trigger_ts"]
    assert qt.live_tp_be_trigger("LONG", 105.0, T + 3.5, b) is None  # stale book never triggers a target


def test_clock_exit_reasons():
    for r in ("PATH_END_15M", "TIME_STOP", "TIME_EXIT", "TIME_BACKSTOP"):
        assert qt.is_clock_exit_reason(r), r
    for r in ("TP1", "HARD_STOP", "BE_LOCK", "EXCHANGE_STOP_45BP", None):
        assert not qt.is_clock_exit_reason(r), r


def test_trades_tail_reads_collector_rows(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    rows = [{"s": T, "tr": [[1, (T + 0.2) * 1000, 0.1, 100.0], [2, (T + 0.7) * 1000, -0.2, 99.5]]},
            {"s": T + 1, "tr": [[3, (T + 1.1) * 1000, 0.1, 100.5]]}]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    tail = qt.TradesTail(str(p))
    got = tail.between(T + 0.5, T + 2)
    assert [g["px"] for g in got] == [99.5, 100.5]
    assert [(g["id"], g["recv_s"]) for g in got] == [(2, T), (3, T + 1)]


def test_helper_never_prices_a_fill():
    tree = ast.parse(open(os.path.join(HERE, "quote_trigger.py"), encoding="utf-8").read())
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not calls & {"paper_fill", "entry_fill", "exit_fill", "price_entry", "price_exit"}


def test_f3_entry_trigger_ref_fields():
    dec = {"direction": "LONG", "bid": 100.0, "ask": 100.5, "bbo_age_sec": 0.4, "signal_ts": T}
    q = tm.entry_quote_fields(dec, bbo_ts=T - 0.4, ws_last_tick=T - 0.4)
    assert (q["entry_trigger_ref_price"], q["entry_trigger_ref_source"], q["entry_trigger_ref_age_s"]) == \
        (100.5, "TICKER_WS", 0.4)
    short = tm.entry_quote_fields({**dec, "direction": "SHORT"}, bbo_ts=T - 0.4, rest_last_tick=T - 0.4)
    assert short["entry_trigger_ref_price"] == 100.0 and short["entry_trigger_ref_source"] == "TICKER_REST"
    book = tm.entry_trigger_ref("LONG", book=_book(T - 1, 99.0, 99.5), ref_ts=T)
    assert book == {"entry_trigger_ref_price": 99.5, "entry_trigger_ref_source": "BOOK", "entry_trigger_ref_age_s": 1.0}
    for k in ("entry_trigger_ref_price", "entry_trigger_ref_source", "entry_trigger_ref_age_s"):
        assert k in tm.MEASUREMENT_KEYS
    legacy = tm.entry_fields("LONG", dec, None)
    assert legacy["entry_trigger_ref_price"] == 100.5


def test_bot_wiring():
    src = open(os.path.join(HERE, "bot.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fns = {n.name: ast.get_source_segment(src, n) for n in tree.body if isinstance(n, ast.FunctionDef)}
    # RK step 2: _apply_position_exits dispatches combo lanes to _apply_combo_lane_exit (same family exit path).
    assert "_apply_combo_lane_exit(" in fns["_apply_position_exits"]
    assert "_realism_family_exit(" in fns["_apply_combo_lane_exit"]
    assert "_paper_lane_entry_core(" in fns["_paper_lane_entry_result"]
    assert "_paper_entry_quote_gate(" in fns["_paper_lane_entry_core"]
    assert "_stamp_trigger_basis(" in fns["_apply_family_tile_exit"]
    assert "trigger_price_source" in fns["_realism_family_exit"]


def test_trades_tail_between_has_no_receive_look_ahead(tmp_path):
    """HM #851 N6: a print stamped <= t1 but written in collector second floor(t1) is not known until floor(t1)+1."""
    p = tmp_path / "bfx_trades.jsonl"
    rows = [{"s": T, "tr": [[1, (T + 0.2) * 1000, 0.1, 100.0]]},
            {"s": T + 1, "tr": [[2, (T + 1.1) * 1000, 0.1, 100.5]]}]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    tail = qt.TradesTail(str(p))
    assert [g["id"] for g in tail.between(T, T + 1.5)] == [1]  # print 2 (ts T+1.1) is only known at T+2
    assert [g["id"] for g in tail.between(T, T + 2.0)] == [1, 2]
