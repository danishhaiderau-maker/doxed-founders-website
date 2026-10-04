"""Post-deploy gate: paper must come back ACTIVE with every registry tile ON.

Runs after the deploy's own maintenance resume. Every tile in the canonical
registry (as reported by /api/status active_tiles) is toggled ON for paper,
whatever its state before maintenance; tiles stay relay-ineligible and the
relay/Bitfinex are never armed. It then proves: paper execution unpaused with
no pause owner, live relay disarmed, and the scheduled AI cycle completing at
least twice after the gate started. Any failure fails the run.

Lanes listed in ``PAPER_TILES_HOLD_OFF`` (comma-separated) are operator-held:
they are toggled OFF instead of ON and the receipt requires them OFF.

The roster is also proven against the checked-out registry
(``combo_pathway_config.ACTIVE_TILE_ORDER``, the same revision the workflow
deployed): the running bot must report exactly those tiles, in that order
(FREEZE21B: the three hypotheses, the random control, Grok Strategist's GS-01..04
and B1..B3 - eleven tiles), and no retired lane
(``RETIRED_TILE_LANES``) may be ON or on the roster. During the 21-day research
freeze (``research_freeze.py``) holding a frozen tile OFF is a freeze break:
the bot refuses it (409 ``RESEARCH_FREEZE_ACTIVE``) unless the environment
carries ``RESEARCH_FREEZE_OVERRIDE=BREAK_21_DAY_RESEARCH_FREEZE`` and a
``RESEARCH_FREEZE_OVERRIDE_REASON``, which are forwarded as the documented
``freeze_override``.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://doxed-btc-bot.fly.dev"
REQUIRED_AI_COMPLETIONS = 2
DEFAULT_DEADLINE_SEC = 15 * 60
POLL_SEC = 10
TRANSIENT_HTTP = {502, 503, 504}


SERVICE_DIR = Path(__file__).resolve().parents[1] / "services" / "btc-conservative-agent"


def checkout_registry() -> tuple[list[str], frozenset[str]]:
    """(active roster in display order, retired lanes) from the checked-out registry (stdlib-only module)."""
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    try:
        registry = importlib.import_module("combo_pathway_config")
    except Exception as exc:  # noqa: BLE001 - fail closed
        raise SystemExit(f"checked-out tile registry unavailable: {type(exc).__name__}: {exc}")
    return [str(lane) for lane in registry.ACTIVE_TILE_ORDER], frozenset(registry.RETIRED_TILE_LANES)


def freeze_override_payload(environ=None) -> dict | None:
    env = os.environ if environ is None else environ
    confirmation = str(env.get("RESEARCH_FREEZE_OVERRIDE") or "").strip()
    reason = str(env.get("RESEARCH_FREEZE_OVERRIDE_REASON") or "").strip()
    return {"confirmation": confirmation, "reason": reason} if confirmation and reason else None


def held_off_lanes(environ=None) -> frozenset[str]:
    raw = str((os.environ if environ is None else environ).get("PAPER_TILES_HOLD_OFF") or "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def tile_enable_plan(current_enabled: dict, registry_lanes: list[str], held: frozenset[str] = frozenset()) -> list[str]:
    """Registry lanes that are not ON yet; every boundary ends with all non-held tiles ON."""
    return [lane for lane in registry_lanes if lane not in held and current_enabled.get(lane) is not True]


def tiles_all_on_receipt(status: dict, state: dict, held: frozenset[str] = frozenset(),
                         expected: list[str] | None = None, retired: frozenset[str] = frozenset()) -> dict:
    lanes = [str(t.get("lane")) for t in status.get("active_tiles") or [] if t.get("lane")]
    enabled = state.get("research_lane_enabled") or {}
    off = [lane for lane in lanes if lane not in held and enabled.get(lane) is not True]
    held_on = [lane for lane in lanes if lane in held and enabled.get(lane) is not False]
    active = [lane for lane in lanes if lane not in held]
    roster_ok = expected is None or lanes == list(expected)
    retired_on = sorted(lane for lane in retired if enabled.get(lane) is True)
    retired_listed = sorted(set(lanes) & set(retired))
    return {"lanes": lanes, "tiles_off": off, "held_off": sorted(held & set(lanes)), "held_not_off": held_on,
            "expected_lanes": list(expected) if expected is not None else None,
            "roster_matches_checkout": roster_ok,
            "retired_on": retired_on, "retired_on_roster": retired_listed,
            "tiles_all_on": (bool(active) and not off and not held_on and roster_ok
                             and not retired_on and not retired_listed),
            "execution_paused": status.get("execution_paused"),
            "pause_owner": status.get("pause_owner") or "",
            "live_armed": status.get("live_armed"),
            "bitfinex_live_enabled": status.get("bitfinex_live_enabled"),
            "force_paper_mode": status.get("force_paper_mode"),
            "source_git_rev": status.get("source_git_rev")}


def enable_all_registry_tiles(request) -> dict:
    """Toggle every registry tile ON for paper and return the verified receipt.

    ``request(path, payload=None)`` returns decoded JSON. Tiles must remain
    relay-ineligible; this never touches relay or Bitfinex arming. The running
    roster must equal the checked-out registry and no retired lane may be ON.
    """
    expected, retired = checkout_registry()
    status = request("/api/status", None)
    eligible = relay_eligible_tiles(status.get("active_tiles") or [])
    if eligible:
        raise SystemExit("registry tiles must remain relay-ineligible: " + ",".join(eligible))
    lanes = [str(t.get("lane")) for t in status.get("active_tiles") or [] if t.get("lane")]
    if not lanes:
        raise SystemExit("registry roster missing from /api/status active_tiles")
    if lanes != expected:
        raise SystemExit("running tile roster does not match the checked-out registry: running="
                         + ",".join(lanes) + " expected=" + ",".join(expected))
    held = held_off_lanes()
    state = request("/api/state", None)
    current = state.get("research_lane_enabled") or {}
    override = freeze_override_payload()
    for lane in [lane for lane in lanes if lane in held and current.get(lane) is not False]:
        payload = {"lane": lane, "enabled": False, **({"freeze_override": override} if override else {})}
        try:
            result = request("/api/toggle_research_lane", payload)
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise SystemExit(f"held lane {lane} cannot be turned OFF during the research freeze "
                                 "(RESEARCH_FREEZE_ACTIVE); set RESEARCH_FREEZE_OVERRIDE and "
                                 "RESEARCH_FREEZE_OVERRIDE_REASON only with the owner's approval")
            raise
        if result.get("enabled") is not False:
            raise SystemExit(f"tile toggle OFF failed for held lane {lane}")
        print(json.dumps({"held_off_tile": lane}), flush=True)
    for lane in tile_enable_plan(current, lanes, held):
        result = request("/api/toggle_research_lane", {"lane": lane, "enabled": True})
        if result.get("enabled") is not True:
            raise SystemExit(f"tile toggle ON failed for {lane}")
        print(json.dumps({"enabled_tile": lane}), flush=True)
    receipt = tiles_all_on_receipt(request("/api/status", None), request("/api/state", None), held,
                                   expected=expected, retired=retired)
    print("tiles receipt " + json.dumps(receipt, sort_keys=True), flush=True)
    if not receipt["tiles_all_on"]:
        raise SystemExit("registry tiles not all ON (held lanes OFF, exact checkout roster, retired OFF): "
                         + ",".join(receipt["tiles_off"] + receipt["held_not_off"] + receipt["retired_on"]
                                    + receipt["retired_on_roster"]
                                    + ([] if receipt["roster_matches_checkout"] else ["ROSTER_MISMATCH"])))
    if receipt["live_armed"] is not False or receipt["bitfinex_live_enabled"] is not False:
        raise SystemExit("live relay/Bitfinex must stay disarmed")
    return receipt


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
    if status.get("pause_owner"):
        problems.append("PAUSE_OWNER:" + str(status.get("pause_owner")))
    return problems


def relay_eligible_tiles(active_tiles: list) -> list[str]:
    return [str(t.get("lane")) for t in active_tiles or [] if t.get("relay_eligible") is not False]


def count_ai_completions(samples: list[float], started: float) -> int:
    """Distinct successful-response timestamps observed after start."""
    return len({round(ts, 3) for ts in samples if ts > started})


def ai_success_sample(ready: dict) -> float:
    """Last successful model response; a completed cycle that timed out is not one."""
    provider = (ready.get("strategy_progress") or {}).get("ai_provider") or {}
    return float(provider.get("last_ai_success_ts") or 0)


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
            try:
                exc.body_json = json.loads(exc.read().decode("utf-8", "replace") or "null")
            except (ValueError, OSError):
                exc.body_json = None
            last = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        time.sleep(min(2 * attempt, 10))
    raise RuntimeError(f"transient failure persisted: {type(last).__name__}") from last


def _ready_sample(token: str) -> dict | None:
    """One /ready sample that tolerates a brief readiness blip.

    /ready answers 503 with its normal JSON body while readiness re-latches
    (WS reconnect, BBO refresh). That body still carries the AI-progress
    clocks this gate counts, so read it instead of failing the run; a
    transport error or a non-JSON body is a skipped sample, not a failure.
    The overall gate deadline still bounds the wait.
    """
    try:
        return _retrying(lambda: _request("/ready", token))
    except urllib.error.HTTPError as exc:
        if exc.code not in TRANSIENT_HTTP:
            raise
        try:
            return json.loads(exc.read().decode("utf-8", "replace") or "null") or None
        except (ValueError, OSError):
            return None
    except RuntimeError as exc:
        last = exc.__cause__ or exc
        body = getattr(last, "body_json", None)
        print(f"/ready transient: {exc}", flush=True)
        return body


def main(argv=None) -> int:
    token = str(os.environ.get("BOT_ADMIN_TOKEN") or "").strip()
    if "--tiles-only" in (sys.argv[1:] if argv is None else argv):
        if not token:
            raise SystemExit("BOT_ADMIN_TOKEN is required")
        # Restart-style jobs return before boot finishes; wait for the roster.
        deadline = time.time() + int(os.environ.get("TILES_ON_DEADLINE_SEC") or DEFAULT_DEADLINE_SEC)
        while True:
            try:
                enable_all_registry_tiles(
                    lambda path, payload=None: _retrying(lambda: _request(path, token, payload)))
                return 0
            except (RuntimeError, urllib.error.HTTPError, SystemExit) as exc:
                if time.time() >= deadline:
                    raise SystemExit(f"registry tiles not forced ON before deadline: {exc}")
                print(f"tiles-only waiting for boot: {type(exc).__name__}: {exc}", flush=True)
                time.sleep(15)
    expected = str(os.environ.get("EXPECTED_REVISION") or "")[:12].lower()
    if not token or len(expected) != 12:
        raise SystemExit("BOT_ADMIN_TOKEN and EXPECTED_REVISION are required")
    deadline_sec = int(os.environ.get("POSTDEPLOY_ACTIVE_DEADLINE_SEC") or DEFAULT_DEADLINE_SEC)

    enable_all_registry_tiles(
        lambda path, payload=None: _retrying(lambda: _request(path, token, payload)))

    started = time.time()
    completions: list[float] = []
    last_problems: list[str] = []
    while time.time() - started < deadline_sec:
        status = _retrying(lambda: _request("/api/status", token))
        last_problems = paper_active_violations(status, expected)
        if last_problems:
            raise SystemExit("paper not active after deploy: " + ",".join(last_problems))
        ready = _ready_sample(token)
        if not isinstance(ready, dict):
            time.sleep(POLL_SEC)
            continue
        progress = ready.get("strategy_progress") or {}
        cycle = progress.get("scheduled_ai_cycle") or {}
        provider = progress.get("ai_provider") or {}
        succeeded = ai_success_sample(ready)
        if succeeded:
            completions.append(succeeded)
        observed = count_ai_completions(completions, started)
        print(json.dumps({
            "ai_successes_after_gate": observed,
            "ai_consecutive_failures": provider.get("consecutive_failures"),
            "ai_last_error_class": provider.get("last_error_class"),
            "last_ai_success_at": provider.get("last_ai_success_at"),
            "stage": cycle.get("stage"),
            "last_poll_reason": cycle.get("last_poll_reason"),
            "signal_generation_ready": ready.get("signal_generation_ready"),
        }, sort_keys=True), flush=True)
        if observed >= REQUIRED_AI_COMPLETIONS:
            print(
                f"Paper ACTIVE on {expected}: {observed} advancing AI cycles "
                f"with successful model responses; live disarmed"
            )
            return 0
        time.sleep(POLL_SEC)
    raise SystemExit(
        f"AI did not return {REQUIRED_AI_COMPLETIONS} successful model responses within "
        f"{deadline_sec}s (observed={count_ai_completions(completions, started)})"
    )


if __name__ == "__main__":
    raise SystemExit(main())
