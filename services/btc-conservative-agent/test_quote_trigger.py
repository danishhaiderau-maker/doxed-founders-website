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


def test_source_flag_default_ticker_and_per_tile():
    assert qt.trigger_price_source("GS01_X", env={}) == "ticker"
    assert qt.trigger_price_source("GS01_X", env={"TRIGGER_PRICE_SOURCE": "book"}) == "book"
    assert qt.trigger_price_source("GS01_X", env={"TRIGGER_PRICE_SOURCE": "nonsense"}) == "ticker"
    env = {"TRIGGER_PRICE_SOURCE": "GS01_X=book, B1_Y=ticker, *=ticker"}
    assert qt.trigger_price_source("gs01_x", env=env) == "book"
    assert qt.trigger_price_source("B1_Y", env=env) == "ticker"
    assert qt.trigger_price_source("OTHER", env=env) == "ticker"
    assert qt.trigger_price_source("OTHER", env={"TRIGGER_PRICE_SOURCE": "*=book"}) == "book"


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


def test_first_of_trade_or_book_top_for_stops():
    rows = [_book(T, 100.5, 101.0), _book(T + 1, 99.9, 100.4)]  # bid reaches 100 when row T+1 completes (T+2)
    trades = [{"ts": T + 1.4, "px": 99.95, "amt": -0.1}]          # a print through 100 earlier
    hit = qt.first_level_trigger("LONG", qt.KIND_STOP, 100.0, T, T + 3, book_rows=rows, trades=trades)
    assert hit["trigger_basis"] == "LAST_TRADE" and hit["trigger_ts"] == T + 1.4
    hit2 = qt.first_level_trigger("LONG", qt.KIND_STOP, 100.0, T, T + 3, book_rows=rows, trades=[])
    assert hit2["trigger_basis"] == "BOOK_TOP" and hit2["trigger_ts"] == T + 2


def test_targets_trigger_on_executable_book_side_only():
    rows = [_book(T, 104.0, 104.5), _book(T + 1, 105.0, 105.5)]
    trades = [{"ts": T + 0.5, "px": 106.0, "amt": 0.1}]
    hit = qt.first_level_trigger("LONG", qt.KIND_TARGET, 105.0, T - 1, T + 3, book_rows=rows, trades=trades)
    assert hit["trigger_basis"] == "BOOK_TOP" and hit["trigger_ts"] == T + 2


def test_exit_events_chronological_dedup_and_compress_keeps_extremes():
    rows = [_book(T + i, 100.0 + (i % 3), 101.0 + (i % 3)) for i in range(200)]
    rows[150] = _book(T + 150, 90.0, 91.0)
    ev = qt.exit_events("LONG", T - 1, T + 300, book_rows=rows, include_trades=False, max_events=16)
    assert len(ev) <= 32 and [e["ts"] for e in ev] == sorted(e["ts"] for e in ev)
    assert any(e["px"] == 90.0 for e in ev)  # the adverse extreme survives compression
    same = qt.exit_events("LONG", T - 1, T + 5, book_rows=[_book(T, 100, 101), _book(T + 1, 100, 101)])
    assert len(same) == 1


def test_stale_threshold_2s_both_sources():
    assert qt.STALE_S == 2.0
    q = qt.selected_quote("LONG", "entry", T + 2.0, "ticker", ticker_px=101.0, ticker_ts=T)
    assert not q["quote_stale"] and q["quote_age_s"] == 2.0
    q = qt.selected_quote("LONG", "entry", T + 2.01, "ticker", ticker_px=101.0, ticker_ts=T)
    assert q["quote_stale"] and q["quote_reason"] == qt.QUOTE_STALE
    b = _book(T, 100.0, 101.0)
    assert qt.entry_quote_gate("LONG", T + 1.5, "book", book=b) is None
    assert qt.entry_quote_gate("LONG", T + 2.5, "book", book=b) == qt.QUOTE_STALE
    assert qt.entry_quote_gate("LONG", T + 0.5, "book", book=None) == qt.QUOTE_STALE


def test_dual_stamp_has_ticker_book_and_trade_views():
    d = qt.dual_stamp("LONG", "exit", T + 1.5, ticker_px=99.0, ticker_ts=T + 1.0, book=_book(T, 100.0, 101.0),
                      last_trade={"ts": T + 1.2, "px": 100.2})
    assert set(d) == set(qt.DUAL_STAMP_KEYS)
    assert d["dual_ticker_px"] == 99.0 and d["dual_ticker_age_s"] == 0.5
    assert d["dual_book_px"] == 100.0 and d["dual_book_age_s"] == 1.5
    assert d["dual_last_trade_px"] == 100.2 and d["dual_last_trade_age_s"] == 0.3


def test_trades_tail_reads_collector_rows(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    rows = [{"s": T, "tr": [[1, (T + 0.2) * 1000, 0.1, 100.0], [2, (T + 0.7) * 1000, -0.2, 99.5]]},
            {"s": T + 1, "tr": [[3, (T + 1.1) * 1000, 0.1, 100.5]]}]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    tail = qt.TradesTail(str(p))
    got = tail.between(T + 0.5, T + 2)
    assert [g["px"] for g in got] == [99.5, 100.5]


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
    assert "_realism_family_exit(" in fns["_apply_position_exits"]
    assert "_paper_entry_quote_gate(" in fns["_paper_lane_entry_result"]
    assert "_stamp_trigger_basis(" in fns["_apply_family_tile_exit"]
    assert "trigger_price_source" in fns["_realism_family_exit"]
