"""Post-deploy gate: paper must come back ACTIVE in the operator's prior state.

Runs after the deploy's own maintenance resume. It restores registry-tile
paper toggles to the operator state recorded before maintenance, then proves:
paper execution unpaused, live relay disarmed, and the scheduled AI cycle
completing at least twice after the gate started. Any failure fails the run.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = "https://doxed-btc-bot.fly.dev"
REQUIRED_AI_COMPLETIONS = 2
DEFAULT_DEADLINE_SEC = 15 * 60
POLL_SEC = 10
TRANSIENT_HTTP = {502, 503, 504}


def parse_prior(raw: str) -> dict:
    try:
        prior = json.loads(raw or "{}")
    except ValueError:
        return {"captured": False}
    return prior if isinstance(prior, dict) else {"captured": False}


def tile_restore_plan(prior: dict, current_enabled: dict, registry_lanes: list[str]) -> dict:
    """Registry lanes whose current toggle differs from the recorded prior state."""
    if not prior.get("captured"):
        return {}
    prior_enabled = prior.get("research_lane_enabled") or {}
    plan = {}
    for lane in registry_lanes:
        if lane in prior_enabled and bool(current_enabled.get(lane)) != bool(prior_enabled[lane]):
            plan[lane] = bool(prior_enabled[lane])
    return plan


def paper_active_violations(status: dict, expected_revision: str) -> list[str]:
    problems = []
    if not str(status.get("source_git_rev") or "").lower().startswith(expected_revision.lower()):
        problems.append("REVISION_MISMATCH")
    if status.get("execution_paused") is not False:
        problems.append("EXECUTION_PAUSED:" + str(status.get("execution_reason") or ""))
    if status.get("manual_admin_pause") is not False:
        problems.append("MANUAL_PAUSE_ARMED")
    if status.get("live_armed") is not False:
        problems.append("LIVE_ARMED")
    if status.get("bitfinex_live_enabled") is not False:
        problems.append("BITFINEX_LIVE_ENABLED")
    if status.get("force_paper_mode") is not True:
        problems.append("FORCE_PAPER_MODE_OFF")
    return problems


def relay_eligible_tiles(active_tiles: list) -> list[str]:
    return [str(t.get("lane")) for t in active_tiles or [] if t.get("relay_eligible") is not False]


def count_ai_completions(samples: list[float], started: float) -> int:
    """Distinct scheduled-cycle completion timestamps observed after start."""
    return len({round(ts, 3) for ts in samples if ts > started})


def _request(path: str, token: str, payload=None, timeout: int = 30) -> dict:
    headers = {"X-Bot-Admin-Token": token, "Cache-Control": "no-cache"}
    data = None if payload is None else json.dumps(payload).encode()
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        BASE + path, data=data, headers=headers, method="POST" if data is not None else "GET"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _retrying(fn, attempts: int = 6):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except urllib.error.HTTPError as exc:
            if exc.code not in TRANSIENT_HTTP:
                raise
            last = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
        time.sleep(min(2 * attempt, 10))
    raise RuntimeError(f"transient failure persisted: {type(last).__name__}")


def main() -> int:
    token = str(os.environ.get("BOT_ADMIN_TOKEN") or "").strip()
    expected = str(os.environ.get("EXPECTED_REVISION") or "")[:12].lower()
    if not token or len(expected) != 12:
        raise SystemExit("BOT_ADMIN_TOKEN and EXPECTED_REVISION are required")
    prior = parse_prior(os.environ.get("PRIOR_OPERATOR_STATE", ""))
    deadline_sec = int(os.environ.get("POSTDEPLOY_ACTIVE_DEADLINE_SEC") or DEFAULT_DEADLINE_SEC)

    status = _retrying(lambda: _request("/api/status", token))
    eligible = relay_eligible_tiles(status.get("active_tiles") or [])
    if eligible:
        raise SystemExit("registry tiles must remain relay-ineligible: " + ",".join(eligible))
    registry_lanes = [str(t.get("lane")) for t in status.get("active_tiles") or [] if t.get("lane")]
    state = _retrying(lambda: _request("/api/state", token))
    plan = tile_restore_plan(prior, state.get("research_lane_enabled") or {}, registry_lanes)
    for lane, enabled in plan.items():
        result = _retrying(
            lambda: _request("/api/toggle_research_lane", token, {"lane": lane, "enabled": enabled})
        )
        if bool(result.get("enabled")) != enabled:
            raise SystemExit(f"tile toggle restore failed for {lane}")
        print(json.dumps({"restored_tile": lane, "enabled": enabled}), flush=True)
    if not prior.get("captured"):
        print("prior operator state unavailable; tile toggles left as persisted", flush=True)

    started = time.time()
    completions: list[float] = []
    last_problems: list[str] = []
    while time.time() - started < deadline_sec:
        status = _retrying(lambda: _request("/api/status", token))
        last_problems = paper_active_violations(status, expected)
        if last_problems:
            raise SystemExit("paper not active after deploy: " + ",".join(last_problems))
        ready = _retrying(lambda: _request("/ready", token))
        cycle = (ready.get("strategy_progress") or {}).get("scheduled_ai_cycle") or {}
        completed = float(cycle.get("completed_ts") or 0)
        if completed:
            completions.append(completed)
        observed = count_ai_completions(completions, started)
        print(json.dumps({
            "ai_completions_after_gate": observed,
            "stage": cycle.get("stage"),
            "last_poll_reason": cycle.get("last_poll_reason"),
            "signal_generation_ready": ready.get("signal_generation_ready"),
        }, sort_keys=True), flush=True)
        if observed >= REQUIRED_AI_COMPLETIONS:
            print(f"Paper ACTIVE on {expected}: {observed} advancing AI cycles; live disarmed")
            return 0
        time.sleep(POLL_SEC)
    raise SystemExit(
        f"AI cadence did not advance {REQUIRED_AI_COMPLETIONS} cycles within {deadline_sec}s "
        f"(observed={count_ai_completions(completions, started)})"
    )


if __name__ == "__main__":
    raise SystemExit(main())
