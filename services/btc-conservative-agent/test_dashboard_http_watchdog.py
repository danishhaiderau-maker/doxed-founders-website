"""Deterministic safety tests for the independent dashboard HTTP watchdog."""

import ast
import threading
from pathlib import Path
from types import SimpleNamespace


BOT_PATH = Path(__file__).with_name("bot.py")
SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE, filename=str(BOT_PATH))


def _function(name):
    return next(
        node
        for node in TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )


def _compile(name, namespace):
    module = ast.Module(body=[_function(name)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace[name]


class _FakeSocket:
    def __init__(self, response=b"HTTP/1.1 200 OK\r\n"):
        self.response = response
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def settimeout(self, _timeout):
        pass

    def sendall(self, payload):
        self.sent += payload

    def recv(self, _size):
        response, self.response = self.response, b""
        return response


def test_local_probe_uses_loopback_ping_and_accepts_only_http_200():
    fake = _FakeSocket()
    probe = _compile(
        "_probe_local_dashboard_control",
        {
            "socket": SimpleNamespace(
                create_connection=lambda address, timeout: fake,
            ),
            "DASHBOARD_PORT": 7002,
            "DASHBOARD_HTTP_WATCHDOG_TIMEOUT_SEC": 1.0,
            "max": max,
            "float": float,
            "OSError": OSError,
            "TimeoutError": TimeoutError,
        },
    )
    assert probe() == (True, "HTTP_200")
    assert fake.sent.startswith(b"GET /api/ping HTTP/1.1\r\n")

    unavailable = _FakeSocket(b"HTTP/1.1 503 Service Unavailable\r\n")
    probe.__globals__["socket"] = SimpleNamespace(
        create_connection=lambda address, timeout: unavailable,
    )
    healthy, detail = probe()
    assert healthy is False
    assert detail == "HTTP/1.1 503 Service Unavailable"


def _restart_gate(*, force_paper=True, live=False, positions=0, pending=0, lock=True):
    class ProbeLock:
        def acquire(self, **_kwargs):
            return lock

        def release(self):
            pass

    gate = _compile(
        "_dashboard_http_restart_allowed",
        {
            "_force_paper_mode_active": lambda: force_paper,
            "trade_lock": ProbeLock(),
            "state": {"live_armed": live},
            "open_positions": [{}] * positions,
            "pending_orders": [{}] * pending,
            "max": max,
            "float": float,
            "bool": bool,
            "len": len,
        },
    )
    return gate()


def test_restart_gate_allows_only_force_paper_disarmed_flat_state():
    assert _restart_gate() is True
    assert _restart_gate(force_paper=False) is False
    assert _restart_gate(live=True) is False
    assert _restart_gate(positions=1) is False
    assert _restart_gate(pending=1) is False
    assert _restart_gate(lock=False) is False


def test_loop_has_conservative_grace_and_exit_is_guarded_by_safety_gate():
    loop = _function("dashboard_http_watchdog_loop")
    text = ast.get_source_segment(SOURCE, loop)
    assert "DASHBOARD_HTTP_WATCHDOG_STARTUP_GRACE_SEC" in text
    assert "DASHBOARD_HTTP_WATCHDOG_FAILURES_BEFORE_EXIT" in text
    gate_index = text.index("if _dashboard_http_restart_allowed():")
    exit_index = text.index("os._exit(75)")
    assert gate_index < exit_index
    assert "not _DASHBOARD_BOOTSTRAP_COMPLETE" in text


def test_watchdog_is_started_as_an_independent_daemon():
    main_text = ast.get_source_segment(SOURCE, _function("main"))
    assert "safe_thread(dashboard_http_watchdog_loop)" in main_text

