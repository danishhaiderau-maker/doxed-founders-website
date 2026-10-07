"""Tests for the fill pricing provenance and the readiness API blueprint."""
from __future__ import annotations

import json

import pytest

from fill_pricing_source import fill_pricing_provenance, assert_realistic_v1_compatible, HEADLINE_MODEL

from bitfinex_readiness_api import blueprint
from order_action_audit import OrderActionAudit


def test_fill_pricing_provenance_is_realistic_v1():
    p = fill_pricing_provenance()
    assert p["headline_model"] == HEADLINE_MODEL
    assert p["pricing_source"].startswith("BITFINEX_ORDER_BOOK")


def test_realistic_v1_compat_guard():
    assert assert_realistic_v1_compatible()["compatible"] is True
    assert assert_realistic_v1_compatible({"headline_model": "OTHER"})["compatible"] is False


@pytest.fixture
def client():
    from flask import Flask
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(blueprint)
    return app.test_client()


def test_status_fails_closed_when_unwired(client):
    # Before bot.py wires a context, every endpoint reports disarmed.
    r = client.get("/api/bitfinex/status")
    assert r.status_code == 200
    body = r.get_json()
    assert body["relay_arm_state"]["live_armed"] is False
    assert body["relay_arm_state"]["force_paper_mode"] is True
    assert body["armed_lanes"] == []
    assert all(not t["eligible"] for t in body["tiles"])


def test_tile_arming_why_not_armed(client):
    r = client.get("/api/bitfinex/tiles/FAMILY_COMMITTED_FADE_TAKER_90/arming")
    assert r.status_code == 200
    body = r.get_json()
    assert body["arming"]["armed"] is False
    assert body["arming"]["explanation"].startswith("Not armed")


def test_audit_endpoint_unwired_is_safe(client):
    r = client.get("/api/bitfinex/audit")
    body = r.get_json()
    assert body["signed"] is False
    assert body["rows"] == 0


def test_overview_aggregates_everything(client):
    r = client.get("/api/bitfinex/overview")
    assert r.status_code == 200
    body = r.get_json()
    assert "relay_arm_state" in body
    assert "switch" in body
    assert "alerts" in body
    assert "fill_pricing" in body
    assert body["fill_pricing"]["headline_model"] == "REALISTIC_V1"


def test_fill_pricing_endpoint(client):
    r = client.get("/api/bitfinex/fill-pricing")
    assert r.get_json()["headline_model"] == "REALISTIC_V1"


def test_wired_context_is_reflected(client, tmp_path):
    """Wire a partially-armed context and confirm the API reflects it safely."""
    audit = OrderActionAudit(tmp_path / "audit.jsonl", key=b"x")
    audit.record(action_type="ORDER_PLACED", trade_id="T1")
    from bitfinex_readiness_api import wire
    wire(
        context_provider=lambda: {
            "global_arm": {
                "force_paper_mode": False, "live_armed": False,
                "bitfinex_live_enabled": False, "relay_delivery_block": None,
                "keys_ok": False,
                "exchange_audit": {"authoritative": False, "fresh": False,
                                   "flat": False, "orphan_order_ids": [],
                                   "orphan_position_ids": []},
                "market_ready": False, "system_ready": False, "manual_pause": True,
            },
            "mark_price": None, "exchange_min_qty": None, "exchange_max_qty": None,
            "stop_coverage_verified": False, "reduce_only_supported": False,
            "paper_trades": [], "live_twins": [],
        },
        audit=audit,
    )
    try:
        r = client.get("/api/bitfinex/audit")
        body = r.get_json()
        assert body["rows"] == 1
        assert body["verify"]["ok"] is True
    finally:
        # Unwire for test isolation.
        wire(context_provider=None, audit=None)
