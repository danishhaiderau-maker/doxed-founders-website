"""LIVE-FIX-1510: DISLOC book-top pricing, cap modes and entry-fidelity enrichment."""
import importlib
import time


def test_book_levels_top():
    import bfx_book_top as b
    bids, asks = {}, {}
    b.apply_levels([[100.0, 1, 2.0], [99.0, 2, 1.0], [101.0, 1, -1.0], [102.0, 1, -3.0]], bids, asks)
    assert max(bids) == 100.0 and min(asks) == 101.0
    b.apply_levels([[101.0, 0, -1]], bids, asks)
    assert min(asks) == 102.0
    b.apply_levels([[100.0, 0, 1]], bids, asks)
    assert max(bids) == 99.0


def _order_row(now):
    return {"event": "DISLOC_ORDER", "trade_id": "dsl-1-1", "research_lane": "FAMILY_DISLOC_BFX_DISLOCATION_1M",
            "side": "LONG", "signal_ts": now - 0.3, "signal_ask": 100000.0, "signal_bid": 99990.0,
            "order_type": "MARKET_TAKER_AT_REAL_TOUCH_AFTER_DELAY", "signal_touch_source": "BOOK",
            "signal_ask_age_ms": 40}


def test_cap_off_default_and_cap_bp(monkeypatch):
    import disloc_live_copy as d
    monkeypatch.delenv("DISLOC_LIVE_TAKER_CUSHION_BP", raising=False)
    d = importlib.reload(d)
    now = time.time()
    [(_, _, x)] = d.disloc_live_copy_events(_order_row(now), now=now)
    assert x["entry_cap_mode"] == "OFF" and x["entry_cap_bp"] is None
    assert x["limit_price"] <= 100000.0 * 1.001  # inside the relay sanity band
    assert x["signal_touch_source"] == "BOOK" and x["signal_ask_age_ms"] == 40
    monkeypatch.setenv("DISLOC_LIVE_TAKER_CUSHION_BP", "5")
    d = importlib.reload(d)
    [(_, _, x)] = d.disloc_live_copy_events(_order_row(now), now=now)
    assert x["entry_cap_mode"] == "CAP_BP" and x["limit_price"] == 100050.0
    monkeypatch.delenv("DISLOC_LIVE_TAKER_CUSHION_BP", raising=False)
    importlib.reload(d)


def test_entry_fidelity_enrichment():
    import live_copy_monitor as lcm
    chains = [
        {"correlation_id": "dsl-1", "order_sent_at_ts": 10.0, "exchange_ack_at_ts": 10.2, "fill_at_ts": 10.25,
         "fill_price": 100030.0, "order_type": "MARKET"},
        {"correlation_id": "dsl-2", "order_sent_at_ts": 20.0, "exchange_ack_at_ts": 20.2, "closed": True,
         "order_type": "IOC"},
    ]
    twins = {"dsl-1": {"side": "LONG", "signal_price": 100000.0, "entry_price": 100010.0,
                       "fill_model": "TAKER_AT_BOOK_TOUCH", "signal_ask_age_ms": 40},
             "dsl-2": {"side": "LONG", "entry_price": 100000.0, "exit_price": 100050.0}}
    out = lcm.enrich_entry_fidelity(chains, twins)
    assert out["attempted"] == 2 and out["filled"] == 1 and out["fill_rate"] == 0.5
    assert out["unfilled_paper_shadow"][0]["paper_bp"] == 5.0
    c = chains[0]
    assert c["fill_model"] == "TAKER_AT_BOOK_TOUCH" and c["signal_ask_age_ms"] == 40
    assert c["rested_s"] == 0.05 and c["slippage_vs_signal_bp"] == 3.0 and c["slippage_vs_paper_bp"] == 2.0
