"""RK 17:24 BINDING / HM 17:30 single rule: shared-helper tests (no ticker, first row at/after, delay pool, liq)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import order_model as om  # noqa: E402
import paper_fill as pf  # noqa: E402

T = 1_800_000_000


def _book(s, bid, ask):
    return {"s": s, "b": [[bid, 5.0]], "a": [[ask, 5.0]]}


def _book_at(rows):
    by = {int(r["s"]): r for r in rows}

    def at(t):
        c = [s for s in by if s + 1 <= t + 1e-9]
        return by[max(c)] if c else None
    return at


def test_first_row_contains_or_follows_trigger_never_pre_trigger():
    rows = [_book(T - 1, 99.0, 99.5), _book(T, 100.0, 100.5), _book(T + 1, 101.0, 101.5)]
    at = _book_at(rows)
    assert pf.first_row_at_or_after(at, T + 0.4)["s"] == T      # print at s + 0.4 -> row s (not s - 1)
    assert pf.first_row_at_or_after(at, T)["s"] == T            # exact boundary belongs to its own second
    assert pf.first_row_at_or_after(at, T + 0.999)["s"] == T
    late = _book_at([_book(T - 1, 99.0, 99.5), _book(T + 2, 102.0, 102.5)])  # row T missing -> next available
    assert pf.first_row_at_or_after(late, T + 0.4)["s"] == T + 2
    assert pf.first_row_at_or_after(_book_at([_book(T - 1, 99.0, 99.5)]), T + 0.4, horizon_s=3) is None


def test_delay_pool_default_max_then_q90():
    d = pf.DelaySampler()
    assert d.delay("L", "exit") == (pf.DEFAULT_DELAY_S, "default_until_n10")
    for v in [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 2.5]:
        d.add("L", "exit", v)
    px, src = d.delay("L", "exit")
    assert (px, src) == (2.5, "lane_max_n10")
    for v in [0.5] * 10:
        d.add("L", "exit", v)
    px, src = d.delay("L", "exit")
    assert src == "lane_q90_n20" and px == 1.1   # nearest-rank q0.90 of 20 = the 18th smallest
    st = pf.delay_stamp(src)
    # RK 24 Oct: the sampler's lane_* label is not in the r17 label set (POOLED_LIVE_ACK_CROSS_LANE only) ->
    # UNVERIFIED; N and the quantile method stay as diagnostics.
    assert st["quantile_method"] == "Q90" and st["pool_n"] == 20 and st["delay_unverified"]
    st = pf.delay_stamp("lane_max_n12")
    assert st["quantile_method"] == "MAX" and st["pool_n"] == 12
    st = pf.delay_stamp("default_until_n10")
    assert st["quantile_method"] == "DEFAULT" and st["delay_unverified"] and st["delay_flag"] == pf.UNVERIFIED_DELAY
    assert pf.MIN_POOL_N == 10 and pf.QUANTILE_SWITCH_N == 20


def test_bfx_truncate_matches_ts_normalize():
    assert om.bfx_truncate_price(63066.88) == 63066.0
    assert om.bfx_truncate_price(100059.9) == 100050.0
    assert om.bfx_truncate_price(99999.99) == 99999.0
    assert om.bfx_truncate_price(0) is None


def test_exchange_stop_level_truncated_and_anchor_drift():
    spec = {"hard_stop_bp": 10}
    lvl = om.exchange_stop_level("LONG", 100003.0, spec)
    if lvl:  # plan available for this spec shape
        assert lvl["exchange_stop_px"] == om.bfx_truncate_price(lvl["exchange_stop_px_untruncated"])
        assert lvl["exchange_stop_rounding"] == "TRUNC_5SF"
    d = om.anchor_drift("LONG", 100000.0, 100010.0)
    assert d["stop_anchor_drift_bp"] == 1.0 and d["stop_anchor_drift_status"] == "MEASURED"
    assert om.anchor_drift("SHORT", 100000.0, 100010.0)["stop_anchor_drift_bp"] == -1.0
    assert om.anchor_drift("LONG", 100000.0, None)["stop_anchor_drift_status"] == "NO_LIVE_TWIN"


def test_tile_options_trades_only_and_stop_basis_verified_default_false():
    o = om.tile_options({"realism_options": {"resident_stop_trigger_basis": "book_trades",
                                             "entry_rest_fill_basis": "trade_through"}})
    assert o["resident_stop_trigger_basis"] == "trades" and o["entry_rest_fill_basis"] == "trade_through"
    assert o["stop_basis_verified"] is False and o["stop_basis_unverified"] is True
    assert o["time_exit_basis"] == "CLOCK" and o["funding_unverified"] is True
    assert om.tile_options(None)["entry_rest_fill_basis"] == "trade_through"  # HM C1: conservative default
    assert om.tile_options({"realism_options": {"entry_rest_fill_basis": "queue_clear"}})["entry_rest_fill_basis"] \
        == "queue_clear"


def test_liquidation_first_of_mark_or_trade():
    # 100x, mm 0.5% -> 50 bp; LONG mark at open 100000 -> liq 99500
    marks = [{"s": T + 5, "mark": 99600.0}, {"s": T + 8, "mark": 99490.0}]
    trades = [{"ts": T + 7.2, "px": 99500.0}]
    out = om.liquidation_check("LONG", 100000.0, 100, marks=marks, trades=trades, stop_trigger_ts=T + 9,
                               stop_fill_px=99520.0)
    assert out["liq_px"] == 99500.0 and out["liq_bp"] == 50.0
    assert out["liq_trigger_source"] == "trade" and out["liq_trigger_ts"] == T + 7.2
    assert out["liq_before_stop"] and out["liq_unverified"] and out["liq_model_validated"] is False
    out = om.liquidation_check("LONG", 100000.0, 100, marks=marks, trades=[], stop_trigger_ts=T + 9,
                               stop_fill_px=99520.0)
    assert out["liq_trigger_source"] == "mark" and out["liq_trigger_ts"] == T + 9 and not out["liq_before_stop"]
    assert out["liq_unverified"] is False
    out = om.liquidation_check("SHORT", 100000.0, 100, marks=[], trades=[], stop_trigger_ts=T, stop_fill_px=100600.0)
    assert out["liq_stop_slipped_past"] and out["liq_unverified"]
    assert om.liquidation_check("LONG", None, 100, marks=None, trades=[], stop_trigger_ts=T,
                                stop_fill_px=None)["liq_unverified"]


def test_r8_force_flattened_rows_are_non_countable():
    import pathlib
    src = pathlib.Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    # HM C2: the row's non_countable is any of the reasons (FORCE_FLATTENED from STATS_EXCLUDED_EXIT_REASONS kept)
    assert '"non_countable": bool(_non_countable_reasons(pos, exit_reason))' in src
    assert 'if str(exit_reason or "").strip().upper() in STATS_EXCLUDED_EXIT_REASONS:\n        out.append("FORCE_FLATTENED")' in src
