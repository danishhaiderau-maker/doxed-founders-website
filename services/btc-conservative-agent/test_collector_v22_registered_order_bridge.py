import ast
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")


class _Logger:
    def __init__(self):
        self.rows = []

    def info(self, message):
        self.rows.append(message)


def _load_bridge(pending, sync_calls):
    tree = ast.parse(BOT_SOURCE)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_promote_collector_v22_registered_order"
    )

    def sync(source, *, path_complete=False):
        sync_calls.append((source, path_complete))
        return {"observation_status": "WAITING_120M"}

    namespace = {
        "_order_multiverse_pending_src": pending,
        "_sync_order_multiverse": sync,
        "logger": _Logger(),
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace["_promote_collector_v22_registered_order"], namespace["logger"]


def test_real_registration_replaces_rejected_provisional_with_execution_evidence():
    pending = {
        "cont-1": {
            "trade_id": "cont-1",
            "created_ts_ts": 100.0,
            "signal_price_at_approve": 50000.0,
            "collector_rejected": True,
            "collector_reject_reason": "WOULD_BLOCK_CHOP",
            "collector_would_block_only": True,
            "collector_ai": {"decision": "APPROVE"},
        }
    }
    calls = []
    bridge, logger = _load_bridge(pending, calls)
    schedule = {
        "schema": "research_order_schedule_v1",
        "authoritative": True,
        "intervals": [{"start_ts": 1000, "limit_price": 49950.0}],
    }
    order = {
        "trade_id": "cont-1",
        "status": "PENDING",
        "created_ts": 1000.0,
        "qty": 0.04,
        "limit_price": 49950.0,
        "research_chase_schedule": schedule,
        "chase_schedule_authoritative": True,
    }

    result = bridge(order, {"final_direction": "LONG", "shared_ai_call_id": "scan-1"})

    assert result == {"observation_status": "WAITING_120M"}
    assert len(calls) == 1
    source, path_complete = calls[0]
    assert path_complete is False
    assert source["created_ts_ts"] == 100.0
    assert source["status"] == "PENDING"
    assert source["qty"] == 0.04
    assert source["research_chase_schedule"] is schedule
    assert source["chase_schedule_authoritative"] is True
    assert source["collector_rejected"] is False
    assert source["collector_original_would_block"] is True
    assert source["collector_original_would_block_reason"] == "WOULD_BLOCK_CHOP"
    assert "collector_reject_reason" not in source
    assert "collector_would_block_only" not in source
    assert "collector_ai" not in source
    assert any("registered paper order promoted provisional" in row for row in logger.rows)


def test_non_pending_or_virtual_registration_cannot_promote_collector_source():
    calls = []
    bridge, _logger = _load_bridge({}, calls)

    assert bridge({"trade_id": "cont-2", "status": "AWAITING_DASHBOARD_CHASE"}) is None
    assert bridge({"trade_id": "", "status": "PENDING"}) is None
    assert bridge(None) is None
    assert calls == []


def test_lane_registration_invokes_bridge_after_schedule_initialization():
    lane_start = BOT_SOURCE.index("def lane_register_pending_order(order: dict):")
    lane_end = BOT_SOURCE.index("\ndef lane_unregister_pending_order", lane_start)
    body = BOT_SOURCE[lane_start:lane_end]

    assert "schedule_initializer(" in body
    assert "collector_bridge(" in body
    assert body.index("schedule_initializer(") < body.index("collector_bridge(")

