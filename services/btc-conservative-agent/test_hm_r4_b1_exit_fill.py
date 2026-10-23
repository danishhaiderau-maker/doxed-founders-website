"""HM R4 B1 + RK 08:03 (R4 one helper, R17): D3 and countability on Fly read ONLY a real Bitfinex exit fill.

The relay reporter (live-copy-fly-reporter.ts, liveExitFillEvidence) sends close.price only for exchange_fill /
stop_fill and always close.price_source; live_copy_monitor.build_chains carries it, live_exit_fill is THE Python
helper. Every other case (stop fired with no fill read / no stop order id, fill read failed or budget-refused, partial
close, non-USD fee -> 'mark'; already flat / post-close reconcile entry fallback -> 'entry_fallback'; old report with
no source -> 'none') is UNREADABLE: LEDGER_PNL_UNREADABLE (AMBER entry block, never a disarm) + non-countable. Never $0.
"""
from pathlib import Path

import pytest

import live_copy_monitor as lcm
import live_exit_fill as lef
import live_guardian as g
import live_tile_gate as ltg

NOW = 1_800_000_000.0
MIR = {"ok": True, "positions": [], "open_orders": [], "orphans": [], "balance": {"derivatives_available_usd": 206.79}}
ENTRY = 100_000.0

# (case, close report fields, expected source, real?)
CASES = [
    ("exchange_fill", {"price": 99_990.0, "price_source": "exchange_fill"}, "exchange_fill", True),
    ("stop_fill", {"price": 99_650.0, "price_source": "stop_fill"}, "stop_fill", True),
    ("stop_fired_no_fill_read", {"price": None, "price_source": "mark", "reported_exit_price": 99_640.0}, "mark", False),
    ("stop_fired_no_order_id", {"price": None, "price_source": "mark", "reported_exit_price": 99_650.0}, "mark", False),
    ("fill_read_failed_or_budget_refused", {"price": None, "price_source": "mark", "reported_exit_price": 99_980.0}, "mark", False),
    ("partial_close", {"price": None, "price_source": "mark", "reported_exit_price": 99_985.0}, "mark", False),
    ("non_usd_fee", {"price": None, "price_source": "mark", "reported_exit_price": 99_975.0}, "mark", False),
    ("already_flat", {"price": None, "price_source": "entry_fallback", "reported_exit_price": ENTRY}, "entry_fallback", False),
    ("reconcile_entry_fallback", {"price": None, "price_source": "entry_fallback", "reported_exit_price": ENTRY}, "entry_fallback", False),
    ("old_report_no_source", {"price": 99_000.0}, "none", False),
    # a stale/forged price with a non-real source must never be read as the fill
    ("mark_with_price", {"price": 99_000.0, "price_source": "mark"}, "mark", False),
]


def _chain(close: dict, *, pnl_usd=0.0, exit_price_top=None) -> dict:
    reports = [
        {"correlation_id": "gs1-a", "account": "A", "type": "ORDER_PLACED", "order": {"side": "LONG", "qty": 0.001, "price": ENTRY}},
        {"correlation_id": "gs1-a", "account": "A", "type": "ORDER_FILLED", "fill": {"price": ENTRY, "qty": 0.001}},
        {"correlation_id": "gs1-a", "account": "A", "type": "POSITION_CLOSED", "close": close, "pnl_usd": pnl_usd,
         "exit_price": exit_price_top, "closed_at_ts": NOW - 60},
    ]
    return lcm.build_chains([], reports)["accounts"][("gs1-a", "A")]


@pytest.mark.parametrize("name,close,source,real", CASES, ids=[c[0] for c in CASES])
def test_chain_carries_source_and_d3_reads_only_real_fills(name, close, source, real):
    c = _chain(close, pnl_usd=0.0, exit_price_top=ENTRY)   # a relay pnl_usd of 0 / top-level entry price is never read
    assert c["exit_price_source"] == source and c["side"] == "LONG"
    pnl = ltg.trade_realised_pnl_usd(c)
    if real:
        assert pnl == pytest.approx((close["price"] - ENTRY) * 0.001)
        assert lef.exit_fill_non_countable_reason(c) is None
        assert ltg.VIOL_PNL_UNREADABLE not in {v["code"] for v in ltg.daily_loss_violations([c], NOW)}
    else:
        assert pnl is None, name                                   # never a $0 loss
        assert ltg.daily_realised_loss([c], NOW)[2] == 0
        assert lef.exit_fill_non_countable_reason(c) == f"EXIT_FILL_UNREADABLE:{source}"
        assert {v["code"] for v in ltg.daily_loss_violations([c], NOW)} == {ltg.VIOL_PNL_UNREADABLE}
        # guardian: AMBER entry block only (never a disarm/flatten)
        red, amber = g.split_violations(g.evaluate([c], MIR, {}, NOW, lane_needle="",
                                                   book={"s": NOW - 0.5, "b": [[ENTRY, 1.0]], "a": [[ENTRY + 10, 1.0]]}))
        assert ltg.VIOL_PNL_UNREADABLE in {x["code"] for x in amber} and not red, (name, red)
        s = ltg.non_countable_summary([c])
        assert s["exit_fill_unreadable_trades"] == 1 and s["by_cause"] == {f"EXIT_FILL_UNREADABLE:{source}": 1}


def test_rk_required_stop_fired_no_fill_read_and_budget_refused_block_and_are_non_countable():
    for name in ("stop_fired_no_fill_read", "fill_read_failed_or_budget_refused"):
        close = next(c[1] for c in CASES if c[0] == name)
        c = _chain(close)
        blocks, _ = ltg.daily_cap_entry_block([c], MIR, {"s": NOW - 0.5, "b": [[ENTRY, 1.0]], "a": [[ENTRY + 10, 1.0]]}, NOW, "gs1")
        assert ltg.VIOL_PNL_UNREADABLE in {b["code"] for b in blocks}, name
        assert lef.exit_fill_non_countable_reason(c) == "EXIT_FILL_UNREADABLE:mark", name


def test_latest_close_report_wins_and_a_later_unreadable_close_clears_the_fill():
    c = _chain({"price": 99_990.0, "price_source": "exchange_fill"})
    assert lef.exit_fill_price(c) == 99_990.0
    reports = [{"correlation_id": "x", "account": "A", "type": "POSITION_CLOSED", "close": {"price": 99_990.0, "price_source": "exchange_fill"}},
               {"correlation_id": "x", "account": "A", "type": "POSITION_CLOSED", "close": {"price": None, "price_source": "mark"}}]
    c2 = lcm.build_chains([], reports)["accounts"][("x", "A")]
    assert lef.exit_fill_price(c2) is None and c2["exit_price_source"] == "mark"


def test_no_pnl_usd_fallback_and_docstring_is_true():
    src = Path(ltg.__file__).read_text()
    body = src.split("def trade_realised_pnl_usd", 1)[1].split("\ndef ", 1)[0]
    assert 'c.get("pnl_usd")' not in body and "There is NO pnl_usd fallback" in body
    assert "_lef.exit_fill_price(c)" in body
    assert lef.REAL_EXIT_FILL_SOURCES == frozenset({"exchange_fill", "stop_fill"})
    # the Python and TS helpers agree on the real sources
    ts = (Path(__file__).resolve().parents[2] / "apps/api/src/trading-agents/live-row-countability.ts").read_text()
    assert "new Set(['exchange_fill', 'stop_fill'])" in ts


def test_hm_r4_n6_per_cause_count_is_uncapped_and_in_the_summary():
    events = [{"code": ltg.STOP_FAILURE_FLATTEN, "cause": "E6_READ_TIMEOUT", "ts": NOW - i} for i in range(50)]  # the capped list
    s = ltg.non_countable_summary([], events, (), {"E6_READ_TIMEOUT": 73, "E2_STOP_REJECTED": 2})
    assert s["by_cause"] == {"STOP_FAILURE_FLATTEN:E6_READ_TIMEOUT": 73, "STOP_FAILURE_FLATTEN:E2_STOP_REJECTED": 2}
    # old state with no running counter: falls back to the (capped) events
    assert ltg.non_countable_summary([], events)["by_cause"] == {"STOP_FAILURE_FLATTEN:E6_READ_TIMEOUT": 50}
    ff = {"closed": True, "correlation_id": "ff", "exit_reason": "LIVE_GUARDIAN_FORCE_FLAT", "exit_fill_price": 1.0,
          "exit_price_source": "exchange_fill"}
    if ltg.force_flat_non_countable(ff):
        assert ltg.non_countable_summary([ff])["by_cause"].get("FORCE_FLAT") == 1
    bot = (Path(__file__).resolve().parent / "bot.py").read_text()
    assert '"stop_failure_cause_counts"' in bot.split("_LIVE_GUARDIAN_PERSIST_KEYS", 1)[1][:2000]
    rec = bot.split("def _live_guardian_record_protection", 1)[1][:3000]
    assert '_cc[str(ev["cause"])] = int(' in rec and '[-50:]' in rec
