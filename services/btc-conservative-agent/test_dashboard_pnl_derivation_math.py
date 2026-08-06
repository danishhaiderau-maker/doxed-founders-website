"""Pure-Python logic test for `_derive_lane_pnl_ledger_from_trades`.

This verifies the per-bucket math against the exact trade set that exposed
the dashboard under-count bug on 2026-08-06. The function lives in bot.py
but is pure (no module-level side effects beyond constants), so we lift a
copy here to test the math without importing bot.py (which pulls in
ccxt/pandas/numpy and a live WS environment).
"""

from __future__ import annotations


STARTING_BALANCE = 500.0


def _normalize_lane_key(lane_or_obj) -> str:
    if isinstance(lane_or_obj, dict):
        return str(lane_or_obj.get("research_lane") or "CONTINUOUS").upper()
    return str(lane_or_obj or "CONTINUOUS").upper()


def _derive_lane_pnl_ledger_from_trades(session_trades) -> dict:
    # Copy of the helper in bot.py; if the implementation drifts the
    # source-level test (test_dashboard_pnl_overlay_counters.py) will fail
    # too, but the math contract is locked here.
    ledger: dict = {}
    for row in session_trades or []:
        if not isinstance(row, dict):
            continue
        lane = _normalize_lane_key(row.get("research_lane") or "")
        if not lane:
            continue
        try:
            pnl = float(row.get("net_pnl_usd") or row.get("pnl") or 0.0)
        except (TypeError, ValueError):
            pnl = 0.0
        bucket = ledger.setdefault(lane, {
            "lane": lane,
            "net_pnl_usd": 0.0,
            "gross_wins_usd": 0.0,
            "gross_losses_usd": 0.0,
            "wins": 0,
            "losses": 0,
            "closes": 0,
            "long_closes": 0,
            "short_closes": 0,
            "long_pnl_usd": 0.0,
            "short_pnl_usd": 0.0,
            "equity_usd": float(STARTING_BALANCE),
        })
        bucket["closes"] = int(bucket.get("closes", 0)) + 1
        bucket["net_pnl_usd"] = round(float(bucket.get("net_pnl_usd", 0)) + pnl, 2)
        bucket["equity_usd"] = round(float(STARTING_BALANCE) + bucket["net_pnl_usd"], 2)
        if pnl > 0:
            bucket["wins"] = int(bucket.get("wins", 0)) + 1
            bucket["gross_wins_usd"] = round(float(bucket.get("gross_wins_usd", 0)) + pnl, 2)
        elif pnl < 0:
            bucket["losses"] = int(bucket.get("losses", 0)) + 1
            bucket["gross_losses_usd"] = round(float(bucket.get("gross_losses_usd", 0)) + pnl, 2)
        d = str(row.get("final_direction") or row.get("dir") or "").upper()
        if d == "LONG":
            bucket["long_closes"] = int(bucket.get("long_closes", 0)) + 1
            bucket["long_pnl_usd"] = round(float(bucket.get("long_pnl_usd", 0)) + pnl, 2)
        elif d == "SHORT":
            bucket["short_closes"] = int(bucket.get("short_closes", 0)) + 1
            bucket["short_pnl_usd"] = round(float(bucket.get("short_pnl_usd", 0)) + pnl, 2)
    return ledger


# Exact closed-trade rows from the 2026-08-06 incident.
BUG_REPORT_TRADES = [
    {"trade_id": "cont-074f065a73cc",  "research_lane": "CONTINUOUS",
     "net_pnl_usd": 0.77, "final_direction": "SHORT"},
    {"trade_id": "cont-81c2292eb0ea",  "research_lane": "CONTINUOUS",
     "net_pnl_usd": 1.87, "final_direction": "LONG"},
    {"trade_id": "tbhv1-3377cb670329", "research_lane": "TYPE_B_HUNTER_V1",
     "net_pnl_usd": 0.99, "final_direction": "LONG"},
    {"trade_id": "tbhv1-060853e9c993", "research_lane": "TYPE_B_HUNTER_V1",
     "net_pnl_usd": -0.30, "final_direction": "SHORT"},
]


def test_continuous_bucket_matches_true_total() -> None:
    ledger = _derive_lane_pnl_ledger_from_trades(BUG_REPORT_TRADES)
    cont = ledger["CONTINUOUS"]
    assert cont["closes"] == 2, cont
    assert cont["net_pnl_usd"] == 2.64, cont
    assert cont["wins"] == 2, cont
    assert cont["losses"] == 0, cont
    assert cont["gross_wins_usd"] == 2.64, cont
    assert cont["gross_losses_usd"] == 0.0, cont
    # direction attribution
    assert cont["long_closes"] == 1, cont
    assert cont["short_closes"] == 1, cont
    assert cont["long_pnl_usd"] == 1.87, cont
    assert cont["short_pnl_usd"] == 0.77, cont
    assert cont["equity_usd"] == 502.64, cont


def test_type_b_bucket_appears_with_correct_totals() -> None:
    """The bug: TYPE_B_HUNTER_V1 was completely missing from the tile."""
    ledger = _derive_lane_pnl_ledger_from_trades(BUG_REPORT_TRADES)
    assert "TYPE_B_HUNTER_V1" in ledger, (
        "TYPE_B_HUNTER_V1 must appear in the derived ledger -- its absence "
        "was the original under-count symptom."
    )
    tb = ledger["TYPE_B_HUNTER_V1"]
    assert tb["closes"] == 2, tb
    assert tb["net_pnl_usd"] == 0.69, tb  # 0.99 + (-0.30)
    assert tb["wins"] == 1, tb
    assert tb["losses"] == 1, tb
    assert tb["gross_wins_usd"] == 0.99, tb
    assert tb["gross_losses_usd"] == -0.30, tb
    assert tb["equity_usd"] == 500.69, tb


def test_no_extra_lanes() -> None:
    ledger = _derive_lane_pnl_ledger_from_trades(BUG_REPORT_TRADES)
    assert set(ledger.keys()) == {"CONTINUOUS", "TYPE_B_HUNTER_V1"}, ledger


def test_combined_total_matches_session_pnl() -> None:
    """The session PnL is the sum of all lane net_pnl_usd values."""
    ledger = _derive_lane_pnl_ledger_from_trades(BUG_REPORT_TRADES)
    combined = round(sum(b["net_pnl_usd"] for b in ledger.values()), 2)
    assert combined == 3.33, combined
    combined_closes = sum(b["closes"] for b in ledger.values())
    assert combined_closes == 4, combined_closes


def test_empty_trades_returns_empty_ledger() -> None:
    assert _derive_lane_pnl_ledger_from_trades([]) == {}
    assert _derive_lane_pnl_ledger_from_trades(None) == {}


def test_rows_missing_lane_default_to_continuous() -> None:
    """Defensive: a malformed row with no research_lane should not crash."""
    ledger = _derive_lane_pnl_ledger_from_trades([
        {"trade_id": "x", "net_pnl_usd": 1.0, "final_direction": "LONG"},
    ])
    assert ledger["CONTINUOUS"]["closes"] == 1


def test_rows_with_garbage_pnl_treated_as_zero() -> None:
    """Defensive: a non-numeric net_pnl_usd should not crash the derivation."""
    ledger = _derive_lane_pnl_ledger_from_trades([
        {"trade_id": "x", "research_lane": "CONTINUOUS",
         "net_pnl_usd": "not-a-number", "final_direction": "LONG"},
    ])
    assert ledger["CONTINUOUS"]["net_pnl_usd"] == 0.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all tests passed")
