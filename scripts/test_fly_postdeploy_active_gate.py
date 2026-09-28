import sys
from pathlib import Path

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


def test_restore_plan_only_reverts_registry_lanes_that_drifted():
    prior = {"captured": True, "research_lane_enabled": {"FAMILY_CHANDELIER_3": True, "FAMILY_ATR_TRAIL": True, "RETIRED": True}}
    current = {"FAMILY_CHANDELIER_3": False, "FAMILY_ATR_TRAIL": True, "RETIRED": False}
    assert gate.tile_restore_plan(prior, current, LANES) == {"FAMILY_CHANDELIER_3": True}


def test_restore_plan_is_empty_when_prior_state_missing():
    assert gate.tile_restore_plan({"captured": False}, {}, LANES) == {}
    assert gate.parse_prior("not json") == {"captured": False}


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
    assert "steps.paper_maintenance.outputs.prior_operator_state" in step


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
