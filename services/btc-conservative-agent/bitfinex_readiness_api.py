"""Flask blueprint exposing the Phase 5 Bitfinex readiness + observability API.

Every endpoint is READ-ONLY and fail-closed. None of these endpoints arms,
disarms, toggles a tile, places/cancels an order, copies paper state to live,
or returns any credential. Arming authority stays with the existing operator
paths (``/api/live_arm`` and ``/api/bitfinex_live``).

The blueprint reads its data from module-level singletons that ``bot.py`` wires
at startup via :func:`wire`. Until wired, every endpoint reports the safe
disarmed state (never a fabricated "armed").
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping, Optional

from flask import Blueprint, jsonify, request

from bitfinex_live_switch import BitfinexLiveSwitch
from health_mismatch_alerts import evaluate_alerts
from lifecycle_telemetry import LifecycleTelemetry
from order_action_audit import OrderActionAudit
from paper_bitfinex_match import match_report
from fill_pricing_source import fill_pricing_provenance

blueprint = Blueprint("bitfinex_readiness", __name__)

_switch = BitfinexLiveSwitch()
_telemetry = LifecycleTelemetry()
_audit: Optional[OrderActionAudit] = None
_context_provider: Optional[Callable[[], Mapping[str, Any]]] = None


def wire(*, context_provider: Callable[[], Mapping[str, Any]],
         audit: OrderActionAudit | None = None,
         switch: BitfinexLiveSwitch | None = None,
         telemetry: LifecycleTelemetry | None = None) -> None:
    """Injected by ``bot.py`` at startup to bind the live runtime state."""
    global _context_provider, _audit, _switch, _telemetry
    _context_provider = context_provider
    if audit is not None:
        _audit = audit
    if switch is not None:
        _switch = switch
    if telemetry is not None:
        _telemetry = telemetry


def _context() -> Mapping[str, Any]:
    if _context_provider is None:
        # Fail-closed default: nothing is armed, nothing is eligible.
        return {
            "global_arm": {
                "force_paper_mode": True, "live_armed": False,
                "bitfinex_live_enabled": False, "relay_delivery_block": None,
                "keys_ok": False,
                "exchange_audit": {"authoritative": False, "fresh": False,
                                   "flat": False, "orphan_order_ids": [],
                                   "orphan_position_ids": []},
                "market_ready": False, "system_ready": False, "manual_pause": True,
            },
            "mark_price": None,
            "exchange_min_qty": None,
            "exchange_max_qty": None,
            "stop_coverage_verified": False,
            "reduce_only_supported": False,
            "paper_trades": [],
            "live_twins": [],
        }
    return _context_provider()


def _size_checks_for_lane(ctx: Mapping[str, Any], lane: str) -> dict:
    from bitfinex_live_switch import compute_size_checks
    from combo_pathway_config import ACTIVE_TILE_REGISTRY
    spec = ACTIVE_TILE_REGISTRY.get(str(lane or "").upper()) or {}
    margin = float(spec.get("requested_margin_usd") or 0.25)
    return compute_size_checks(
        margin_usd=margin,
        leverage=100,
        mark_price=ctx.get("mark_price"),
        exchange_min_qty=ctx.get("exchange_min_qty"),
        exchange_max_qty=ctx.get("exchange_max_qty"),
        stop_coverage_verified=bool(ctx.get("stop_coverage_verified")),
        reduce_only_supported=bool(ctx.get("reduce_only_supported")),
    )


@blueprint.get("/api/bitfinex/status")
def status():
    ctx = _context()
    ga = ctx.get("global_arm") or {}
    # Re-evaluate every lane so the returned eligibility is always fresh and
    # derived from the canonical registry (never a hard-coded second list).
    rows = []
    for snap in _switch.status()["rows"]:
        lane = snap["lane"]
        ev = _switch.evaluate(lane, global_arm=ga, size_checks=_size_checks_for_lane(ctx, lane))
        rows.append({
            "lane": lane,
            "tile_number": snap.get("tile_number"),
            "label": ev.get("label"),
            "bitfinex_live_orders": bool(snap.get("bitfinex_live_orders")),
            "paper_toggle_key": "research_lane_enabled",
            "eligible": ev.get("eligible"),
            "denials": ev.get("denials"),
            "relay_eligible": ev.get("relay_eligible"),
            "relay_capability": ev.get("relay_capability"),
            "requested_margin_usd": ev.get("requested_margin_usd"),
            "last_denial": snap.get("last_denial") or [],
            "last_denied_at": snap.get("last_denied_at"),
            "last_allow_ts": snap.get("last_allow_ts"),
        })
    return jsonify({
        "schema": "bitfinex_readiness_status_v1",
        "relay_arm_state": {
            "live_armed": bool(ga.get("live_armed")),
            "bitfinex_live_enabled": bool(ga.get("bitfinex_live_enabled")),
            "force_paper_mode": bool(ga.get("force_paper_mode")),
            "relay_delivery_block": ga.get("relay_delivery_block"),
            "keys_ok": bool(ga.get("keys_ok")),
        },
        "tiles": rows,
        "armed_lanes": [r["lane"] for r in rows if r["bitfinex_live_orders"]],
        "server_ts": time.time(),
    })


@blueprint.get("/api/bitfinex/tiles/<lane>/arming")
def tile_arming(lane: str):
    ctx = _context()
    ga = ctx.get("global_arm") or {}
    whynot = _switch.why_not_armed(lane, global_arm=ga,
                                   size_checks=_size_checks_for_lane(ctx, lane))
    return jsonify({
        "lane": str(lane).upper(),
        "arming": whynot,
        "relay_arm_state": {
            "live_armed": bool(ga.get("live_armed")),
            "bitfinex_live_enabled": bool(ga.get("bitfinex_live_enabled")),
            "force_paper_mode": bool(ga.get("force_paper_mode")),
        },
        "server_ts": time.time(),
    })


@blueprint.get("/api/bitfinex/audit")
def audit():
    if _audit is None:
        return jsonify({"schema": "bitfinex_order_action_audit_v1", "rows": 0,
                        "records": [], "verify": {"ok": False, "reason": "AUDIT_NOT_WIRED"},
                        "signed": False}), 200
    a = request.args
    records = _audit.query(
        action_type=a.get("action_type"),
        trade_id=a.get("trade_id"),
        intent_id=a.get("intent_id"),
        lane=a.get("lane"),
        since_seq=int(a["since_seq"]) if a.get("since_seq") else None,
        limit=min(int(a.get("limit", 200)), 1000),
    )
    st = _audit.status()
    return jsonify({**st, "records": records})


@blueprint.get("/api/bitfinex/matches")
def matches():
    ctx = _context()
    return jsonify(match_report(ctx.get("paper_trades") or [], ctx.get("live_twins") or []))


@blueprint.get("/api/bitfinex/telemetry")
def telemetry():
    trade_id = request.args.get("trade_id")
    if trade_id:
        row = _telemetry.get(trade_id)
        return jsonify({"found": row is not None, "trade": row})
    return jsonify(_telemetry.snapshot())


@blueprint.get("/api/bitfinex/alerts")
def alerts():
    ctx = _context()
    return jsonify(evaluate_alerts(
        match_report=match_report(ctx.get("paper_trades") or [], ctx.get("live_twins") or []),
        switch_status=_switch.status(),
        global_arm=ctx.get("global_arm") or {},
        telemetry=_telemetry.snapshot(),
    ))


@blueprint.get("/api/bitfinex/fill-pricing")
def fill_pricing():
    return jsonify(fill_pricing_provenance())


@blueprint.get("/api/bitfinex/overview")
def overview():
    """One-place aggregate for the self-aware surface / monitors."""
    ctx = _context()
    ga = ctx.get("global_arm") or {}
    mr = match_report(ctx.get("paper_trades") or [], ctx.get("live_twins") or [])
    return jsonify({
        "schema": "bitfinex_readiness_overview_v1",
        "relay_arm_state": {
            "live_armed": bool(ga.get("live_armed")),
            "bitfinex_live_enabled": bool(ga.get("bitfinex_live_enabled")),
            "force_paper_mode": bool(ga.get("force_paper_mode")),
            "relay_delivery_block": ga.get("relay_delivery_block"),
        },
        "switch": _switch.status(),
        "match": {"paper_count": mr["paper_count"], "matched_count": mr["matched_count"],
                  "unmatched_count": mr["unmatched_count"], "diverged_count": mr["diverged_count"]},
        "alerts": evaluate_alerts(match_report=mr, switch_status=_switch.status(),
                                  global_arm=ga, telemetry=_telemetry.snapshot()),
        "fill_pricing": fill_pricing_provenance(),
        "audit": _audit.status() if _audit is not None else {"rows": 0, "signed": False},
        "server_ts": time.time(),
    })
