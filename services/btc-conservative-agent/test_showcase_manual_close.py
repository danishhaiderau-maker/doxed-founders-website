from pathlib import Path


BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")


def test_positions_table_has_one_trade_scoped_close_action():
    assert "<th>Action</th>" in BOT_SOURCE
    assert "closeShowcasePosition(this.dataset.tradeId)" in BOT_SOURCE
    assert "Close paper position" in BOT_SOURCE
    assert "This does not close a Bitfinex position." in BOT_SOURCE


def test_manual_close_endpoint_is_single_position_and_paper_only():
    assert "@app.route('/api/positions/close', methods=['POST'])" in BOT_SOURCE
    assert 'body.get("trade_id")' in BOT_SOURCE
    assert 'close_position(matches[0], "ADMIN_MANUAL_CLOSE")' in BOT_SOURCE
    assert '"scope": "showcase_paper_only"' in BOT_SOURCE
    endpoint = BOT_SOURCE[
        BOT_SOURCE.index("def api_close_showcase_position"):
        BOT_SOURCE.index("@app.route('/api/toggle_early_fail")
    ].lower()
    assert "close all" not in endpoint
