"""Proof-bound continuation of a failed Fly receipt-bootstrap acceptance gate."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request


WORKFLOW_NAME = "Deploy Fly BTC bot"
WORKFLOW_PATH = ".github/workflows/fly-bot-deploy.yml"
TRANSIENT_HTTP = {502, 503, 504}


def _exact_revision(value: str) -> str:
    revision = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{12}", revision):
        raise RuntimeError("resume-bootstrap requires an exact 12-character deployed revision")
    return revision


def validate_failed_run(expected: str, run_id: str, fetch_json) -> dict:
    expected = _exact_revision(expected)
    if not str(run_id or "").isdigit():
        raise RuntimeError("resume-bootstrap requires the failed guarded deploy run id")
    run = fetch_json(f"runs/{run_id}")
    head_sha = str(run.get("head_sha") or "").lower()
    if (
        not re.fullmatch(r"[0-9a-f]{40}", head_sha)
        or head_sha[:12] != expected
        or run.get("conclusion") != "failure"
        or run.get("event") != "workflow_dispatch"
        or run.get("name") != WORKFLOW_NAME
        or str(run.get("path") or "").split("@", 1)[0] != WORKFLOW_PATH
    ):
        raise RuntimeError("bootstrap proof run does not bind the exact failed deployed revision")
    jobs = (fetch_json(f"runs/{run_id}/jobs?per_page=100").get("jobs") or [])
    deploy_jobs = [job for job in jobs if job.get("name") == "test-and-deploy"]
    if len(deploy_jobs) != 1 or deploy_jobs[0].get("conclusion") != "failure":
        raise RuntimeError("bootstrap proof lacks one failed test-and-deploy job")
    steps = deploy_jobs[0].get("steps") or []

    def unique_step(name, conclusion):
        matches = [step for step in steps if step.get("name") == name and step.get("conclusion") == conclusion]
        if len(matches) != 1:
            raise RuntimeError(f"bootstrap proof lacks one {conclusion} {name} step")
        return matches[0]

    deployed = unique_step("Deploy the exact source revision", "success")
    live = unique_step("Prove liveness, execution safety, and exact revision", "success")
    failed = unique_step("Complete receipt bootstrap inside exact-revision maintenance", "failure")
    preserved = unique_step("Best-effort preserve safe paper maintenance after failed guarded deploy", "success")
    if not (int(deployed["number"]) < int(live["number"]) < int(failed["number"]) < int(preserved["number"])):
        raise RuntimeError("bootstrap proof steps are out of order")
    if any(step.get("name") == "Resume paper execution after exact-revision acceptance" and step.get("conclusion") == "success" for step in steps):
        raise RuntimeError("failed proof run already resumed paper execution")
    return {"failed_run_id": str(run_id), "head_sha": head_sha, "revision": expected}


def _transient(exc: BaseException) -> bool:
    # HTTPError inherits URLError, so classify its status before the broader
    # transport exception or a 401/403 would be retried until the deadline.
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in TRANSIENT_HTTP
    return isinstance(exc, (urllib.error.URLError, TimeoutError))


def observe_status(request_json, *, until, monotonic, sleep):
    attempt = 0
    last = None
    while monotonic() < until:
        attempt += 1
        try:
            return request_json("/api/status", None)
        except Exception as exc:
            if not _transient(exc):
                raise
            last = exc
        delay = min(2 ** min(attempt - 1, 4), max(0.0, until - monotonic()))
        print(f"transient status observation attempt={attempt} error={type(last).__name__}", flush=True)
        if delay:
            sleep(delay)
    raise RuntimeError(f"bounded status observation unavailable: {type(last).__name__}")


def _common_safe(status: dict, expected: str, *, paused: bool) -> tuple[dict, dict, dict]:
    progress = status.get("strategy_progress") or {}
    pipeline = status.get("lifecycle_pipeline") or {}
    bootstrap = pipeline.get("receipt_bootstrap") or {}
    safe = (
        str(status.get("source_git_rev") or "").lower() == expected
        and status.get("process_alive") is True
        and progress.get("ok") is True
        and progress.get("trade_lock_available") is True
        and status.get("force_paper_mode") is True
        and status.get("bitfinex_live_enabled") is False
        and status.get("live_armed") is False
        and status.get("execution_paused") is paused
        and status.get("manual_admin_pause") is paused
        and int(progress.get("open_positions") or 0) == 0
        and int(progress.get("pending_orders") or 0) == 0
        and pipeline.get("owner") is True
        and pipeline.get("running") is True
        and pipeline.get("source_revision_match") is True
    )
    if not safe:
        raise RuntimeError("resume-bootstrap lost exact-revision safe paper state")
    return progress, pipeline, bootstrap


def _require_complete(bootstrap: dict) -> None:
    if bootstrap.get("blocked") is True or bootstrap.get("status") == "BLOCKED":
        raise RuntimeError("resume-bootstrap observed a fail-closed blocker")
    if not (
        bootstrap.get("required") is True
        and bootstrap.get("status") == "COMPLETE"
        and bootstrap.get("complete") is True
    ):
        raise RuntimeError("receipt bootstrap is not complete")


def continue_bootstrap(expected: str, request_json, *, monotonic=time.monotonic, sleep=time.sleep, timeout=45 * 60) -> dict:
    expected = _exact_revision(expected)
    deadline = monotonic() + timeout
    bootstrap_deadline = deadline - min(60, timeout / 4)
    last = None
    while monotonic() < bootstrap_deadline:
        status = observe_status(request_json, until=bootstrap_deadline, monotonic=monotonic, sleep=sleep)
        last = status
        _, _, bootstrap = _common_safe(status, expected, paused=True)
        if bootstrap.get("blocked") is True or bootstrap.get("status") == "BLOCKED":
            raise RuntimeError("resume-bootstrap observed a fail-closed blocker")
        if bootstrap.get("required") is True and bootstrap.get("status") == "COMPLETE" and bootstrap.get("complete") is True:
            break
        sleep(min(3, max(0.0, bootstrap_deadline - monotonic())))
    else:
        raise RuntimeError("resume-bootstrap bounded deadline expired")

    # Sole mutation. An ambiguous response propagates and is never retried.
    resumed = request_json("/api/resume", {})
    if resumed.get("status") != "resumed" or resumed.get("execution_paused") is not False:
        raise RuntimeError("single paper resume was not acknowledged")
    final = observe_status(request_json, until=deadline, monotonic=monotonic, sleep=sleep)
    _, _, bootstrap = _common_safe(final, expected, paused=False)
    _require_complete(bootstrap)
    return final


def _http_clients(token: str):
    base = "https://doxed-btc-bot.fly.dev"
    headers = {"X-Bot-Admin-Token": token, "Cache-Control": "no-cache"}
    def request_json(path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(base + path, data=data, headers={**headers, **({"Content-Type": "application/json"} if data is not None else {})}, method="POST" if data is not None else "GET")
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    return request_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("validate-proof", "continue"))
    parser.add_argument("--expected", required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.mode == "validate-proof":
        token = str(os.environ.get("GH_TOKEN") or "").strip()
        repository = str(os.environ.get("GH_REPOSITORY") or "").strip()
        if not token or not repository:
            raise RuntimeError("GitHub proof credentials are missing")
        base = f"https://api.github.com/repos/{repository}/actions/"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        def fetch(path):
            with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=15) as response:
                return json.load(response)
        print(json.dumps(validate_failed_run(args.expected, args.run_id, fetch), sort_keys=True))
    else:
        token = str(os.environ.get("BOT_ADMIN_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("BOT_ADMIN_TOKEN is missing")
        result = continue_bootstrap(args.expected, _http_clients(token))
        print(json.dumps({"revision": args.expected, "resumed": True, "source_git_rev": result.get("source_git_rev")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
