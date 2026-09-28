"""Source-level regression test for the dashboard PnL under-count bug.

Bug (detected 2026-08-06): under live trading, `/api/state` is served from
the ACTIVE_EXECUTION_OVERLAY path. That path builds the heavy presentation
snapshot once and then overlays only a bounded key list from the canonical
relay execution snapshot each cycle. The list omitted the PnL / counter /
lane ledger keys, so:

  * `trade_count_session` froze at the value from the first heavy build
    (e.g. 1 even when 5 trades had closed).
  * `session_pnl_usd` froze at the first heavy build value.
  * `lane_pnl_ledger` froze, so tiles for lanes whose first close happened
    after the heavy build (e.g. TYPE_B_HUNTER_V1) never appeared.

The fix:
  1. `_build_relay_execution_state_snapshot` now emits `trade_count_session`,
     `trade_count`, `session_pnl_usd`, `trades_display_limit`,
     `lane_pnl_ledger`, `account_balance`, and `equity` (computed from the
     same `recent_trades` slice already shipped as `snapshot["trades"]`).
  2. The overlay key list in `_api_state_cache_refresher_loop` now includes
     those keys so they refresh every cycle.
  3. `_derive_lane_pnl_ledger_from_trades` is a pure derivation that
     guarantees the tile matches the trades table even if the incremental
     `update_lane_pnl_ledger` ever misses a close.

We use AST source inspection (matching the pattern in
test_relay_execution_state_telemetry.py) because bot.py imports
ccxt/pandas/numpy and a live Bitfinex WS environment at module load -- not
appropriate for focused unit tests.
"""

from __future__ import annotations

import ast
from pathlib import Path

BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)


def _function(name: str) -> ast.FunctionDef:
    return next(
        node
        for node in BOT_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_derive_lane_pnl_ledger_helper_exists() -> None:
    """Pure derivation helper must exist with the documented signature.

    This is the safety net that lets the dashboard tile recover even if the
    incremental `update_lane_pnl_ledger` ever misses a close (exception path,
    restart replay gap, race). Deriving from the trades list is what makes
    the tile match the trades table.
    """
    func = _function("_derive_lane_pnl_ledger_from_trades")
    args = [a.arg for a in func.args.args]
    assert args == ["session_trades"], (
        "_derive_lane_pnl_ledger_from_trades must accept a single "
        "`session_trades` parameter so it can be called from the relay "
        "snapshot with the same trades slice already shipped to the dashboard."
    )


def test_relay_snapshot_emits_pnl_and_counter_keys() -> None:
    """_build_relay_execution_state_snapshot must emit the PnL/counter keys.

    The dashboard's ACTIVE_EXECUTION_OVERLAY path overlays keys from this
    snapshot every cycle. Without these keys here the overlay cannot
    refresh the tile numbers, and they freeze at the first heavy build.
    """
    body = ast.get_source_segment(BOT_SOURCE, _function("_build_relay_execution_state_snapshot"))
    assert body is not None
    expected_keys = [
        'snapshot["trade_count_session"]',
        'snapshot["trade_count"]',
        'snapshot["session_pnl_usd"]',
        'snapshot["trades_display_limit"]',
        'snapshot["lane_pnl_ledger"]',
        'snapshot["lane_position_counts"]',
        'snapshot["account_balance"]',
        'snapshot["equity"]',
    ]
    missing = [k for k in expected_keys if k not in body]
    assert not missing, (
        "_build_relay_execution_state_snapshot is missing PnL/counter/ledger "
        f"assignments: {missing}. These keys feed the dashboard overlay -- "
        "without them the tile freezes at the first heavy build."
    )
    # The lane ledger must be derived from the same trades slice that ships
    # as snapshot["trades"], not from a stale incremental counter.
    assert "_derive_lane_pnl_ledger_from_trades(recent_trades)" in body, (
        "lane_pnl_ledger in the relay snapshot must be derived via "
        "_derive_lane_pnl_ledger_from_trades(recent_trades) so the tile "
        "matches the trades table exactly."
    )


def test_overlay_key_list_includes_pnl_and_counter_keys() -> None:
    """The ACTIVE_EXECUTION_OVERLAY key list must refresh PnL/counter keys.

    This is the actual fix for the regression: the overlay previously
    omitted these keys so they were frozen at the first heavy build.
    """
    body = ast.get_source_segment(BOT_SOURCE, _function("_api_state_cache_refresher_loop"))
    assert body is not None
    expected_overlay_keys = [
        '"trade_count_session"',
        '"trade_count"',
        '"session_pnl_usd"',
        '"trades_display_limit"',
        '"lane_pnl_ledger"',
        '"lane_position_counts"',
        '"account_balance"',
        '"equity"',
    ]
    missing = [k for k in expected_overlay_keys if k not in body]
    assert not missing, (
        "_api_state_cache_refresher_loop overlay key list is missing: "
        f"{missing}. Without these keys in the overlay the dashboard tile "
        "freezes at the first heavy build and under-counts trades that "
        "close afterwards (regression detected 2026-08-06)."
    )


def test_relay_snapshot_emits_live_dashboard_truth_fields() -> None:
    """Active execution must not freeze market/evidence identity at boot."""
    body = ast.get_source_segment(
        BOT_SOURCE, _function("_build_relay_execution_state_snapshot")
    )
    assert body is not None
    expected = [
        '"bid": state.get("bid")',
        '"ask": state.get("ask")',
        '"bid_size_btc": state.get("bid_size_btc")',
        '"ask_size_btc": state.get("ask_size_btc")',
        '"spread_usd": state.get("spread_usd")',
        '"support_resistance": copy.deepcopy(',
        '"funding": copy.deepcopy(',
        '"last_fetch_success": utc_iso()',
        '"fresh_epoch_id": _collector_v22_epoch_id()',
        '"tile_registry_signature": active_tile_registry_signature()',
        '"active_tiles": active_tile_lifecycle_manifest()',
    ]
    missing = [item for item in expected if item not in body]
    assert not missing, f"relay snapshot is missing live truth fields: {missing}"


def test_ws_ticker_updates_standard_dashboard_bbo_fields() -> None:
    body = ast.get_source_segment(BOT_SOURCE, _function("_process_ws_ticker_update"))
    assert body is not None
    expected = [
        'state["bid_size_btc"] = bid_qty',
        'state["ask_size_btc"] = ask_qty',
        'state["spread_usd"] = round(ask - bid, 2)',
        'state["spread_pct"] = round((ask - bid) / bid * 100.0, 5)',
        'state["bbo_ts"] = tick_now',
    ]
    missing = [item for item in expected if item not in body]
    assert not missing, f"ticker callback would leave dashboard BBO stale: {missing}"


def test_relay_snapshot_derives_lane_exposure_from_current_rows() -> None:
    """Patient Chase tile counts must not freeze at the first heavy build."""
    body = ast.get_source_segment(
        BOT_SOURCE, _function("_build_relay_execution_state_snapshot")
    )
    assert body is not None
    assert 'snapshot["lane_position_counts"] = {}' in body
    assert "for position in positions_copy" in body
    assert "for order in pending_copy" in body
    assert "_normalize_lane_key(position) == lane_name" in body
    assert "_normalize_lane_key(order) == lane_name" in body


def _overlay_key_list_literal() -> tuple:
    """Parse the overlay `for key in (...)` tuple literal as a Python tuple."""
    loop = _function("_api_state_cache_refresher_loop")
    for node in ast.walk(loop):
        if isinstance(node, ast.For):
            iter_node = node.iter
            if isinstance(iter_node, ast.Tuple):
                keys = []
                for elt in iter_node.elts:
                    if isinstance(elt, ast.Constant):
                        keys.append(elt.value)
                # The overlay tuple is the one that includes "price".
                if "price" in keys:
                    return tuple(keys)
    raise AssertionError("Could not locate the overlay `for key in (...)` tuple")


def test_overlay_key_list_has_no_duplicates() -> None:
    """No key may appear twice in the overlay tuple (idempotent overlay)."""
    keys = _overlay_key_list_literal()
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"overlay key list has duplicates: {dupes}"


def test_overlay_key_list_contains_core_money_keys() -> None:
    """Sanity: the overlay list must still include the original core keys."""
    keys = _overlay_key_list_literal()
    for required in ("price", "trades", "positions", "orders"):
        assert required in keys, (
            f"overlay key list lost core money-path key '{required}'"
        )


def test_overlay_key_list_contains_live_dashboard_truth_fields() -> None:
    keys = _overlay_key_list_literal()
    required = {
        "bid", "ask", "bid_size_btc", "ask_size_btc", "spread_usd",
        "support_resistance", "funding", "daily_pnl_usd",
        "last_fetch_success", "fresh_epoch_id", "tile_registry_signature",
        "active_tiles", "tile_architecture_version", "tile_registry_schema",
    }
    missing = sorted(required.difference(keys))
    assert not missing, (
        "active dashboard overlay would freeze current runtime fields: "
        f"{missing}"
    )


if __name__ == "__main__":
    # Manual run without pytest.
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all tests passed")
