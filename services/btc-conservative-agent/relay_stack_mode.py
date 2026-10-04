"""Relay-stack mode: one config switch for the Bitfinex copy-relay plumbing on Fly.

Pure module (no bot.py, Flask or network). ``RELAY_STACK_MODE`` selects:

* ``active`` (default, unchanged behaviour): the relay-state pusher process,
  the 1 s canonical execution refresher, the /api/relay-state background
  refresher and the relay outbox HTTP delivery loop all run.
* ``research_only``: the bot is paper-only research. Nothing is pushed to the
  platform relay:

  - ``fly-entrypoint.sh`` does not start ``fly_relay_state_pusher.py``;
  - the /api/relay-state background refresher is not started; the route builds
    a bounded snapshot on demand (single non-blocking builder) for monitors;
  - the canonical execution snapshot (/api/relay-execution-state) is still
    published because the dashboard overlay and deploy flat-checks read it,
    but at ``RESEARCH_ONLY_EXECUTION_REFRESH_SEC`` instead of every second, so
    its trade-lock hold no longer dominates the single core;
  - the outbox delivery loop makes no HTTP call: durable lifecycle events stay
    on disk untouched (never ACKed, rewritten or deleted) and the stale-owner
    guard keeps observing, but the stale-owner state is reported as INFO
    (``RELAY_DISABLED_RESEARCH_ONLY``) instead of a permanent collection ALARM.

Nothing here arms, disarms or reads exchange credentials. Re-enabling the relay
is a config change back to ``active`` plus the existing re-arm runbook.
"""
from __future__ import annotations

import os
from typing import Mapping

ENV_NAME = "RELAY_STACK_MODE"
ACTIVE = "active"
RESEARCH_ONLY = "research_only"
MODES = (ACTIVE, RESEARCH_ONLY)
# Status label used wherever a disabled relay used to raise an alarm.
DISABLED_STATUS = "RELAY_DISABLED_RESEARCH_ONLY"
RESEARCH_ONLY_EXECUTION_REFRESH_SEC = 5.0
RESEARCH_ONLY_EXECUTION_MAX_STALE_SEC = 15.0
# The outbox loop still runs the local stale-owner observation (no HTTP).
RESEARCH_ONLY_OUTBOX_OBSERVE_SEC = 60.0


def mode(env: Mapping[str, str] | None = None) -> str:
    """Configured mode; unknown values fail safe to ``active`` (the audited default path)."""
    raw = ((env if env is not None else os.environ).get(ENV_NAME) or "").strip().lower()
    return raw if raw in MODES else ACTIVE


def research_only(env: Mapping[str, str] | None = None) -> bool:
    return mode(env) == RESEARCH_ONLY


def status(env: Mapping[str, str] | None = None) -> dict:
    current = mode(env)
    disabled = current == RESEARCH_ONLY
    return {
        "relay_stack_mode": current,
        "relay_delivery_enabled": not disabled,
        "relay_state_pusher_enabled": not disabled,
        "relay_state_background_refresher_enabled": not disabled,
        "stale_owner_alarm_suppressed": disabled,
        "status": DISABLED_STATUS if disabled else "ACTIVE",
    }
