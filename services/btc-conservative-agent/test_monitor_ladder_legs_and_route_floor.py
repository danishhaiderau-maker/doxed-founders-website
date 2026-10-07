"""Ladder legs on CSV-reloaded rows, the selected_calls floor, zero ledger rows, B2 /ready clock."""
import ast
from pathlib import Path

import monitor_api
import monitor_tiles

BOT_SOURCE = (Path(__file__).resolve().parent / "bot.py").read_text(encoding="utf-8")

# gb1-78752adf1cf0 as reloaded from trades_3factor.csv after the 2026-10-04 22:15 boot:
# the receipts column is the Python repr text, not a list.
GB1 = {
    "trade_id": "gb1-78752adf1cf0", "research_lane": "FAMILY_GSB1_CVD_DIV_REGIME", "dir": "LONG",
    "entry": "85153.0", "exit": "85255.18", "leverage": "100", "pnl": "9.96", "pnl_margin_pct": "9.96",
    "net_pnl_usd": "0.02", "execution_qty": "0.000147", "policy_original_qty": "0.000294",
    "trading_fees_usd": "0.0", "funding_fees_usd": "0.0", "exit_reason": "GS_ATR_TAKE_PROFIT",
    "partial_exit_receipts": (
        "[{'ts': '2026-10-04T09:18:28.133019+00:00', 'reason': 'GS_LADDER_TP1', 'close_fraction': 0.5, "
        "'remaining_fraction': 0.5, 'price': 85221.1224, 'closed_qty': 0.000146794593261541, "
        "'realized_gross_usd': 0.01, 'cumulative_realized_net_usd': 0.01}, "
        "{'ts': '2026-10-04T09:24:10.119217+00:00', 'reason': 'GS_ATR_TAKE_PROFIT', 'close_fraction': 0.5, "
        "'remaining_fraction': 0.0, 'price': 85255.1836, 'closed_qty': 0.000146794593261541, "
        "'realized_gross_usd': None, 'cumulative_realized_net_usd': 0.01}]"),
}


def test_csv_reloaded_receipts_are_parsed_and_the_tp1_leg_counts():
    legs = monitor_api.partial_exit_legs(GB1)
    assert [leg["reason"] for leg in legs] == ["GS_LADDER_TP1", "GS_ATR_TAKE_PROFIT"]
    bp = monitor_api.trade_net_bp(GB1)
    assert 9.5 < bp < 10.5, bp  # was 6.00 with the TP1 leg dropped
    row = monitor_tiles.tile_trade_row(dict(GB1), status="closed", short_names={}, epoch_id="e",
                                       forced_reasons=["ADMIN_MANUAL_CLOSE"])
    assert len(row["exit"]["legs"]) == 2 and row["exit"]["legs"][0]["price"] == 85221.1224
    assert 9.5 < row["pnl"]["bp"] < 10.5


def test_json_and_list_receipts_parse_and_garbage_is_empty():
    as_list = dict(GB1, partial_exit_receipts=ast.literal_eval(GB1["partial_exit_receipts"]))
    assert len(monitor_api.partial_exit_legs(as_list)) == 2
    assert monitor_api.partial_exit_legs({"partial_exit_receipts": '[{"price": 1.0, "closed_qty": 2}]'})[0]["price"] == 1.0
    for junk in ("", "nan", "None", "[]", "not a list", None, 7):
        assert monitor_api.partial_exit_legs({"partial_exit_receipts": junk}) == []


def test_missing_legs_on_a_partial_trade_fall_back_to_the_booked_return():
    # gb3-d5223cd595df shape: half closed on TP1, receipts lost -> recompute books that half at 0.
    row = dict(GB1, partial_exit_receipts="", pnl_margin_pct="7.69", dir="SHORT", entry="85379.0",
               exit="85316.0", execution_qty="0.000146", policy_original_qty="0.000293")
    assert abs(monitor_api.trade_net_bp(row) - 7.69) < 1e-9


def test_full_size_trade_without_legs_still_uses_prices():
    row = {"dir": "LONG", "entry": 100.0, "exit": 100.1, "execution_qty": 1.0, "policy_original_qty": 1.0,
           "pnl_margin_pct": 99.0, "leverage": 100}
    assert abs(monitor_api.trade_net_bp(row) - 10.0) < 1e-6


def _compile(*names):
    tree = ast.parse(BOT_SOURCE)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names)
    ns = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot_subset", "exec"), ns)
    return ns


def test_selected_calls_never_below_the_epoch_orders():
    ns = _compile("_floor_selected_calls")
    counts = {"FAMILY_COMMITTED_FADE_TAKER_90": {"selected_calls": 0, "pending": 0, "open": 0, "closed": 2, "expired": 0},
              "FAMILY_GS01_XV_PREMIUM_ATR_TP": {"selected_calls": 9, "pending": 0, "open": 1, "closed": 6, "expired": 1}}
    ns["_floor_selected_calls"](counts)
    ha, gs1 = counts["FAMILY_COMMITTED_FADE_TAKER_90"], counts["FAMILY_GS01_XV_PREMIUM_ATR_TP"]
    assert ha["selected_calls"] == 2 and ha["selected_calls_basis"] == "epoch_order_floor"
    assert ha["selected_calls_linked"] == 0
    assert gs1["selected_calls"] == 9 and gs1["selected_calls_basis"] == "linked_shared_ai_calls"


def test_floor_runs_wherever_ledger_closed_counts_are_applied():
    start = BOT_SOURCE.index("def _apply_ledger_closed_counts(")
    body = BOT_SOURCE[start:BOT_SOURCE.index("\ndef ", start + 10)]
    assert "_floor_selected_calls(tile_route_counts)" in body


def test_every_active_tile_gets_a_ledger_row():
    ns = _compile("_empty_lane_pnl_bucket")
    ns.update(STARTING_BALANCE=500.0, LANE_LEDGER_WL_BASIS="PRICE_BP_NET_OF_FEES")
    bucket = ns["_empty_lane_pnl_bucket"]("FAMILY_GSB2_REGIME_SWITCHER")
    assert bucket["closes"] == 0 and bucket["net_pnl_usd"] == 0.0 and bucket["equity_usd"] == 500.0
    start = BOT_SOURCE.index("def _session_trade_accounting_locked(")
    body = BOT_SOURCE[start:BOT_SOURCE.index("\ndef ", start + 10)]
    assert 'globals().get("ACTIVE_TILE_ORDER")' in body and "_empty_lane_pnl_bucket(lane_key)" in body


def test_ready_fills_a_missing_signal_clock_from_the_evaluator_without_touching_the_registry():
    ns = _compile("_tile_row_with_display_signal_clock")

    class CvdEvaluator:
        SIGNAL_CLOCK = "BAR_CLOSE_3M_CVD_EVALUATOR"

    ns["_XVL_EVALUATORS"] = {"FAMILY_GSB2_REGIME_SWITCHER": CvdEvaluator()}
    tile = {"lane": "FAMILY_GSB2_REGIME_SWITCHER", "entry_policy": {"signal_clock": None, "kind": "x"}}
    out = ns["_tile_row_with_display_signal_clock"](tile)
    assert out["entry_policy"]["signal_clock"] == "BAR_CLOSE_3M_CVD_EVALUATOR"
    assert out["entry_policy"]["signal_clock_source"] == "evaluator:CvdEvaluator"
    assert tile["entry_policy"]["signal_clock"] is None  # registry row not mutated
    shared = {"lane": "FAMILY_COMMITTED_FADE_TAKER_90", "entry_policy": {"signal_clock": None}}
    assert ns["_tile_row_with_display_signal_clock"](shared) is shared
    declared = {"lane": "FAMILY_GSB2_REGIME_SWITCHER", "entry_policy": {"signal_clock": "PER_SECOND"}}
    assert ns["_tile_row_with_display_signal_clock"](declared) is declared
    assert "_tile_row_with_display_signal_clock(tile)" in BOT_SOURCE
