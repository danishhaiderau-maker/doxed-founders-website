from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "fly-bot-deploy.yml"


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step(job, name):
    matches = [step for step in job["steps"] if step.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def test_postdeploy_addressability_covers_slow_first_boot():
    job = _workflow()["jobs"]["test-and-deploy"]
    script = _step(job, "Re-enter maintenance and flatten the exact deployed revision")["run"]
    assert "addressable_deadline = time.monotonic() + 15 * 60" in script
    assert "range(1, 41)" not in script
    assert job["timeout-minutes"] >= 120


def test_held_maintenance_deploy_skips_bootstrap_wait_but_resume_requires_it():
    job = _workflow()["jobs"]["test-and-deploy"]
    step = _step(job, "Complete receipt bootstrap inside exact-revision maintenance")
    assert "inputs.keep_maintenance_pause == true" in step["if"]
    assert step["if"].strip().startswith("${{ !(")
    resume = (Path(__file__).resolve().parent / "fly_failure_paper_resume.py").read_text(encoding="utf-8")
    assert '"bootstrap_complete_if_required"' in resume


def test_reset_plan_and_verify_never_use_one_long_machine_exec():
    job = _workflow()["jobs"]["clean-epoch-boundary-reset"]
    text = "\n".join(str(step.get("run") or "") for step in job["steps"])
    assert "flyctl machine exec" not in "\n".join(
        line for line in text.splitlines() if "clean_epoch_reset_plan.py" in line
    )
    assert text.count("scripts/fly_detached_exec.py") == 3
    for label in ("reset-plan", "reset-plan-before", "reset-verify"):
        assert f"--label {label} " in text
    assert job["timeout-minutes"] >= 90


def test_resume_paper_mode_is_resume_only_and_proves_tiles_on():
    workflow = _workflow()
    options = workflow[True]["workflow_dispatch"]["inputs"]["mode"]["options"]
    assert "resume-paper" in options
    job = workflow["jobs"]["resume-paper"]
    assert "inputs.mode == 'resume-paper'" in job["if"]
    text = "\n".join(str(step.get("run") or "") for step in job["steps"])
    assert "scripts/fly_failure_paper_resume.py" in text
    assert "scripts/fly_postdeploy_active_gate.py" in text
    for forbidden in ("flyctl deploy", "machines restart", "flatten", "positions/close", "rearm", "arm-live"):
        assert forbidden not in text
    assert "expected_bootstrap_revision" in str(job)


def test_pre_start_wipe_stays_held_and_needs_the_plan_token():
    job = _workflow()["jobs"]["clean-epoch-wipe"]
    assert job["env"]["PRE_START"] == "${{ inputs.keep_maintenance_pause == true }}"
    text = "\n".join(str(step.get("run") or "") for step in job["steps"])
    assert 'scope_args="--pre-start"' in text
    assert 'want="DELETE-PRE-START:"+os.environ["PLAN_SHA"][:12]' in text
    assert "flyctl machine exec --app doxed-btc-bot --timeout 600" not in text
    assert text.count("scripts/fly_detached_exec.py") == 2
    resume = _step(job, "Resume paper")
    gate = _step(job, "Prove paper active with every registry tile ON")
    for step in (resume, gate):
        assert "inputs.keep_maintenance_pause != true" in step["if"]
    held = _step(job, "Prove the pre-start hold survived the wipe")
    assert "inputs.keep_maintenance_pause == true" in held["if"]
    assert '"pause_owner": "DEPLOY_MAINTENANCE"' in held["run"]
