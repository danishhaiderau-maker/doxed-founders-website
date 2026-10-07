"""Fail-closed validation shared by the scheduled Fly monitor jobs."""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Sequence


class MonitorContractError(ValueError):
    """A monitor endpoint did not provide the required current safe receipt."""


def require_health(payload: Any, *, status: int) -> Mapping[str, Any]:
    if status != 200 or not isinstance(payload, dict):
        raise MonitorContractError("Fly /health liveness response is unavailable")
    if payload.get("probe_contract") != "PROCESS_LIVENESS_ONLY":
        raise MonitorContractError("Fly /health returned an unknown probe contract")
    if payload.get("process_alive") is not True:
        raise MonitorContractError("Fly process liveness failed")
    if not (
        payload.get("force_paper_mode") is True
        and payload.get("live_armed") is False
        and payload.get("bitfinex_live_enabled") is False
    ):
        raise MonitorContractError("Fly bot execution safety drift")
    return payload


def require_ready(payload: Any, *, status: int) -> Mapping[str, Any]:
    if status != 200 or not isinstance(payload, dict):
        raise MonitorContractError("Fly /ready response is unavailable or stale")
    if payload.get("ok") is not True or payload.get("process_ready") is not True:
        raise MonitorContractError("Fly strict strategy readiness failed")
    if not isinstance(payload.get("strategy_progress"), dict):
        raise MonitorContractError("Fly /ready omitted strategy_progress")
    if not isinstance(payload.get("active_tiles"), list):
        raise MonitorContractError("Fly /ready omitted active_tiles")
    return payload


def require_strategy_progress(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    progress = payload["strategy_progress"]
    if progress.get("ok") is not True:
        reasons = progress.get("reasons") or ["UNKNOWN_PROGRESS_FAILURE"]
        raise MonitorContractError(
            "Fly strategy progress stalled: "
            f"reasons={reasons} ws_age={progress.get('ws_age_sec')} "
            f"ai_age={progress.get('ai_age_sec')} "
            f"trade_lock_available={progress.get('trade_lock_available')} "
            f"positions={progress.get('open_positions')} "
            f"pending={progress.get('pending_orders')}"
        )
    return progress


DEPLOY_JOB_NAME = "test-and-deploy"
RESUME_BOOTSTRAP_JOB_NAME = "resume-bootstrap"


def _require_full_sha(run: Mapping[str, Any], sha: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise MonitorContractError(f"Deploy run {run.get('id')} has an invalid head SHA")


def resolve_deployed_revision(
    runs: Sequence[Mapping[str, Any]],
    jobs_for_run: Callable[[Any], Sequence[Mapping[str, Any]]],
    live_revision: str = "",
) -> tuple[str, str]:
    """Return (latest deployed SHA, in-flight deploy SHA or "").

    ``runs`` are fly-bot-deploy workflow runs, newest first. A run is a
    deploy-completion signal when either its ``test-and-deploy`` job succeeded
    (a normal guarded deploy), or its ``resume-bootstrap`` job succeeded and its
    head SHA equals ``live_revision`` (a fail-closed deploy that resumed paper
    without shipping a new image). Inspect/restart/flatten dispatches can
    succeed without deploying anything and are ignored.
    """
    in_flight = ""
    for run in runs:
        sha = str(run.get("head_sha") or "")
        jobs = jobs_for_run(run.get("id"))
        deploy_jobs = [j for j in jobs if j.get("name") == DEPLOY_JOB_NAME]
        if deploy_jobs:
            job = deploy_jobs[0]
            if run.get("status") != "completed":
                if not in_flight and job.get("conclusion") != "skipped":
                    in_flight = sha
                continue
            if run.get("conclusion") == "success" and job.get("conclusion") == "success":
                _require_full_sha(run, sha)
                return sha, in_flight
            continue
        resume_jobs = [j for j in jobs if j.get("name") == RESUME_BOOTSTRAP_JOB_NAME]
        if not resume_jobs:
            continue
        job = resume_jobs[0]
        if run.get("status") != "completed":
            continue
        if run.get("conclusion") == "success" and job.get("conclusion") == "success":
            # A resume-bootstrap continuation ships no image; it resumes paper on
            # the already-deployed revision. Accept it only when its head SHA is
            # what Fly actually reports, so a later unrelated commit cannot be
            # mistaken for the live revision.
            if not live_revision or sha != live_revision:
                continue
            _require_full_sha(run, sha)
            return sha, in_flight
    raise MonitorContractError("No successful Fly deploy run found")


def require_deployed_revision(actual: str, *, deployed: str, in_flight: str = "") -> None:
    if actual == deployed or (in_flight and actual == in_flight):
        return
    raise MonitorContractError(
        f"Fly revision drift: latest successful deploy {deployed[:12]}, Fly reports {actual[:12]}"
    )


def require_tile_registry(
    payload: Mapping[str, Any],
    *,
    expected_version: str,
    expected_signature: str,
    expected_lanes: Sequence[str],
) -> None:
    runtime_lanes = [row.get("lane") for row in payload["active_tiles"] if isinstance(row, dict)]
    if (
        payload.get("bot_version") != expected_version
        or payload.get("tile_registry_signature") != expected_signature
        or runtime_lanes != list(expected_lanes)
    ):
        raise MonitorContractError(
            "Fly tile registry drift: "
            f"version={payload.get('bot_version')} lanes={runtime_lanes} "
            f"signature={payload.get('tile_registry_signature')}"
        )
