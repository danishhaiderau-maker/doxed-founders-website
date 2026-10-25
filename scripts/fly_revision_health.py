"""Serving-revision deploy facts for system-health ``fly.revision``.

One helper (R4): match the Fly tip to its guarded ``fly-bot-deploy`` run and
derive ``deploy_run_id``, ``conclusion``, ``failed_steps``, and
``paper_resumed_by``. AMBER when that deploy did not conclude ``success``.
Read-only: never starts or cancels workflows.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

# Step / job names that prove how paper was resumed after a deploy attempt.
_ACCEPTANCE_RESUME = "Resume paper execution after exact-revision acceptance"
_GUARANTEED_RESUME = "Guaranteed paper resume after failed guarded deploy"
_RESUME_PAPER_JOB = "resume-paper"

PAPER_RESUMED_BY_ACCEPTANCE = "acceptance"
PAPER_RESUMED_BY_GUARANTEED = "guaranteed"
PAPER_RESUMED_BY_RESUME_PAPER = "resume-paper"


def _norm_sha(value: Any) -> str:
    return str(value or "").strip().lower()


def shas_match(a: Any, b: Any) -> bool:
    """True when short/full git SHAs name the same commit."""
    left, right = _norm_sha(a), _norm_sha(b)
    if not left or not right:
        return False
    return left.startswith(right) or right.startswith(left)


def run_id(run: Mapping[str, Any] | None) -> int | str | None:
    if not isinstance(run, Mapping):
        return None
    for key in ("databaseId", "id", "deploy_run_id"):
        value = run.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return str(value)[:32]
    return None


def match_serving_deploy(
    fly_rev: str,
    runs: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any] | None:
    """Newest completed guarded-deploy run whose headSha matches the serving tip."""
    tip = _norm_sha(fly_rev)
    if not tip or len(tip) < 7:
        return None
    matched: list[dict[str, Any]] = []
    for raw in runs or []:
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("status") or "") != "completed":
            continue
        if not shas_match(tip, raw.get("headSha") or raw.get("head_sha")):
            continue
        matched.append(dict(raw))
    if not matched:
        return None
    matched.sort(key=lambda r: str(r.get("createdAt") or r.get("created_at") or ""), reverse=True)
    return matched[0]


def failed_steps_from_jobs(jobs: Sequence[Mapping[str, Any]] | None) -> list[str]:
    """Step names that concluded failure / cancelled / timed_out (bounded)."""
    out: list[str] = []
    seen: set[str] = set()
    for job in jobs or []:
        if not isinstance(job, Mapping):
            continue
        for step in job.get("steps") or []:
            if not isinstance(step, Mapping):
                continue
            conclusion = str(step.get("conclusion") or "").lower()
            if conclusion not in ("failure", "cancelled", "timed_out", "startup_failure"):
                continue
            name = str(step.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(name)
            if len(out) >= 12:
                return out
    return out


def paper_resumed_by_from_jobs(jobs: Sequence[Mapping[str, Any]] | None) -> str | None:
    """Who released paper after the deploy attempt (acceptance / guaranteed / resume-paper)."""
    acceptance = False
    guaranteed = False
    resume_paper = False
    for job in jobs or []:
        if not isinstance(job, Mapping):
            continue
        job_name = str(job.get("name") or "")
        job_conclusion = str(job.get("conclusion") or "").lower()
        if job_name == _RESUME_PAPER_JOB and job_conclusion == "success":
            resume_paper = True
        for step in job.get("steps") or []:
            if not isinstance(step, Mapping):
                continue
            if str(step.get("conclusion") or "").lower() != "success":
                continue
            name = str(step.get("name") or "")
            if name == _ACCEPTANCE_RESUME:
                acceptance = True
            elif name == _GUARANTEED_RESUME:
                guaranteed = True
    # Prefer the success-path resume when both somehow ran; else guaranteed; else resume-paper mode.
    if acceptance:
        return PAPER_RESUMED_BY_ACCEPTANCE
    if guaranteed:
        return PAPER_RESUMED_BY_GUARANTEED
    if resume_paper:
        return PAPER_RESUMED_BY_RESUME_PAPER
    return None


def deploy_did_not_succeed(conclusion: Any, *, status: Any = "completed") -> bool:
    """True when a completed guarded deploy did not conclude success."""
    if str(status or "") not in ("", "completed"):
        return False
    return str(conclusion or "").lower() not in ("", "success")


def serving_revision_fields(
    fly_rev: str,
    deploy_snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Structured fields for ``fly.revision`` observed_fields / observed text.

    Prefers an explicit ``serving_deploy`` block on the laptop snapshot (includes
    jobs). Falls back to matching ``runs`` by headSha when that block is absent.
    """
    tip = _norm_sha(fly_rev)
    snap = deploy_snapshot if isinstance(deploy_snapshot, Mapping) else {}
    serving = snap.get("serving_deploy") if isinstance(snap.get("serving_deploy"), Mapping) else None
    run: Mapping[str, Any] | None = None
    if serving and shas_match(tip, serving.get("headSha") or serving.get("head_sha")):
        run = serving
    else:
        run = match_serving_deploy(tip, snap.get("runs") if isinstance(snap.get("runs"), list) else None)
        if run is None and serving and not tip:
            run = serving
    if run is None:
        return {
            "deploy_run_id": None,
            "conclusion": None,
            "failed_steps": [],
            "paper_resumed_by": None,
            "head_sha": tip[:40] or None,
            "matched": False,
        }
    jobs = run.get("jobs") if isinstance(run.get("jobs"), list) else []
    conclusion = run.get("conclusion")
    return {
        "deploy_run_id": run_id(run),
        "conclusion": None if conclusion is None else str(conclusion),
        "failed_steps": failed_steps_from_jobs(jobs),
        "paper_resumed_by": paper_resumed_by_from_jobs(jobs),
        "head_sha": _norm_sha(run.get("headSha") or run.get("head_sha"))[:40] or tip[:40] or None,
        "matched": True,
        "status": str(run.get("status") or "completed"),
        "event": run.get("event"),
    }


def format_revision_observed(
    *,
    fly_rev: str,
    master_sha: str,
    fields: Mapping[str, Any],
) -> str:
    """Human observed line for the banner (includes the four serving-deploy fields)."""
    tip = _norm_sha(fly_rev)
    master = _norm_sha(master_sha)
    same = bool(master) and shas_match(tip, master)
    obs = (
        f"fly={tip[:12] or '?'} master={master[:12] or '?'}"
        f"{' (current)' if same else ' (master ahead; deploy queued)'}"
    )
    if not fields.get("matched"):
        return obs + "; serving deploy run unknown"
    rid = fields.get("deploy_run_id")
    conclusion = fields.get("conclusion") or "?"
    obs += f"; deploy_run_id={rid} conclusion={conclusion}"
    steps = list(fields.get("failed_steps") or [])
    if steps:
        obs += f"; failed_steps={steps[:6]}"
    resumed = fields.get("paper_resumed_by")
    if resumed:
        obs += f"; paper_resumed_by={resumed}"
    return obs
