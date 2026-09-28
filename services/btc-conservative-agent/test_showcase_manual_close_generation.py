"""Focused generation-fence contract for the paper position close endpoint."""

import os
import sys
import copy

import pytest


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


@pytest.fixture
def isolated_manual_close(monkeypatch):
    position = {"trade_id": "paper-generation-test", "status": "OPEN"}
    with bot.trade_lock:
        saved_positions = list(bot.open_positions)
        saved_orders = list(bot.pending_orders)
        saved_trades = list(bot.trades)
        bot.open_positions[:] = [position]
        bot.pending_orders[:] = []
        bot.trades[:] = []
    with bot._RELAY_EXECUTION_CACHE_LOCK:
        saved_generation = bot._RELAY_EXECUTION_MONEY_STATE_GENERATION
        saved_cache = (
            bot._RELAY_EXECUTION_CACHE_PAYLOAD,
            bot._RELAY_EXECUTION_CACHE_BODY,
            bot._RELAY_EXECUTION_CACHE_AT,
        )

    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_patch_api_state_cache_fields", lambda **_fields: None)
    monkeypatch.setattr(
        bot,
        "_build_relay_execution_state_snapshot",
        lambda: {
            "money_state_generation": bot._RELAY_EXECUTION_MONEY_STATE_GENERATION,
            "positions": copy.deepcopy(bot.open_positions),
            "state_integrity": {},
        },
    )

    def close_position(target, reason):
        assert target is position
        assert reason == "ADMIN_MANUAL_CLOSE"
        with bot.trade_lock:
            bot.open_positions.remove(target)
            bot.trades.append({"trade_id": target["trade_id"], "exit_reason": reason})

    monkeypatch.setattr(bot, "close_position", close_position)
    try:
        yield position
    finally:
        with bot.trade_lock:
            bot.open_positions[:] = saved_positions
            bot.pending_orders[:] = saved_orders
            bot.trades[:] = saved_trades
        with bot._RELAY_EXECUTION_CACHE_LOCK:
            bot._RELAY_EXECUTION_MONEY_STATE_GENERATION = saved_generation
            (
                bot._RELAY_EXECUTION_CACHE_PAYLOAD,
                bot._RELAY_EXECUTION_CACHE_BODY,
                bot._RELAY_EXECUTION_CACHE_AT,
            ) = saved_cache


def test_manual_close_returns_the_advanced_relay_money_generation(isolated_manual_close):
    before = bot._RELAY_EXECUTION_MONEY_STATE_GENERATION

    with bot.app.test_client() as client:
        response = client.post(
            "/api/positions/close",
            json={"trade_id": isolated_manual_close["trade_id"]},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "closed"
    assert payload["trade_id"] == isolated_manual_close["trade_id"]
    assert payload["scope"] == "showcase_paper_only"
    assert isinstance(payload["money_state_generation"], int)
    assert payload["money_state_generation"] == before + 1
    assert bot._RELAY_EXECUTION_MONEY_STATE_GENERATION == before + 1
    assert bot._RELAY_EXECUTION_CACHE_PAYLOAD is None
    assert bot._RELAY_EXECUTION_CACHE_BODY is None
    assert bot.open_positions == []
    assert bot.trades == [{
        "trade_id": isolated_manual_close["trade_id"],
        "exit_reason": "ADMIN_MANUAL_CLOSE",
    }]

    with bot.app.test_client() as client:
        fresh = client.get(
            "/api/relay-execution-state?fresh=1",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

    assert fresh.status_code == 200
    fresh_payload = fresh.get_json()
    assert fresh_payload["money_state_generation"] == payload["money_state_generation"]
    assert fresh_payload["positions"] == []
