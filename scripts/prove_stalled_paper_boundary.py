#!/usr/bin/env python3
"""Fail-closed Path C gate for a paper-only pending stall.

Eligibility is read only from https://doxed-btc-bot.fly.dev/ready.
The process-liveness probe is not an eligibility source: on the running
image it is PROCESS_LIVENESS_ONLY and omits strategy_progress.

Live GET of /ready (Fly image 37f3a57a) has no top-level force_paper_mode.
The equivalent force-paper flag is both live_entry_arm_block_reason and
trading_block_reason equal to FORCE_PAPER_MODE (can_open_live_entry returns
that reason first when force-paper is active). strategy_progress.live_armed
is the live disarm flag. If a payload also carries force_paper_mode or
top-level live_armed, those must agree and stay disarmed.

Stall window, accepted evidence only:
  * Newest 30 completed runs of .github/workflows/fly-bot-deploy.yml.
  * A run counts only when the step "Prove the current Fly owner and every
    relay account are flat" finished success or failure. Skipped steps
    (recover dispatches) are ignored.
  * The latest such step must be a failure whose deploy log contains the
    flat-check JSON showcase.positions == 0 and showcase.pendingOrders > 0.
    That is the tip-block class. Other flat-check failures do not qualify.
  * That newest qualifying failure must be at most 6 hours old.
  * The oldest qualifying failure in the same streak (newer than the latest
    flat-check success, if any) must be at least 2 hours old.
  * When two or more qualifying logs exist, newest showcase pendingOrders
    must be >= the oldest. A drop is a net drain and is refused. One log
    cannot show an integer change; the 2 hour age plus a current /ready
    book that is still open=0 and pending>0 is the window.
  * Showcase counts are taken only from those already recorded deploy logs.
    This gate does not call the showcase relay route. Open and pending for
    eligibility come only from /ready strategy_progress.

This class does not require an incident latch or a trade-lock reason.
pending>0 alone is refused. live_armed true is always refused.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

READY_URL = "https://doxed-btc-bot.fly.dev/ready"
WORKFLOW_FILE = "fly-bot-deploy.yml"
FLAT_CHECK_STEP_NAME = "Prove the current Fly owner and every relay account are flat"
FORCE_PAPER_REASON = "FORCE_PAPER_MODE"
STALL_WINDOW = timedelta(hours=2)
RECENT_FLAT_CHECK_MAX_AGE = timedelta(hours=6)
MAX_COMPLETED_RUNS = 30
_GHA_LOG_PREFIX = re.compile(
    r"^[^\t]*\t[^\t]*\t\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\s?"
)


@dataclass(frozen=True)
class FlatCheckObservation:
    run_id: int
    conclusion: str
    completed_at: datetime
    showcase_positions: int | None = None
    showcase_pending_orders: int | None = None
    run_url: str = ""


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def ready_eligibility_failures(ready: object) -> list[str]:
    """Return reasons the /ready payload is outside the Path C class."""
    if not isinstance(ready, dict):
        return ["ready payload is not an object"]
    progress = ready.get("strategy_progress")
    if not isinstance(progress, dict):
        return ["ready.strategy_progress is missing"]

    reasons: list[str] = []
    if progress.get("live_armed") is not False:
        reasons.append("strategy_progress.live_armed must be false")
    if "live_armed" in ready and ready.get("live_armed") is not False:
        reasons.append("top-level live_armed must be false")
    if ready.get("bitfinex_live_enabled") is True or progress.get("bitfinex_live_enabled") is True:
        reasons.append("bitfinex_live_enabled must be false")
    if ready.get("live_entry_armable") is True or ready.get("trading_ready") is True:
        reasons.append("live entry must stay disarmed")

    if "force_paper_mode" in ready:
        if ready.get("force_paper_mode") is not True:
            reasons.append("force_paper_mode must be true")
    elif (
        ready.get("live_entry_arm_block_reason") != FORCE_PAPER_REASON
        or ready.get("trading_block_reason") != FORCE_PAPER_REASON
    ):
        reasons.append(
            "force-paper flag missing: need force_paper_mode true or both "
            "live_entry_arm_block_reason and trading_block_reason equal to "
            "FORCE_PAPER_MODE"
        )

    open_positions = _as_int(progress.get("open_positions"))
    pending_orders = _as_int(progress.get("pending_orders"))
    if open_positions != 0:
        reasons.append("strategy_progress.open_positions must be 0")
    if pending_orders is None or pending_orders <= 0:
        reasons.append("strategy_progress.pending_orders must be > 0")
    return reasons


def _qualifies(obs: FlatCheckObservation) -> bool:
    return (
        obs.conclusion == "failure"
        and obs.showcase_positions == 0
        and obs.showcase_pending_orders is not None
        and obs.showcase_pending_orders > 0
    )


def stall_window_failures(
    observations: list[FlatCheckObservation],
    now: datetime,
) -> tuple[list[str], dict]:
    """Prove the tip-block stall window. Empty reasons means proven."""
    if now.tzinfo is None:
        return ["stall clock must be timezone-aware"], {}
    ran = [
        obs
        for obs in observations
        if obs.conclusion in {"success", "failure", "cancelled"}
    ]
    if not ran:
        return ["no completed flat-check step in the newest deploy runs"], {}
    ran.sort(key=lambda obs: obs.completed_at, reverse=True)
    latest = ran[0]
    if latest.completed_at > now:
        return ["flat-check timestamp is in the future"], {}
    if latest.conclusion == "success":
        return ["latest flat-check succeeded; tip is not blocked"], {}
    if not _qualifies(latest):
        return [
            "latest flat-check is not a paper pending stall "
            "(deploy log must show showcase positions=0 and pendingOrders>0)"
        ], {}

    streak: list[FlatCheckObservation] = []
    for obs in ran:
        if obs.conclusion == "success":
            break
        if obs.completed_at > now:
            return ["flat-check timestamp is in the future"], {}
        if _qualifies(obs):
            streak.append(obs)
    newest = streak[0]
    oldest = streak[-1]
    age_newest = now - newest.completed_at
    age_oldest = now - oldest.completed_at
    if age_newest > RECENT_FLAT_CHECK_MAX_AGE:
        return [
            "pending-stall flat-check is stale "
            f"({age_newest} old; need one within {RECENT_FLAT_CHECK_MAX_AGE})"
        ], {}
    if age_oldest < STALL_WINDOW:
        return [
            "stall window is not proven "
            f"({age_oldest} since the earliest pending-stall flat-check; "
            f"need at least {STALL_WINDOW})"
        ], {}
    if newest.showcase_pending_orders < oldest.showcase_pending_orders:
        return [
            "showcase pendingOrders net drain "
            f"{oldest.showcase_pending_orders} -> {newest.showcase_pending_orders}"
        ], {}
    evidence = {
        "newest_run_id": newest.run_id,
        "oldest_run_id": oldest.run_id,
        "newest_run_url": newest.run_url,
        "oldest_run_url": oldest.run_url,
        "newest_completed_at": newest.completed_at.isoformat(),
        "oldest_completed_at": oldest.completed_at.isoformat(),
        "window_sec": int(age_oldest.total_seconds()),
        "showcase_pending_newest": newest.showcase_pending_orders,
        "showcase_pending_oldest": oldest.showcase_pending_orders,
        "showcase_positions": 0,
        "qualifying_runs": len(streak),
    }
    return [], evidence


def strip_actions_log(text: str) -> str:
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        lines.append(_GHA_LOG_PREFIX.sub("", line))
    return "\n".join(lines)


_SHOWCASE_BLOCK = re.compile(r'"showcase"\s*:\s*\{([^{}]*)\}', re.DOTALL)
_SHOWCASE_POSITIONS = re.compile(r'"positions"\s*:\s*(-?\d+|null)\b')
_SHOWCASE_PENDING = re.compile(r'"pendingOrders"\s*:\s*(-?\d+|null)\b')


def parse_showcase_counts(log_text: str) -> tuple[int, int] | None:
    """Return (positions, pendingOrders) from a flat-check deploy log.

    Actions wraps each line and prints ``bash -e {0}`` before the script's
    JSON, so the first brace is not the flat-check document. The showcase
    object is flat; the last one in the log is the step's own output.
    """
    body = strip_actions_log(log_text)
    blocks = list(_SHOWCASE_BLOCK.finditer(body))
    if not blocks:
        return None
    block = blocks[-1].group(1)
    positions_match = _SHOWCASE_POSITIONS.search(block)
    pending_match = _SHOWCASE_PENDING.search(block)
    if positions_match is None or pending_match is None:
        return None
    if positions_match.group(1) == "null" or pending_match.group(1) == "null":
        return None
    return int(positions_match.group(1)), int(pending_match.group(1))


def parse_github_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp lacks a timezone: {value}")
    return parsed.astimezone(timezone.utc)


def _github_request(url: str, token: str, accept: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "prove-stalled-paper-boundary",
        },
    )

    class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
            if redirected is not None:
                redirected.remove_header("Authorization")
            return redirected

    opener = urllib.request.build_opener(_DropAuthOnRedirect)
    with opener.open(request, timeout=30) as response:
        return response.read()


def _github_json(url: str, token: str) -> dict:
    raw = _github_request(url, token, "application/vnd.github+json")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"GitHub API returned a non-object for {url}")
    return payload


def _flat_check_step(job: dict) -> dict | None:
    for step in job.get("steps") or []:
        if isinstance(step, dict) and step.get("name") == FLAT_CHECK_STEP_NAME:
            return step
    return None


_ZIP_MAGIC = b"PK\x03\x04"
_LOG_FETCH_ATTEMPTS = 3
_LOG_FETCH_SLEEP_SEC = 2


def _obvious_log_error_page(body: bytes) -> bool:
    """True for an HTML document or a GitHub JSON error, not a job log."""
    sample = body.lstrip()[:64].lower()
    if sample.startswith((b"<!doctype", b"<html", b"<head", b"<?xml")):
        return True
    if len(body) > 65536 or not sample.startswith((b"{", b"[")):
        return False
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict) or "showcase" in payload:
        return False
    if "documentation_url" in payload:
        return True
    return isinstance(payload.get("message"), str)


def _log_text_from_bytes(log_bytes: bytes) -> str:
    """Decode a job-log download that is either a zip bundle or plain text.

    GitHub's logs redirect returns a zip when the body starts with the ZIP
    local-file magic, and plain text otherwise. A corrupt zip raises
    SystemExit instead of zipfile.BadZipFile.
    """
    if not log_bytes:
        raise SystemExit("job logs download failed closed: empty body")
    if _obvious_log_error_page(log_bytes):
        raise SystemExit("job logs download failed closed: HTML or JSON error page")
    if log_bytes.startswith(_ZIP_MAGIC):
        try:
            with zipfile.ZipFile(io.BytesIO(log_bytes)) as bundle:
                chunks = []
                for name in bundle.namelist():
                    if name.endswith("/"):
                        continue
                    chunks.append(bundle.read(name).decode("utf-8", "replace"))
        except zipfile.BadZipFile:
            raise SystemExit(
                "job logs download failed closed: file is not a readable zip"
            ) from None
        return "\n".join(chunks)
    return log_bytes.decode("utf-8", "replace")


def _showcase_from_job_logs(log_bytes: bytes) -> tuple[int, int] | None:
    return parse_showcase_counts(_log_text_from_bytes(log_bytes))


def _fetch_job_log_bytes(
    url: str,
    token: str,
    *,
    attempts: int = _LOG_FETCH_ATTEMPTS,
    sleep_sec: float = _LOG_FETCH_SLEEP_SEC,
) -> bytes:
    """GET job logs, retrying empty, error-page, and transient responses."""
    last = "no attempt"
    for attempt in range(1, attempts + 1):
        try:
            body = _github_request(url, token, "application/vnd.github+json")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = f"transient failure: {exc}"
        else:
            if not isinstance(body, (bytes, bytearray)):
                last = "transient failure: logs response was not bytes"
            else:
                body = bytes(body)
                if not body:
                    last = "empty body"
                else:
                    try:
                        _log_text_from_bytes(body)
                    except SystemExit as exc:
                        last = str(exc)
                    else:
                        return body
        if attempt < attempts:
            time.sleep(sleep_sec)
    raise SystemExit(
        f"job logs download failed closed after {attempts} attempts: {last}"
    )


def fetch_flat_check_observations(
    repository: str,
    token: str,
    *,
    max_runs: int = MAX_COMPLETED_RUNS,
) -> list[FlatCheckObservation]:
    runs_url = (
        "https://api.github.com/repos/"
        f"{repository}/actions/workflows/{WORKFLOW_FILE}/runs"
        f"?status=completed&per_page={max_runs}"
    )
    listed = _github_json(runs_url, token)
    runs = listed.get("workflow_runs")
    if not isinstance(runs, list):
        raise SystemExit("GitHub Actions run list missing workflow_runs")
    runs = [run for run in runs if isinstance(run, dict)]
    runs.sort(key=lambda run: str(run.get("created_at") or ""), reverse=True)

    observations: list[FlatCheckObservation] = []
    for run in runs[:max_runs]:
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        jobs_payload = _github_json(
            f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/jobs",
            token,
        )
        jobs = jobs_payload.get("jobs")
        if not isinstance(jobs, list):
            raise SystemExit(f"run {run_id} jobs payload is missing")
        step = None
        job_id = None
        for job in jobs:
            if not isinstance(job, dict):
                continue
            found = _flat_check_step(job)
            if found is not None:
                step = found
                job_id = job.get("id")
                break
        if step is None:
            continue
        conclusion = str(step.get("conclusion") or "")
        if conclusion == "skipped" or conclusion not in {"success", "failure", "cancelled"}:
            continue
        completed_raw = step.get("completed_at")
        if not isinstance(completed_raw, str) or not completed_raw:
            raise SystemExit(f"run {run_id} flat-check step has no completed_at")
        positions = None
        pending = None
        if conclusion == "failure":
            if not isinstance(job_id, int):
                raise SystemExit(f"run {run_id} flat-check failure has no job id")
            log_bytes = _fetch_job_log_bytes(
                f"https://api.github.com/repos/{repository}/actions/jobs/{job_id}/logs",
                token,
            )
            parsed = _showcase_from_job_logs(log_bytes)
            if parsed is not None:
                positions, pending = parsed
        observations.append(
            FlatCheckObservation(
                run_id=run_id,
                conclusion=conclusion,
                completed_at=parse_github_time(completed_raw),
                showcase_positions=positions,
                showcase_pending_orders=pending,
                run_url=str(run.get("html_url") or ""),
            )
        )
        if conclusion == "success":
            break
    return observations


def fetch_ready(url: str = READY_URL) -> dict:
    last = "no attempt"
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(url, timeout=20) as response:
                payload = json.load(response)
                status = getattr(response, "status", 200)
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read().decode("utf-8", "replace")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                last = f"HTTP {status} was not JSON"
                time.sleep(3)
                continue
        except Exception as exc:
            last = str(exc)
            time.sleep(3)
            continue
        if isinstance(payload, dict) and isinstance(payload.get("strategy_progress"), dict):
            return payload
        last = f"attempt {attempt} HTTP {status} missing strategy_progress"
        time.sleep(3)
    raise SystemExit(f"unable to read ready strategy_progress: {last}")


def main() -> None:
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    repository = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    if not token:
        raise SystemExit("GITHUB_TOKEN missing; stall window cannot be proven")
    if not repository or repository.count("/") != 1:
        raise SystemExit("GITHUB_REPOSITORY missing; stall window cannot be proven")

    now = datetime.now(timezone.utc)
    ready = fetch_ready()
    observations = fetch_flat_check_observations(repository, token)
    reasons = ready_eligibility_failures(ready)
    stall_reasons, evidence = stall_window_failures(observations, now)
    reasons.extend(stall_reasons)
    if reasons:
        raise SystemExit("paper pending-stall recover refused: " + "; ".join(reasons))

    progress = ready["strategy_progress"]
    summary = {
        "recovery": "paper_pending_stall",
        "ready_url": READY_URL,
        "source_git_rev": ready.get("source_git_rev"),
        "live_armed": progress.get("live_armed"),
        "force_paper_mode": ready.get("force_paper_mode", FORCE_PAPER_REASON),
        "open_positions": progress.get("open_positions"),
        "pending_orders": progress.get("pending_orders"),
        "stall": evidence,
    }
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
