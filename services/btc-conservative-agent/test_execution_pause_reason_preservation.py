"""Execute the actual entry gate without starting the production bot."""
import ast
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
import pytest


def gate(state, status, *, lane=None, scan=False, enabled=True, active=0):
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "execution_allowed")
    env = dict(state=state, state_lock=RLock(),
        manual_admin_pause_active=lambda: False, risk_trading_allowed=lambda: True,
        is_ai_scan_lane=lambda _: scan, lane_orders_allowed=lambda _: enabled,
        research_isolation_enabled=lambda: False,
        get_active_signal_count=lambda: active, get_effective_max_active_signals=lambda: 10,
        get_execution_status=lambda: status, logger=SimpleNamespace(warning=lambda *a: None))
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), env)
    return env["execution_allowed"](lane)


@pytest.mark.parametrize("options,expected", [
    ({"lane": "scan", "scan": True}, "AI_SCAN_NO_ORDERS"),
    ({"lane": "disabled", "enabled": False}, "LANE_DISABLED"),
    ({"active": 10}, "MAX_ACTIVE_SIGNALS"),
])
@pytest.mark.parametrize("paused", [True, False])
def test_early_entry_rejections_preserve_pause_cause(options, expected, paused):
    state = {"execution_paused": paused, "execution_reason": "STALE_DATA_HARD_STOP"}
    assert gate(state, "ACTIVE", **options) is False
    assert state["entry_gate_status"] == expected
    assert state["execution_reason"] == ("STALE_DATA_HARD_STOP" if paused else expected)


@pytest.mark.parametrize("reason", ["STALE_DATA_HARD_STOP", "DAILY_DRAWDOWN", "BLOCKED", "THREAD_CRASH"])
def test_existing_pause_cause_survives_generic_entry_status(reason):
    state = {"execution_paused": True, "execution_reason": reason, "_pause_priority": 99}
    assert gate(state, "BLOCKED") is False
    assert state["execution_reason"] == reason
    assert state["_pause_priority"] == 99
    assert state["entry_gate_status"] == "BLOCKED"


def test_unpaused_readiness_failure_still_reports_blocked():
    state = {"execution_paused": False, "execution_reason": "ALLOWED"}
    assert gate(state, "BLOCKED") is False
    assert state["execution_reason"] == "BLOCKED"


def test_concurrent_pause_cannot_be_overwritten_by_active_snapshot():
    state = {"execution_paused": True, "execution_reason": "DAILY_DRAWDOWN"}
    assert gate(state, "ACTIVE") is False
    assert state["execution_reason"] == "DAILY_DRAWDOWN"


def test_unpaused_active_gate_still_allows():
    state = {"execution_paused": False}
    assert gate(state, "ACTIVE") is True
    assert state["execution_reason"] == "ALLOWED"


@pytest.mark.parametrize("reason,cleared", [
    ("WS_STALE", True), ("STALE_DATA_HARD_STOP", True),
    ("PRICE_STALE_OR_MISSING", True), ("DAILY_DRAWDOWN", False),
    ("BLOCKED", False), ("ADMIN_MANUAL", False),
])
def test_entry_rejection_then_actual_health_recovery(reason, cleared):
    state = {"execution_paused": True, "execution_reason": reason,
             "manual_admin_pause": reason == "ADMIN_MANUAL", "_pause_priority": 99}
    # The generic rejection must not erase the cause needed by recovery.
    assert gate(state, "BLOCKED") is False
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    names = {"system_health_check", "_clear_execution_pause_if_reason"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    env = dict(state=state, state_lock=RLock(), time=SimpleNamespace(time=lambda: 100),
        _WS_RECOVERABLE_PAUSE_REASONS={"WS_STALE", "STALE_DATA_HARD_STOP", "PRICE_STALE_OR_MISSING"},
        _recompute_system_readiness=lambda _: {"system_ready": True},
        _market_data_health_snapshot=lambda _: {"market_data_ready": True, "market_data_mode": "WS"},
        logger=SimpleNamespace(info=lambda *a: None, warning=lambda *a: None))
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot.py", "exec"), env)
    assert env["system_health_check"]() is True
    assert state["execution_paused"] is (not cleared)
    assert state["execution_reason"] == ("" if cleared else reason)
    assert state["_pause_priority"] == (0 if cleared else 99)
