"""Execute the actual entry gate without starting the production bot."""
import ast
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
import pytest


def gate(state, status):
    tree = ast.parse(Path(__file__).with_name("bot.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "execution_allowed")
    env = dict(state=state, state_lock=RLock(),
        manual_admin_pause_active=lambda: False, risk_trading_allowed=lambda: True,
        get_active_signal_count=lambda: 0, get_effective_max_active_signals=lambda: 10,
        get_execution_status=lambda: status, logger=SimpleNamespace(warning=lambda *a: None))
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), env)
    return env["execution_allowed"]()


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
