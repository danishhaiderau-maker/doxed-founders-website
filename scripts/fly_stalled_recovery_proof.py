#!/usr/bin/env python3
"""Validate the immutable Actions receipt used by recover-stalled-runtime."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
import re
import urllib.request
from urllib.parse import urlsplit


WORKFLOW_NAME = "Deploy Fly BTC bot"
WORKFLOW_PATH = ".github/workflows/fly-bot-deploy.yml"
MAX_CONTINUATION_LOG_BYTES = 2 * 1024 * 1024


class _CrossHostCredentialStrippingRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


def _time(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise RuntimeError("recovery proof timestamp is missing")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _unique_job(jobs: list[dict], name: str, conclusion: str) -> dict:
    found = [j for j in jobs if j.get("name") == name]
    if len(found) != 1 or found[0].get("conclusion") != conclusion:
        raise RuntimeError(f"expected exactly one {conclusion} {name} job")
    return found[0]


def _unique_step(job: dict, name: str, conclusion: str) -> dict:
    found = [s for s in (job.get("steps") or []) if s.get("name") == name and s.get("conclusion") == conclusion]
    if len(found) != 1:
        raise RuntimeError(f"expected one {conclusion} {name} step, found {len(found)}")
    return found[0]


def _workflow_run(run: dict, *, conclusion: str) -> None:
    if (
        run.get("conclusion") != conclusion
        or run.get("event") != "workflow_dispatch"
        or run.get("name") != WORKFLOW_NAME
        or str(run.get("path") or "").split("@", 1)[0] != WORKFLOW_PATH
    ):
        raise RuntimeError(f"run is not the expected {conclusion} guarded workflow dispatch")


def _json_log_objects(log: bytes) -> list[dict]:
    objects = []
    for raw in log.decode("utf-8", errors="replace").splitlines():
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end < start:
            continue
        try:
            value = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    return objects


def validate(expected: str, deploy_run: dict, deploy_jobs: list[dict], continuation_run: dict | None = None,
             continuation_jobs: list[dict] | None = None, continuation_log: bytes | None = None) -> str:
    if not re.fullmatch(r"[0-9a-f]{12}", expected):
        raise RuntimeError("stalled-runtime recovery requires an exact 12-character revision")
    head = str(deploy_run.get("head_sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", head) or head[:12] != expected:
        raise RuntimeError("guarded deployment proof does not bind the expected revision")

    if deploy_run.get("conclusion") == "success":
        _workflow_run(deploy_run, conclusion="success")
        job = _unique_job(deploy_jobs, "test-and-deploy", "success")
        deployed = _unique_step(job, "Deploy the exact source revision", "success")
        live = _unique_step(job, "Prove liveness, execution safety, and exact revision", "success")
        if int(deployed["number"]) >= int(live["number"]):
            raise RuntimeError("successful guarded deployment receipts are out of order")
        if continuation_run is not None:
            raise RuntimeError("a continuation run is not permitted with an already successful deploy proof")
        return "successful-guarded-deploy"

    _workflow_run(deploy_run, conclusion="failure")
    if continuation_run is None or continuation_jobs is None:
        raise RuntimeError("failed bootstrap observation requires its successful continuation receipt")
    job = _unique_job(deploy_jobs, "test-and-deploy", "failure")
    deployed = _unique_step(job, "Deploy the exact source revision", "success")
    live = _unique_step(job, "Prove liveness, execution safety, and exact revision", "success")
    bootstrap = _unique_step(job, "Complete receipt bootstrap inside exact-revision maintenance", "failure")
    skipped_resume = _unique_step(job, "Resume paper execution after exact-revision acceptance", "skipped")
    preserve = _unique_step(job, "Best-effort preserve safe paper maintenance after failed guarded deploy", "success")
    if not (int(deployed["number"]) < int(live["number"]) < int(bootstrap["number"]) < int(skipped_resume["number"]) < int(preserve["number"])):
        raise RuntimeError("failed guarded deployment receipts are out of order")

    _workflow_run(continuation_run, conclusion="success")
    continuation_job = _unique_job(continuation_jobs, "resume-bootstrap", "success")
    validate_step = _unique_step(continuation_job, "Validate failed guarded bootstrap proof", "success")
    continue_step = _unique_step(continuation_job, "Continue exact-revision bootstrap and resume paper once", "success")
    skipped = _unique_step(continuation_job, "Best-effort preserve bootstrap continuation maintenance", "skipped")
    if not (int(validate_step["number"]) < int(continue_step["number"]) < int(skipped["number"])):
        raise RuntimeError("bootstrap continuation receipts are out of order")
    if _time(deploy_run.get("updated_at") or deploy_run.get("completed_at")) >= _time(continuation_run.get("created_at")):
        raise RuntimeError("bootstrap continuation does not chronologically follow the failed deploy")
    if continuation_log is None or len(continuation_log) > MAX_CONTINUATION_LOG_BYTES:
        raise RuntimeError("bounded bootstrap continuation log receipt is missing or oversized")
    objects = _json_log_objects(continuation_log)
    proof_receipts = [row for row in objects if set(row) == {"failed_run_id", "head_sha", "revision"}]
    resume_receipts = [row for row in objects if set(row) == {"resumed", "revision", "source_git_rev"}]
    if len(proof_receipts) != 1 or proof_receipts[0] != {
        "failed_run_id": str(deploy_run.get("id") or ""),
        "head_sha": head,
        "revision": expected,
    }:
        raise RuntimeError("continuation log does not uniquely bind the failed deploy proof")
    if len(resume_receipts) != 1 or resume_receipts[0] != {
        "resumed": True,
        "revision": expected,
        "source_git_rev": expected,
    }:
        raise RuntimeError("continuation log does not uniquely prove exact-revision resume")
    return "failed-deploy-plus-successful-bootstrap-continuation"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--continuation-run-id", default="")
    args = parser.parse_args()
    if not args.run_id.isdigit() or (args.continuation_run_id and not args.continuation_run_id.isdigit()):
        raise RuntimeError("recovery proof run ids must be decimal Actions ids")
    repo, token = os.environ["GH_REPOSITORY"], os.environ["GH_TOKEN"]
    base = f"https://api.github.com/repos/{repo}/actions"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    def fetch(path: str) -> dict:
        with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=15) as response:
            return json.load(response)

    deploy_run = fetch(f"/runs/{args.run_id}")
    deploy_jobs = fetch(f"/runs/{args.run_id}/jobs?per_page=100").get("jobs") or []
    continuation_run = continuation_jobs = continuation_log = None
    if args.continuation_run_id:
        continuation_run = fetch(f"/runs/{args.continuation_run_id}")
        continuation_jobs = fetch(f"/runs/{args.continuation_run_id}/jobs?per_page=100").get("jobs") or []
        continuation_job = _unique_job(continuation_jobs, "resume-bootstrap", "success")
        log_url = f"https://api.github.com/repos/{repo}/actions/jobs/{continuation_job['id']}/logs"
        opener = urllib.request.build_opener(_CrossHostCredentialStrippingRedirect())
        with opener.open(urllib.request.Request(log_url, headers=headers), timeout=30) as response:
            continuation_log = response.read(MAX_CONTINUATION_LOG_BYTES + 1)
        if len(continuation_log) > MAX_CONTINUATION_LOG_BYTES:
            raise RuntimeError("bootstrap continuation job log exceeds bounded proof size")
    mode = validate(args.expected, deploy_run, deploy_jobs, continuation_run, continuation_jobs, continuation_log)
    print(f"Stalled runtime recovery proof accepted ({mode}): {args.expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
