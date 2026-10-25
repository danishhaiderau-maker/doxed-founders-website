"""Exact-revision receipt-bootstrap gate for guarded Fly deploys.

Waits for receipt bootstrap COMPLETE while paper stays in DEPLOY_MAINTENANCE.
Fails loudly and immediately when the lifecycle worker is terminal-stalled
(e.g. SOURCE_LEDGER_TRUNCATED with zero progress) so the deploy's failure-path
resume can release the pause instead of sitting idle for the full 90-minute
budget.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

from fly_resume_bootstrap import _http_clients, _transient


# Worker failures that cannot make bootstrap progress without an explicit
# repair. Matching is by prefix so "SOURCE_LEDGER_TRUNCATED:order_intent.jsonl"
# is covered by the bare code.
TERMINAL_BOOTSTRAP_ERROR_PREFIXES = (
    "SOURCE_LEDGER_TRUNCATED",
    "SOURCE_LEDGER_ROTATED",
    "SOURCE_LEDGER_PREFIX_CHANGED",
    "TRUNCATED_JSONL_LINE",
)


def _error_code(status: dict) -> str:
    pipeline = status.get("lifecycle_pipeline") or {}
    return str(pipeline.get("last_error_code") or "").strip().upper()


def bootstrap_terminal_stall(status: dict) -> dict | None:
    """Return a diagnostic when required bootstrap cannot progress, else None.

    A stall is: bootstrap still required/incomplete/unblocked, the lifecycle
    worker's last outcome is WORKER_FAILED, the error code is a terminal ledger
    fault, and zero bytes/records have been indexed. Healthy in-progress
    bootstrap (RUNNING with progress, or PENDING without a terminal fault)
    returns None so the gate keeps waiting.
    """
    pipeline = status.get("lifecycle_pipeline") or {}
    bootstrap = pipeline.get("receipt_bootstrap") or {}
    if bootstrap.get("required") is not True:
        return None
    if bootstrap.get("complete") is True or bootstrap.get("status") == "COMPLETE":
        return None
    if bootstrap.get("blocked") is True or bootstrap.get("status") == "BLOCKED":
        return None
    if str(pipeline.get("last_outcome") or "").upper() != "WORKER_FAILED":
        return None
    code = _error_code(status)
    if not code or not any(code == prefix or code.startswith(prefix + ":")
                           for prefix in TERMINAL_BOOTSTRAP_ERROR_PREFIXES):
        return None
    records = bootstrap.get("records_indexed")
    bytes_indexed = bootstrap.get("bytes_indexed")
    no_progress = (
        (not isinstance(records, int) or isinstance(records, bool) or records <= 0)
        and (not isinstance(bytes_indexed, int) or isinstance(bytes_indexed, bool) or bytes_indexed <= 0)
    )
    if not no_progress:
        return None
    return {
        "code": "RECEIPT_BOOTSTRAP_TERMINAL_STALL",
        "last_outcome": pipeline.get("last_outcome"),
        "last_error_code": pipeline.get("last_error_code"),
        "failure_count": pipeline.get("failure_count"),
        "backoff_sec": pipeline.get("backoff_sec"),
        "receipt_bootstrap": {
            "required": bootstrap.get("required"),
            "status": bootstrap.get("status"),
            "complete": bootstrap.get("complete"),
            "records_indexed": bootstrap.get("records_indexed"),
            "bytes_indexed": bootstrap.get("bytes_indexed"),
        },
    }


def bootstrap_complete(status: dict) -> bool:
    bootstrap = (status.get("lifecycle_pipeline") or {}).get("receipt_bootstrap") or {}
    return (
        bootstrap.get("required") is not True
        or (bootstrap.get("status") == "COMPLETE" and bootstrap.get("complete") is True)
    )


def maintenance_safe(status: dict, expected: str) -> tuple[bool, list[str]]:
    """Exact-revision, paper-only, flat, paused maintenance predicates."""
    expected = str(expected or "").strip().lower()
    progress = status.get("strategy_progress") or {}
    pipeline = status.get("lifecycle_pipeline") or {}

    def count(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    open_positions = count(progress.get("open_positions"))
    pending_orders = count(progress.get("pending_orders"))
    checks = {
        "revision_exact": str(status.get("source_git_rev") or "").lower() == expected,
        "force_paper_mode": status.get("force_paper_mode") is True,
        "bitfinex_live_disabled": status.get("bitfinex_live_enabled") is False,
        "live_disarmed": status.get("live_armed") is False,
        "execution_paused": status.get("execution_paused") is True,
        "manual_admin_pause": status.get("manual_admin_pause") is True,
        "open_positions_zero": open_positions == 0,
        "pending_orders_zero": pending_orders == 0,
        "lifecycle_owner": pipeline.get("owner") is True,
        "lifecycle_running": pipeline.get("running") is True,
        "lifecycle_revision_match": pipeline.get("source_revision_match") is True,
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    return not failed, failed


def observe_status(request_json, *, until, monotonic=time.monotonic, sleep=time.sleep) -> dict:
    attempt = 0
    last = None
    while monotonic() < until:
        attempt += 1
        try:
            status = request_json("/api/status?fresh=1", None)
            if isinstance(status, dict):
                return status
            last = TypeError("status is not an object")
        except Exception as exc:
            if not _transient(exc):
                raise
            last = exc
        delay = min(2 ** min(attempt - 1, 4), max(0.0, until - monotonic()))
        print(f"transient status observation attempt={attempt} error={type(last).__name__}", flush=True)
        if delay:
            sleep(delay)
    raise RuntimeError(f"bounded status observation unavailable: {type(last).__name__}")


def wait_for_bootstrap(
    request_json,
    expected: str,
    *,
    monotonic=time.monotonic,
    sleep=time.sleep,
    timeout=90 * 60,
    reassert_pause: bool = True,
) -> dict:
    """Block until bootstrap COMPLETE on exact-revision maintenance, or fail loudly."""
    expected = str(expected or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{12}", expected):
        raise RuntimeError("bootstrap gate requires an exact 12-character deployed revision")
    observed_bootstrap_rows = 21_353
    conservative_records_per_cycle = 512
    backlog_interval_seconds = 1
    minimum_cycles = (
        observed_bootstrap_rows + conservative_records_per_cycle - 1
    ) // conservative_records_per_cycle
    if timeout < minimum_cycles * backlog_interval_seconds:
        raise RuntimeError("configured bootstrap timeout cannot cover audited scheduling floor")
    deadline = monotonic() + timeout
    if reassert_pause:
        # Preceding postdeploy boundary is already paused; reassert once so this
        # gate cannot create a repeated persistent-config write/fsync loop.
        request_json("/api/pause", {"owner": "DEPLOY_MAINTENANCE"})
    last = None
    while monotonic() < deadline:
        status = observe_status(request_json, until=deadline, monotonic=monotonic, sleep=sleep)
        last = status
        safe, failed = maintenance_safe(status, expected)
        if not safe:
            raise RuntimeError(
                "receipt bootstrap lost exact-revision flat maintenance: "
                + json.dumps({"failed": failed}, sort_keys=True)
            )
        pipeline = status.get("lifecycle_pipeline") or {}
        bootstrap = pipeline.get("receipt_bootstrap") or {}
        print(json.dumps({
            "receipt_bootstrap": bootstrap,
            "last_outcome": pipeline.get("last_outcome"),
            "last_error_code": pipeline.get("last_error_code"),
            "revision": expected,
        }, sort_keys=True), flush=True)
        stall = bootstrap_terminal_stall(status)
        if stall is not None:
            raise RuntimeError(
                "receipt bootstrap terminal stall (fail loud; failure-path must release pause): "
                + json.dumps(stall, sort_keys=True, separators=(",", ":"))
            )
        if bootstrap.get("blocked") is True or bootstrap.get("status") == "BLOCKED":
            raise RuntimeError(
                "receipt bootstrap reported a fail-closed blocker: "
                + json.dumps(bootstrap, sort_keys=True)
            )
        if bootstrap_complete(status):
            print(f"Receipt bootstrap complete in maintenance on {expected}", flush=True)
            return status
        sleep(min(3, max(0.0, deadline - monotonic())))
    raise RuntimeError(
        "receipt bootstrap did not complete before bounded maintenance timeout: "
        + json.dumps((last or {}).get("lifecycle_pipeline") or {}, sort_keys=True)
    )


def main() -> int:
    token = str(os.environ.get("BOT_ADMIN_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("BOT_ADMIN_TOKEN is missing")
    expected = str(os.environ.get("EXPECTED_REVISION") or "").strip().lower()[:12]
    timeout = int(os.environ.get("BOOTSTRAP_TIMEOUT_SEC") or 90 * 60)
    wait_for_bootstrap(_http_clients(token), expected, timeout=timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
