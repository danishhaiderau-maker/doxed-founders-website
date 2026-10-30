"""#747 contract: the all-pages open-trade gate is wired into the normal deploy path and cannot be skipped by it."""
import re
from pathlib import Path

WF = (Path(__file__).resolve().parents[1] / ".github/workflows/fly-bot-deploy.yml").read_text(encoding="utf-8")
JOB = WF[WF.index("\n  test-and-deploy:"):]
STEP = "Pre-deploy gate - live OFF, force_paper, no open trade on ANY /tiles/trades page (fails closed)"


def _step_block():
    i = JOB.index(f"- name: {STEP}")
    j = JOB.index("\n      - name:", i + 10)
    return JOB[i:j]


def test_gate_runs_before_the_maintenance_boundary_and_any_flatten():
    assert JOB.index(STEP) < JOB.index("- name: Enter durable authenticated paper maintenance boundary")
    assert JOB.index(STEP) < JOB.index("- name: Re-enter maintenance and flatten the exact deployed revision")


def test_normal_deploy_cannot_skip_the_gate():
    blk = _step_block()
    cond = re.search(r"if: (.+)", blk).group(1).strip()
    assert cond == "github.event_name != 'workflow_dispatch' || inputs.mode == 'deploy'"
    # R8: tip is workflow_dispatch-only; mode=deploy evaluates true. Recovery modes of this job skip it.
    assert "recover" not in cond.replace("'recover-unready'", "")
    assert "continue-on-error" not in blk


def test_deploy_mode_does_not_wait_for_flat_and_allows_open_paper_trades():
    blk = _step_block()
    assert "--allow-open" in blk and "--wait-seconds 1200" not in blk


def test_failed_gate_leaves_paper_maintenance_cleanup_skipped():
    # cleanup/pause steps only run once paper_maintenance or deploy_source actually started
    assert "steps.paper_maintenance.outcome == 'success' || steps.paper_maintenance.outcome == 'failure'" in JOB
    assert "id: paper_maintenance" in JOB and JOB.index(STEP) < JOB.index("id: paper_maintenance")


def test_live_rearm_note_present():
    assert "live re-arm path keeps the strict gate" in WF
