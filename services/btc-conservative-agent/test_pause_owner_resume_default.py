"""Resume-ON-by-default contract for paper execution across deploys/restarts.

Only a deliberate operator pause or a safety gate may keep paper paused after
a restart. A deploy-maintenance pause is cleared by the deploy's own resume,
and a deploy resume never ends an operator or safety pause.
"""

if __name__ != "__main__":
    import pytest
    pytest.skip("script-style suite; executed by test_script_style_suites.py",
                allow_module_level=True)

import json
import os
import sys
import tempfile

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


_original_save_persistent_config = bot.save_persistent_config
config_dir = tempfile.mkdtemp(prefix="pause-owner-config-")
config_path = os.path.join(config_dir, "config-7002.json")
bot.get_config_file = lambda: config_path
bot._resolve_config_file_for_load = lambda: config_path
bot._disarm_live_control = lambda reason="TEST": {"cancel": {"failed": [], "ok": []}, "exit_only": {}}
bot.pipeline_state_sync = lambda: None
bot._DASHBOARD_BOOTSTRAP_COMPLETE = True
bot._resume_active_reset_receipt_exists = lambda: False
bot._recompute_system_readiness = lambda: {
    "system_ready": True,
    "ws_transport_ready": True,
    "readiness_reasons": [],
}
LOOPBACK = {"REMOTE_ADDR": "127.0.0.1"}
DEPLOY = {"clear_admin_manual_pause": True, "owner": "DEPLOY_MAINTENANCE"}


def reset_state():
    with bot.state_lock:
        for key in bot._persistent_config_keys():
            bot.state.setdefault(key, None)
        bot.state["manual_admin_pause"] = False
        bot.state["pause_intent"] = None
        bot.state["execution_paused"] = False
        bot.state["execution_reason"] = ""
        bot.state["_pause_priority"] = 0
        lanes = dict(bot.state.get("research_lane_enabled") or {})
        for lane in bot.COMBO_EXECUTION_LANES:
            lanes[lane] = True
        bot.state["research_lane_enabled"] = lanes


def simulate_restart():
    """Persist, wipe in-memory control state, then boot from config."""
    _original_save_persistent_config()
    with bot.state_lock:
        bot.state["manual_admin_pause"] = False
        bot.state["pause_intent"] = None
        bot.state["execution_paused"] = False
        bot.state["execution_reason"] = ""
        bot.state["_pause_priority"] = 0
        bot.state["research_lane_enabled"] = {}
    bot.load_persistent_config()
    bot.reset_transient_runtime_state()


def owner():
    with bot.state_lock:
        return bot._pause_owner_locked()


print("=" * 72)
print("Pause ownership / resume-ON-by-default regression tests")
print("=" * 72)

print("\n[1] Restart with operator resume ON comes back active, tiles unchanged")
reset_state()
simulate_restart()
check("not paused after restart", bot.state.get("execution_paused") is False)
check("no pause owner after restart", owner() is None)
lanes = bot.state.get("research_lane_enabled") or {}
check(
    "all registry tiles still ON after restart",
    all(lanes.get(lane) is True for lane in bot.COMBO_EXECUTION_LANES),
    detail=str(lanes),
)

print("\n[2] Deploy maintenance pause is cleared by the deploy resume")
reset_state()
with bot.app.test_client() as client:
    paused = client.post("/api/pause", json={"owner": "DEPLOY_MAINTENANCE"}, environ_base=LOOPBACK)
check("deploy pause accepted", paused.status_code == 200, detail=str(paused.get_json()))
check("deploy pause owner recorded", (paused.get_json() or {}).get("pause_owner") == "DEPLOY_MAINTENANCE")
simulate_restart()
check("deploy pause survives the restart window", bot.state.get("execution_paused") is True)
check("owner survives the restart window", owner() == "DEPLOY_MAINTENANCE")
with bot.app.test_client() as client:
    resumed = client.post("/api/resume", json=DEPLOY, environ_base=LOOPBACK)
body = resumed.get_json() or {}
check("deploy resume returns resumed", body.get("status") == "resumed", detail=str(body))
check("paper active after deploy resume", bot.state.get("execution_paused") is False)
check("manual flag cleared", bot.state.get("manual_admin_pause") is False)
simulate_restart()
check("deploy pause does not persist past successful deploy", bot.state.get("execution_paused") is False)

print("\n[3] Operator pause stays paused through deploy pause + restart + deploy resume")
reset_state()
with bot.app.test_client() as client:
    client.post("/api/pause", environ_base=LOOPBACK)
    check("operator owns pause", owner() == "OPERATOR")
    redeploy = client.post("/api/pause", json={"owner": "DEPLOY_MAINTENANCE"}, environ_base=LOOPBACK)
check(
    "deploy pause does not downgrade operator pause",
    (redeploy.get_json() or {}).get("pause_owner") == "OPERATOR" and owner() == "OPERATOR",
)
simulate_restart()
check("operator pause survives restart", bot.state.get("execution_paused") is True and owner() == "OPERATOR")
with bot.app.test_client() as client:
    retained = client.post("/api/resume", json=DEPLOY, environ_base=LOOPBACK)
body = retained.get_json() or {}
check("deploy resume retains operator pause", body.get("status") == "operator_pause_retained", detail=str(body))
check("still paused", bot.state.get("execution_paused") is True)
with bot.app.test_client() as client:
    operator_resume = client.post("/api/resume", json={}, environ_base=LOOPBACK)
check(
    "platform bare resume ends operator pause",
    (operator_resume.get_json() or {}).get("status") == "resumed",
    detail=str(operator_resume.get_json()),
)
check("paper active after operator resume", bot.state.get("execution_paused") is False)

print("\n[4] Safety gate stays paused with its reason after deploy resume")
reset_state()
bot.set_execution_paused("DAILY_DRAWDOWN")
check("safety owns non-manual pause", owner() == "SAFETY")
check(
    "status surfaces owner and reason",
    bot._execution_control_fields_locked()["pause_owner"] == "SAFETY"
    and bot.state.get("execution_reason") == "DAILY_DRAWDOWN",
)
bot.set_live_copy_coordination_state(bot.COORD_STATE_FULLY_PAUSED, "test relay pause")
check("relay coordination pause owned by SAFETY", owner() == "SAFETY")
with bot.app.test_client() as client:
    retained = client.post("/api/resume", json=DEPLOY, environ_base=LOOPBACK)
body = retained.get_json() or {}
check("deploy resume retains safety pause", body.get("status") == "operator_pause_retained", detail=str(body))
check("safety pause owner reported", body.get("pause_owner") == "SAFETY")
check("still paused under safety", bot.state.get("execution_paused") is True)
bot.set_live_copy_coordination_state(bot.COORD_STATE_RUNNING_TOGETHER, "")

print("\n[5] Legacy unattributed manual pause may be cleared by deploy tooling")
reset_state()
with bot.state_lock:
    bot.state["manual_admin_pause"] = True
bot.set_execution_paused("ADMIN_MANUAL")
check("legacy pause is UNATTRIBUTED_MANUAL", owner() == bot.PAUSE_OWNER_UNATTRIBUTED)
with bot.app.test_client() as client:
    resumed = client.post("/api/resume", json=DEPLOY, environ_base=LOOPBACK)
check("deploy resume clears legacy pause", (resumed.get_json() or {}).get("status") == "resumed")

print("\n[6] Active reset pointer: deploy resume retains an operator hold, nothing else resumes")
reset_state()
bot._resume_active_reset_receipt_exists = lambda: True
with bot.app.test_client() as client:
    client.post("/api/pause", environ_base=LOOPBACK)
    retained = client.post("/api/resume", json=DEPLOY, environ_base=LOOPBACK)
    body = retained.get_json() or {}
    check("deploy resume retains operator hold during reset", retained.status_code == 200
          and body.get("status") == "operator_pause_retained" and body.get("pause_owner") == "OPERATOR",
          detail=f"{retained.status_code} {body}")
    check("operator hold still paused", bot.state.get("execution_paused") is True and owner() == "OPERATOR")
    bare = client.post("/api/resume", json={}, environ_base=LOOPBACK)
    check("operator resume still blocked by reset", bare.status_code == 409
          and (bare.get_json() or {}).get("reason") == "FRESH_COLLECTION_RESET_IN_PROGRESS", detail=str(bare.get_json()))
    check("still paused after blocked resume", bot.state.get("execution_paused") is True)
reset_state()
with bot.app.test_client() as client:
    client.post("/api/pause", json={"owner": "DEPLOY_MAINTENANCE"}, environ_base=LOOPBACK)
    blocked = client.post("/api/resume", json=DEPLOY, environ_base=LOOPBACK)
check("deploy pause is not resumed during reset", blocked.status_code == 409
      and bot.state.get("execution_paused") is True, detail=str(blocked.get_json()))
bot._fresh_collection_lock.acquire()
try:
    reset_state()
    with bot.app.test_client() as client:
        client.post("/api/pause", environ_base=LOOPBACK)
        retained = client.post("/api/resume", json=DEPLOY, environ_base=LOOPBACK)
    check("operator hold retained while reset lock is held",
          (retained.get_json() or {}).get("status") == "operator_pause_retained", detail=str(retained.get_json()))
finally:
    bot._fresh_collection_lock.release()
bot._resume_active_reset_receipt_exists = lambda: False

with open(config_path, "r", encoding="utf-8") as handle:
    saved = json.load(handle)
check("pause_intent is a persisted config key", "pause_intent" in saved)

print("\n" + "=" * 72)
print(f"PASS={passed} FAIL={failed}")
print("=" * 72)
if failed:
    sys.exit(1)


def test_resume_clears_pause_intent_on_the_cached_dashboard_payload() -> None:
    """After a guarded-deploy resume /api/state must not keep DEPLOY_MAINTENANCE."""
    import ast as _ast
    from pathlib import Path as _Path

    source = _Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    tree = _ast.parse(source)

    def body(name: str) -> str:
        node = next(n for n in tree.body if isinstance(n, _ast.FunctionDef) and n.name == name)
        return _ast.get_source_segment(source, node) or ""

    resume = body("api_resume")
    assert 'state["pause_intent"] = None' in resume
    assert "pause_intent=None," in resume
    overlay = body("_api_state_cache_refresher_loop")
    assert '"pause_intent",' in overlay and '"pause_owner",' in overlay
    relay = body("_build_relay_execution_state_snapshot")
    assert '"pause_intent": (' in relay
