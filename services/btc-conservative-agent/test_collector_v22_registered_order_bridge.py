import ast
import copy
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


def _load_refresh(pending, upserts):
    tree = ast.parse(BOT_SOURCE)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef)
        and item.name == "_refresh_collector_v22_registered_order_evidence"
    )

    def upsert(event_id, source, *, epoch_id):
        upserts.append((event_id, copy.deepcopy(source), epoch_id))

    namespace = {
        "copy": copy,
        "_order_multiverse_pending_src": pending,
        "_collector_v22_epoch_id": lambda: "epoch-1",
        "upsert_provisional_event": upsert,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT_PATH), "exec"), namespace)
    return namespace["_refresh_collector_v22_registered_order_evidence"]


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


def test_reprice_and_terminal_close_refresh_durable_schedule_snapshot():
    pending = {
        "cont-3": {
            "trade_id": "cont-3",
            "status": "PENDING",
            "qty": 0.03,
            "collector_rejected": False,
            "research_chase_schedule": {"authoritative": True, "intervals": []},
        }
    }
    upserts = []
    refresh = _load_refresh(pending, upserts)
    schedule = {
        "schema": "research_chase_schedule_v1",
        "authoritative": True,
        "intervals": [
            {"start_ts": 1000, "end_ts": 1100, "limit_price": 50000.0},
            {"start_ts": 1100, "end_ts": None, "limit_price": 50010.0},
        ],
    }
    order = {
        "trade_id": "cont-3",
        "status": "PENDING",
        "qty": 0.03,
        "limit_price": 50010.0,
        "limit_chase_count": 4,
        "last_chase_ts": 1100.0,
        "research_chase_schedule": schedule,
        "chase_schedule_authoritative": True,
    }

    assert refresh(order) is True
    assert len(upserts) == 1
    _event_id, durable, epoch_id = upserts[-1]
    assert epoch_id == "epoch-1"
    assert durable["limit_chase_count"] == 4
    assert len(durable["research_chase_schedule"]["intervals"]) == 2

    schedule["intervals"][-1]["end_ts"] = 1200
    order["status"] = "CANCELLED"
    assert refresh(order) is True
    assert upserts[-1][1]["status"] == "CANCELLED"
    assert upserts[-1][1]["research_chase_schedule"]["intervals"][-1]["end_ts"] == 1200

    # Durable rows are copies, not aliases to later runtime mutations.
    schedule["intervals"][-1]["end_ts"] = 1300
    assert upserts[-1][1]["research_chase_schedule"]["intervals"][-1]["end_ts"] == 1200


def test_schedule_mutation_paths_refresh_collector_after_mutation():
    assert BOT_SOURCE.count("_refresh_collector_v22_registered_order_evidence") >= 6
    assert "schedule_reprice(\n                order," in BOT_SOURCE
    assert "schedule_close(\n                    order," in BOT_SOURCE
