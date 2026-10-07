"""Bitfinex readiness + full-lifecycle observability for the self-aware daemon.

Fetches the bot's read-only ``/api/bitfinex/overview`` (fail-closed) and reduces
it to one verdict plus the mismatch alerts the bot already computed. This
section never arms, toggles a tile, places an order, or copies paper state to
live; it only reports what the bot says about its own relay/arm state.

The bot endpoint is authoritative for the per-tile switch, deny reasons, audit,
twin match and lifecycle telemetry. When it is unreachable this section reports
``UNREACHABLE`` and the check degrades to SKIP rather than fabricating an armed
or healthy state.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from .facts import http_json, iso

BOT_OVERVIEW_URL = os.environ.get(
    "SELF_AWARE_BITFINEX_URL",
    "http://127.0.0.1:7002/api/bitfinex/overview",
)

SCHEMA = "self_aware_bitfinex_readiness_v1"

_CRITICAL = "CRITICAL"


def run(state: dict[str, Any] | None = None, now: float | None = None,
        fetch: Callable = http_json) -> dict[str, Any]:
    """Fetch the bot's readiness overview and reduce it to one verdict."""
    now = time.time() if now is None else float(now)
    body, error, elapsed = fetch(BOT_OVERVIEW_URL, timeout=10)
    if body is None:
        return {
            "schema": SCHEMA,
            "generated_at": iso(now),
            "url": BOT_OVERVIEW_URL,
            "status": "UNREACHABLE",
            "error": error,
            "elapsed_sec": round(elapsed, 3),
            "verdict": "UNKNOWN",
            "relay_arm_state": None,
            "live_armed": None,
            "bitfinex_live_enabled": None,
            "force_paper_mode": None,
            "switch": None,
            "match": None,
            "alert_count": 0,
            "critical_count": 0,
            "alerts": [],
            "fill_pricing_model": None,
            "audit": None,
            "note": "fail-closed: nothing is reported armed when the bot is unreachable",
        }

    ga = body.get("relay_arm_state") or {}
    alerts_doc = body.get("alerts") or {}
    alerts = list(alerts_doc.get("alerts") or [])
    critical = [a for a in alerts if a.get("severity") == _CRITICAL]
    warnings = [a for a in alerts if a.get("severity") != _CRITICAL]
    switch = body.get("switch") or {}

    verdict = "RED" if critical else ("AMBER" if warnings else "GREEN")
    status = "DEGRADED" if (critical or warnings) else "OK"

    return {
        "schema": SCHEMA,
        "generated_at": iso(now),
        "url": BOT_OVERVIEW_URL,
        "status": status,
        "verdict": verdict,
        "elapsed_sec": round(elapsed, 3),
        "relay_arm_state": ga,
        "live_armed": bool(ga.get("live_armed")),
        "bitfinex_live_enabled": bool(ga.get("bitfinex_live_enabled")),
        "force_paper_mode": bool(ga.get("force_paper_mode")),
        "relay_delivery_block": ga.get("relay_delivery_block"),
        "switch": {
            "tile_count": switch.get("tile_count"),
            "armed_lane_count": switch.get("armed_lane_count"),
            "armed_lanes": list(switch.get("armed_lanes") or []),
            "registry_signature": switch.get("registry_signature"),
        },
        "match": body.get("match") or {},
        "alert_count": alerts_doc.get("alert_count", len(alerts)),
        "critical_count": alerts_doc.get("critical_count", len(critical)),
        "alerts": alerts,
        "fill_pricing_model": (body.get("fill_pricing") or {}).get("headline_model"),
        "audit": body.get("audit") or {},
        "note": "per-stage latency and resource telemetry: /api/bitfinex/telemetry on the bot",
    }
