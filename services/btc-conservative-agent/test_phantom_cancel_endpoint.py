"""Cure 2 — Fly phantom paper position cancellation endpoint.

Static source-text contract checks (same pattern as test_showcase_manual_close).
A full Flask integration test is impractical here because bot.py boots a live
trading pipeline at import time; the existing tests in this folder use the
same source-text assertion style for the same reason.
"""

from pathlib import Path


BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def _endpoint_body() -> str:
    start = BOT_SOURCE.index("def api_reconcile_phantom_cancel()")
    # End at the next top-level @app.route that follows this endpoint.
    end = BOT_SOURCE.index("@app.route('/api/toggle_early_fail'", start)
    return BOT_SOURCE[start:end]


def main() -> None:
    # Endpoint is wired and admin-gated.
    assert "@app.route('/api/reconcile/phantom-cancel', methods=['POST'])" in BOT_SOURCE
    assert "def api_reconcile_phantom_cancel()" in BOT_SOURCE
    assert "PHANTOM_CANCEL_REASON = \"PHANTOM_CANCEL_BY_RELAY\"" in BOT_SOURCE

    body = _endpoint_body()

    # Required body field validated.
    assert 'body.get("trade_id")' in body
    assert '"trade_id is required"' in body

    # Idempotency — both the "already CLOSED" and "not in open_positions"
    # branches return ok without re-writing.
    assert "already_cancelled" in body
    assert "Phantom-cancel idempotent skip" in body
    assert "Phantom-cancel no-op" in body

    # Real-fill guard — refuses to cancel a position that has any real
    # Bitfinex marker.
    assert "bitfinex_position_id" in body
    assert "bitfinex_live_entry" in body
    assert "real_fill_recorded" in body
    assert "refused: position has a real Bitfinex fill marker" in body

    # Ambiguity guard.
    assert "ambiguous open position" in body

    # Capacity release — the phantom must release its lane slot.
    assert "lane_unregister_open_position(pos)" in body

    # $0 PnL outcome recorded with the phantom reason.
    assert '"net_pnl_usd": 0.0' in body
    assert '"exit_reason": PHANTOM_CANCEL_REASON' in body
    assert "log_trade_outcome_jsonl(trade_row, pos)" in body

    # Persists state + refreshes the cached /api/state snapshot so Railway's
    # next poll reflects the cancellation.
    assert "save_positions()" in body
    assert "save_persistent_config()" in body
    assert "_patch_api_state_cache_fields(" in body

    # Emits a POSITION_CLOSED relay event so downstream subscribers learn the
    # trade is no longer open.
    assert "_push_showcase_relay_event(" in body
    assert '"POSITION_CLOSED"' in body

    # Uses the same close lock as close_position — no race with the strategy.
    assert "with position_close_lock:" in body

    # Re-validates under the close lock (concurrent close_position race).
    assert "closed under lock" in body

    # Marks the position dict consistently.
    assert 'pos["status"] = "CLOSED"' in body
    assert 'pos["exit_reason"] = PHANTOM_CANCEL_REASON' in body

    # Response shape.
    assert '"ok": True' in body
    assert "cancelled_trade_id" in body
    assert '"scope": "showcase_paper_only"' in body

    # NO Bitfinex reduce-only / submit-order call from this endpoint — the
    # phantom is paper-only by definition.
    assert "_maybe_bitfinex_close" not in body
    assert "submit_stop_order" not in body
    assert "submit_limit_order" not in body

    print("Phantom-cancel endpoint contract tests passed")


if __name__ == "__main__":
    main()
