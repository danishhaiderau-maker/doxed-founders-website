"""Regression tests for /api/resume clearing DAILY_DRAWDOWN when system_ready=False.

Background: ``/api/resume`` used to hard-block on ``system_ready=true``. But
``system_ready`` can't stabilize while the bot is paused (the pipeline that
ticks readiness components doesn't run), so once DAILY_DRAWDOWN tripped, the
bot could not be resumed via API without a process restart — a deadlock.

The fix lets ``/api/resume`` succeed when system_ready is false AS LONG AS:
  - the WS transport is genuinely healthy (``ws_transport_ready=True``)
  - the active pause reason is in the resumable set (DAILY_DRAWDOWN,
    LOSS_STREAK, ADMIN_MANUAL, MANUAL)

All other safety gates stay enforced. These tests pin that contract so the
deadlock Danish hit on 2026-08-04 cannot silently come back.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    ok = bool(condition)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" ({detail})" if detail and not ok else ""))
    if ok:
        passed += 1
    else:
        failed += 1


# Stub out persistence + live disarm side-effects so the handler can run in tests.
bot.save_persistent_config = lambda: None
bot._disarm_live_control = lambda reason="TEST": {"cancel": {"failed": [], "ok": []}, "exit_only": {}}
bot.pipeline_state_sync = lambda: None
# Allow /api/* handlers to run during tests (the bootstrap guard otherwise 503s).
bot._DASHBOARD_BOOTSTRAP_COMPLETE = True
# Direct loopback control is always allowed without a token; we explicitly test
# the proxied/forwarded case in [5] below with a real token.


def reset_pause_state():
    with bot.state_lock:
        bot.state["execution_paused"] = False
        bot.state["execution_reason"] = ""
        bot.state["_pause_priority"] = 0
        bot.state["manual_admin_pause"] = False


def _stub_runtime(system_ready, ws_transport_ready, reasons=None):
    """Pin _recompute_system_readiness to a known-shape dict.

    Matches the real return shape of _runtime_readiness_components so the
    handler reads the same fields it would in production.
    """
    payload = {
        "system_ready": system_ready,
        "ws_transport_ready": ws_transport_ready,
        "readiness_reasons": reasons if reasons is not None else [],
    }
    bot._recompute_system_readiness = lambda: payload


print("=" * 72)
print("/api/resume DAILY_DRAWDOWN deadlock workaround regression tests")
print("=" * 72)


# ---------------------------------------------------------------------------
# [1] Resume succeeds when system_ready=False but WS healthy and reason=DAILY_DRAWDOWN
# ---------------------------------------------------------------------------
print("\n[1] Resume succeeds: system_ready=False, ws=True, reason=DAILY_DRAWDOWN")
reset_pause_state()
bot.set_execution_paused("DAILY_DRAWDOWN")
check("precondition: paused for DAILY_DRAWDOWN", bot.state.get("execution_reason") == "DAILY_DRAWDOWN")
_stub_runtime(system_ready=False, ws_transport_ready=True, reasons=["READINESS_STABILIZING"])
with bot.app.test_client() as client:
    resp = client.post("/api/resume", environ_base={"REMOTE_ADDR": "127.0.0.1"})
check(
    "resume returns 200 (deadlock cleared)",
    resp.status_code == 200,
    detail=f"status={resp.status_code}",
)
body = resp.get_json() or {}
check("response status is 'resumed'", body.get("status") == "resumed")
check("execution_paused cleared in state", bot.state.get("execution_paused") is False)
check("execution_reason cleared", bot.state.get("execution_reason") == "")


# ---------------------------------------------------------------------------
# [2] Resume still fails when WS unhealthy (real market-data issue)
# ---------------------------------------------------------------------------
print("\n[2] Resume still fails when WS unhealthy (regardless of pause reason)")
reset_pause_state()
bot.set_execution_paused("DAILY_DRAWDOWN")
_stub_runtime(system_ready=False, ws_transport_ready=False, reasons=["WS_NOT_READY"])
with bot.app.test_client() as client:
    resp = client.post("/api/resume", environ_base={"REMOTE_ADDR": "127.0.0.1"})
check(
    "resume returns 409 when WS unhealthy",
    resp.status_code == 409,
    detail=f"status={resp.status_code}",
)
body = resp.get_json() or {}
check("response status is 'resume_blocked'", body.get("status") == "resume_blocked")
check("reason surfaces the WS issue", body.get("reason") == "WS_NOT_READY")
check("bot stays paused", bot.state.get("execution_paused") is True)
check("DAILY_DRAWDOWN reason preserved", bot.state.get("execution_reason") == "DAILY_DRAWDOWN")


# ---------------------------------------------------------------------------
# [3] Resume succeeds when system_ready=False, ws=True, reason=ADMIN_MANUAL
#     (manual pause is also in the resumable set)
# ---------------------------------------------------------------------------
print("\n[3] Resume succeeds for ADMIN_MANUAL pause under same conditions")
reset_pause_state()
with bot.state_lock:
    bot.state["manual_admin_pause"] = True
bot.set_execution_paused("ADMIN_MANUAL")
check("precondition: paused for ADMIN_MANUAL", bot.state.get("execution_reason") == "ADMIN_MANUAL")
_stub_runtime(system_ready=False, ws_transport_ready=True, reasons=["READINESS_STABILIZING"])
with bot.app.test_client() as client:
    resp = client.post("/api/resume", environ_base={"REMOTE_ADDR": "127.0.0.1"})
check(
    "resume returns 200 for ADMIN_MANUAL with healthy WS",
    resp.status_code == 200,
    detail=f"status={resp.status_code}",
)
check("manual_admin_pause flag cleared", bot.state.get("manual_admin_pause") is False)
check("execution_paused cleared", bot.state.get("execution_paused") is False)


# ---------------------------------------------------------------------------
# [4] Resume still fails for non-resumable reasons even with healthy WS
#     (e.g. STALE_DATA_HARD_STOP — that's a real market-data incident, not a
#     deadlock workaround case)
# ---------------------------------------------------------------------------
print("\n[4] Resume still fails for non-resumable pause (STALE_DATA_HARD_STOP)")
reset_pause_state()
bot.set_execution_paused("STALE_DATA_HARD_STOP")
check(
    "precondition: paused for STALE_DATA_HARD_STOP",
    bot.state.get("execution_reason") == "STALE_DATA_HARD_STOP",
)
_stub_runtime(system_ready=False, ws_transport_ready=True, reasons=["READINESS_STABILIZING"])
with bot.app.test_client() as client:
    resp = client.post("/api/resume", environ_base={"REMOTE_ADDR": "127.0.0.1"})
check(
    "resume returns 409 for non-resumable reason",
    resp.status_code == 409,
    detail=f"status={resp.status_code}",
)
check("bot stays paused", bot.state.get("execution_paused") is True)
check("pause reason preserved", bot.state.get("execution_reason") == "STALE_DATA_HARD_STOP")


# ---------------------------------------------------------------------------
# [5] Proxied resume still requires admin token (auth gate intact)
# ---------------------------------------------------------------------------
print("\n[5] Proxied /api/resume still requires admin token (auth intact)")
reset_pause_state()
bot.set_execution_paused("DAILY_DRAWDOWN")
original_token = bot._BOT_ADMIN_TOKEN
original_bootstrap = bot._DASHBOARD_BOOTSTRAP_COMPLETE
bot._BOT_ADMIN_TOKEN = "required-test-token"
bot._DASHBOARD_BOOTSTRAP_COMPLETE = True
try:
    _stub_runtime(system_ready=False, ws_transport_ready=True, reasons=["READINESS_STABILIZING"])
    with bot.app.test_client() as client:
        resp = client.post(
            "/api/resume",
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
            headers={"X-Forwarded-For": "203.0.113.10"},
        )
    check(
        "proxied resume without token returns 401",
        resp.status_code == 401,
        detail=f"status={resp.status_code}",
    )
    check("pause stays active after auth rejection", bot.state.get("execution_paused") is True)
finally:
    bot._BOT_ADMIN_TOKEN = original_token
    bot._DASHBOARD_BOOTSTRAP_COMPLETE = original_bootstrap


# ---------------------------------------------------------------------------
# [6] system_ready=True path still works (the classic happy path is intact)
# ---------------------------------------------------------------------------
print("\n[6] system_ready=True still resumes (happy path intact)")
reset_pause_state()
bot.set_execution_paused("DAILY_DRAWDOWN")
_stub_runtime(system_ready=True, ws_transport_ready=True, reasons=[])
with bot.app.test_client() as client:
    resp = client.post("/api/resume", environ_base={"REMOTE_ADDR": "127.0.0.1"})
check(
    "resume returns 200 when system_ready=True",
    resp.status_code == 200,
    detail=f"status={resp.status_code}",
)
check("execution_paused cleared", bot.state.get("execution_paused") is False)


print("\n" + "=" * 72)
print(f"PASS={passed} FAIL={failed}")
print("=" * 72)
if failed:
    sys.exit(1)
