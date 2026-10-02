"""Dead-man heartbeat and schedule-gap detection for the scheduled Fly monitor.

GitHub delivers ``*/15`` schedules best-effort (66% delivery and a 63 min gap
were observed), and a skipped run cannot report itself. Each run therefore
compares "now" with the newest evidence of the previous run:

- the ``FLY_MONITOR_HEARTBEAT`` repository variable (written at the end of
  every run when a token with Actions-variables write access is configured);
- ``last_run.ts`` in the cached incident state;
- the previous run of this workflow in the Actions runs API (needs only
  ``actions: read``, so it works without the variable or the cache).

The heartbeat value is compact JSON so PowerShell and Python both parse it:
``{"at": "<UTC ISO>", "run_id": "...", "attempt": "...", "crashed": false, "restored": true}``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

HEARTBEAT_VARIABLE = "FLY_MONITOR_HEARTBEAT"
SCHEDULE_GAP_SEC = 45 * 60.0
MONITOR_RUNS_PATH = "/actions/workflows/fly-bot-monitor.yml/runs?per_page=10"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.timestamp() if parsed.tzinfo is not None else None


def format_heartbeat(now: float, *, run_id: str, attempt: str, crashed: bool, restored: bool) -> str:
    return json.dumps(
        {"at": iso(now), "run_id": str(run_id), "attempt": str(attempt), "crashed": crashed, "restored": restored},
        separators=(",", ":"),
    )


def parse_heartbeat(raw: str | None) -> float | None:
    """Epoch seconds of a heartbeat value; also accepts a bare ISO time or epoch."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            return None
        return parse_iso(data.get("at")) if isinstance(data, dict) else None
    try:
        return float(text)
    except ValueError:
        return parse_iso(text)


def previous_run_ts(runs: Iterable[Mapping[str, Any]], current_run_id: str, now: float) -> float | None:
    """Creation time of the newest other run of this workflow before ``now``."""
    for run in runs:
        if str(run.get("id")) == str(current_run_id):
            continue
        ts = parse_iso(run.get("created_at"))
        if ts is not None and ts <= now:
            return ts
    return None


def schedule_gap_findings(candidates: Mapping[str, float | None], now: float) -> tuple[dict[str, str], str]:
    """(findings, note) from the newest previous-run evidence; no evidence -> no finding."""
    known = {name: ts for name, ts in candidates.items() if ts is not None}
    if not known:
        return {}, "no previous monitor heartbeat (first run, or variable/cache/runs API all unavailable)"
    source, newest = max(known.items(), key=lambda item: item[1])
    gap = now - newest
    note = f"previous monitor run evidence {iso(newest)} via {source}; gap {gap / 60:.0f} min"
    if gap <= SCHEDULE_GAP_SEC:
        return {}, note
    return {
        "monitor_schedule_gap": (
            f"no monitor run for {gap / 60:.0f} min before this one (> {SCHEDULE_GAP_SEC / 60:.0f} min; "
            f"newest evidence {iso(newest)} via {source}): GitHub skipped scheduled */15 runs, "
            "so findings during the gap were not observed"
        )
    }, note
