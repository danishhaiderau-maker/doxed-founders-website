"""Reserved EMERGENCY admission class for the dashboard HTTP server (pure; no I/O).

HM D-B (16 Oct 2026): pause / disarm / flatten POSTs must never be answered with the 503
``dashboard_busy`` that saturated probes or presentation polls get.  The server peeks the HTTP
request line, and this module decides (method + exact path) whether a request is an operator
safety control.  Only POST is ever emergency; ``GET /api/status`` and friends stay in the probe
class.  Labels are fixed strings so a tile/lane name or query string is never logged.

Which routes actually disarm or flatten (verified in bot.py):
  * ``POST /api/pause``            -> ``_disarm_live_control("ADMIN_PAUSE:...")`` (pause AND disarm)
  * ``POST /api/live_arm``         -> ``_disarm_live_control("LIVE_ARM_DISARMED")`` on arm=false
  * ``POST /api/bitfinex_live``    -> ``_disarm_live_control("BITFINEX_DISARMED")`` on enabled=false
  * ``POST /api/positions/close``  -> flatten one position (DEPLOY_FORCE_FLAT / ADMIN_MANUAL_CLOSE)
There is no separate disarm or flatten-all route.
"""
from __future__ import annotations

EMERGENCY_CAP = 4
EMERGENCY_DISPATCH_RESERVE = 4

EMERGENCY_POST_PATHS = frozenset({
    b"/api/pause",
    b"/api/resume",
    b"/api/live_arm",
    b"/api/bitfinex_live",
    b"/api/positions/close",
    b"/api/live-copy/output",
    b"/api/live-copy/protection-bootstrap",
    b"/api/live-copy-coordination",
    b"/api/live-copy/executor-capability",
    b"/api/admin/emergency-wal/clear-alarms",
    b"/api/toggle_research_lane",
})
# (prefix, suffix, fixed label) for per-tile POSTs: /api/live-copy/tiles/<lane>/eligibility etc.
EMERGENCY_POST_PATTERNS = (
    (b"/api/live-copy/tiles/", b"/eligibility", "/api/live-copy/tiles/*/eligibility"),
    (b"/api/bitfinex/tiles/", b"/live-orders", "/api/bitfinex/tiles/*/live-orders"),
)


def parse_request_line(head: bytes) -> tuple[bytes, bytes]:
    """(METHOD, path-without-query) from the first bytes of a request; (b"", b"") if unparseable."""
    try:
        first = bytes(head or b"").split(b"\r\n", 1)[0]
        parts = first.split(b" ")
        if len(parts) < 2:
            return b"", b""
        return parts[0].upper(), parts[1].split(b"?", 1)[0]
    except (TypeError, ValueError):
        return b"", b""


def emergency_label(method: bytes, path: bytes) -> str | None:
    """Fixed label when (method, path) is an emergency control, else None."""
    if bytes(method or b"").upper() != b"POST":
        return None
    path = bytes(path or b"")
    if path in EMERGENCY_POST_PATHS:
        return path.decode("ascii")
    for prefix, suffix, label in EMERGENCY_POST_PATTERNS:
        if path.startswith(prefix) and path.endswith(suffix):
            lane = path[len(prefix):-len(suffix)]
            if lane and b"/" not in lane:
                return label
    return None
