"""Incident deduplication policy for the scheduled Fly monitor.

A condition alerts once when it has persisted long enough, then re-alerts at
most every ``realert_sec`` while it stays active. Transitional conditions are
suppressed while a guarded deploy or deploy-maintenance pause is active, but
only for a bounded grace window so a stuck deploy still alerts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

STATE_VERSION = 1
HOUR = 3600.0


@dataclass(frozen=True)
class Policy:
    min_runs: int
    min_age_sec: float
    realert_sec: float
    suppress_in_maintenance: bool


POLICIES: Mapping[str, Policy] = {
    # Paper-only / disarmed contract broken: never suppressed, alert at once.
    "safety": Policy(1, 0.0, 1 * HOUR, False),
    # Bot unreachable or process not alive after in-run retries.
    "unreachable": Policy(2, 10 * 60.0, 6 * HOUR, True),
    "process_down": Policy(2, 10 * 60.0, 6 * HOUR, True),
    # Strict strategy readiness / progress (restarts and WS gaps self-heal).
    "not_ready": Policy(3, 30 * 60.0, 6 * HOUR, True),
    "revision_drift": Policy(2, 20 * 60.0, 6 * HOUR, True),
    "registry_drift": Policy(2, 20 * 60.0, 6 * HOUR, True),
    # Emitted only once the pause has already lasted PAUSED_ALERT_SEC.
    "paper_paused": Policy(1, 0.0, 6 * HOUR, False),
    # The monitor itself cannot read GitHub deploy state.
    "monitor_error": Policy(3, 60 * 60.0, 12 * HOUR, False),
    # Volume pressure is monotonic and unaffected by deploys.
    "disk_warn": Policy(1, 0.0, 12 * HOUR, False),
    "disk_critical": Policy(1, 0.0, 2 * HOUR, False),
    # Emitted only once DEPLOY_MAINTENANCE has owned the pause for an hour;
    # maintenance must not suppress the alert for a stuck deploy itself.
    "deploy_stuck": Policy(1, 0.0, 3 * HOUR, False),
    "eval_stale": Policy(2, 15 * 60.0, 6 * HOUR, True),
    "ai_stale": Policy(2, 15 * 60.0, 6 * HOUR, True),
    # The bot raises this only after 10 min of failing calls with no success.
    "ai_no_success": Policy(1, 0.0, 3 * HOUR, True),
    # Informational (annotation only) until the segment pipeline is live.
    "transfer_lag": Policy(2, 60 * 60.0, 12 * HOUR, False),
    # Research collection quality; the bot already requires a 1h sample, and
    # a deploy/restart legitimately empties the window, so suppress in maintenance.
    "multiverse_empty_path": Policy(2, 30 * 60.0, 6 * HOUR, True),
    "multiverse_tape_source": Policy(2, 15 * 60.0, 6 * HOUR, True),
    "multiverse_worker_stalled": Policy(2, 15 * 60.0, 6 * HOUR, True),
    "multiverse_worker_restarted": Policy(1, 15 * 60.0, 6 * HOUR, True),
    "touch_grid_coverage": Policy(2, 30 * 60.0, 6 * HOUR, True),
    # Laptop supervisor dead-man heartbeat (it cannot report its own death).
    "laptop_silent": Policy(1, 0.0, 12 * HOUR, False),
    # Operator-requested end-to-end proof of the notification channel.
    "test_alert": Policy(1, 0.0, 0.0, False),
}
MAINTENANCE_GRACE_SEC = 90 * 60.0
PAUSED_ALERT_SEC = 2 * HOUR
CLEAR_RUNS_TO_RESOLVE = 2


def empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "conditions": {},
        "maintenance_since": None,
        "paused_since": None,
        "deploy_pause_since": None,
    }


def normalize_state(raw: Any, policies: Mapping[str, Policy] = POLICIES) -> dict[str, Any]:
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return empty_state()
    state = empty_state()
    conditions = raw.get("conditions")
    if isinstance(conditions, dict):
        state["conditions"] = {
            str(k): dict(v) for k, v in conditions.items() if isinstance(v, dict) and k in policies
        }
    for key in ("maintenance_since", "paused_since", "deploy_pause_since"):
        value = raw.get(key)
        state[key] = float(value) if isinstance(value, (int, float)) else None
    return state


def track_pause(state: dict[str, Any], paused: bool | None, now: float) -> float:
    """Record continuous pause duration; ``None`` (unknown) keeps the clock."""
    if paused is None:
        since = state.get("paused_since")
        return now - since if since is not None else 0.0
    if not paused:
        state["paused_since"] = None
        return 0.0
    if state.get("paused_since") is None:
        state["paused_since"] = now
    return now - state["paused_since"]


def evaluate(
    state: dict[str, Any],
    findings: Mapping[str, str],
    *,
    now: float,
    maintenance: bool,
    informational: frozenset[str] = frozenset(),
    policies: Mapping[str, Policy] = POLICIES,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Update ``state`` in place; return (decisions, resolved).

    Each decision has ``key``, ``message`` and ``action`` in
    {"alert", "known", "pending", "suppressed", "info"}. Only "alert" should
    fail the run; resolved entries are previously alerted conditions that
    cleared. Keys in ``informational`` are reported as "info" and never enter
    incident state, so they cannot open, re-alert, or hold open the issue.
    """
    findings = dict(findings)
    info = [
        {"key": key, "message": findings.pop(key), "action": "info"}
        for key in sorted(informational & findings.keys())
    ]
    if maintenance:
        if state.get("maintenance_since") is None:
            state["maintenance_since"] = now
    else:
        state["maintenance_since"] = None
    in_grace = bool(maintenance and now - state["maintenance_since"] < MAINTENANCE_GRACE_SEC)

    conditions: dict[str, dict[str, Any]] = state["conditions"]
    decisions: list[dict[str, Any]] = []
    for key, message in findings.items():
        policy = policies[key]
        entry = conditions.setdefault(key, {"first_seen": now, "runs": 0, "last_alert": None})
        entry["runs"] = int(entry.get("runs") or 0) + 1
        entry["clear_runs"] = 0
        entry["last_message"] = message
        last_alert = entry.get("last_alert")
        if policy.suppress_in_maintenance and in_grace and last_alert is None:
            action = "suppressed"
        elif last_alert is not None:
            action = "alert" if now - float(last_alert) >= policy.realert_sec else "known"
        elif entry["runs"] >= policy.min_runs and now - float(entry["first_seen"]) >= policy.min_age_sec:
            action = "alert"
        else:
            action = "pending"
        if action == "alert":
            entry["last_alert"] = now
        decisions.append({"key": key, "message": message, "action": action})

    resolved: list[dict[str, Any]] = []
    for key in [k for k in conditions if k not in findings]:
        entry = conditions[key]
        if entry.get("last_alert") is None:
            del conditions[key]
            continue
        entry["clear_runs"] = int(entry.get("clear_runs") or 0) + 1
        if entry["clear_runs"] >= CLEAR_RUNS_TO_RESOLVE:
            resolved.append({"key": key, "message": entry.get("last_message", "")})
            del conditions[key]
    return decisions + info, resolved


def active_alerted(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {k: v for k, v in state["conditions"].items() if v.get("last_alert") is not None}
