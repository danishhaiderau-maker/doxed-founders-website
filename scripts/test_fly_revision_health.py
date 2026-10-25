"""Serving-rev deploy fields for system-health fly.revision (Boss 22:20 AEDT item 3)."""
from __future__ import annotations

import fly_revision_health as frh


SHA = "e8303023949c3e98dd1409fc25e96b0a52e6ab55"
OTHER = "ec0aafda42c69e242f2bdb57c3684378d426656a"


def _jobs(*, acceptance=None, guaranteed=None, failed=None, resume_paper=None):
    steps = []
    if failed:
        steps.append({"name": failed, "conclusion": "failure"})
    if acceptance is True:
        steps.append({"name": frh._ACCEPTANCE_RESUME, "conclusion": "success"})
    elif acceptance is False:
        steps.append({"name": frh._ACCEPTANCE_RESUME, "conclusion": "skipped"})
    if guaranteed is True:
        steps.append({"name": frh._GUARANTEED_RESUME, "conclusion": "success"})
    jobs = [{"name": "test-and-deploy", "conclusion": "failure" if failed else "success", "steps": steps}]
    if resume_paper:
        jobs.append({"name": "resume-paper", "conclusion": "success", "steps": []})
    return jobs


def test_match_serving_deploy_prefers_tip_sha_not_newest_other():
    runs = [
        {"databaseId": 9, "status": "completed", "conclusion": "failure", "createdAt": "2026-10-25T11:00:00Z",
         "headSha": OTHER},
        {"databaseId": 8, "status": "completed", "conclusion": "failure", "createdAt": "2026-10-25T10:00:00Z",
         "headSha": SHA},
        {"databaseId": 7, "status": "completed", "conclusion": "success", "createdAt": "2026-10-25T09:00:00Z",
         "headSha": SHA},
    ]
    matched = frh.match_serving_deploy(SHA[:12], runs)
    assert matched["databaseId"] == 8  # newest completed for serving tip


def test_failed_steps_and_guaranteed_resume():
    jobs = _jobs(
        failed="Complete receipt bootstrap inside exact-revision maintenance",
        acceptance=False,
        guaranteed=True,
    )
    assert frh.failed_steps_from_jobs(jobs) == [
        "Complete receipt bootstrap inside exact-revision maintenance"
    ]
    assert frh.paper_resumed_by_from_jobs(jobs) == frh.PAPER_RESUMED_BY_GUARANTEED


def test_acceptance_resume_wins_over_guaranteed():
    jobs = _jobs(acceptance=True, guaranteed=True)
    assert frh.paper_resumed_by_from_jobs(jobs) == frh.PAPER_RESUMED_BY_ACCEPTANCE


def test_serving_revision_fields_amber_inputs():
    snap = {
        "serving_deploy": {
            "databaseId": 40184559814,
            "status": "completed",
            "conclusion": "failure",
            "headSha": SHA,
            "jobs": _jobs(
                failed="Complete receipt bootstrap inside exact-revision maintenance",
                acceptance=False,
                guaranteed=True,
            ),
        }
    }
    fields = frh.serving_revision_fields(SHA[:12], snap)
    assert fields["matched"] is True
    assert fields["deploy_run_id"] == 40184559814
    assert fields["conclusion"] == "failure"
    assert fields["failed_steps"] == [
        "Complete receipt bootstrap inside exact-revision maintenance"
    ]
    assert fields["paper_resumed_by"] == "guaranteed"
    assert frh.deploy_did_not_succeed(fields["conclusion"]) is True
    obs = frh.format_revision_observed(fly_rev=SHA[:12], master_sha=OTHER, fields=fields)
    assert "deploy_run_id=40184559814" in obs
    assert "conclusion=failure" in obs
    assert "paper_resumed_by=guaranteed" in obs
    assert "failed_steps=" in obs


def test_success_serving_deploy_not_failed():
    snap = {
        "runs": [{
            "databaseId": 1, "status": "completed", "conclusion": "success",
            "createdAt": "2026-10-25T10:00:00Z", "headSha": SHA,
            "jobs": _jobs(acceptance=True),
        }]
    }
    fields = frh.serving_revision_fields(SHA, snap)
    assert fields["conclusion"] == "success"
    assert fields["paper_resumed_by"] == "acceptance"
    assert frh.deploy_did_not_succeed(fields["conclusion"]) is False
