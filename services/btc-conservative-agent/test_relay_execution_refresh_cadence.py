"""Regression contract for bounded Fly canonical-snapshot CPU demand."""

from __future__ import annotations

import ast
from pathlib import Path


SOURCE_PATH = Path(__file__).with_name("bot.py")
SOURCE = SOURCE_PATH.read_text(encoding="utf-8-sig")
TREE = ast.parse(SOURCE)


def _assignment(name: str) -> ast.AST:
    for node in TREE.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return node.value
    raise AssertionError(f"missing assignment: {name}")


def _function(name: str) -> ast.FunctionDef:
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function: {name}")


def test_relay_execution_periodic_fallback_cannot_be_configured_subsecond() -> None:
    value = _assignment("_RELAY_EXECUTION_REFRESH_INTERVAL_SEC")
    assert isinstance(value, ast.Call)
    assert isinstance(value.func, ast.Name) and value.func.id == "max"
    assert isinstance(value.args[0], ast.Constant)
    assert float(value.args[0].value) >= 1.0

    env_call = value.args[1]
    assert isinstance(env_call, ast.Call)
    getenv_call = env_call.args[0]
    assert isinstance(getenv_call, ast.Call)
    assert isinstance(getenv_call.func, ast.Attribute)
    assert getenv_call.func.attr == "getenv"
    assert getenv_call.args[0].value == "RELAY_EXECUTION_REFRESH_INTERVAL_SEC"
    assert float(getenv_call.args[1].value) >= 1.0


def test_relay_execution_staleness_remains_fail_closed_and_loop_is_serial() -> None:
    max_stale = ast.get_source_segment(
        SOURCE, _assignment("_RELAY_EXECUTION_MAX_STALE_SEC")
    )
    route = ast.get_source_segment(SOURCE, _function("api_relay_execution_state"))
    loop = ast.get_source_segment(
        SOURCE, _function("_relay_execution_cache_refresher_loop")
    )

    assert 'float(os.getenv("RELAY_EXECUTION_MAX_STALE_SEC", "4.0"))' in max_stale
    assert "age > _RELAY_EXECUTION_MAX_STALE_SEC" in route
    assert "response.status_code = 503" in route
    assert loop.count("_publish_relay_execution_snapshot()") == 1
    assert "shutdown_event.wait(_RELAY_EXECUTION_REFRESH_INTERVAL_SEC)" in loop


def test_fresh_relay_lock_timeout_is_classified_retryable_not_http_500() -> None:
    route = ast.get_source_segment(SOURCE, _function("api_relay_execution_state"))

    assert "except TimeoutError" in route
    assert "published = None" in route
    assert "if published is None" in route
    assert "generation-matching execution snapshot is rebuilding" in route
    assert "), 503" in route
