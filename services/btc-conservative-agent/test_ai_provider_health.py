"""AI liveness is proven by successful model responses, never by attempts.

Regression for the 2026-10-01 DeepSeek outage: every trading_direction call
timed out from ~18:56Z, yet /api/status kept ai_age ~150 s and guarded deploys
passed "2 advancing AI cycles" because both counted attempts/cycle completions.
"""

import ast
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

BOT_PATH = Path(__file__).with_name("bot.py")
TREE = ast.parse(BOT_PATH.read_text(encoding="utf-8"), filename=str(BOT_PATH))
FUNCTIONS = {
    "classify_ai_provider_error",
    "record_ai_provider_outcome",
    "_epoch_iso",
    "ai_provider_health_snapshot",
    "call_deepseek_api",
}
ASSIGNS = {
    "AI_NO_SUCCESS_ALERT_SEC",
    "AI_PROVIDER_HEALTH_PURPOSES",
    "AI_PROVIDER_NOT_ATTEMPTED_PREFIXES",
    "_ai_provider_health_lock",
    "_ai_provider_health",
}


def _assign_name(node):
    if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
        return node.targets[0].id
    return None


def compile_provider(unrecorded):
    nodes = [n for n in TREE.body if _assign_name(n) in ASSIGNS]
    nodes += [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    assert {_assign_name(n) for n in nodes if _assign_name(n)} == ASSIGNS
    namespace = {
        "os": os, "threading": threading, "time": time,
        "datetime": datetime, "timezone": timezone,
        "_call_deepseek_api_unrecorded": unrecorded,
    }
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace


def _timeout(*_a, **_k):
    raise RuntimeError(
        "HTTP_ERROR:HTTPSConnectionPool(host='api.deepseek.com', port=443): Read timed out."
    )


def test_timed_out_calls_never_count_as_ai_progress():
    ns = compile_provider(_timeout)
    for _ in range(3):
        with pytest.raises(RuntimeError, match="Read timed out"):
            ns["call_deepseek_api"]([], purpose="trading_direction")
    snap = ns["ai_provider_health_snapshot"]()
    assert snap["last_ai_success_at"] is None and snap["ai_success_age_sec"] is None
    assert snap["consecutive_failures"] == 3
    assert snap["last_error_class"] == "TIMEOUT"
    assert snap["last_ai_attempt_at"] is not None
    assert snap["alert"] is None  # not yet 10 minutes


def test_no_success_for_more_than_ten_minutes_alerts_then_success_clears():
    ns = compile_provider(_timeout)
    record = ns["record_ai_provider_outcome"]
    t0 = 1_790_880_960.0  # 2026-10-01T18:56:00Z last success
    record("trading_direction", ok=True, now=t0, latency_ms=1163, model_echo="deepseek-flash")
    for minute in (38, 42, 44):
        record("trading_direction", ok=False, now=t0 + minute * 60, error=RuntimeError("HTTP_ERROR:Read timed out"))
    snap = ns["ai_provider_health_snapshot"](t0 + 9 * 60)
    assert snap["alert"] is None
    snap = ns["ai_provider_health_snapshot"](t0 + 45 * 60)
    assert snap["alert"] == "AI_NO_SUCCESS_10M"
    assert snap["last_ai_success_at"].startswith("2026-10-01T18:56:00")
    assert snap["consecutive_failures"] == 3
    assert snap["ai_success_age_sec"] == pytest.approx(45 * 60)
    record("trading_direction", ok=True, now=t0 + 158 * 60, latency_ms=1900, model_echo="deepseek-flash")
    snap = ns["ai_provider_health_snapshot"](t0 + 159 * 60)
    assert snap["alert"] is None and snap["consecutive_failures"] == 0
    assert snap["last_model_echo"] == "deepseek-flash"
    assert snap["successes_since_boot"] == 2 and snap["failures_since_boot"] == 3


def test_success_records_model_echo_and_returns_text_latency_pair():
    ns = compile_provider(lambda *a, **k: ("{}", 1200, "deepseek-flash"))
    assert ns["call_deepseek_api"]([], purpose="trading_direction") == ("{}", 1200)
    snap = ns["ai_provider_health_snapshot"]()
    assert snap["successes_since_boot"] == 1 and snap["last_latency_ms"] == 1200
    assert snap["last_model_echo"] == "deepseek-flash"


def test_shadow_and_local_refusals_do_not_touch_primary_provider_truth():
    ns = compile_provider(_timeout)
    with pytest.raises(RuntimeError):
        ns["call_deepseek_api"]([], purpose="trading_direction_shadow")
    paused = compile_provider(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ADMIN_MANUAL_PAUSE")))
    with pytest.raises(RuntimeError):
        paused["call_deepseek_api"]([], purpose="trading_direction")
    for namespace in (ns, paused):
        snap = namespace["ai_provider_health_snapshot"]()
        assert snap["consecutive_failures"] == 0 and snap["last_ai_attempt_at"] is None


@pytest.mark.parametrize("status,expected", [
    (401, "AUTH"), (402, "INSUFFICIENT_BALANCE"), (429, "RATE_LIMIT"), (503, "PROVIDER_5XX"), (400, "HTTP_4XX"),
])
def test_error_classes_are_bounded_and_never_carry_provider_bodies(status, expected):
    ns = compile_provider(_timeout)
    exc = RuntimeError(f"HTTP_{status}:secret-ish body")
    exc.http_status = status
    assert ns["classify_ai_provider_error"](exc) == expected
    assert ns["classify_ai_provider_error"](RuntimeError("INVALID_DEEPSEEK_MODEL:x")) == "MODEL_CONFIG"
    assert ns["classify_ai_provider_error"](RuntimeError("EMPTY_CONTENT")) == "BAD_RESPONSE"
    assert ns["classify_ai_provider_error"](RuntimeError("MISSING_API_KEY")) == "MISSING_API_KEY"


def test_real_requests_timeout_message_classifies_as_timeout():
    ns = compile_provider(_timeout)
    message = f"HTTP_ERROR:{requests.exceptions.ReadTimeout('Read timed out. (read timeout=60)')}"
    assert ns["classify_ai_provider_error"](RuntimeError(message)) == "TIMEOUT"


def test_status_and_dashboard_expose_success_truth():
    source = BOT_PATH.read_text(encoding="utf-8")
    status = source[source.index("@app.route('/api/status')\n@app.route('/status')\ndef status():"):]
    status = status[: status.index("\n@app.route", 60)]
    for key in ('"last_ai_success_at"', '"ai_consecutive_failures"', '"ai_alert"', '"ai_provider_health"'):
        assert key in status, key
    snapshot = source[source.index("def _strategy_progress_health_snapshot("):]
    snapshot = snapshot[: snapshot.index("\ndef ", 10)]
    assert '"ai_age_sec": ai_provider["ai_success_age_sec"]' in snapshot
    assert "and not ai_provider_failing" in snapshot
    assert "aiSuccessBanner" in source
