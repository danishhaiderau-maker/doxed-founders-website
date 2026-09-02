from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from fly_stalled_recovery_proof import validate


REV = "acc99ac9cee8"
SHA = REV + "0" * 28


def step(number, name, conclusion):
    return {"number": number, "name": name, "conclusion": conclusion}


def run(conclusion, created, *, head=SHA):
    return {
        "id": 33677658447,
        "head_sha": head,
        "conclusion": conclusion,
        "event": "workflow_dispatch",
        "name": "Deploy Fly BTC bot",
        "path": ".github/workflows/fly-bot-deploy.yml@refs/heads/codex/test",
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "updated_at": (created + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
    }


NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def successful_fixture():
    r = run("success", NOW)
    jobs = [{"name": "test-and-deploy", "conclusion": "success", "steps": [
        step(10, "Deploy the exact source revision", "success"),
        step(12, "Prove liveness, execution safety, and exact revision", "success"),
    ]}]
    return r, jobs


def chain_fixture():
    failed = run("failure", NOW)
    failed_jobs = [{"name": "test-and-deploy", "conclusion": "failure", "steps": [
        step(10, "Deploy the exact source revision", "success"),
        step(12, "Prove liveness, execution safety, and exact revision", "success"),
        step(13, "Complete receipt bootstrap inside exact-revision maintenance", "failure"),
        step(14, "Resume paper execution after exact-revision acceptance", "skipped"),
        step(16, "Best-effort preserve safe paper maintenance after failed guarded deploy", "success"),
    ]}]
    continued = run("success", NOW + timedelta(minutes=20), head="f" * 40)
    continued["id"] = 33683115049
    continued_jobs = [{"id": 100424120667, "name": "resume-bootstrap", "conclusion": "success", "steps": [
        step(2, "Validate failed guarded bootstrap proof", "success"),
        step(3, "Continue exact-revision bootstrap and resume paper once", "success"),
        step(4, "Best-effort preserve bootstrap continuation maintenance", "skipped"),
    ]}]
    return failed, failed_jobs, continued, continued_jobs


def chain_log(*, failed_run_id="33677658447", head_sha=SHA, revision=REV, source_git_rev=REV):
    return (
        f"resume-bootstrap\tstep\t2026-09-03T00:00:00Z "
        f'{{"failed_run_id":"{failed_run_id}","head_sha":"{head_sha}","revision":"{revision}"}}\n'
        f"resume-bootstrap\tstep\t2026-09-03T00:01:00Z "
        f'{{"resumed":true,"revision":"{revision}","source_git_rev":"{source_git_rev}"}}\n'
    ).encode()


def test_existing_successful_guarded_deploy_proof_remains_accepted_without_continuation():
    r, jobs = successful_fixture()
    assert validate(REV, r, jobs) == "successful-guarded-deploy"


def test_exact_failed_bootstrap_then_successful_continuation_chain_is_accepted():
    assert validate(REV, *chain_fixture(), chain_log()) == "failed-deploy-plus-successful-bootstrap-continuation"


@pytest.mark.parametrize("mutation", [
    "wrong_revision", "wrong_workflow", "wrong_event", "deploy_not_failed", "deploy_not_unique",
    "liveness_missing", "bootstrap_not_failed", "resume_not_skipped", "preservation_not_success", "deploy_order",
    "continuation_failed", "continuation_wrong_job", "continuation_job_not_unique", "validate_missing", "continue_missing",
    "preservation_not_skipped", "continuation_order", "chronology",
])
def test_chain_rejects_every_broken_receipt(mutation):
    failed, failed_jobs, continued, continued_jobs = deepcopy(chain_fixture())
    if mutation == "wrong_revision": failed["head_sha"] = "b" * 40
    elif mutation == "wrong_workflow": continued["path"] = ".github/workflows/other.yml"
    elif mutation == "wrong_event": continued["event"] = "push"
    elif mutation == "deploy_not_failed": failed["conclusion"] = "success"
    elif mutation == "deploy_not_unique": failed_jobs.append(deepcopy(failed_jobs[0]))
    elif mutation == "liveness_missing": failed_jobs[0]["steps"][1]["conclusion"] = "failure"
    elif mutation == "bootstrap_not_failed": failed_jobs[0]["steps"][2]["conclusion"] = "success"
    elif mutation == "resume_not_skipped": failed_jobs[0]["steps"][3]["conclusion"] = "success"
    elif mutation == "preservation_not_success": failed_jobs[0]["steps"][4]["conclusion"] = "failure"
    elif mutation == "deploy_order": failed_jobs[0]["steps"][0]["number"] = 14
    elif mutation == "continuation_failed": continued["conclusion"] = "failure"
    elif mutation == "continuation_wrong_job": continued_jobs[0]["name"] = "test-and-deploy"
    elif mutation == "continuation_job_not_unique": continued_jobs.append(deepcopy(continued_jobs[0]))
    elif mutation == "validate_missing": continued_jobs[0]["steps"][0]["conclusion"] = "failure"
    elif mutation == "continue_missing": continued_jobs[0]["steps"][1]["conclusion"] = "failure"
    elif mutation == "preservation_not_skipped": continued_jobs[0]["steps"][2]["conclusion"] = "success"
    elif mutation == "continuation_order": continued_jobs[0]["steps"][0]["number"] = 5
    elif mutation == "chronology": continued["created_at"] = failed["updated_at"]
    with pytest.raises(RuntimeError):
        validate(REV, failed, failed_jobs, continued, continued_jobs, chain_log())


def test_successful_proof_rejects_extraneous_continuation():
    r, jobs = successful_fixture()
    continued = run("success", NOW + timedelta(minutes=20))
    with pytest.raises(RuntimeError, match="not permitted"):
        validate(REV, r, jobs, continued, [])


def test_chain_rejects_failed_continue_even_if_preservation_succeeds():
    failed, failed_jobs, continued, continued_jobs = deepcopy(chain_fixture())
    continued["conclusion"] = "failure"
    continued_jobs[0]["conclusion"] = "failure"
    continued_jobs[0]["steps"][1]["conclusion"] = "failure"
    continued_jobs[0]["steps"][2]["conclusion"] = "success"
    with pytest.raises(RuntimeError):
        validate(REV, failed, failed_jobs, continued, continued_jobs, chain_log())


@pytest.mark.parametrize("log", [
    chain_log(failed_run_id="999"),
    chain_log(head_sha="b" * 40),
    chain_log(revision="bbbbbbbbbbbb"),
    chain_log(source_git_rev="bbbbbbbbbbbb"),
    chain_log() + chain_log(),
    b"timestamp {not-json}\n",
])
def test_chain_rejects_tampered_missing_or_duplicate_log_receipts(log):
    with pytest.raises(RuntimeError):
        validate(REV, *chain_fixture(), log)
