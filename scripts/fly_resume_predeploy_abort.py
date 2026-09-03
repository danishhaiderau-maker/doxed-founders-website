"""Proof-bound paper resume after a guarded deploy aborts before deployment."""

from __future__ import annotations

import argparse
import json
import os
import re

from fly_resume_bootstrap import _http_clients, _transient, observe_status, preserve_maintenance


WORKFLOW_NAME = "Deploy Fly BTC bot"
WORKFLOW_PATH = ".github/workflows/fly-bot-deploy.yml"
PREDEPLOY_STEPS = (
    ("Enter durable authenticated paper maintenance boundary", "success"),
    ("Prove the current Fly owner and every relay account are flat", "failure"),
)
MUST_NOT_RUN_STEPS = (
    "Recheck maintenance boundary immediately before deploy",
    "Deploy the exact source revision",
    "Re-enter maintenance and flatten the exact deployed revision",
    "Prove liveness, execution safety, and exact revision",
    "Complete receipt bootstrap inside exact-revision maintenance",
    "Resume paper execution after exact-revision acceptance",
)
PRESERVE_STEP = "Best-effort preserve safe paper maintenance after failed guarded deploy"


def _revision12(value: str, label: str) -> str:
    revision = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{12}", revision):
        raise RuntimeError(f"{label} requires an exact 12-character revision")
    return revision


def _sha40(value: str, label: str) -> str:
    revision = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise RuntimeError(f"{label} requires an exact 40-character SHA")
    return revision


def validate_failed_run(incumbent: str, candidate: str, run_id: str, fetch_json) -> dict:
    incumbent = _revision12(incumbent, "resume-predeploy-abort incumbent")
    candidate = _sha40(candidate, "resume-predeploy-abort candidate")
    if candidate[:12] == incumbent:
        raise RuntimeError("candidate and incumbent revisions must differ")
    if not str(run_id or "").isdigit():
        raise RuntimeError("resume-predeploy-abort requires the failed guarded deploy run id")
    run = fetch_json(f"runs/{run_id}")
    if (
        str(run.get("head_sha") or "").lower() != candidate
        or run.get("status") != "completed"
        or run.get("conclusion") != "failure"
        or run.get("event") != "workflow_dispatch"
        or run.get("name") != WORKFLOW_NAME
        or str(run.get("path") or "").split("@", 1)[0] != WORKFLOW_PATH
    ):
        raise RuntimeError("predeploy-abort proof does not bind the exact failed candidate run")
    jobs = fetch_json(f"runs/{run_id}/jobs?per_page=100").get("jobs") or []
    deploy_jobs = [job for job in jobs if job.get("name") == "test-and-deploy"]
    if len(deploy_jobs) != 1 or deploy_jobs[0].get("conclusion") != "failure":
        raise RuntimeError("predeploy-abort proof lacks one failed test-and-deploy job")
    steps = deploy_jobs[0].get("steps") or []

    def unique(name: str, conclusion: str) -> dict:
        matches = [step for step in steps if step.get("name") == name and step.get("conclusion") == conclusion]
        if len(matches) != 1:
            raise RuntimeError(f"predeploy-abort proof lacks one {conclusion} {name} step")
        return matches[0]

    maintenance = unique(*PREDEPLOY_STEPS[0])
    failed = unique(*PREDEPLOY_STEPS[1])
    skipped = [unique(name, "skipped") for name in MUST_NOT_RUN_STEPS]
    preserved = unique(PRESERVE_STEP, "success")
    ordered = [maintenance, failed, *skipped, preserved]
    if [int(step["number"]) for step in ordered] != sorted(int(step["number"]) for step in ordered):
        raise RuntimeError("predeploy-abort proof steps are out of order")
    # Defend against duplicate step records with a different conclusion.
    protected_names = set(MUST_NOT_RUN_STEPS)
    if any(step.get("name") in protected_names and step.get("conclusion") != "skipped" for step in steps):
        raise RuntimeError("a deployment or postdeploy acceptance step ran")
    return {
        "candidate_sha": candidate,
        "failed_run_id": str(run_id),
        "incumbent_revision": incumbent,
    }


def _observed_revision(status: dict) -> str | None:
    value = str(status.get("source_git_rev") or "").strip().lower()
    return value[:12] if re.fullmatch(r"[0-9a-f]{12}|[0-9a-f]{40}", value) else None


def _require_status(status: dict, incumbent: str, *, paused: bool) -> None:
    progress = status.get("strategy_progress") or {}
    pipeline = status.get("lifecycle_pipeline") or {}
    bootstrap = pipeline.get("receipt_bootstrap") or {}
    open_positions = progress.get("open_positions")
    pending_orders = progress.get("pending_orders")
    checks = {
        "incumbent_revision_exact": _observed_revision(status) == incumbent,
        "force_paper_mode": status.get("force_paper_mode") is True,
        "bitfinex_live_disabled": status.get("bitfinex_live_enabled") is False,
        "live_disarmed": status.get("live_armed") is False,
        "pause_state": status.get("execution_paused") is paused,
        "manual_pause_state": status.get("manual_admin_pause") is paused,
        "process_alive": status.get("process_alive") is True,
        "system_ready": status.get("system_ready") is True,
        "strategy_progress_ok": progress.get("ok") is True,
        "trade_lock_progressing": progress.get("trade_lock_progressing") is True,
        "lifecycle_owner": pipeline.get("owner") is True,
        "lifecycle_running": pipeline.get("running") is True,
        "lifecycle_revision_match": pipeline.get("source_revision_match") is True,
        "bootstrap_required": bootstrap.get("required") is True,
        "bootstrap_complete": bootstrap.get("status") == "COMPLETE" and bootstrap.get("complete") is True,
        "bootstrap_not_blocked": bootstrap.get("blocked") is not True,
    }
    if paused:
        checks.update({
            "open_positions_zero": type(open_positions) is int and open_positions == 0,
            "pending_orders_zero": type(pending_orders) is int and pending_orders == 0,
        })
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise RuntimeError("unsafe predeploy-abort status: " + json.dumps({"failed_predicates": failed}, sort_keys=True))


def observe_fresh_relay_state(request_json, *, until, monotonic, sleep) -> dict:
    attempt = 0
    last = None
    while monotonic() < until:
        attempt += 1
        try:
            return request_json("/api/relay-execution-state?fresh=1", None)
        except Exception as exc:
            if not _transient(exc):
                raise
            last = exc
        delay = min(2 ** min(attempt - 1, 4), max(0.0, until - monotonic()))
        print(
            f"transient fresh relay observation attempt={attempt} error={type(last).__name__}",
            flush=True,
        )
        if delay:
            sleep(delay)
    raise RuntimeError(
        f"bounded fresh relay observation unavailable: {type(last).__name__}"
    )


def resume_incumbent(incumbent: str, candidate: str, request_json, *, monotonic, sleep, timeout=120) -> dict:
    incumbent = _revision12(incumbent, "resume-predeploy-abort incumbent")
    candidate = _sha40(candidate, "resume-predeploy-abort candidate")
    if candidate[:12] == incumbent:
        raise RuntimeError("candidate and incumbent revisions must differ")
    deadline = monotonic() + timeout
    before = observe_status(request_json, until=deadline, monotonic=monotonic, sleep=sleep)
    _require_status(before, incumbent, paused=True)
    relay = observe_fresh_relay_state(
        request_json,
        until=deadline,
        monotonic=monotonic,
        sleep=sleep,
    )
    generation = relay.get("money_state_generation")
    if (
        type(generation) is not int or generation < 0
        or relay.get("orders") != [] or relay.get("positions") != []
    ):
        raise RuntimeError("fresh incumbent paper relay state is not generation-current and flat")
    # Sole resume mutation. An ambiguous response propagates and is never retried.
    resumed = request_json("/api/resume", {})
    if resumed.get("status") != "resumed" or resumed.get("execution_paused") is not False:
        raise RuntimeError("single incumbent paper resume was not acknowledged")
    final = observe_status(request_json, until=deadline, monotonic=monotonic, sleep=sleep)
    _require_status(final, incumbent, paused=False)
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("validate-proof", "resume", "preserve-maintenance"))
    parser.add_argument("--incumbent", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.mode == "validate-proof":
        token = str(os.environ.get("GH_TOKEN") or "").strip()
        repository = str(os.environ.get("GH_REPOSITORY") or "").strip()
        if not token or not repository:
            raise RuntimeError("GitHub proof credentials are missing")
        import urllib.request
        base = f"https://api.github.com/repos/{repository}/actions/"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        def fetch(path):
            with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=15) as response:
                return json.load(response)
        result = validate_failed_run(args.incumbent, args.candidate, args.run_id, fetch)
    else:
        token = str(os.environ.get("BOT_ADMIN_TOKEN") or "").strip()
        if not token:
            raise RuntimeError("BOT_ADMIN_TOKEN is missing")
        client = _http_clients(token)
        if args.mode == "resume":
            import time
            result = resume_incumbent(args.incumbent, args.candidate, client, monotonic=time.monotonic, sleep=time.sleep)
        else:
            result = preserve_maintenance(args.incumbent, client)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
