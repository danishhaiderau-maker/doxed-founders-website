"""HM R5 C1 + RK 09:10 (Fly side), nits 6 and 7.

* RK 09:10 (3): an UNREADABLE close (price None, price_source 'none') blocks entries (LEDGER_PNL_UNREADABLE, AMBER) and is
  non-countable; the relay's later clearing re-stamp (EXIT_FILL_RECONCILED -> a second POSITION_CLOSED with the real fill,
  price_source stop_fill, VWAP over the partial fills) wins, and D3 recomputes from it.
* nit 6: the entry side of D3 / countability is the entry order's OWN trades only (fill.source exchange_trades); a limit /
  mark / position-base reference fallback is ENTRY_FILL_UNREADABLE (no P&L, non-countable, entry blocked).
* nit 7: stop_fill_overshoots uses the real exit fill (exit_fill_price), never the display exit_price.
"""
from pathlib import Path

import pytest

import live_copy_monitor as lcm
import live_exit_fill as lef
import live_tile_gate as ltg

NOW = 1_800_000_000.0
MIR = {"ok": True, "positions": [], "open_orders": [], "orphans": [], "balance": {"derivatives_available_usd": 206.79}}
BOOK = {"s": NOW - 0.5, "b": [[100_000.0, 1.0]], "a": [[100_010.0, 1.0]]}
ENTRY = 100_000.0


def _reports(*closes, fill_source="exchange_trades", reason="EXCHANGE_STOP"):
    out = [
        {"correlation_id": "gs1-a", "account": "A", "type": "ORDER_PLACED", "order": {"side": "LONG", "qty": 0.002, "price": ENTRY + 5}},
        {"correlation_id": "gs1-a", "account": "A", "type": "ORDER_FILLED",
         "fill": {"price": ENTRY, "qty": 0.002, "source": fill_source}},
        {"correlation_id": "gs1-a", "account": "A", "type": "STOP_CONFIRMED", "stop": {"price": 99_700.0}},
    ]
    for cl in closes:
        out.append({"correlation_id": "gs1-a", "account": "A", "type": "POSITION_CLOSED",
                    # HM R6 B1: the REAL reporter shape: close.fill_at_ts (Bitfinex fill time); no top-level closed_at_ts
                    "close": {"reason": reason, "fill_at_ts": (NOW - 60) if cl.get("price") else None, **cl}})
    return out


def _chain(*closes, **k):
    return lcm.build_chains([], _reports(*closes, **k))["accounts"][("gs1-a", "A")]


UNREADABLE = {"price": None, "price_source": "none", "reported_exit_price": None, "reconciled": False}
# RK 09:10 (2): VWAP of two partial stop fills, 0.0012 @ 99 690 + 0.0008 @ 99 640 = 99 670
VWAP = (0.0012 * 99_690.0 + 0.0008 * 99_640.0) / 0.002
CLEARED = {"price": VWAP, "price_source": "stop_fill", "exit_fill_vwap": VWAP, "reconciled": True}


def test_unreadable_close_blocks_and_is_non_countable_with_null_exit_price():
    c = _chain(UNREADABLE)
    assert c["exit_fill_price"] is None and lef.exit_fill_price(c) is None
    assert ltg.trade_realised_pnl_usd(c) is None
    assert lef.exit_fill_non_countable_reason(c) == "EXIT_FILL_UNREADABLE:none"
    blocks, _ = ltg.daily_cap_entry_block([c], MIR, BOOK, NOW, "gs1")
    assert ltg.VIOL_PNL_UNREADABLE in {b["code"] for b in blocks}


def test_d3_recomputes_after_the_clearing_read_with_the_vwap_of_partial_fills():
    before = _chain(UNREADABLE)
    after = _chain(UNREADABLE, CLEARED)
    assert ltg.trade_realised_pnl_usd(before) is None
    assert VWAP == pytest.approx(99_670.0)
    assert lef.exit_fill_price(after) == pytest.approx(VWAP)
    assert ltg.trade_realised_pnl_usd(after) == pytest.approx((VWAP - ENTRY) * 0.002)
    assert lef.exit_fill_non_countable_reason(after) is None
    per, total, n = ltg.daily_realised_loss([after], NOW)
    assert n == 1 and total == pytest.approx((ENTRY - VWAP) * 0.002)
    assert ltg.VIOL_PNL_UNREADABLE not in {v["code"] for v in ltg.daily_loss_violations([after], NOW)}


@pytest.mark.parametrize("src", ["limit_reference_fallback", "order_reference_fallback", "mark_reference_fallback",
                                 "position_base_fallback", None])
def test_nit6_entry_is_real_fill_only(src):
    c = _chain(CLEARED, fill_source=src)
    assert lef.entry_fill_price(c) is None
    assert ltg.trade_realised_pnl_usd(c) is None                        # no limit-price fallback in D3
    assert lef.exit_fill_non_countable_reason(c) == f"ENTRY_FILL_UNREADABLE:{src or 'none'}"
    assert {v["code"] for v in ltg.daily_loss_violations([c], NOW)} == {ltg.VIOL_PNL_UNREADABLE}
    s = ltg.non_countable_summary([c])
    assert s["by_cause"].get(f"ENTRY_FILL_UNREADABLE:{src or 'none'}") == 1
    real = _chain(CLEARED)
    assert lef.entry_fill_price(real) == ENTRY and lef.exit_fill_non_countable_reason(real) is None


def test_nit6_d3_body_reads_the_entry_helper_only():
    src = Path(ltg.__file__).read_text()
    body = src.split("def trade_realised_pnl_usd", 1)[1].split("\ndef ", 1)[0]
    assert "_lef.entry_fill_price(c)" in body and 'c.get("fill_price")' not in body


def test_nit7_overshoot_uses_the_real_exit_fill():
    c = _chain({"price": 99_650.0, "price_source": "stop_fill"})
    c["exit_price"] = 99_000.0                                           # a display price must never be read
    (o,) = ltg.stop_fill_overshoots([c])
    assert o["fill_px"] == 99_650.0 and o["overshoot_bp"] == pytest.approx(round((99_700 - 99_650) / 99_700 * 1e4, 3))
    (u,) = ltg.stop_fill_overshoots([_chain(dict(UNREADABLE))])
    assert u["fill_px"] is None and u["overshoot_bp"] is None             # unreadable: no sample, never the mark
    src = Path(ltg.__file__).read_text().split("def stop_fill_overshoots", 1)[1].split("\ndef ", 1)[0]
    assert "_lef.exit_fill_price(c)" in src and 'c.get("exit_price")' not in src
