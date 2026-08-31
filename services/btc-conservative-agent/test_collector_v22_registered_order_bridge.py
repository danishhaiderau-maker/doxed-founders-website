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

    def warning(self, message):
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


def _load_refresh(pending, upserts, terminal_writes=None):
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
        "os": type("_OS", (), {"getcwd": staticmethod(lambda: "C:/canonical")}),
        "copy": copy,
        "logger": _Logger(),
        "_collector_epoch_serialized": lambda fn: fn,
        "_order_multiverse_pending_src": pending,
        "_collector_v22_epoch_id": lambda: "epoch-1",
        "upsert_provisional_event": upsert,
        "dual_write_terminal_paper_schedule": lambda order, signal, **kwargs: (
            terminal_writes.append((copy.deepcopy(order), copy.deepcopy(signal), kwargs))
            if terminal_writes is not None else None
        ),
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
        "signed_quantity_constraints": {"schema": "signed_quantity_constraints_v1"},
        "quantity_constraints_status": {"supported": True},
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
    assert source["signed_quantity_constraints"] == {"schema": "signed_quantity_constraints_v1"}
    assert source["quantity_constraints_status"] == {"supported": True}
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


def test_lane_registration_enqueues_bridge_after_schedule_initialization():
    lane_start = BOT_SOURCE.index("def lane_register_pending_order(order: dict):")
    lane_end = BOT_SOURCE.index("\ndef lane_unregister_pending_order", lane_start)
    body = BOT_SOURCE[lane_start:lane_end]

    assert "schedule_initializer(" in body
    assert ".submit(" in body
    assert body.index("schedule_initializer(") < body.index(".submit(")
    assert "collector_bridge(" not in body


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


def test_terminal_refresh_appends_v3_schedule_evidence_without_affecting_open_mutations():
    pending = {"cont-4": {"trade_id": "cont-4", "collector_rejected": False}}
    upserts = []
    terminal_writes = []
    refresh = _load_refresh(pending, upserts, terminal_writes)
    schedule = {
        "authoritative": True,
        "intervals": [{"start_ts": 1, "end_ts": None, "limit_price": 100}],
    }
    order = {
        "trade_id": "cont-4", "status": "PENDING", "qty": .1,
        "research_chase_schedule": schedule, "chase_schedule_authoritative": True,
    }
    assert refresh(order) is True
    assert terminal_writes == []

    schedule["intervals"][0]["end_ts"] = 2
    schedule["terminal_ts"] = 2
    schedule["terminal_reason"] = "FILLED"
    order["status"] = "FILLED"
    assert refresh(
        order, {"shared_ai_call_id": "scan-4"}, lifecycle_final=True,
    ) is True
    assert len(terminal_writes) == 1
    captured_order, captured_signal, kwargs = terminal_writes[0]
    assert captured_order["status"] == "FILLED"
    assert captured_signal["shared_ai_call_id"] == "scan-4"
    assert kwargs == {
        "epoch_id": "epoch-1", "data_dir": "C:/canonical",
        "lifecycle_final": True,
    }


def test_temporary_pull_resume_then_final_expiry_publishes_one_selectable_schedule():
    pending = {"cont-resume": {"trade_id": "cont-resume", "collector_rejected": False}}
    upserts = []
    terminal_writes = []
    refresh = _load_refresh(pending, upserts, terminal_writes)
    signal = {"shared_ai_call_id": "scan-resume"}
    schedule = {
        "authoritative": True,
        "intervals": [
            {"start_ts": 1, "end_ts": 2, "limit_price": 100},
        ],
        "terminal_ts": 2,
        "terminal_reason": "VIRTUAL_CHASE_HIDE",
    }
    order = {
        "trade_id": "cont-resume", "status": "CANCELLED", "qty": .1,
        "research_chase_schedule": schedule,
        "chase_schedule_authoritative": True,
    }

    # Pulling a resting order into virtual chase is not lifecycle final.
    assert refresh(order, signal, lifecycle_final=False) is True
    assert terminal_writes == []

    # The same policy lifecycle resumes with a later interval.
    schedule["terminal_ts"] = None
    schedule["terminal_reason"] = None
    schedule["intervals"].append(
        {"start_ts": 4, "end_ts": None, "limit_price": 101},
    )
    order["status"] = "PENDING"
    assert refresh(order, signal, lifecycle_final=False) is True
    assert terminal_writes == []

    # Final expiry closes the complete accumulated schedule exactly once.
    schedule["intervals"][-1]["end_ts"] = 7
    schedule["terminal_ts"] = 7
    schedule["terminal_reason"] = "TTL_EXPIRED"
    order["status"] = "EXPIRED"
    assert refresh(order, signal, lifecycle_final=True) is True
    assert len(terminal_writes) == 1
    captured_order, _captured_signal, kwargs = terminal_writes[0]
    assert len(captured_order["research_chase_schedule"]["intervals"]) == 2
    assert kwargs["lifecycle_final"] is True


def test_terminal_schedule_evidence_failure_is_isolated_from_execution_refresh():
    pending = {"cont-5": {"trade_id": "cont-5", "collector_rejected": False}}
    upserts = []
    refresh = _load_refresh(pending, upserts, [])
    namespace = refresh.__globals__
    namespace["dual_write_terminal_paper_schedule"] = lambda *args, **kwargs: (_ for _ in ()).throw(
        ValueError("V3_CAUSAL_IDENTITY_INCOMPLETE")
    )
    order = {
        "trade_id": "cont-5", "status": "FILLED", "qty": .1,
        "chase_schedule_authoritative": True,
        "research_chase_schedule": {
            "authoritative": True,
            "intervals": [{"start_ts": 1, "end_ts": 2, "limit_price": 100}],
            "terminal_ts": 2, "terminal_reason": "FILLED",
        },
    }
    assert refresh(order, lifecycle_final=True) is True
    assert upserts[-1][1]["status"] == "FILLED"


def test_schedule_mutation_paths_refresh_collector_after_mutation():
    assert BOT_SOURCE.count("_refresh_collector_v22_registered_order_evidence") >= 6
    assert "schedule_reprice(\n            order," in BOT_SOURCE
    assert "schedule_close(\n                order," in BOT_SOURCE


def test_filled_terminal_schedule_is_published_only_after_open_commit_wins():
    process_start = BOT_SOURCE.index("def process_pending_orders():")
    process_end = BOT_SOURCE.index("\ndef fill_order(order):", process_start)
    process_body = BOT_SOURCE[process_start:process_end]
    fill_start = process_end
    fill_end = BOT_SOURCE.index("\ndef _observable_exit_price", fill_start)
    fill_body = BOT_SOURCE[fill_start:fill_end]

    # Touch detection delegates to fill_order without claiming FILLED evidence.
    fill_loop = process_body[process_body.rindex("    for order, fill_signal in fills:"):]
    assert "fill_order(order)" in fill_loop
    assert "lifecycle_final=True" not in fill_loop

    # The terminal write follows the atomic OPEN lifecycle winner. The earlier
    # manual-pause branch remains free to publish its sole cancellation truth.
    commit = fill_body.index("fill_snapshot = _finalize_position_open_lifecycle(")
    guard = fill_body.index("if fill_snapshot is None:", commit)
    terminal = fill_body.index("lifecycle_final=True", guard)
    assert commit < guard < terminal


def test_runtime_registration_captures_constraints_before_evidence_enqueue():
    lane_start = BOT_SOURCE.index("def lane_register_pending_order(order: dict):")
    lane_end = BOT_SOURCE.index("\ndef lane_unregister_pending_order", lane_start)
    body = BOT_SOURCE[lane_start:lane_end]
    assert 'capture_helper = globals().get("_capture_runtime_quantity_constraints")' in body
    assert body.index("capture_helper =") < body.index(".submit(")
    assert 'order["market_microstructure_symbol"]' in body


def test_partial_fill_freezes_requested_quantity_before_runtime_qty_is_narrowed():
    start = BOT_SOURCE.index("def resolve_sim_fill_price(order: dict) -> float:")
    end = BOT_SOURCE.index("\n\n_TRIGGER_CONSISTENT_EXIT_REASONS", start)
    body = BOT_SOURCE[start:end]
    freeze = body.index('order.setdefault("requested_qty", order.get("qty"))')
    mutation = body.index('order["qty"] = result["filled_qty"]')
    assert freeze < mutation
    assert 'order["filled_qty"] = result.get("filled_qty")' in body
    assert 'order["remaining_qty"] = result.get("unfilled_qty")' in body
