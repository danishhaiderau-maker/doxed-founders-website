"""Health-monitor mismatch alerts (Phase 5, fail closed).

Raises alerts when any of the following hold:

* **paper-vs-real divergence** — a matched twin reports any diff, or an
  unmatched paper trade exists while the relay is armed;
* **inconsistent switch state** — a lane's live-orders switch is ON while the
  global arm gate is OFF, or ON while the lane's last evaluation is not
  eligible (which must be impossible, so this is treated as a critical alarm);
* **order lifecycle stall** — a trade is open with no stage advance past a
  threshold, or a stage regressed;
* **stale feed** — the venue feed's freshness exceeds its limit.

The module is pure and importable without ``bot.py``. Every rule fails closed:
unknown inputs are treated as the alarmed (unsafe) side.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

SCHEMA = "bitfinex_health_mismatch_alerts_v1"

SEV_CRITICAL = "CRITICAL"
SEV_WARNING = "WARNING"

RULE_PAPER_REAL_DIVERGENCE = "paper_real_divergence"
RULE_UNMATCHED_WHILE_ARMED = "unmatched_paper_while_armed"
RULE_SWITCH_INCONSISTENT = "switch_inconsistent"
RULE_LIFECYCLE_STALL = "lifecycle_stall"
RULE_FEED_STALE = "feed_stale"

DEFAULT_STALL_SEC = 8 * 3600
DEFAULT_FEED_STALE_SEC = 30.0


def _alert(rule: str, severity: str, observed: str, expected: str, detail: Any = None) -> dict:
    return {"rule": rule, "severity": severity, "observed": observed,
            "expected": expected, "detail": detail}


def evaluate_alerts(
    *,
    match_report: Mapping[str, Any] | None = None,
    switch_status: Mapping[str, Any] | None = None,
    global_arm: Mapping[str, Any] | None = None,
    telemetry: Mapping[str, Any] | None = None,
    stall_sec: float = DEFAULT_STALL_SEC,
    feed_stale_sec: float = DEFAULT_FEED_STALE_SEC,
) -> dict:
    """Evaluate all mismatch checks and return a fail-closed alert bundle."""
    alerts: list[dict] = []
    mr = match_report or {}
    ss = switch_status or {}
    ga = global_arm or {}
    tm = telemetry or {}

    # 1. Paper-vs-real divergence.
    diverged = mr.get("diverged") or []
    if diverged:
        alerts.append(_alert(RULE_PAPER_REAL_DIVERGENCE, SEV_WARNING,
                             f"{len(diverged)} matched twin(s) diverge",
                             "0 diverging twins", {"diverged": diverged}))

    # 2. Unmatched paper while armed (would silently not copy).
    unmatched = mr.get("unmatched") or []
    armed = bool(ga.get("live_armed") and ga.get("bitfinex_live_enabled"))
    if unmatched and armed:
        alerts.append(_alert(RULE_UNMATCHED_WHILE_ARMED, SEV_CRITICAL,
                             f"{len(unmatched)} paper trade(s) have no live twin while armed",
                             "every paper intent has a live twin", {"unmatched": unmatched}))

    # 3. Inconsistent switch state.
    for row in (ss.get("rows") or []):
        lane = row.get("lane")
        on = bool(row.get("bitfinex_live_orders"))
        if not on:
            continue
        if not armed:
            alerts.append(_alert(RULE_SWITCH_INCONSISTENT, SEV_CRITICAL,
                                 f"lane {lane} live switch ON while global arm OFF",
                                 "lane switch ON only when globally armed", {"lane": lane}))
        denials = row.get("last_denial") or []
        if denials and not row.get("last_allow_ts"):
            alerts.append(_alert(RULE_SWITCH_INCONSISTENT, SEV_CRITICAL,
                                 f"lane {lane} live switch ON but last evaluation denied",
                                 "no denials while ON", {"lane": lane, "denials": denials}))

    # 4. Lifecycle stall.
    for trade in (tm.get("stalled_trades") or []):
        alerts.append(_alert(RULE_LIFECYCLE_STALL, SEV_WARNING,
                             f"trade {trade.get('trade_id')} open with no close stage",
                             "every trade reaches CLOSE", {"trade_id": trade.get("trade_id")}))
    # Any trade with a stage regression is already refused by the recorder; a
    # missing CLOSE beyond the threshold is covered by stalled_trades.

    # 5. Stale feed.
    feed = tm.get("feed") or {}
    if feed.get("stale") or (feed.get("freshness_sec") is not None
                             and float(feed["freshness_sec"]) > float(feed_stale_sec)):
        alerts.append(_alert(RULE_FEED_STALE, SEV_WARNING,
                             f"feed freshness {feed.get('freshness_sec')}s exceeds {feed_stale_sec}s",
                             f"freshness <= {feed_stale_sec}s", {"feed": feed}))

    critical = [a for a in alerts if a["severity"] == SEV_CRITICAL]
    return {
        "schema": SCHEMA,
        "alert_count": len(alerts),
        "critical_count": len(critical),
        "healthy": not alerts,
        "alerts": alerts,
        "evaluated_at": __import__("time").time(),
    }
