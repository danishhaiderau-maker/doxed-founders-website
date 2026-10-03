"""Guaranteed paper resume on every failure exit of the guarded deploy job.

Paper must never sit idle after a deploy attempt.  Whatever revision is
running when the job fails (the unchanged incumbent or a deployed candidate
that missed a later acceptance check) is resumed through the normal
deploy-owned /api/resume path, but only when it is provably paper-only,
disarmed, and healthy.  Operator and safety pauses are always retained.
"""

from __future__ import annotations

import json
import os
import re
import time

from fly_resume_bootstrap import _http_clients, _transient

RETAINED_OWNERS = frozenset({"OPERATOR", "SAFETY"})
RESUME_PAYLOAD = {"clear_admin_manual_pause": True, "owner": "DEPLOY_MAINTENANCE"}
MAX_RESUME_POSTS = 3


def parse_prior(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {"captured": False}
    return value if isinstance(value, dict) else {"captured": False}


def identity_failures(status: dict) -> list[str]:
    checks = {
        "force_paper_mode": status.get("force_paper_mode") is True,
        "bitfinex_live_disabled": status.get("bitfinex_live_enabled") is False,
        "live_disarmed": status.get("live_armed") is False,
    }
    return sorted(name for name, passed in checks.items() if not passed)


def readiness_failures(status: dict) -> list[str]:
    pipeline = status.get("lifecycle_pipeline") or {}
    bootstrap = pipeline.get("receipt_bootstrap") or {}
    revision = str(status.get("source_git_rev") or "").strip().lower()
    checks = {
        "revision_reported": bool(re.fullmatch(r"[0-9a-f]{12}|[0-9a-f]{40}", revision)),
        "process_alive": status.get("process_alive") is True,
        "system_ready": status.get("system_ready") is True,
        "lifecycle_owner": pipeline.get("owner") is True,
        "lifecycle_running": pipeline.get("running") is True,
        "bootstrap_not_blocked": bootstrap.get("blocked") is not True and bootstrap.get("status") != "BLOCKED",
        "bootstrap_complete_if_required": bootstrap.get("required") is not True or (
            bootstrap.get("status") == "COMPLETE" and bootstrap.get("complete") is True
        ),
    }
    return sorted(name for name, passed in checks.items() if not passed)


def is_active(status: dict) -> bool:
    return status.get("execution_paused") is False and status.get("manual_admin_pause") is False


def _observe(request_json, *, until, monotonic, sleep) -> dict:
    attempt = 0
    last = None
    while monotonic() < until:
        attempt += 1
        try:
            status = request_json("/api/status", None)
            if isinstance(status, dict):
                return status
            last = TypeError("status is not an object")
        except Exception as exc:
            if not _transient(exc):
                raise
            last = exc
        print(f"failure-resume status attempt={attempt} error={type(last).__name__}", flush=True)
        sleep(min(2 ** min(attempt - 1, 4), max(0.0, until - monotonic())))
    raise RuntimeError(f"failure-resume status unavailable: {type(last).__name__}")


def guaranteed_resume(request_json, prior: dict, *, monotonic=time.monotonic, sleep=time.sleep, timeout=12 * 60) -> dict:
    deadline = monotonic() + timeout
    posts = 0
    last_failed: list[str] = []
    while monotonic() < deadline:
        status = _observe(request_json, until=deadline, monotonic=monotonic, sleep=sleep)
        unsafe = identity_failures(status)
        if unsafe:
            raise RuntimeError("failure-resume refused non-paper identity: " + json.dumps(unsafe))
        revision = str(status.get("source_git_rev") or "")[:12]
        if is_active(status):
            return {"status": "ACTIVE", "revision": revision, "resume_posts": posts}
        owner = str(status.get("pause_owner") or "").upper()
        if owner in RETAINED_OWNERS:
            return {"status": "OPERATOR_PAUSE_RETAINED", "pause_owner": owner, "revision": revision}
        if prior.get("captured") is True and prior.get("operator_paused") is True:
            return {"status": "OPERATOR_PAUSE_RETAINED", "pause_owner": "PRIOR_OPERATOR", "revision": revision}
        last_failed = readiness_failures(status)
        if last_failed:
            print("failure-resume waiting for readiness: " + json.dumps(last_failed), flush=True)
            sleep(min(10, max(0.0, deadline - monotonic())))
            continue
        if posts >= MAX_RESUME_POSTS:
            raise RuntimeError("failure-resume exhausted bounded resume attempts")
        posts += 1
        try:
            resumed = request_json("/api/resume", RESUME_PAYLOAD)
        except Exception as exc:
            # Deploy-owned resume is idempotent and cannot clear an operator
            # or safety pause, so an ambiguous response is re-observed and
            # only then retried.
            if not _transient(exc):
                raise
            print(f"failure-resume post={posts} unconfirmed error={type(exc).__name__}", flush=True)
            sleep(3)
            continue
        if resumed.get("status") == "operator_pause_retained":
            return {"status": "OPERATOR_PAUSE_RETAINED", "pause_owner": resumed.get("pause_owner"), "revision": revision}
        print(f"failure-resume post={posts} response={resumed.get('status')}", flush=True)
        sleep(3)
    raise RuntimeError("failure-resume deadline expired: " + json.dumps(last_failed))


def main() -> int:
    token = str(os.environ.get("BOT_ADMIN_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("BOT_ADMIN_TOKEN is missing")
    prior = parse_prior(os.environ.get("PRIOR_OPERATOR_STATE", ""))
    timeout = int(os.environ.get("FAILURE_RESUME_TIMEOUT_SEC") or 12 * 60)
    try:
        result = guaranteed_resume(_http_clients(token), prior, timeout=timeout)
    except Exception as exc:
        print(f"::error::paper was NOT resumed after the failed deploy: {exc}", flush=True)
        raise
    print("failure-resume " + json.dumps(result, sort_keys=True), flush=True)
    if result["status"] == "ACTIVE":
        from fly_postdeploy_active_gate import enable_all_registry_tiles
        enable_all_registry_tiles(_http_clients(token))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
