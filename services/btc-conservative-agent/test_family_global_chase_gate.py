import ast
import threading
from pathlib import Path


BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
TREE = ast.parse(BOT_SOURCE)


def _function(name):
    return next(
        node for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _compile_functions(*names, namespace):
    nodes = [_function(name) for name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<family-chase-gate>", "exec"), namespace)
    return namespace


def test_registered_family_uses_global_virtual_defer_and_minimum_bucket():
    buckets = {
        "0_chases": False,
        "1_chase": False,
        "2_chases": False,
        "3_chases": True,
        "4_chases": True,
        "5+_chases": True,
    }
    namespace = {
        "state_lock": threading.RLock(),
        "state": {"ai_enabled": True, "leverage": 100},
        "MAX_RESEARCH_LEVERAGE": 100,
        "is_patient_chase_lane": lambda lane: str(lane).startswith("FAMILY_"),
        "lane_orders_allowed": lambda _lane: True,
        "_signal_spread_gate_blocked": lambda *_args: (_ for _ in ()).throw(
            AssertionError("family must not inherit the Continuous spread gate")
        ),
        "dashboard_ai_band_blocks": lambda *_args: (_ for _ in ()).throw(
            AssertionError("family must not inherit the advisory AI-band gate")
        ),
        "ensure_signal_capacity": lambda: True,
        "get_chase_execution_buckets": lambda: buckets,
        "dashboard_virtual_chase_submit_ready": lambda signal: signal.get("age_bucket") in (3, 4, 5),
        "VIRTUAL_CHASE_AWAITING_STATUSES": {"AWAITING_DASHBOARD_CHASE"},
        "_signal_ai_win_prob": lambda *_args: 0,
        "logger": type("Logger", (), {"info": lambda *_args, **_kwargs: None})(),
    }
    _compile_functions("evaluate_dashboard_execution_gate", namespace=namespace)
    gate = namespace["evaluate_dashboard_execution_gate"]

    family = {"research_lane": "FAMILY_ATR_TARGET_2_5", "age_bucket": 0}
    assert gate(family, {}, stage="promote") == (False, "CHASE_BUCKET_DEFER", True)

    waiting = {**family, "status": "AWAITING_DASHBOARD_CHASE"}
    assert gate(waiting, {}, stage="promote") == (False, "CHASE_BUCKET_WAIT", False)

    selected = {**family, "age_bucket": 3}
    assert gate(selected, {}, stage="promote") == (True, "OK", False)
    assert gate(selected, {}, stage="submit") == (True, "OK", False)


def test_settings_change_reconciles_existing_family_pending_orders():
    source = ast.get_source_segment(BOT_SOURCE, _function("enforce_dashboard_chase_gates_on_pending"))
    assert 'is_patient_chase_lane(order.get("research_lane"))' not in source
    assert "chase_age_window_should_cancel(age_sec)" in source
    assert "_cancel_pending_for_chase_gate(order)" in source


def test_disabled_early_bucket_cannot_leave_a_resting_order_fillable():
    buckets = {
        "0_chases": False,
        "1_chase": False,
        "2_chases": False,
        "3_chases": True,
        "4_chases": True,
        "5+_chases": True,
    }

    def chase_count_bucket(n):
        n = min(max(int(n or 0), 0), 5)
        return "5+_chases" if n >= 5 else ("1_chase" if n == 1 else f"{n}_chases")

    namespace = {
        "Optional": __import__("typing").Optional,
        "CHASE_WINDOW_SEC": 300,
        "CHASE_WINDOW_MAX_INDEX": 5,
        "CHASE_EXECUTION_BUCKET_ORDER": tuple(buckets),
        "get_chase_execution_buckets": lambda: buckets,
        "chase_count_bucket": chase_count_bucket,
    }
    _compile_functions(
        "chase_age_window_index",
        "last_enabled_chase_count",
        "min_enabled_chase_count",
        "chase_bucket_allowed",
        "chase_age_window_should_cancel",
        namespace=namespace,
    )
    should_cancel = namespace["chase_age_window_should_cancel"]
    assert should_cancel(0) is True
    assert should_cancel(14 * 60) is True
    assert should_cancel(15 * 60) is False
    assert should_cancel(20 * 60) is False
    assert should_cancel(27 * 60) is False


def test_existing_family_order_is_pulled_into_nonterminal_virtual_wait():
    signal = {
        "trade_id": "fat-existing",
        "research_lane": "FAMILY_ATR_TARGET_2_5",
        "status": "PENDING",
        "order_placed": True,
        "created_ts_ts": 100.0,
        "expires_ts": 10_000.0,
    }
    events = []
    expired = []
    namespace = {
        "trades_map": {"fat-existing": {"signal_ref": signal}},
        "time": type("Clock", (), {"time": staticmethod(lambda: 500.0)})(),
        "_order_signal_age_sec": lambda *_args: 400.0,
        "chase_age_window_index": lambda _age: 1,
        "_cancel_pending_order_confirmed": lambda *_args, **_kwargs: {"finalized": True},
        "_emit_genome_execution_event": lambda name, payload: events.append((name, payload)),
        "_next_enabled_chase_after": lambda count: 3 if count < 3 else None,
        "get_chase_execution_buckets": lambda: {"3_chases": True},
        "_signal_virtual_chase_count": lambda row: int(row.get("dashboard_virtual_chase_count") or 0),
        "SIGNAL_TTL_SEC": 1800,
        "SIGNAL_STATUS_AWAITING_DASHBOARD_CHASE": "AWAITING_DASHBOARD_CHASE",
        "_record_expired_order": lambda *args: expired.append(args),
        "pipeline_state_sync": lambda: None,
        "logger": type(
            "Logger",
            (),
            {
                "info": lambda *_args, **_kwargs: None,
                "critical": lambda *_args, **_kwargs: None,
            },
        )(),
    }
    _compile_functions("_cancel_pending_for_chase_gate", namespace=namespace)

    assert namespace["_cancel_pending_for_chase_gate"](
        {"trade_id": "fat-existing", "research_lane": "FAMILY_ATR_TARGET_2_5"}
    ) is True
    assert signal["status"] == "AWAITING_DASHBOARD_CHASE"
    assert signal["order_placed"] is False
    assert signal["dashboard_virtual_chase_count"] == 1
    assert signal["outcome"] == "PENDING"
    assert signal["exit_reason"] is None
    assert expired == []
    assert events[0][0] == "ORDER_CANCELLED"


def test_family_offset_policy_remains_separate_from_global_chase_timing_gate():
    placement = ast.get_source_segment(BOT_SOURCE, _function("_place_simulated_limit_order"))
    assert "registered_offset_policy = is_patient_chase_lane(lane)" in placement
    assert "dashboard_exact_chase_managed = not registered_offset_policy" in placement
    gate = ast.get_source_segment(BOT_SOURCE, _function("evaluate_dashboard_execution_gate"))
    assert "registered_family = is_patient_chase_lane(lane)" in gate
    assert "if not registered_family:" in gate
    assert 'if not any(get_chase_execution_buckets().values()):' in gate


def test_family_reprice_directly_obeys_global_age_window_gate():
    chase = ast.get_source_segment(BOT_SOURCE, _function("_apply_family_policy_chase"))
    assert "if not chase_age_window_may_reprice(age_sec):" in chase
    assert chase.index("if not chase_age_window_may_reprice(age_sec):") < chase.index(
        "_compute_limit_chase_target("
    )


def test_last_enabled_window_holds_resting_family_limit_without_reprice():
    buckets = {
        "0_chases": False,
        "1_chase": False,
        "2_chases": False,
        "3_chases": True,
        "4_chases": True,
        "5+_chases": True,
    }

    def chase_count_bucket(n):
        n = min(max(int(n or 0), 0), 5)
        return "5+_chases" if n >= 5 else ("1_chase" if n == 1 else f"{n}_chases")

    namespace = {
        "Optional": __import__("typing").Optional,
        "CHASE_WINDOW_SEC": 300,
        "CHASE_WINDOW_MAX_INDEX": 5,
        "CHASE_EXECUTION_BUCKET_ORDER": tuple(buckets),
        "get_chase_execution_buckets": lambda: buckets,
        "chase_count_bucket": chase_count_bucket,
    }
    _compile_functions(
        "chase_age_window_index",
        "last_enabled_chase_count",
        "chase_bucket_allowed",
        "chase_age_window_may_reprice",
        namespace=namespace,
    )
    may_reprice = namespace["chase_age_window_may_reprice"]
    assert may_reprice(14 * 60) is False
    assert may_reprice(15 * 60) is True
    assert may_reprice(29 * 60) is True
    assert may_reprice(31 * 60) is False


def test_tile_api_and_dashboard_distinguish_global_submit_from_template_reprice():
    annotate = ast.get_source_segment(BOT_SOURCE, _function("_annotate_lanes_with_exec_mode"))
    assert 'spec["chase_timing"]' in annotate
    assert '"global_submit_buckets": selected' in annotate
    assert '"template_reprice_buckets": template' in annotate
    assert "first appear only inside a globally selected bucket" in annotate
    assert "Effective order timing:" in BOT_SOURCE
    assert "global submit buckets" in BOT_SOURCE
    assert "tile reprice template" in BOT_SOURCE
    assert "Global submit windows" in BOT_SOURCE
    assert "Reprice template" in BOT_SOURCE


def test_compact_family_tile_chip_names_reprice_template_not_chase():
    common_source = Path(__file__).with_name("family_policy_common.py").read_text(encoding="utf-8")
    tree = ast.parse(common_source)
    dashboard_policy = ast.get_source_segment(
        common_source,
        next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "dashboard_policy"
        ),
    )
    assert 'f"Reprice template {' in dashboard_policy
    assert 'f"Chase {' not in dashboard_policy
    assert "The tile Reprice template label is its post-submit schedule" in BOT_SOURCE
