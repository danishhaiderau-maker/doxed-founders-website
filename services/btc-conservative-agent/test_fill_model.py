"""REALISTIC_V1 shared fill model: rules, row-API vs genome-grid parity, exit booking, declarations."""
import math

import numpy as np
import pytest

from research import fill_model as fm
from research import genome_grid_study as g


def _row(ts, bid, ask, *, bid_qty=2.0, ask_qty=2.0, tlow=None, thigh=None, sell_qty=0.0, buy_qty=0.0,
         sell_vwap=None, buy_vwap=None, fresh=True):
    return {"schema": "market_microstructure_1s_v1", "bucket_ts": ts, "bid": bid, "ask": ask, "bid_qty": bid_qty,
            "ask_qty": ask_qty, "trade_low": tlow, "trade_high": thigh, "sell_qty": sell_qty, "buy_qty": buy_qty,
            "sell_vwap": sell_vwap, "buy_vwap": buy_vwap, "fresh": fresh, "valid_bbo": True, "source_age_sec": 0.2,
            "last": (bid + ask) / 2}


def test_taker_fills_at_opposite_bbo_after_latency_with_size_walk():
    rows = {t: _row(t, 100.0 + t, 101.0 + t, ask_qty=0.5) for t in range(1000, 1020)}
    out = fm.taker_fill_rows(rows, side="LONG", qty=0.2, decision_ts=1000.3, latency_sec=4.0)
    assert out["fill_ts"] == 1005 and out["fill_price"] == 1106.0 and out["size_walk"]["binding"] is False
    big = fm.taker_fill_rows(rows, side="LONG", qty=1.25, decision_ts=1000.3, latency_sec=4.0)
    # 0.5 @ ask, 0.5 @ ask+spread, 0.25 @ ask+2*spread (no-L2 proxy)
    assert big["size_walk"]["binding"] and big["size_walk"]["levels"] == 3
    assert big["fill_price"] == pytest.approx((0.5 * 1106 + 0.5 * 1107 + 0.25 * 1108) / 1.25)
    sell = fm.taker_fill_rows(rows, side="SHORT", qty=0.2, decision_ts=1000.3, latency_sec=4.0)
    assert sell["fill_price"] == 1105.0  # bid


def test_touch_is_not_a_fill_but_trade_through_and_queue_consumption_are():
    sched = [{"start_ts": 0, "end_ts": 10, "limit_price": 99.0}]
    touch = {t: _row(t, 99.5, 100.0) for t in range(10)}
    touch[3] = _row(3, 99.0, 99.5, bid_qty=1.0, tlow=99.0, sell_qty=0.4, sell_vwap=99.0)
    assert fm.maker_fill_rows(touch, side="LONG", qty=0.1, schedule=sched)["status"] == "NO_FILL"
    assert fm.optimistic_touch_rows(touch, side="LONG", schedule=sched)["status"] == "FILLED"
    queue = dict(touch)
    queue[5] = _row(5, 99.0, 99.5, bid_qty=1.0, tlow=99.0, sell_qty=0.75, sell_vwap=99.0)
    out = fm.maker_fill_rows(queue, side="LONG", qty=0.1, schedule=sched)
    assert out["basis"] == "QUEUE_CONSUMED_AT_LIMIT" and out["queue_estimate"] == 1.0 and out["fill_ts"] == 5
    through = dict(touch)
    through[7] = _row(7, 98.5, 99.0, tlow=98.9, sell_qty=0.01, sell_vwap=98.9)
    out = fm.maker_fill_rows(through, side="LONG", qty=0.1, schedule=sched)
    assert out["basis"] == "TRADE_THROUGH" and out["fill_price"] == 99.0 and out["liquidity"] == "MAKER"
    cross = {t: _row(t, 98.0, 98.9 if t == 4 else 100.0) for t in range(10)}  # BBO cross without a print
    res = fm.maker_fill_rows(cross, side="LONG", qty=0.1, schedule=sched)
    assert res["status"] == "NO_FILL" and res["diagnostics"]["bbo_cross_without_print_sec"] == 1


def test_partial_fill_and_marketable_at_placement():
    sched = [{"start_ts": 0, "end_ts": 6, "limit_price": 99.0}]
    rows = {t: _row(t, 99.0, 99.5, bid_qty=0.3) for t in range(6)}
    rows[2] = _row(2, 99.0, 99.5, bid_qty=0.3, tlow=99.0, sell_qty=0.35, sell_vwap=99.0)
    out = fm.maker_fill_rows(rows, side="LONG", qty=0.1, schedule=sched)
    assert out["status"] == "PARTIAL" and out["filled_fraction"] == pytest.approx(0.5)
    mkt = {t: _row(t, 98.0, 98.8) for t in range(6)}
    out = fm.maker_fill_rows(mkt, side="LONG", qty=0.1, schedule=sched)
    assert out["basis"] == "MARKETABLE_AT_PLACEMENT" and out["liquidity"] == "TAKER" and out["fill_price"] == 98.8


def _arrays(rows, n):
    keys = {"bid": "bid", "ask": "ask", "bid_qty": "bid_qty", "ask_qty": "ask_qty", "tlow": "trade_low",
            "thigh": "trade_high", "buy_qty": "buy_qty", "sell_qty": "sell_qty", "buy_vwap": "buy_vwap",
            "sell_vwap": "sell_vwap", "last": "last"}
    w = {k: np.array([np.nan if rows[t].get(src) is None else rows[t][src] for t in range(n)], float)
         for k, src in keys.items()}
    w["low"], w["high"], w["fresh"] = w["last"], w["last"], np.ones(n)
    return w


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("mode", ["mixed", "queue"])
def test_genome_grid_maker_matches_row_api(seed, side, mode):
    """Vectorised grid fill == row receipt fill (same model, two consumers)."""
    rng = np.random.default_rng(seed)
    n = 900
    mid = 85_000 + np.cumsum(rng.normal(0, 2 if mode == "queue" else 6, n)).round()
    rows = {}
    qty_choices = [0.0001, 0.0003, 0.0005] if mode == "queue" else [0.0003, 0.01, 2.0]
    for t in range(n):
        bid, ask = float(mid[t]), float(mid[t] + 1)
        r = _row(t, bid, ask, bid_qty=float(rng.choice(qty_choices)), ask_qty=float(rng.choice(qty_choices)))
        if rng.random() < (0.5 if mode == "queue" else 0.3):
            if mode == "queue":
                px = bid if side == "LONG" else ask
            else:
                px = float(bid + rng.integers(-3, 2)) if side == "LONG" else float(ask + rng.integers(-1, 4))
            q = float(rng.choice([0.0001, 0.0002, 0.001]))
            if side == "LONG":
                r.update(trade_low=px, sell_qty=q, sell_vwap=px if rng.random() < 0.7 else px + 0.5)
            else:
                r.update(trade_high=px, buy_qty=q, buy_vwap=px if rng.random() < 0.7 else px - 0.5)
        rows[t] = r
    entry = {"offset_pct": 0.01, "chase_id": "all_on_s50_i60", "ttl_sec": 600}
    w = _arrays(rows, n)
    grid = g.realistic_maker_fill(entry, side, float(mid[0]), w, start=5)
    limit = fm.round_limit_passive(g.limit_schedule(entry, side, float(mid[0]), w["bid"][5:605], w["ask"][5:605]), side)
    change = [0] + [i for i in range(1, 600) if limit[i] != limit[i - 1]] + [600]
    sched = [{"start_ts": 5 + a, "end_ts": 5 + b, "limit_price": float(limit[a])} for a, b in zip(change, change[1:])]
    ref = fm.maker_fill_rows(rows, side=side, qty=g._qty(float(limit[0])), schedule=sched)
    if ref["status"] == "NO_FILL":
        assert grid is None
    else:
        assert grid is not None, ref
        assert grid[0] == ref["fill_ts"] and grid[1] == pytest.approx(ref["fill_price"])
        assert grid[2] == pytest.approx(ref["filled_fraction"]) and grid[3] == (ref["liquidity"] == "MAKER")


def test_realistic_exit_booking_targets_at_level_and_latency_on_marketable_exits():
    age = np.arange(10, dtype=float)
    cur = np.array([0, 1, 2, 3, -40, -45, -50, -50, -50, -50], float)
    margin, j = fm.realistic_exit_margin(cur, age, 4, "PHYSICAL_HARD_STOP", latency_sec=1.0)
    assert (margin, j) == (-45.0, 5)
    assert fm.realistic_exit_margin(cur, age, 3, "ATR_TAKE_PROFIT", latency_sec=1.0, target_margin=2.5) == (2.5, 3)
    path = {"age": age, "cur": cur, "mfe": np.maximum.accumulate(cur), "mae": np.minimum.accumulate(cur),
            "thr_cur": np.full(10, np.nan)}
    spec = {"loss_protection": {"hard_stop_margin_pct": 40.0}, "profit_protection": {"mode": "ATR_TARGET", "atr_tp_k": 0.25}}
    # optimistic/canonical: target at bid >= 2.0 (atr 0.08 -> 8 margin * 0.25 = 2.0) booked at the mark (2.0 at t2)
    assert g.fast_replay(path, spec, 0.08)["exit_reason"] == "ATR_TAKE_PROFIT"
    # realistic: no trade printed through the target -> no target fill; the hard stop fills 1 s late
    real = g.fast_replay(path, spec, 0.08, realistic=True, exit_latency_sec=1.0)
    assert real["exit_reason"] == "PHYSICAL_HARD_STOP" and real["portfolio_margin_return_pct"] == -45.0
    path["thr_cur"][3] = 2.2
    real = g.fast_replay(path, spec, 0.08, realistic=True, exit_latency_sec=1.0)
    assert real["exit_reason"] == "ATR_TAKE_PROFIT" and real["portfolio_margin_return_pct"] == pytest.approx(2.0)


def test_declaration_fees_and_fingerprint_are_explicit():
    dec = fm.fill_model_declaration()
    assert dec["fill_model"] == "REALISTIC_V1" and dec["headline_role"] == "HEADLINE"
    assert dec["shadow_role"] == "COMPARISON_SHADOW_NOT_HEADLINE" and dec["fill_model_fingerprint"].startswith("sha256:")
    fees = fm.fee_fields(25.0, maker=True)
    assert set(fees) >= {"maker_fee_rate", "taker_fee_rate", "fee_usd", "fee_profile_id", "liquidity"}
    rec = fm.fill_record(realistic={"status": "FILLED", "fill_price": 100.0, "filled_qty": 0.25, "liquidity": "TAKER"},
                         shadow={"status": "FILLED", "fill_price": 99.9}, side="LONG", qty=0.25, latency_sec=6.5,
                         tape_id="tape:x", fill_id="fill:y")
    assert rec["fill_model"] == "REALISTIC_V1" and rec["optimistic_shadow"]["role"] == fm.SHADOW_ROLE
    assert rec["tape_id"] == "tape:x" and rec["fill_id"] == "fill:y" and rec["latency_sec"] == 6.5


def test_measure_latency_falls_back_without_ledgers(tmp_path):
    out = fm.measure_decision_latency(tmp_path)
    assert out["source"] == "DEFAULT_NO_MEASUREMENT" and out["latency_sec"] == fm.DEFAULT_DECISION_LATENCY_SEC

@pytest.mark.parametrize("prints", [(3, 6, 9), (3, 6)])
def test_queue_consumed_and_partial_parity(prints):
    n = 1000
    rows = {t: _row(t, 84991.0, 84992.0, bid_qty=0.0004) for t in range(n)}
    for t in prints:
        rows[5 + t] = _row(5 + t, 84991.0, 84992.0, bid_qty=0.0004, tlow=84991.0, sell_qty=0.0003, sell_vwap=84991.0)
    entry = {"offset_pct": 0.01, "chase_id": "no_chase", "ttl_sec": 900}
    w = _arrays(rows, n)
    grid = g.realistic_maker_fill(entry, "LONG", 85000.0, w, start=5)
    qty = g._qty(84991.0)
    ref = fm.maker_fill_rows(rows, side="LONG", qty=qty, schedule=[{"start_ts": 5, "end_ts": 905, "limit_price": 84991.0}])
    if len(prints) == 3:
        assert ref["basis"] == "QUEUE_CONSUMED_AT_LIMIT" and ref["queue_estimate"] == 0.0004 and ref["fill_ts"] == 14
        assert grid == (14, 84991.0, 1.0, 1)
    else:
        assert ref["status"] == "PARTIAL" and ref["filled_fraction"] == pytest.approx(0.0002 / qty)
        assert grid[0] == ref["fill_ts"] == 11 and grid[2] == pytest.approx(ref["filled_fraction"]) and grid[3] == 1


def test_print_evidence_is_the_aggressor_side_vwap_not_the_trade_extreme():
    # Trade low below the buy limit but the sell VWAP above it: no proof our bid was reached.
    ev = fm.maker_print_evidence(_row(1, 99.0, 99.5, tlow=98.5, sell_qty=0.3, sell_vwap=99.2), "LONG", 99.0)
    assert not ev["through"] and ev["at_limit_qty"] == 0.0 and ev["extreme_through_unproven"]
    # No trade low at all (pre 2026-09-30 tape) but sell VWAP below the limit: a proven trade-through.
    assert fm.maker_print_evidence(_row(1, 99.0, 99.5, sell_qty=0.3, sell_vwap=98.9), "LONG", 99.0)["through"]
    assert fm.maker_print_evidence(_row(1, 99.0, 99.5, sell_qty=0.3, sell_vwap=99.0), "LONG", 99.0)["at_limit_qty"] == 0.3
    assert fm.maker_print_evidence(_row(1, 100.5, 101.0, buy_qty=0.2, buy_vwap=101.2), "SHORT", 101.0)["through"]
