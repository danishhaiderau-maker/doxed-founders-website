"""Alerts section on Fly: the watcher push retains bounded alarm history; /alerts, JSON route, dashboard section."""
import time

import system_health_alerts as sha
from test_system_health_alerts import _events, _iso, _report, NOW

AUTH = {"X-Bot-Admin-Token": "health-test-token"}
REMOTE = {"REMOTE_ADDR": "198.51.100.7"}


def _bot(monkeypatch):
    import bot

    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "health-test-token")
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setitem(bot._SYSTEM_HEALTH_REPORT, "report", None)
    monkeypatch.setitem(bot._SYSTEM_HEALTH_ALARMS, "events", [])
    monkeypatch.setitem(bot._SYSTEM_HEALTH_ALARMS, "statuses", {})
    monkeypatch.setitem(bot._SYSTEM_HEALTH_ALARMS, "statuses_at", None)
    return bot


def test_fly_report_retains_pushed_alarm_history_and_serves_it(monkeypatch):
    bot = _bot(monkeypatch)
    client = bot.app.test_client()
    events = [dict(e, at=_iso(time.time() - (NOW - sha._parse_ts(e["at"])))) for e in _events()]
    body = _report(alarm_events=events[:3], check_status={"ws.ticks": "RED"})
    assert client.post("/api/system-health/report", json=body, environ_base=REMOTE).status_code == 401
    first = client.post("/api/system-health/report", json=body, headers=AUTH, environ_base=REMOTE).get_json()
    assert first["alarm_history"]["count"] == 3
    again = client.post("/api/system-health/report", json=_report(alarm_events=events), headers=AUTH,
                        environ_base=REMOTE).get_json()
    assert again["alarm_history"]["count"] == len(events)
    assert again["alarm_history"]["through_ts"] == max(sha._parse_ts(e["at"]) for e in events)
    public = client.get("/api/system-health/alerts", environ_base=REMOTE)
    assert public.status_code == 200 and public.headers["Cache-Control"] == "no-store"
    data = public.get_json()
    assert data["schema"] == sha.SCHEMA and data["counts"]["active_red"] == 1
    assert all("technical_hint" not in e for e in data["alerts"])
    admin = client.get("/api/system-health/alerts", headers=AUTH, environ_base=REMOTE).get_json()
    assert admin["alerts"][0]["technical_hint"] == "technical hint"
    page = client.get("/alerts", environ_base=REMOTE)
    assert page.status_code == 200 and "Live price feed is flowing" in page.get_data(as_text=True)


def test_fly_report_without_alarm_events_keeps_previous_history(monkeypatch):
    bot = _bot(monkeypatch)
    client = bot.app.test_client()
    events = [dict(e, at=_iso(time.time() - 60)) for e in _events()[:1]]
    client.post("/api/system-health/report", json=_report(alarm_events=events), headers=AUTH, environ_base=REMOTE)
    out = client.post("/api/system-health/report", json=_report(), headers=AUTH, environ_base=REMOTE).get_json()
    assert out["alarm_history"]["count"] == 1


def test_fly_dashboard_has_alerts_section(monkeypatch):
    bot = _bot(monkeypatch)
    html = bot.app.test_client().get("/", environ_base=REMOTE).get_data(as_text=True)
    assert 'id="alertsSection"' in html and html.count(sha.SECTION_MARKER) == 1
    assert '"/api/system-health/alerts"' in html
