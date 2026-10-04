"""Alerts section: episode history, analyzer routes, insights feed (Fly routes: test_system_health_alerts_fly.py)."""
import json
import time
from datetime import datetime, timezone

import system_health_alerts as sha
import system_health_banner as shb

NOW = datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc).timestamp()


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _ev(minutes_ago, event, check, observed="seen", **extra):
    return {"schema": sha.EVENT_SCHEMA, "at": _iso(NOW - minutes_ago * 60), "event": event, "check": check,
            "status": {"OPEN": "RED", "STILL_RED": "RED", "AMBER": "AMBER"}.get(event, "GREEN"),
            "observed": observed, "threshold": "thr", "hint": "technical hint",
            "runbook": f"docs/SYSTEM_HEALTH_RUNBOOK.md#{check.replace('.', '-')}", **extra}


def _events():
    return [
        _ev(300, "OPEN", "analyzer.api", ":9001 unreachable for 25m"),
        _ev(290, "RECOVERED", "analyzer.api", "ok=True", opened_at=_iso(NOW - 300 * 60), open_for="10m"),
        _ev(120, "AMBER", "deepseek.balance", "$1.22 USD"),
        _ev(30, "OPEN", "ws.ticks", "trade tick 500s old"),
        _ev(20, "STILL_RED", "ws.ticks", "trade tick 900s old"),
        _ev(10, "RECOVERED", "fly.process", "alive", opened_at=_iso(NOW - 50 * 60)),  # opening outside the window
    ]


def _report(verdict="AMBER", age=0, **extra):
    return {"schema": shb.SCHEMA, "generated_at": _iso(time.time() - age), "verdict": verdict,
            "counts": {"AMBER": 1}, "failing": [], "open_alarms": [], **extra}


def test_history_pairs_events_into_episodes_active_first_then_newest():
    h = sha.build_history(sha.merge_events([], _events(), now=NOW), now=NOW)
    assert h["counts"] == {"active": 2, "active_red": 1, "active_amber": 1, "total": 4}
    first, second = h["active"]
    assert (first["check"], first["severity"], first["level"]) == ("ws.ticks", "RED", "RED")
    assert first["duration_text"] == "30m" and first["latest_observed"] == "trade tick 900s old"
    assert first["observed"] == "trade tick 500s old"
    assert second["check"] == "deepseek.balance" and second["severity"] == "AMBER"
    past = [e for e in h["alerts"] if not e["active"]]
    assert [e["check"] for e in past] == ["fly.process", "analyzer.api"]
    api = past[1]
    assert api["severity"] == "RECOVERED" and api["level"] == "RED" and api["duration_text"] == "10m"
    assert api["title"] == "Analyzer dashboard is up" and "replaced during an analyzer run" in api["likely_cause"]
    assert api["started"] == {"aest": "2026-10-02 11:00 AEST", "utc": "01:00 UTC", "iso": "2026-10-02T01:00:00Z"}
    assert api["cleared"]["utc"] == "01:10 UTC"
    assert api["runbook_url"].endswith("/docs/SYSTEM_HEALTH_RUNBOOK.md#analyzer-api")
    assert past[0]["duration_text"] == "40m"  # RECOVERED whose OPEN was before the retained window


def test_latest_statuses_close_episodes_whose_clear_event_was_never_logged():
    h = sha.build_history(sha.merge_events([], _events(), now=NOW), now=NOW,
                          statuses={"ws.ticks": "GREEN", "deepseek.balance": "AMBER"}, statuses_at=NOW - 60)
    assert [e["check"] for e in h["active"]] == ["deepseek.balance"]
    ws = next(e for e in h["alerts"] if e["check"] == "ws.ticks")
    assert ws["severity"] == "RECOVERED" and "no clear event logged" in ws["clear_note"]


def test_merge_is_deduplicated_sanitized_and_bounded():
    events = sha.merge_events([], _events() + _events(), now=NOW)
    assert len(events) == len(_events())
    huge = [dict(_ev(1, "AMBER", "x.y"), observed="o" * 10_000, secret="leak")]
    clean = sha.merge_events(events, huge + [{"event": "BOGUS"}, "junk"], now=NOW)
    assert len(clean) == len(events) + 1
    assert len(clean[-1]["observed"]) == 240 and "secret" not in clean[-1]
    old = [_ev(31 * 24 * 60, "AMBER", "old.check")]
    assert all(e["check"] != "old.check" for e in sha.merge_events([], old, now=NOW))
    many = [_ev(i / 100.0, "AMBER", f"c{i}") for i in range(sha.RETAIN_EVENTS + 50)]
    bounded = []
    for start in range(0, len(many), sha.MAX_EVENTS_PER_PUSH):
        bounded = sha.merge_events(bounded, many[start:start + sha.MAX_EVENTS_PER_PUSH], now=NOW)
    assert len(bounded) == sha.RETAIN_EVENTS


def test_html_is_plain_english_and_escaped():
    events = sha.merge_events([], _events() + [_ev(5, "AMBER", "x.y", "<script>alert(1)</script>")], now=NOW)
    page = sha.render_alerts_html(sha.build_history(events, now=NOW), title="Alerts", nav_links=(("Home", "/"),))
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page
    assert "Active now" in page and "RECOVERED (was RED)" in page and "AEST" in page and "UTC" in page
    assert "How to fix (runbook)" in page and "Live price feed is flowing" in page


def _write_state(tmp_path, events, report=None):
    (tmp_path / "health").mkdir(exist_ok=True)
    (tmp_path / "health" / "alarms.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    if report is not None:
        (tmp_path / "health" / "system-health-latest.json").write_text(json.dumps(report), encoding="utf-8")


def _recent(events):
    return [dict(e, at=_iso(time.time() - (NOW - sha._parse_ts(e["at"])))) for e in events]


def test_analyzer_reads_alarm_log_directly_and_bypasses_cache(monkeypatch, tmp_path):
    from research import research_dashboard as dashboard

    _write_state(tmp_path, _recent(_events()))
    monkeypatch.setenv("DOXXED_LAPTOP_CHAIN_STATE", str(tmp_path))
    client = dashboard.app.test_client()
    first = client.get("/api/system-health/alerts")
    assert first.status_code == 200 and first.headers.get("X-Research-Cache") is None
    assert first.get_json()["counts"]["active"] == 2
    _write_state(tmp_path, _recent(_events()[:2]))
    assert client.get("/api/system-health/alerts").get_json()["counts"]["active"] == 0
    page = client.get("/alerts").get_data(as_text=True)
    assert "Analyzer dashboard is up" in page and "RECOVERED (was RED)" in page
    assert ("Alerts", "/alerts") == dashboard.DECISION_NAV_LINKS[0]


def test_insights_exposes_alert_history(monkeypatch, tmp_path):
    from strategy_lab import insights

    _write_state(tmp_path, _recent(_events()))
    monkeypatch.setattr(insights, "STATE_DIR", str(tmp_path))
    comp = insights.alerts_component(time.time())
    assert comp["status"] == insights.OK
    assert [a["check"] for a in comp["data"]["active"]] == ["ws.ticks", "deepseek.balance"]
    assert comp["data"]["alerts"][0]["started"]["aest"].endswith(("AEST", "AEDT"))
    monkeypatch.setattr(insights, "STATE_DIR", str(tmp_path / "absent"))
    assert insights.alerts_component(time.time())["status"] == insights.UNAVAILABLE


def test_state_round_trips_and_survives_corruption(tmp_path):
    path = tmp_path / "vol" / sha.STATE_FILE
    events = sha.merge_events([], _events(), now=NOW)
    assert sha.save_state(path, events=events, statuses={"ws.ticks": "RED", "bad": "PURPLE"},
                          statuses_at=NOW - 60, digest={"schema": "d"}, digest_ts=NOW - 30, now=NOW)
    got = sha.load_state(path, now=NOW)
    assert got["events"] == events and got["statuses"] == {"ws.ticks": "RED"}
    assert got["statuses_at"] == NOW - 60 and got["digest"] == {"schema": "d"} and got["digest_ts"] == NOW - 30
    history = sha.build_history(got["events"], now=NOW)
    assert any(e["check"] == "ws.ticks" for e in history["active"])
    # Retention still applies after a long downtime; a corrupt or foreign file restores nothing.
    assert sha.load_state(path, now=NOW + (sha.RETAIN_DAYS + 1) * 86400)["events"] == []
    path.write_text("{not json", encoding="utf-8")
    assert sha.load_state(path, now=NOW)["events"] == []
    path.write_text(json.dumps({"schema": "other", "events": events}), encoding="utf-8")
    assert sha.load_state(path, now=NOW)["events"] == []
    assert sha.load_state(tmp_path / "missing.json", now=NOW)["digest"] is None
    assert not list(path.parent.glob("*.tmp-*"))


def test_alert_times_follow_sydney_daylight_saving() -> None:
    import system_health_alerts as sha
    from datetime import datetime, timezone

    summer = datetime(2026, 10, 4, 8, 41, tzinfo=timezone.utc).timestamp()
    winter = datetime(2026, 7, 1, 1, 0, tzinfo=timezone.utc).timestamp()
    assert sha.times(summer)["aest"] == "2026-10-04 19:41 AEDT"
    assert sha.times(winter)["aest"] == "2026-07-01 11:00 AEST"
    # The tzdata-less fallback (Windows laptop) applies the same NSW rule.
    saved = sha._SYDNEY
    try:
        sha._SYDNEY = None
        assert sha.times(summer)["aest"] == "2026-10-04 19:41 AEDT"
        assert sha.times(winter)["aest"] == "2026-07-01 11:00 AEST"
        assert sha.times(datetime(2026, 10, 3, 15, 59, tzinfo=timezone.utc).timestamp())["aest"].endswith("AEST")
        assert sha.times(datetime(2027, 4, 3, 16, 0, tzinfo=timezone.utc).timestamp())["aest"].endswith("AEST")
        assert sha.times(datetime(2027, 4, 3, 15, 59, tzinfo=timezone.utc).timestamp())["aest"].endswith("AEDT")
    finally:
        sha._SYDNEY = saved
    page = sha.render_alerts_html({"alerts": [], "counts": {}, "generated_at": None})
    assert "Times are AEST (UTC+10)" not in page and "AEDT" in page
