"""Best-effort preserve of DEPLOY_MAINTENANCE after a failed guarded deploy.

The durable pause lease is ``manual_admin_pause`` + ``pause_intent`` on the Fly
volume config. Preserve must restore that lease when a deploy fails mid-flight
AND paper is still supposed to be held.

It must NOT re-assert the lease after a clear deploy-owned or admin resume has
already released paper on a healthy paper-only identity. That race is what made
Danish's mid-bootstrap ``/api/resume`` flip back within seconds: the bootstrap
gate failed with "lost exact-revision flat maintenance", then this path POSTed
``/api/pause`` again while Guaranteed paper resume was still blocked on
bootstrap completeness.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request

from fly_resume_bootstrap import _http_clients, _transient


def identity_safe_flat(status: dict) -> tuple[bool, dict]:
    """Paper-only, disarmed, flat — safe to leave running or to hold paused."""
    progress = status.get("strategy_progress") or {}
    open_positions = progress.get("open_positions")
    pending_orders = progress.get("pending_orders")
    counts_valid = (
        isinstance(open_positions, int)
        and not isinstance(open_positions, bool)
        and open_positions >= 0
        and isinstance(pending_orders, int)
        and not isinstance(pending_orders, bool)
        and pending_orders >= 0
    )
    checks = {
        "force_paper_mode": status.get("force_paper_mode") is True,
        "bitfinex_live_disabled": status.get("bitfinex_live_enabled") is False,
        "live_disarmed": status.get("live_armed") is False,
        "counts_valid": counts_valid,
        "open_positions_zero": counts_valid and open_positions == 0,
        "pending_orders_zero": counts_valid and pending_orders == 0,
    }
    failed = sorted(name for name, ok in checks.items() if not ok)
    return not failed, {"failed": failed, "open_positions": open_positions, "pending_orders": pending_orders}


def already_resumed_safe(status: dict) -> bool:
    """True when a clear resume already released the deploy lease on safe paper."""
    if status.get("execution_paused") is not False:
        return False
    if status.get("manual_admin_pause") is not False:
        return False
    safe, _ = identity_safe_flat(status)
    return safe


def preserve_maintenance(request_json, *, attempts: int = 5, sleep=time.sleep) -> dict:
    """Restore DEPLOY_MAINTENANCE only when paper is not already safely resumed.

    Returns a result dict with ``status`` in:
    - ``SKIPPED_ALREADY_RESUMED`` — lease left cleared; do not re-assert
    - ``PRESERVED`` — pause posted and verified
    """
    last = None
    for attempt in range(1, max(1, int(attempts)) + 1):
        try:
            before = request_json("/api/status?fresh=1", None)
            if already_resumed_safe(before):
                print(
                    "preserve-maintenance skipped: paper already resumed on safe "
                    "paper-only identity (will not re-assert DEPLOY_MAINTENANCE)",
                    flush=True,
                )
                return {
                    "status": "SKIPPED_ALREADY_RESUMED",
                    "revision": str(before.get("source_git_rev") or "")[:12],
                }
            safe, detail = identity_safe_flat(before)
            if not safe:
                raise RuntimeError(
                    "preserve-maintenance refused unsafe identity: "
                    + json.dumps(detail, sort_keys=True)
                )
            paused = request_json("/api/pause", {"owner": "DEPLOY_MAINTENANCE"})
            if paused.get("status") != "paused" or paused.get("execution_paused") is not True:
                raise RuntimeError("preserve-maintenance pause was not acknowledged")
            final = request_json("/api/status?fresh=1", None)
            final_safe, final_detail = identity_safe_flat(final)
            if not (
                final_safe
                and final.get("execution_paused") is True
                and final.get("manual_admin_pause") is True
            ):
                raise RuntimeError(
                    "preserve-maintenance was not verified: "
                    + json.dumps(final_detail, sort_keys=True)
                )
            print(
                "Failure path preserved paper-only maintenance; no unaccepted revision was resumed",
                flush=True,
            )
            return {
                "status": "PRESERVED",
                "revision": str(final.get("source_git_rev") or "")[:12],
            }
        except Exception as exc:
            if not _transient(exc) and attempt >= attempts:
                raise
            last = exc
            print(
                f"preserve-maintenance attempt={attempt} error={type(exc).__name__}",
                flush=True,
            )
            sleep(min(attempt, 5))
    raise RuntimeError(f"preserve-maintenance failed: {type(last).__name__}")


def main() -> int:
    token = str(os.environ.get("BOT_ADMIN_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("BOT_ADMIN_TOKEN is missing")
    result = preserve_maintenance(_http_clients(token))
    print("preserve-maintenance " + json.dumps(result, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
