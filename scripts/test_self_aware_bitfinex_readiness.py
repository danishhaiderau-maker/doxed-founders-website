"""Tests for scripts/self_aware/bitfinex_readiness: fetch reduction + verdicts."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_aware import bitfinex_readiness as bfx  # noqa: E402


def _overview(*, live_armed=False, bitfinex_live_enabled=False, alerts=None):
    alerts = alerts or []
    return {
        "schema": "bitfinex_readiness_overview_v1",
        "relay_arm_state": {
            "live_armed": live_armed,
            "bitfinex_live_enabled": bitfinex_live_enabled,
            "force_paper_mode": True,
            "relay_delivery_block": None,
        },
        "switch": {"tile_count": 13, "armed_lane_count": 0, "armed_lanes": [],
                   "registry_signature": "abc"},
        "match": {"paper_count": 0, "matched_count": 0, "unmatched_count": 0, "diverged_count": 0},
        "alerts": {"schema": "bitfinex_health_mismatch_alerts_v1", "alert_count": len(alerts),
                   "critical_count": sum(1 for a in alerts if a["severity"] == "CRITICAL"),
                   "healthy": not alerts, "alerts": alerts},
        "fill_pricing": {"headline_model": "REALISTIC_V1"},
        "audit": {"rows": 0, "signed": False},
        "server_ts": 123.0,
    }


def test_unreachable_fails_closed():
    doc = bfx.run(fetch=lambda url, timeout=10: (None, "ConnectionRefusedError: refused", 0.01))
    assert doc["status"] == "UNREACHABLE"
    assert doc["verdict"] == "UNKNOWN"
    assert doc["live_armed"] is None
    assert doc["alerts"] == []


def test_healthy_disarmed():
    doc = bfx.run(fetch=lambda url, timeout=10: (_overview(), None, 0.01))
    assert doc["status"] == "OK"
    assert doc["verdict"] == "GREEN"
    assert doc["live_armed"] is False
    assert doc["bitfinex_live_enabled"] is False
    assert doc["alert_count"] == 0


def test_critical_alert_is_red():
    alerts = [{"rule": "switch_inconsistent", "severity": "CRITICAL",
               "observed": "lane X ON while armed OFF", "expected": ""}]
    doc = bfx.run(fetch=lambda url, timeout=10: (_overview(alerts=alerts), None, 0.01))
    assert doc["verdict"] == "RED"
    assert doc["status"] == "DEGRADED"
    assert doc["critical_count"] == 1


def test_warning_alert_is_amber():
    alerts = [{"rule": "paper_real_divergence", "severity": "WARNING",
               "observed": "1 diverging twin", "expected": ""}]
    doc = bfx.run(fetch=lambda url, timeout=10: (_overview(alerts=alerts), None, 0.01))
    assert doc["verdict"] == "AMBER"
    assert doc["critical_count"] == 0


def test_diagnose_check_maps_healthy_to_green(monkeypatch):
    from self_aware import diagnose
    doc = bfx.run(fetch=lambda url, timeout=10: (_overview(), None, 0.01))
    monkeypatch.setattr(diagnose.bitfinex_readiness, "run", lambda state, now=None: doc)
    f = {"now": 123.0}
    finding = diagnose.check_bitfinex_readiness(f, {}, None, {})
    assert finding.id == "bitfinex.readiness"
    assert finding.severity == "GREEN"
    assert finding.evidence["live_armed"] is False
    assert f["bitfinex_readiness"] is doc


def test_diagnose_check_unreachable_is_skip(monkeypatch):
    from self_aware import diagnose
    doc = bfx.run(fetch=lambda url, timeout=10: (None, "refused", 0.01))
    monkeypatch.setattr(diagnose.bitfinex_readiness, "run", lambda state, now=None: doc)
    finding = diagnose.check_bitfinex_readiness({"now": 123.0}, {}, None, {})
    assert finding.severity == "SKIP"
