"""Local self-contained mode: no Railway / Neon coupling (day-1 after Fly delete).

HM + Strategist confirm: no research/monitoring data depends on Neon or Railway.
Danish via RK t781u: Railway backend + Neon will be removed; Vercel goes static —
local paper bot must be self-contained.

When enabled:
* paper must not stall on ``LIVE_RELAY_COORDINATION_NOT_CONFIRMED_BY_RAILWAY_*``
* platform_health must not alert on missing Railway/Neon
* ``fly_relay_state_pusher`` must not run
* live-copy coordination POST from Railway is refused / N/A
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

from local_fresh_runtime import is_local_fresh_start

SCHEMA = "local_cloud_decouple_v1"
ENV_DISABLE = "DISABLE_RAILWAY_NEON_COUPLING"
ENV_SELF_CONTAINED = "LOCAL_SELF_CONTAINED"

# Display / receipt labels (never a live-arm signal).
COORD_STATE_LOCAL_PAPER_ISOLATED = "LOCAL_PAPER_ISOLATED"
COORD_REASON_LOCAL_NO_RAILWAY = "LOCAL_PAPER_NO_RAILWAY_NEON_COUPLING"
HM_STRATEGIST_CITE = (
    "HM + Strategist: no research/monitoring depends on Neon/Railway; "
    "Danish/RK t781u: Railway+Neon removed, Vercel static — local bot self-contained"
)

SKIPPED_PLATFORMS = frozenset({"railway", "neon", "fly"})


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def cloud_coupling_disabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when local paper must ignore Railway/Neon/Fly cloud coupling."""
    env = environ if environ is not None else os.environ
    if is_local_fresh_start(env):
        return True
    if _truthy(env.get(ENV_DISABLE)) or _truthy(env.get(ENV_SELF_CONTAINED)):
        return True
    return False


def apply_decouple_environ(environ: Optional[Mapping[str, str]] = None) -> None:
    """Force self-contained flags (mutates environ)."""
    env = environ if environ is not None else os.environ
    env[ENV_DISABLE] = "1"
    env[ENV_SELF_CONTAINED] = "1"
    # Existing relay-stack switch: no pusher / outbox HTTP.
    env["RELAY_STACK_MODE"] = "research_only"
    # Do not treat Admin→Neon→push-showcase-bot as credential authority.
    if str(env.get("CREDENTIALS_FROM") or "").strip().lower() in (
        "railway",
        "neon",
        "push-showcase-bot",
        "admin",
    ):
        env["CREDENTIALS_FROM"] = "local_env"
    # Clear Railway identity leftovers that could re-enable probes/credentials.
    for name in (
        "RAILWAY_ENVIRONMENT",
        "RAILWAY_PROJECT_ID",
        "RAILWAY_API_TOKEN",
        "RAILWAY_TOKEN",
        "SHOWCASE_RELAY_WEBHOOK_URL",
    ):
        env.pop(name, None)


def coordination_display_override() -> dict[str, Any]:
    """Replace Railway-unconfirmed stall UX for local paper."""
    return {
        "live_copy_coordination_state": COORD_STATE_LOCAL_PAPER_ISOLATED,
        "live_copy_coordination_enforced_state": "RUNNING_TOGETHER",
        "live_copy_coordination_confirmed": True,
        "live_copy_coordination_updated_at": "LOCAL_BOOT",
        "live_copy_coordination_ui_reason": COORD_REASON_LOCAL_NO_RAILWAY,
        "live_copy_coordination_railway_required": False,
        "cloud_coupling_disabled": True,
        "hm_strategist_cite": HM_STRATEGIST_CITE,
    }


def platform_probes_for_mode(
    probes: Mapping[str, Any],
    environ: Optional[Mapping[str, str]] = None,
) -> dict[str, Any]:
    """Drop Railway/Neon/(deleted) Fly probes so missing cloud does not alert."""
    if not cloud_coupling_disabled(environ):
        return dict(probes)
    return {k: v for k, v in probes.items() if k not in SKIPPED_PLATFORMS}


def skipped_platform_snapshot(name: str, now: float) -> dict[str, Any]:
    return {
        "platform": name,
        "state": "skipped",
        "skipped": True,
        "reason": COORD_REASON_LOCAL_NO_RAILWAY,
        "checked_at": now,
        "revision": None,
        "latency_ms": None,
        "hm_strategist_cite": HM_STRATEGIST_CITE,
    }


def relay_pusher_disabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    return cloud_coupling_disabled(environ)


def status(environ: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    disabled = cloud_coupling_disabled(environ)
    return {
        "schema": SCHEMA,
        "cloud_coupling_disabled": disabled,
        "skipped_platforms": sorted(SKIPPED_PLATFORMS) if disabled else [],
        "relay_pusher_disabled": disabled,
        "live_copy_coordination_railway_required": not disabled,
        "cite": HM_STRATEGIST_CITE,
    }
