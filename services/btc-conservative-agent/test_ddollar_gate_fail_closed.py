"""DDollar gate errors fail closed, are counted, and never touch paper lanes."""
import ast
import logging
import sys
import types
from pathlib import Path

import pytest

import bitfinex_live_executor as bx

BOT = Path(__file__).with_name("bot.py")


@pytest.fixture(autouse=True)
def isolated_gate(monkeypatch):
    monkeypatch.setattr(bx, "_DDOLLAR_CACHE", {"ts": 0.0, "balance": None, "reason": ""})
    monkeypatch.setattr(bx, "_DDOLLAR_ERRORS", {"errors": 0, "last_error_ts": 0.0, "last_error": None})
    monkeypatch.setattr(bx, "_persist", lambda: None)


def test_fetch_exception_blocks_and_counts(monkeypatch):
    monkeypatch.setenv("DDOLLAR_GATE_URL", "https://ddollar.invalid/balance")

    def boom(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(bx._urllib_request, "urlopen", boom)
    allowed, reason = bx._ddollar_gate_ok_for_entry()
    assert allowed is False and "DDollar fetch failed" in reason
    status = bx.ddollar_gate_status()
    assert status["passed"] is False
    assert status["errors"] == 1 and status["last_error_ts"]
    assert "OSError" in status["last_error"]


def test_evaluation_exception_returns_ddollar_gate_error(monkeypatch):
    def broken():
        raise KeyError("balance")

    monkeypatch.setattr(bx, "ddollar_gate_status", broken)
    allowed, reason = bx._ddollar_gate_ok_for_entry()
    assert allowed is False
    assert reason.startswith(bx.DDOLLAR_GATE_ERROR)
    assert bx._DDOLLAR_ERRORS["errors"] == 1


def test_market_entry_not_submitted_when_gate_errors(monkeypatch):
    monkeypatch.setattr(bx, "ddollar_gate_status", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    calls = []
    result = bx.submit_market_entry(object(), lambda *a, **k: calls.append(a), "tBTCF0:USTF0",
                                    "LONG", 0.0002, 100, "t1")
    assert result is None and calls == []


def load_block_reason(mode, monkeypatch, executor):
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    names = {"_note_ddollar_gate_error", "lane_execution_block_reason"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    monkeypatch.setitem(sys.modules, "bitfinex_live_executor", executor)
    ns = {
        "logger": logging.getLogger("test_ddollar"),
        "EXEC_MODE_PAPER": "PAPER", "EXEC_MODE_LIVE": "LIVE",
        "EXEC_MODE_EXIT_ONLY": "EXIT_ONLY",
        "execution_mode_for_lane": lambda lane: mode,
        "_private_api_keys_ok": lambda: True,
    }
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "bot.py", "exec"), ns)
    return ns["lane_execution_block_reason"]


def raising_executor(counter):
    def gate():
        raise RuntimeError("gate exploded")

    return types.SimpleNamespace(
        _ddollar_gate_ok_for_entry=gate,
        record_ddollar_gate_error=lambda exc: counter.append(type(exc).__name__),
    )


def test_bot_live_lane_gate_exception_fails_closed(monkeypatch):
    counter = []
    reason = load_block_reason("LIVE", monkeypatch, raising_executor(counter))("ANY_LANE")
    assert reason == "DDOLLAR_GATE_BLOCKED (DDOLLAR_GATE_ERROR)"
    assert counter == ["RuntimeError"]


def test_paper_lane_never_consults_ddollar(monkeypatch):
    counter = []
    reason = load_block_reason("PAPER", monkeypatch, raising_executor(counter))("ANY_LANE")
    assert reason is None and counter == []
