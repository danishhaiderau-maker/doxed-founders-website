"""Runtime regression for the bounded cold dashboard projection."""

import bot


class _DeepcopyBomb:
    def __deepcopy__(self, memo):  # pragma: no cover - must never be called
        raise AssertionError("dashboard projection invoked an unbounded copy hook")


def test_projection_bounds_append_only_histories_without_copy_hooks(monkeypatch):
    monkeypatch.setattr(bot, "_DASHBOARD_STATE_NESTED_ITEMS_MAX", 64)
    marker = _DeepcopyBomb()
    source = {
        "history": [{"seq": index} for index in range(10_000)],
        "nested": {str(index): index for index in range(1_000)},
        "custom": marker,
        "order_book": [{"large": True}],
    }

    projected = bot._bounded_dashboard_state_projection(source)

    assert len(projected["history"]) == 64
    assert projected["history"][0]["seq"] == 9_936
    assert len(projected["nested"]) == 64
    assert projected["custom"] is marker
    assert "order_book" not in projected


def test_projection_detaches_mutable_builtin_containers(monkeypatch):
    monkeypatch.setattr(bot, "_DASHBOARD_STATE_NESTED_ITEMS_MAX", 64)
    source = {"small": {"rows": [{"value": 1}]}}
    projected = bot._bounded_dashboard_state_projection(source)
    source["small"]["rows"][0]["value"] = 2
    assert projected["small"]["rows"][0]["value"] == 1
