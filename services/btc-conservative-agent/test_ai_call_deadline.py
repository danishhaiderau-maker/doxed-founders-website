"""A DeepSeek call can never block the AI loop past its total wall-clock deadline.

Regression for 2026-10-01 21:24-21:30Z: DeepSeek queued a request and kept the
connection alive with blank lines, so the 60 s idle timeout never fired and a
single trading_direction call took 354 s, swallowing two 180 s AI cycles.
"""

import ast
import http.server
import json
import os
import socket
import threading
import time
import types
from pathlib import Path

import pytest
import requests

BOT_PATH = Path(__file__).with_name("bot.py")
TREE = ast.parse(BOT_PATH.read_text(encoding="utf-8"), filename=str(BOT_PATH))
FUNCTIONS = {
    "_deepseek_post_with_deadline",
    "_abort_streaming_response",
    "classify_ai_provider_error",
    "build_ai_error_result",
}
ASSIGNS = {"AI_DEADLINE_MAX_ABANDONED", "_ai_deadline_lock", "_ai_deadline_state"}


def _assign_name(node):
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return None


@pytest.fixture()
def ns():
    nodes = [n for n in TREE.body if _assign_name(n) in ASSIGNS]
    nodes += [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    assert {n.name for n in nodes if isinstance(n, ast.FunctionDef)} == FUNCTIONS
    namespace = {
        "os": os, "threading": threading, "time": time, "socket": socket, "requests": requests,
        "_deepseek_config_receipt": lambda: ("deepseek-flash", "disabled"),
    }
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace


BODY = json.dumps({"model": "deepseek-flash", "system_fingerprint": "fp-test",
                   "choices": [{"message": {"content": "{\"direction\": \"NO_TRADE\"}"}}]}).encode()


class _Handler(http.server.BaseHTTPRequestHandler):
    mode = "fast"

    def log_message(self, *_args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.mode == "silent":
            time.sleep(8)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if self.mode == "fast":
            self.send_header("Content-Length", str(len(BODY)))
            self.end_headers()
            self.wfile.write(BODY)
            return
        self.end_headers()
        try:
            for _ in range(200):  # DeepSeek keep-alive while queued: blank lines
                self.wfile.write(b"\n")
                self.wfile.flush()
                time.sleep(0.1)
            self.wfile.write(BODY)
        except OSError:
            pass


@pytest.fixture()
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def _url(httpd):
    return f"http://127.0.0.1:{httpd.server_address[1]}/v1/chat/completions"


def _post(ns, httpd, deadline, idle=60):
    return ns["_deepseek_post_with_deadline"](
        _url(httpd), headers={"Content-Type": "application/json"},
        json_payload={"model": "deepseek-flash"}, idle_timeout=idle, deadline_sec=deadline,
    )


def _wait_no_abandoned(ns, limit=5.0):
    stop = time.monotonic() + limit
    while time.monotonic() < stop:
        if ns["_ai_deadline_state"]["abandoned_in_flight"] == 0:
            return True
        time.sleep(0.05)
    return False


def test_fast_response_returns_body_with_served_model(ns, server):
    _Handler.mode = "fast"
    status, body = _post(ns, server, deadline=5)
    assert status == 200
    payload = json.loads(body)
    assert payload["model"] == "deepseek-flash" and payload["system_fingerprint"] == "fp-test"
    assert ns["_ai_deadline_state"] == {"abandoned_in_flight": 0, "deadline_exceeded_total": 0}


def test_slow_keepalive_stream_is_cut_at_the_total_deadline(ns, server):
    """Blank lines every 100 ms would defeat a 60 s idle timeout for 20 s."""
    _Handler.mode = "trickle"
    started = time.monotonic()
    with pytest.raises(RuntimeError, match=r"^AI_DEADLINE_EXCEEDED:2s$") as info:
        _post(ns, server, deadline=1.5, idle=60)
    elapsed = time.monotonic() - started
    assert 1.4 <= elapsed < 2.5, elapsed
    assert info.value.latency_ms == 1500
    assert ns["classify_ai_provider_error"](info.value) == "DEADLINE"
    assert ns["_ai_deadline_state"]["deadline_exceeded_total"] == 1
    assert _wait_no_abandoned(ns), "cancelled worker thread did not exit"


def _requests_with_read_timeout_after(extra_sec):
    """Real requests whose socket read timeout lands well after the deadline.

    The worker clamps its read timeout to deadline_sec, so on Windows' coarse
    timer the socket timeout and the caller's deadline wait race; pushing the
    socket timeout out makes the total deadline the only thing that can cut.
    """
    shim = types.ModuleType("requests_read_timeout_shim")
    shim.__dict__.update(requests.__dict__)

    def post(*args, timeout, **kwargs):
        connect, read = timeout
        return requests.post(*args, timeout=(connect, read + extra_sec), **kwargs)

    shim.post = post
    return shim


def test_server_that_never_answers_is_cut_at_the_deadline(ns, server):
    _Handler.mode = "silent"
    ns["requests"] = _requests_with_read_timeout_after(5.0)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match=r"^AI_DEADLINE_EXCEEDED:1s$"):
        _post(ns, server, deadline=1.0, idle=60)
    assert time.monotonic() - started < 1.8
    assert ns["_ai_deadline_state"]["deadline_exceeded_total"] == 1


def test_abandoned_backlog_refuses_new_calls_immediately(ns, server):
    _Handler.mode = "fast"
    ns["_ai_deadline_state"]["abandoned_in_flight"] = ns["AI_DEADLINE_MAX_ABANDONED"]
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="AI_DEADLINE_EXCEEDED:backlog=2"):
        _post(ns, server, deadline=5)
    assert time.monotonic() - started < 0.2


def test_transport_errors_still_surface_as_requests_exceptions(ns):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(requests.RequestException):
        ns["_deepseek_post_with_deadline"](
            f"http://127.0.0.1:{port}/", headers={}, json_payload={}, idle_timeout=5, deadline_sec=3,
        )


def test_timed_out_call_becomes_neutral_no_trade_ai_failure(ns):
    exc = RuntimeError("AI_DEADLINE_EXCEEDED:75s")
    exc.latency_ms = 75000
    result = ns["build_ai_error_result"](exc, trade_id="call-1")
    assert result["direction"] == "NO_TRADE" and result["decision"] == "AI_ERROR"
    assert result["ai_error"] is True and result["approved"] is False
    assert result["ai_failure_class"] == "DEADLINE" and result["latency_ms"] == 75000
    assert result["deepseek_served_model"] is None and result["deepseek_model"] == "deepseek-flash"


def test_deadline_fits_inside_the_ai_cadence_and_wraps_the_real_call():
    source = BOT_PATH.read_text(encoding="utf-8")
    assert 'float(os.getenv("AI_CALL_DEADLINE_SEC", "75"))' in source
    call = source[source.index("def _call_deepseek_api_unrecorded("):]
    call = call[: call.index("\ndef ", 10)]
    assert "_deepseek_post_with_deadline(" in call and "requests.post(" not in call
    assert "deadline_sec=min(float(timeout or AI_CALL_DEADLINE_SEC), AI_CALL_DEADLINE_SEC)" in call
    assert '"system_fingerprint"' in call and '"served_model"' in call
