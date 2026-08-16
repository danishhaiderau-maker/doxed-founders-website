import copy

import bot
import pytest


@pytest.fixture(autouse=True)
def _ready_dashboard(monkeypatch):
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "retired-route-test-token")


AUTH = {"X-Bot-Admin-Token": "retired-route-test-token"}


def test_profit_gates_mutation_is_gone_and_state_is_unchanged():
    before = copy.deepcopy({
        "profit_gates_lane_enabled": bot.state.get("profit_gates_lane_enabled"),
        "profit_gates_enforced": bot.state.get("profit_gates_enforced"),
    })
    response = bot.app.test_client().post("/api/toggle_profit_gates", headers=AUTH)
    assert response.status_code == 410
    assert response.get_json() == {
        "status": "RETIRED_RESEARCH_CONTROL",
        "mutable": False,
        "message": "Profit Gates is retired historical research and cannot be enabled.",
    }
    assert {
        "profit_gates_lane_enabled": bot.state.get("profit_gates_lane_enabled"),
        "profit_gates_enforced": bot.state.get("profit_gates_enforced"),
    } == before


def test_tile2_reset_is_authenticated_but_cannot_mutate(monkeypatch):
    monkeypatch.setattr(
        bot,
        "reset_tile2_counters_for_fresh_holdout",
        lambda: (_ for _ in ()).throw(AssertionError("retired reset must not execute")),
    )
    client = bot.app.test_client()
    assert client.post("/api/tile2/reset_counters").status_code == 401
    response = client.post(
        "/api/tile2/reset_counters",
        headers=AUTH,
    )
    assert response.status_code == 410
    payload = response.get_json()
    assert payload["status"] == "RETIRED_RESEARCH_CONTROL"
    assert payload["mutable"] is False


def test_tile2_metrics_remain_read_only_historical_evidence():
    response = bot.app.test_client().get("/api/tile2/metrics", headers=AUTH)
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["research_surface_status"] == "RETIRED_HISTORICAL"
    assert payload["mutable"] is False
    assert payload.get("lane") == bot.RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC
