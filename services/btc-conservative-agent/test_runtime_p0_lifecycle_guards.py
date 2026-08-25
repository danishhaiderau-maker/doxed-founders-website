import ast
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(BOT_SOURCE)


def _function(name):
    return next(
        node
        for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def test_pending_fill_slow_evidence_is_outside_trade_lock():
    node = _function("process_pending_orders")
    for child in ast.walk(node):
        if not isinstance(child, (ast.With, ast.AsyncWith)):
            continue
        if not any("trade_lock" in ast.unparse(item.context_expr) for item in child.items):
            continue
        locked = ast.unparse(child)
        assert "_pending_limit_ready_for_fill" not in locked
        assert "_refresh_collector_v22_registered_order_evidence" not in locked
        assert "close_research_order_schedule" not in locked


def test_chase_research_persistence_is_outside_trade_lock():
    for name in ("_commit_relay_limit_chase",):
        node = _function(name)
        for child in ast.walk(node):
            if not isinstance(child, (ast.With, ast.AsyncWith)):
                continue
            if not any("trade_lock" in ast.unparse(item.context_expr) for item in child.items):
                continue
            locked = ast.unparse(child)
            assert "append_research_reprice_interval" not in locked
            assert "_refresh_collector_v22_registered_order_evidence" not in locked


def test_watchdog_never_self_restarts_with_pending_orders():
    body = ast.unparse(_function("watchdog_loop"))
    assert "progress['pending_orders'] == 0" in body


def test_terminal_execution_guards_both_collector_paths():
    sync = ast.unparse(_function("_sync_order_multiverse"))
    rejected = ast.unparse(_function("persist_rejected_opportunity"))
    assert "_execution_trade_is_terminal(tid)" in sync
    assert "source['status'] = 'CLOSED'" in sync
    assert "_execution_trade_is_terminal(tid)" in rejected
    assert "terminal execution suppresses stale rejected provisional" in rejected


def test_pending_registration_is_once_only_by_trade_identity():
    body = ast.unparse(_function("lane_register_pending_order"))
    assert "tid in handoff_ids" in body
    assert "for row in position_rows" in body
    assert "for row in trade_rows" in body
    assert "for row in expired_rows" in body
    assert "order['registration_suppressed_reason'] = 'RETIRED_LIFECYCLE'" in body


def test_health_and_session_expose_v31_with_explicit_legacy_writer():
    session = ast.unparse(_function("_write_research_session"))
    health = ast.unparse(_function("health"))
    for body in (session, health):
        assert "'collector_version': COLLECTOR_V31_VERSION" in body
        assert "'legacy_collector_version': COLLECTOR_V22_VERSION" in body
