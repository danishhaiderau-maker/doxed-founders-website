"""Fly.io platform status: feed parsing (captured fixtures), unreachable feed, classification, correlation."""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fly_platform_status as fps  # noqa: E402
import system_health as sh  # noqa: E402
from self_aware import diagnose, fly_platform  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "fly_status"
V1 = json.loads((FIX / "summary_v1_20261002.json").read_text(encoding="utf-8"))
V2 = json.loads((FIX / "summary_v2_20261002.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 2, 15, 10, tzinfo=timezone.utc).timestamp()


def _fetcher(by_suffix: dict):
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        for suffix, result in by_suffix.items():
            if url.endswith(suffix):
                return result
        return None, "UNREACHABLE"
    fetch.calls = calls
    return fetch


def _incident(title="Machines API errors", comps=("SIN - Singapore",), kind_key="ongoing_incidents", **extra):
    doc = copy.deepcopy(V1)
    doc[kind_key] = doc.get(kind_key) or []
    doc[kind_key].append({"id": "01INC", "name": title, "status": "investigating", "url": "https://status.flyio.net/incidents/01INC",
                          "affected_components": [{"name": c} for c in comps], "last_update_message": "Investigating", **extra})
    return doc


def _snap(doc, now=NOW):
    return fps.fetch_snapshot(now, fetch=_fetcher({"/api/v1/summary": (doc, None)}))


# ---------------------------------------------------------------- parsing

def test_parse_v1_fixture_lists_scheduled_regional_maintenance():
    p = fps.parse_v1(V1)
    titles = {e["title"]: e for e in p["events"]}
    assert set(titles) == {"Network Maintenance in ORD", "Network Maintenance in EWR"}
    assert titles["Network Maintenance in ORD"]["components"] == ["ORD - Chicago, Illinois (US)"]
    assert titles["Network Maintenance in ORD"]["kind"] == "scheduled"
    assert titles["Network Maintenance in ORD"]["starts_at"] == "2026-10-06T09:00:00Z"


def test_parse_v2_fixture_operational_with_maintenances():
    p = fps.parse_v2(V2)
    assert p["indicator"] == "none" and p["degraded_components"] == []
    assert {e["title"] for e in p["events"]} == {"Network Maintenance in ORD", "Network Maintenance in EWR"}
    assert all(e["url"].startswith("https://status.flyio.net/incidents/") for e in p["events"])


def test_v1_preferred_then_v2_fallback_then_unreachable():
    s = fps.fetch_snapshot(NOW, fetch=_fetcher({"/api/v1/summary": (V1, None), "/summary.json": (V2, None)}))
    assert s["ok"] and s["source"] == "incident_io_v1"
    s = fps.fetch_snapshot(NOW, fetch=_fetcher({"/api/v1/summary": ({"unexpected": 1}, None),
                                                "/summary.json": (V2, None)}))
    assert s["ok"] and s["source"] == "statuspage_v2" and s["errors"]["incident_io_v1"].startswith("PARSE_")
    s = fps.fetch_snapshot(NOW, fetch=_fetcher({}))
    assert not s["ok"] and s["errors"] == {"incident_io_v1": "UNREACHABLE", "statuspage_v2": "UNREACHABLE"}


def test_cache_five_minutes_and_error_retry():
    fetch = _fetcher({"/api/v1/summary": (V1, None)})
    cache: dict = {}
    fps.cached_snapshot(cache, NOW, fetch=fetch)
    fps.cached_snapshot(cache, NOW + 299, fetch=fetch)
    assert len(fetch.calls) == 1
    fps.cached_snapshot(cache, NOW + 301, fetch=fetch)
    assert len(fetch.calls) == 2


def test_unreachable_keeps_last_good_marked_stale_then_skip_then_amber():
    cache: dict = {}
    fps.cached_snapshot(cache, NOW, fetch=_fetcher({"/api/v1/summary": (V1, None)}))
    down = _fetcher({})
    s = fps.cached_snapshot(cache, NOW + 400, fetch=down)
    assert s["ok"] and s["stale"] and s["down_for_sec"] == 0
    r = fps.assess(s, [], NOW + 400, "sin")
    assert r["status"] == sh.GREEN and "feed currently unreachable" in r["summary"]
    s = fps.cached_snapshot({}, NOW, fetch=down)
    r = fps.assess(s, [], NOW, "sin")
    assert r["status"] == sh.SKIP and r["classification"] == "UNREACHABLE" and "status feed unreachable" in r["summary"]
    r = fps.assess({**s, "down_for_sec": 2 * 3600}, [], NOW, "sin")
    assert r["status"] == sh.AMBER


# ---------------------------------------------------------------- classification

def test_fixture_today_is_info_app_unaffected():
    r = fps.assess(_snap(V1), [{"id": "fly.process", "status": "GREEN"}], NOW, "sin")
    assert r["status"] == sh.GREEN and r["classification"] == "INFO"
    assert r["summary"].startswith("platform notice - app unaffected")
    assert all(e["level"] == "INFO" and not e["affects_app"] for e in r["events"])


def test_status_page_provider_change_is_info_even_in_progress_with_components():
    doc = _incident("Change in Status Page Provider", comps=("Customer Applications", "Dashboard"),
                    kind_key="in_progress_maintenances", starts_at="2026-09-30T00:00:00Z")
    r = fps.assess(_snap(doc), [], NOW, "sin")
    ev = next(e for e in r["events"] if e["id"] == "01INC")
    assert ev["kind"] == "maintenance" and ev["level"] == "INFO"
    assert r["status"] == sh.GREEN and r["classification"] == "INFO"


def test_other_region_incident_is_info():
    r = fps.assess(_snap(_incident("Elevated errors in ORD", comps=("ORD - Chicago, Illinois (US)",))), [], NOW, "sin")
    assert r["status"] == sh.GREEN and r["classification"] == "INFO"


def test_our_region_or_component_incident_is_amber_when_app_healthy():
    for comps in (("SIN - Singapore",), ("Machines API",), ("Persistent Storage (Volumes)",), ("Deployments",),
                  ("Remote Builds",)):
        r = fps.assess(_snap(_incident(comps=comps)), [{"id": "fly.process", "status": "GREEN"}], NOW, "sin")
        assert r["status"] == sh.AMBER, comps
        assert "app unaffected" in r["summary"] and r["correlation"]["likely_platform"] is True


def test_unscoped_incident_text_matching():
    r = fps.assess(_snap(_incident("Proxy errors in all regions", comps=())), [], NOW, "sin")
    assert r["status"] == sh.AMBER
    r = fps.assess(_snap(_incident("Degraded networking in Singapore", comps=())), [], NOW, "sin")
    assert r["status"] == sh.AMBER
    r = fps.assess(_snap(_incident("Dashboard login slow in FRA", comps=())), [], NOW, "sin")
    assert r["status"] == sh.GREEN


def test_scheduled_maintenance_in_our_region_is_info_until_window_opens():
    doc = copy.deepcopy(V1)
    doc["scheduled_maintenances"].append({"id": "01SIN", "name": "Network Maintenance in SIN", "url": "u",
                                          "affected_components": [{"name": "SIN - Singapore"}],
                                          "starts_at": "2026-10-02T16:00:00Z", "ends_at": "2026-10-02T17:00:00Z"})
    r = fps.assess(_snap(doc), [], NOW, "sin")
    assert r["status"] == sh.GREEN and "upcoming in our scope" in r["summary"]
    later = NOW + 3600 + 60
    r = fps.assess(fps.fetch_snapshot(later, fetch=_fetcher({"/api/v1/summary": (doc, None)})), [], later, "sin")
    assert r["status"] == sh.AMBER


def test_region_read_from_fly_toml():
    assert fps.app_region() == "sin"


# ---------------------------------------------------------------- correlation

def test_red_only_when_our_fly_checks_also_fail_and_annotated():
    checks = [{"id": "fly.process", "status": "RED", "hint": "Fly process reports not alive"},
              {"id": "ws.ticks", "status": "AMBER", "hint": ""},
              {"id": "fly.revision", "status": "AMBER", "hint": "master ahead"},
              {"id": "analyzer.api", "status": "RED", "hint": ""}]
    r = fps.assess(_snap(_incident()), checks, NOW, "sin")
    assert r["status"] == sh.RED and r["summary"].startswith("likely Fly platform incident: Machines API errors")
    assert {f["id"] for f in r["app_failing_checks"]} == {"fly.process", "ws.ticks"}
    assert fps.annotate_checks(checks, r) == 2
    assert checks[0]["hint"].startswith("likely Fly platform incident: Machines API errors (https://status.flyio.net/")
    assert checks[0]["platform_correlation"]["likely_platform"] is True
    assert "platform_correlation" not in checks[2] and "platform_correlation" not in checks[3]


def test_failing_app_with_no_platform_incident_is_ours():
    checks = [{"id": "shipper.progress", "status": "RED", "hint": "stalled"}]
    r = fps.assess(_snap(V1), checks, NOW, "sin")
    assert r["status"] == sh.GREEN and r["correlation"]["likely_platform"] is False
    assert "failing app checks are ours" in r["correlation"]["note"]
    fps.annotate_checks(checks, r)
    assert checks[0]["hint"] == "stalled" and checks[0]["platform_correlation"]["likely_platform"] is False


def test_watcher_evaluate_adds_check_and_report_block():
    inputs = {"now": NOW, "errors": {}, "fly_platform": _snap(_incident())}
    checks = sh.evaluate(inputs, {})
    fp = next(c for c in checks if c["id"] == "fly.platform_status")
    assert fp["subsystem"] == "fly" and fp["observed_fields"]["classification"] in ("AMBER", "RED")
    assert fp["observed_fields"]["region"] == "sin" and fp["observed_fields"]["events"]
    fly_process = next(c for c in checks if c["id"] == "fly.process")
    if fly_process["status"] in ("AMBER", "RED"):
        assert fp["status"] == sh.RED and "likely Fly platform incident" in fly_process["hint"]
    assert "fly_platform_status" in sh.WATCHER_FEATURES
    without = sh.evaluate({"now": NOW, "errors": {}}, {})
    assert not any(c["id"] == "fly.platform_status" for c in without)


def test_selfaware_finding_annotates_fly_findings_without_network_when_offline():
    found = [diagnose.Finding("fly.reachability", "t", "attribution", "RED", "down", "up",
                              causes=[{"cause": "unknown", "text": "?"}]),
             diagnose.Finding("prog.analyzer", "t", "analyzer", "RED", "stale", "fresh")]
    state = {"fly_platform_cache": {"snapshot": _snap(_incident()), "attempt_ts": NOW}}
    facts = {"now": NOW, "watcher": {"checks": []}}
    fd = diagnose.check_fly_platform(facts, found, state)
    assert fd.id == "fly.platform_status" and fd.severity == "RED"
    assert found[0].causes[0]["cause"] == "fly_platform_incident" and len(found[0].causes) == 1
    assert "platform_correlation" not in found[1].evidence
    assert facts["fly_platform"]["classification"] == "RED"
    offline = diagnose.check_fly_platform({"now": NOW, "watcher": {}}, [], {})
    assert offline.severity == "SKIP" and "NOT_PROBED" in offline.observed


def test_selfaware_live_probe_uses_cache_and_fetch():
    fetch = _fetcher({"/api/v1/summary": (V1, None)})
    state: dict = {}
    res = fly_platform.assess({"now": NOW, "svc_9011": {}, "watcher": {}}, [], state, NOW, fetch=fetch)
    assert res["classification"] == "INFO" and len(fetch.calls) == 1
    fly_platform.assess({"now": NOW, "svc_9011": {}, "watcher": {}}, [], state, NOW + 60, fetch=fetch)
    assert len(fetch.calls) == 1


def test_selfaware_overview_renders_platform_block():
    pytest.importorskip("duckdb")
    from self_aware import server
    html = server.render_fly_platform(fps.compact(fps.assess(_snap(V1), [], NOW, "sin")))
    assert "Fly platform" in html and "INFO" in html and "Network Maintenance in ORD" in html
    assert server.ROUTES["/api/selfaware/fly-platform"] is server.Handler.fly_platform
    assert "not evaluated" in server.render_fly_platform(None)


def test_diagnose_run_registers_platform_finding(monkeypatch):
    monkeypatch.setattr(diagnose, "signals", lambda f: {})
    found = [f for f in diagnose.run(SimpleNamespace(), None, {"now": NOW, "watcher": {}}, {})
             if f.id == "fly.platform_status"]
    assert len(found) == 1
