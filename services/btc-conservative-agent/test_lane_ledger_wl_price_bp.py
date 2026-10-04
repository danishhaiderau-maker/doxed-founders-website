"""W/L comes from price-based net bp in every lane ledger (freeze21b audit X1).

The API ledger classified cent-rounded ``net_pnl_usd`` and the disk ledger raw
USD, so a +0.23 bp break-even lock (books $0.00) was a W on disk and nothing in
the API: GS-01 read 3W/1L vs 2W/1L for the same four closes.
"""
import copy

import pytest

import bot
import monitor_api

NOTIONAL = 25.0
LANE = "FAMILY_GS01_XV_PREMIUM_ATR_TP"


def _row(trade_id, entry, exit_price, direction="LONG", legs=(), lane=LANE):
    qty = NOTIONAL / entry
    sign = 1.0 if direction == "LONG" else -1.0
    receipts, remaining = [], qty
    gross = 0.0
    for fraction, price in legs:
        closed = qty * fraction
        remaining -= closed
        leg_gross = (price - entry) * sign * closed
        gross += leg_gross
        receipts.append({"closed_qty": closed, "price": price, "realized_gross_usd": leg_gross,
                         "remaining_fraction": remaining / qty})
    gross += (exit_price - entry) * sign * remaining
    return {"trade_id": trade_id, "research_lane": lane, "dir": direction, "entry": entry, "exit": exit_price,
            "execution_qty": remaining, "policy_original_qty": qty if legs else None,
            "partial_exit_receipts": receipts, "trading_fees_usd": 0.0, "funding_fees_usd": 0.0,
            "net_pnl_usd": round(gross, 2), "margin_usdt": 0.25, "leverage": 100,
            "pnl": round(gross / 0.25 * 100, 2), "exit_reason": "GS_BREAKEVEN_LOCK"}


# The four in-window GS-01 policy closes (audit section 6).
GS01 = [
    _row("gs1-15af01d9b636", 85146.0, 85245.04),
    _row("gs1-7840611f7242", 85270.0, 85272.0),     # +0.235 bp, books $0.00
    _row("gs1-cc098b1943c6", 85273.0, 85366.84),
    _row("gs1-988ec0905d1a", 85394.0, 85322.0),
]


def test_be_micro_win_is_a_win_by_bp_although_it_books_zero_cents():
    row = GS01[1]
    assert row["net_pnl_usd"] == 0.0
    assert monitor_api.trade_net_bp(row) == pytest.approx(0.2345, abs=1e-3)
    assert monitor_api.wl_class(monitor_api.trade_net_bp(row)) == "W"
    assert monitor_api.wl_class(monitor_api.trade_net_bp(row), be_band_bp=0.5) == "BE"


def test_ladder_legs_are_qty_weighted_not_cent_rounded():
    b1 = _row("gb1-78752adf1cf0", 85153.0, 85255.18, legs=((0.5, 85221.12),))
    assert monitor_api.trade_net_bp(b1) == pytest.approx(10.0, abs=0.01)


def test_booked_bp_wins_and_fees_count():
    assert monitor_api.trade_net_bp({**GS01[1], "net_pnl_bp": -0.1}) == -0.1
    with_fee = {**GS01[1], "trading_fees_usd": 0.01}  # 4 bp of fees turns the BE lock into a loss
    assert monitor_api.wl_class(monitor_api.trade_net_bp(with_fee)) == "L"


def test_api_ledger_and_disk_ledger_agree(tmp_path, monkeypatch):
    derived = bot._derive_lane_pnl_ledger_from_trades(copy.deepcopy(GS01))[LANE]
    monkeypatch.setattr(bot, "LANE_PNL_LEDGER_FILE", str(tmp_path / "lane_pnl_ledger.json"))
    monkeypatch.setitem(bot.state, "lane_pnl_ledger", {})
    for row in GS01:
        bot.update_lane_pnl_ledger(LANE, "CLOSE", row["net_pnl_usd"], row["dir"],
                                   net_pnl_bp=monitor_api.trade_net_bp(row))
    incremental = bot.state["lane_pnl_ledger"][LANE]
    assert (derived["wins"], derived["losses"]) == (incremental["wins"], incremental["losses"]) == (3, 1)
    assert derived["wl_basis"] == incremental["wl_basis"] == "PRICE_BP_NET_OF_FEES"


def test_monitor_lane_stats_use_the_same_rule():
    shaped = [{"close_ts": i, "net_pnl_usd": r["net_pnl_usd"], "notional_usd": NOTIONAL, "direction": "LONG",
               "pnl_margin_pct": r["pnl"], "leverage": 100, "net_pnl_bp": monitor_api.trade_net_bp(r)}
              for i, r in enumerate(GS01)]
    stats = monitor_api.lane_stats(shaped)
    assert (stats["wins"], stats["losses"]) == (3, 1)


def test_unknown_bp_falls_back_to_booked_usd():
    assert monitor_api.wl_class(None, net_usd=0.02) == "W"
    assert monitor_api.wl_class(None, net_usd=0.0) == "BE"
