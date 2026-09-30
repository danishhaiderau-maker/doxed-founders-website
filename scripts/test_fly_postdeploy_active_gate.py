import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fly_postdeploy_active_gate as gate

LANES = ["FAMILY_CHANDELIER_3", "FAMILY_ATR_TRAIL"]
WORKFLOW = (Path(__file__).resolve().parents[1] / ".github/workflows/fly-bot-deploy.yml").read_text(encoding="utf-8")


def _active_status(**overrides):
    status = {
        "source_git_rev": "abcdef123456",
        "execution_paused": False,
        "manual_admin_pause": False,
        "live_armed": False,
        "bitfinex_live_enabled": False,
        "force_paper_mode": True,
    }
    status.update(overrides)
    return status


def test_every_registry_lane_not_on_is_enabled_whatever_its_prior_state():
    current = {"FAMILY_CHANDELIER_3": False, "FAMILY_ATR_TRAIL": True, "RETIRED": False}
    assert gate.tile_enable_plan(current, LANES) == ["FAMILY_CHANDELIER_3"]
    assert gate.tile_enable_plan({}, LANES) == LANES


class _Bot:
    def __init__(self, enabled, relay_eligible=False, live_armed=False):
        self.enabled = dict(enabled)
        self.relay_eligible = relay_eligible
        self.live_armed = live_armed
        self.toggles = []

    def __call__(self, path, payload=None):
        if path == "/api/status":
            return {**_active_status(live_armed=self.live_armed), "pause_owner": "",
                    "active_tiles": [{"lane": lane, "relay_eligible": self.relay_eligible} for lane in LANES]}
        if path == "/api/state":
            return {"research_lane_enabled": dict(self.enabled)}
        assert path == "/api/toggle_research_lane" and payload["enabled"] is True
        self.toggles.append(payload["lane"])
        self.enabled[payload["lane"]] = True
        return {"lane": payload["lane"], "enabled": True}


def test_enable_all_registry_tiles_turns_every_tile_on_and_returns_receipt():
    bot = _Bot({"FAMILY_CHANDELIER_3": False})
    receipt = gate.enable_all_registry_tiles(bot)
    assert bot.toggles == LANES
    assert receipt["tiles_all_on"] is True and receipt["tiles_off"] == []
    assert receipt["pause_owner"] == "" and receipt["live_armed"] is False
    assert receipt["bitfinex_live_enabled"] is False


def test_enable_all_registry_tiles_refuses_relay_eligible_or_armed_state():
    with pytest.raises(SystemExit, match="relay-ineligible"):
        gate.enable_all_registry_tiles(_Bot({}, relay_eligible=True))
    with pytest.raises(SystemExit, match="disarmed"):
        gate.enable_all_registry_tiles(_Bot({}, live_armed=True))


def test_pause_owner_is_a_paper_active_violation():
    assert gate.paper_active_violations(_active_status(pause_owner="OPERATOR"), "abcdef123456") == [
        "PAUSE_OWNER:OPERATOR"]


def test_every_resume_path_forces_all_tiles_on():
    scripts = Path(__file__).resolve().parent
    for name in ("fly_failure_paper_resume.py", "fly_resume_bootstrap.py", "fly_resume_predeploy_abort.py"):
        assert "enable_all_registry_tiles" in (scripts / name).read_text(encoding="utf-8"), name
    for job in ("repair-execution-tail", "repair-lifecycle-cursor", "repair-lifecycle-tail",
                "restart-only", "recover-startup-crash", "recover-memory"):
        block = re.search(rf"\n  {re.escape(job)}:\n(.*?)(?=\n  [a-z0-9-]+:\n|\Z)", WORKFLOW, re.S).group(1)
        assert "fly_postdeploy_active_gate.py --tiles-only" in block, job


def test_active_state_requires_unpaused_paper_and_disarmed_live():
    assert gate.paper_active_violations(_active_status(), "abcdef123456") == []
    problems = gate.paper_active_violations(
        _active_status(execution_paused=True, execution_reason="ADMIN_MANUAL", live_armed=True, force_paper_mode=False),
        "abcdef123456",
    )
    assert "EXECUTION_PAUSED:ADMIN_MANUAL" in problems
    assert "LIVE_ARMED" in problems
    assert "FORCE_PAPER_MODE_OFF" in problems
    assert gate.paper_active_violations(_active_status(), "000000000000") == ["REVISION_MISMATCH"]


def test_relay_eligible_tiles_fail_the_gate():
    tiles = [{"lane": "A", "relay_eligible": False}, {"lane": "B", "relay_eligible": True}, {"lane": "C"}]
    assert gate.relay_eligible_tiles(tiles) == ["B", "C"]


def test_ai_cadence_counts_distinct_completions_after_start():
    assert gate.count_ai_completions([90.0, 110.0, 110.0, 150.0], started=100.0) == 2
    assert gate.count_ai_completions([90.0, 90.0], started=100.0) == 0


def test_workflow_runs_gate_after_resume_unless_hold_or_operator_pause():
    assert "python scripts/fly_postdeploy_active_gate.py" in WORKFLOW
    step = WORKFLOW[WORKFLOW.index("Assert paper active with advancing AI cadence"):]
    step = step[: step.index("run: python scripts/fly_postdeploy_active_gate.py")]
    assert "steps.paper_resume.outputs.operator_pause_retained != 'true'" in step
    assert "inputs.keep_maintenance_pause == true" in step


def test_workflow_deploy_pauses_and_resumes_are_deploy_owned():
    assert "STICKY_ADMIN_MANUAL_PAUSE" not in WORKFLOW
    for line in WORKFLOW.splitlines():
        if '"/api/resume"' in line and "json.dumps" not in line and "post(" not in line:
            assert "DEPLOY_MAINTENANCE" in line, line
    held = WORKFLOW[WORKFLOW.index("Keep true-flat ADMIN_MANUAL after exact-revision acceptance"):]
    held = held[: held.index("Best-effort preserve safe paper maintenance")]
    assert 'post("/api/pause", {})' in held
    assert "operator_pause_retained" in held


def test_keep_maintenance_pause_defaults_false():
    block = WORKFLOW[WORKFLOW.index("keep_maintenance_pause:"):]
    block = block[: block.index("lifecycle_reset_proof:")]
    assert "default: false" in block
