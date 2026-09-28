"""Execute the real admission functions without booting a market-data worker."""
import ast
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)
NAMES = {
    "manual_admin_pause_active", "_finish_execution_admission",
    "evaluate_execution_admission", "execution_allowed",
    "execution_admission_snapshot", "_execution_control_fields_locked",
    "_clear_execution_pause_if_reason",
}


@pytest.fixture
def ns():
    state = {"execution_paused": False, "execution_reason": "",
             "_pause_priority": 0, "manual_admin_pause": False}
    env = {
        "state": state, "state_lock": threading.RLock(), "logger": Mock(),
        "time": SimpleNamespace(time=lambda: 123.0),
        "is_ai_scan_lane": lambda lane: lane == "AI_SCAN",
        "lane_orders_allowed": lambda lane: lane != "OFF",
        "risk_trading_allowed": lambda: True,
        "research_isolation_enabled": lambda: True,
        "get_active_signal_count": lambda: 0,
        "get_effective_max_active_signals": lambda: 20,
        "get_execution_status": lambda: "RESEARCH_ALLOW",
        "_WS_RECOVERABLE_PAUSE_REASONS": frozenset({
            "WS_STALE", "STALE_DATA_HARD_STOP", "PRICE_STALE_OR_MISSING"}),
    }
    module = ast.Module(body=[n for n in TREE.body
                             if isinstance(n, ast.FunctionDef) and n.name in NAMES],
                        type_ignores=[])
    exec(compile(module, "bot.py", "exec"), env)
    return env


def pause_tuple(state):
    return tuple(state.get(k) for k in
                 ("execution_paused", "execution_reason", "_pause_priority",
                  "manual_admin_pause"))


@pytest.mark.parametrize("cause,priority", [
    ("GENOME_IDENTITY_INVALID", 1000), ("SIMULATION_ONLY", 150),
    ("CSV_FAILURE", 100), ("THREAD_CRASH", 1), ("ADMIN_MANUAL", 200),
    ("UNKNOWN_LEGACY_PAUSE", 77), ("", 0),
])
@pytest.mark.parametrize("branch", ["scan", "lane", "risk", "capacity", "status", "allowed"])
def test_admission_never_replaces_pause_authority(ns, cause, priority, branch):
    ns["state"].update(execution_paused=True, execution_reason=cause,
                       _pause_priority=priority)
    before = pause_tuple(ns["state"])
    lane = "AI_SCAN" if branch == "scan" else "OFF" if branch == "lane" else "FAMILY"
    if branch == "risk":
        ns["risk_trading_allowed"] = lambda: False
    elif branch == "capacity":
        ns["get_active_signal_count"] = lambda: 20
    elif branch == "status":
        ns["get_execution_status"] = lambda: "BLOCKED"
    verdict = ns["evaluate_execution_admission"](lane)
    assert verdict == (False, cause or "EXECUTION_PAUSED")
    assert pause_tuple(ns["state"]) == before


def test_manual_flag_does_not_downgrade_stronger_identity_pause(ns):
    ns["state"].update(manual_admin_pause=True, execution_paused=True,
                       execution_reason="GENOME_IDENTITY_INVALID", _pause_priority=1000)
    before = pause_tuple(ns["state"])
    assert ns["evaluate_execution_admission"]() == (False, "GENOME_IDENTITY_INVALID")
    assert pause_tuple(ns["state"]) == before


def test_manual_flag_alone_blocks_without_fabricating_pause_transition(ns):
    ns["state"]["manual_admin_pause"] = True
    before = pause_tuple(ns["state"])
    assert ns["execution_allowed"]() is False
    assert ns["execution_admission_snapshot"]() == {"allowed": False, "reason": "ADMIN_MANUAL"}
    assert pause_tuple(ns["state"]) == before


def test_capacity_rejection_does_not_latch_pause(ns):
    ns["get_active_signal_count"] = lambda: 20
    before = pause_tuple(ns["state"])
    assert ns["evaluate_execution_admission"]("FAMILY") == (False, "MAX_ACTIVE_SIGNALS")
    assert pause_tuple(ns["state"]) == before
    ns["get_active_signal_count"] = lambda: 0
    assert ns["evaluate_execution_admission"]("FAMILY") == (True, "ALLOWED")
    assert pause_tuple(ns["state"]) == before


def test_pause_racing_readiness_wins_final_admission(ns):
    def readiness():
        ns["state"].update(execution_paused=True, execution_reason="CSV_FAILURE",
                           _pause_priority=100)
        return "RESEARCH_ALLOW"
    ns["get_execution_status"] = readiness
    assert ns["evaluate_execution_admission"]() == (False, "CSV_FAILURE")
    assert ns["state"]["_pause_priority"] == 100


def test_local_reason_does_not_change_when_other_lane_evaluates(ns):
    first = ns["evaluate_execution_admission"]("OFF")
    second = ns["evaluate_execution_admission"]("AI_SCAN")
    assert first == (False, "LANE_DISABLED")
    assert second == (False, "AI_SCAN_NO_ORDERS")
    assert ns["state"]["last_execution_admission"]["reason"] == "AI_SCAN_NO_ORDERS"
    assert first[1] == "LANE_DISABLED"


def test_control_snapshot_copies_complete_tuple_and_diagnostics(ns):
    ns["state"].update(execution_paused=True, execution_reason="CSV_FAILURE",
                       _pause_priority=100)
    ns["evaluate_execution_admission"]("FAMILY")
    with ns["state_lock"]:
        snap = ns["_execution_control_fields_locked"]()
    assert pause_tuple(snap) == pause_tuple(ns["state"])
    ns["state"]["last_execution_admission"]["reason"] = "later"
    assert snap["last_execution_admission"]["reason"] == "CSV_FAILURE"


def test_ws_recovery_cannot_clear_hidden_non_ws_pause(ns):
    ns["state"].update(execution_paused=True, execution_reason="CSV_FAILURE",
                       _pause_priority=100)
    ns["get_active_signal_count"] = lambda: 20
    ns["execution_allowed"]()
    assert ns["_clear_execution_pause_if_reason"]("WS_STALE") is False
    assert ns["_clear_execution_pause_if_reason"]("CSV_FAILURE") is False
    assert ns["state"]["execution_reason"] == "CSV_FAILURE"


def test_pipeline_and_debug_use_local_admission_result():
    assert "exec_allowed, exec_reason = evaluate_execution_admission(research_lane)" in SOURCE
    assert '"execution": execution_admission_snapshot()' in SOURCE
    for name in ("evaluate_execution_admission", "_finish_execution_admission"):
        node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
        calls = {n.func.id for n in ast.walk(node)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert not calls & {"set_execution_paused", "api_resume", "circuit_breaker_cancel_pending"}


def test_dashboard_overlay_includes_priority_and_admission():
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef)
                and n.name == "_api_state_cache_refresher_loop")
    names = {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert {"execution_paused", "execution_reason", "_pause_priority",
            "manual_admin_pause", "last_execution_admission"} <= names
