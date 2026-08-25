import bot
import pytest


@pytest.fixture(autouse=True)
def _ready_dashboard(monkeypatch):
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "retired-route-test-token")


AUTH = {"X-Bot-Admin-Token": "retired-route-test-token"}


@pytest.mark.parametrize(
    ("method", "path"),
    (
        ("post", "/api/toggle_profit_gates"),
        ("post", "/api/tile2/reset_counters"),
        ("get", "/api/tile2/metrics"),
    ),
)
def test_retired_research_routes_are_physically_absent(method, path):
    response = getattr(bot.app.test_client(), method)(path, headers=AUTH)
    assert response.status_code == 404


def test_retired_research_route_implementations_are_absent():
    for name in (
        "reset_tile2_counters_for_fresh_holdout",
        "tile2_policy_descriptor",
        "profit_gates_lane_enabled",
    ):
        assert not hasattr(bot, name)
