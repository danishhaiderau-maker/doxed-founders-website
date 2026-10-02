"""Laptop-chain incident escalation: rules, dedup, issue lifecycle, heartbeat."""

import json
import subprocess
import urllib.error
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import fly_monitor_rules as rules
import laptop_chain_incident as lci
from test_fly_monitor_run import _FakeIssues

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc).timestamp()


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "1Z"


def _analyzer(age_sec):
    return {"state": "SUCCEEDED", "exitCode": 0, "lastSuccessAt": None if age_sec is None else _iso(NOW - age_sec)}


def _alerts(age_sec=60, codes=()):
    return {"checkedAt": _iso(NOW - age_sec), "alerts": [{"code": c, "detail": "d"} for c in codes]}


# --- rules ----------------------------------------------------------------

def test_powershell_seven_digit_timestamps_parse():
    assert lci.parse_utc("2026-09-29T16:45:29.3365318Z") == pytest.approx(
        datetime(2026, 9, 29, 16, 45, 29, 336531, tzinfo=timezone.utc).timestamp()
    )
    assert lci.parse_utc(None) is None and lci.parse_utc("garbage") is None


def test_healthy_chain_has_no_findings():
    assert lci.findings(_analyzer(1800), _alerts(), NOW) == {}


def test_analyzer_not_completed_for_more_than_two_hours():
    assert lci.findings(_analyzer(2 * 3600 - 60), _alerts(), NOW) == {}
    found = lci.findings(_analyzer(2 * 3600 + 60), _alerts(), NOW)
    assert set(found) == {"analyzer_stale"} and "2.0h" in found["analyzer_stale"]


def test_analyzer_that_never_completed_is_stale():
    found = lci.findings({"state": "FAILED", "exitCode": 6, "lastSuccessAt": None}, _alerts(), NOW)
    assert "has not COMPLETED for ever" in found["analyzer_stale"] and "exit=6" in found["analyzer_stale"]
    assert "analyzer_stale" in lci.findings(None, _alerts(), NOW)


def test_segment_pull_dead_from_fresh_monitor_output():
    found = lci.findings(_analyzer(60), _alerts(codes=["SEGMENT_PULL_DEAD", "SEGMENT_PULL_STALE"]), NOW)
    assert set(found) == {"segment_pull_dead"}


def test_stale_or_missing_monitor_output_is_monitor_stale_not_pull_state():
    found = lci.findings(_analyzer(60), _alerts(age_sec=3600, codes=["SEGMENT_PULL_DEAD"]), NOW)
    assert set(found) == {"monitor_stale"}
    assert set(lci.findings(_analyzer(60), None, NOW)) == {"monitor_stale"}


def test_segment_pull_dead_needs_two_ticks_and_ten_minutes():
    state = lci.alerts.empty_state()
    run = lambda t: {d["key"]: d["action"] for d in lci.alerts.evaluate(
        state, {"segment_pull_dead": "x"}, now=t, maintenance=False, policies=lci.POLICIES)[0]}
    assert run(0) == {"segment_pull_dead": "pending"}
    assert run(300) == {"segment_pull_dead": "pending"}
    assert run(600) == {"segment_pull_dead": "alert"}
    assert run(900) == {"segment_pull_dead": "known"}


# --- end-to-end with a fake GitHub ----------------------------------------

class _Client(_FakeIssues):
    def __init__(self):
        super().__init__()
        self.heartbeats = []

    def set_heartbeat(self, now):
        self.heartbeats.append(now)


def _write(tmp_path, analyzer, active, now=NOW, health=None):
    (tmp_path / "alerts").mkdir(exist_ok=True)
    (tmp_path / "health").mkdir(exist_ok=True)
    (tmp_path / "analyzer-run.status.json").write_text(json.dumps(analyzer), encoding="utf-8-sig")
    (tmp_path / "alerts" / "active-alerts.json").write_text(json.dumps(active), encoding="utf-8")
    report = {"generated_ts": now, "verdict": "GREEN", "open_alarms": [], "failing": []} if health is None else health
    (tmp_path / "health" / "system-health-latest.json").write_text(json.dumps(report), encoding="utf-8")


def _args(tmp_path, **kw):
    base = dict(state_dir=str(tmp_path), repo="o/r", analyzer_max_age_min=120.0,
                test_alert=False, dry_run=False, no_heartbeat=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_one_issue_per_incident_and_close_on_recovery(tmp_path):
    client = _Client()
    _write(tmp_path, _analyzer(3 * 3600), _alerts())
    for i in range(6):  # 30 minutes of supervisor ticks
        _write(tmp_path, _analyzer(3 * 3600 + 300 * i), _alerts(), now=NOW + 300 * i)
        lci.run(_args(tmp_path), client=client, now=NOW + 300 * i)
    assert len(client.issues) == 1 and client.issues[0]["labels"] == [lci.LABEL]
    assert client.comments == []

    _write(tmp_path, _analyzer(60), _alerts(), now=NOW + 1800)
    lci.run(_args(tmp_path), client=client, now=NOW + 1800)
    assert client.issues[0]["state"] == "open"
    lci.run(_args(tmp_path), client=client, now=NOW + 2100)
    assert client.issues[0]["state"] == "closed"
    assert client.comments[-1].startswith("Recovered: `analyzer_stale`")


def test_test_alert_fires_then_closes(tmp_path):
    client = _Client()
    _write(tmp_path, _analyzer(60), _alerts())
    result = lci.run(_args(tmp_path, test_alert=True), client=client, now=NOW)
    assert [(d["key"], d["action"]) for d in result["decisions"]] == [("test_alert", "alert")]
    assert len(client.issues) == 1
    lci.run(_args(tmp_path), client=client, now=NOW + 300)
    lci.run(_args(tmp_path), client=client, now=NOW + 600)
    assert client.issues[0]["state"] == "closed"


def test_failed_issue_sync_is_retried_next_tick(tmp_path):
    class Broken(_Client):
        def call(self, *a, **k):
            raise urllib.error.HTTPError("u", 502, "bad gateway", None, None)

    _write(tmp_path, _analyzer(3 * 3600), _alerts())
    result = lci.run(_args(tmp_path), client=Broken(), now=NOW)
    assert result["synced"] is False and "issue sync failed" in result["error"]
    client = _Client()
    lci.run(_args(tmp_path), client=client, now=NOW + 300)
    assert len(client.issues) == 1


def test_heartbeat_is_throttled_to_every_fifteen_minutes(tmp_path):
    client = _Client()
    _write(tmp_path, _analyzer(60), _alerts())
    for i in range(7):
        lci.run(_args(tmp_path), client=client, now=NOW + 300 * i)
    assert client.heartbeats == [NOW, NOW + 900, NOW + 1800]


def test_missing_or_stale_health_verdict_is_an_incident_not_silence(tmp_path):
    assert set(lci.system_health_findings(None, NOW)) == {"system_health_stale"}
    stale = lci.system_health_findings({"generated_ts": NOW - 16 * 60, "verdict": "GREEN"}, NOW)
    assert set(stale) == {"system_health_stale"} and "16 min old" in stale["system_health_stale"]
    assert lci.system_health_findings({"generated_ts": NOW - 60, "open_alarms": []}, NOW) == {}
    client = _Client()
    _write(tmp_path, _analyzer(60), _alerts(), health={"generated_ts": NOW - 3600, "open_alarms": []})
    result = lci.run(_args(tmp_path), client=client, now=NOW)
    assert [(d["key"], d["action"]) for d in result["decisions"]] == [("system_health_stale", "alert")]
    assert len(client.issues) == 1


def test_safety_reds_are_split_from_suppressible_reds():
    report = {"generated_ts": NOW - 60, "open_alarms": ["bitfinex.exposure", "fly.paused"],
              "failing": [{"id": "bitfinex.exposure", "observed": "qty=0.001"}, {"id": "fly.paused", "observed": "x"}]}
    found = lci.system_health_findings(report, NOW)
    assert set(found) == {"system_health_safety_red", "system_health_red"}
    assert "bitfinex.exposure" in found["system_health_safety_red"] and "fly.paused" in found["system_health_red"]
    assert lci.POLICIES["system_health_safety_red"].suppress_in_maintenance is False
    assert lci.POLICIES["system_health_red"].suppress_in_maintenance is True


def _snapshot(paused, owner, age_sec=60):
    return {"ok": True, "observedAt": _iso(NOW - age_sec), "execution_paused": paused, "pause_owner": owner}


def test_maintenance_comes_from_the_deploy_pause_in_a_fresh_runtime_snapshot():
    assert lci.deploy_maintenance(_snapshot(True, "DEPLOY_MAINTENANCE"), NOW) is True
    assert lci.deploy_maintenance(_snapshot(True, "SAFETY"), NOW) is False
    assert lci.deploy_maintenance(_snapshot(False, None), NOW) is False
    assert lci.deploy_maintenance(_snapshot(True, "DEPLOY_MAINTENANCE", age_sec=3600), NOW) is False
    assert lci.deploy_maintenance(None, NOW) is False


def test_deploy_maintenance_suppresses_health_red_for_at_most_ninety_minutes(tmp_path):
    client = _Client()
    red = lambda t: {"generated_ts": t, "open_alarms": ["fly.paused"], "failing": [{"id": "fly.paused"}]}
    actions = []
    for minute in (0, 30, 60, 89, 91):
        now = NOW + minute * 60
        _write(tmp_path, _analyzer(60), _alerts(age_sec=60 - minute * 60), now=now, health=red(now))
        (tmp_path / "fly_runtime_snapshot_v1.json").write_text(json.dumps(
            {"ok": True, "observedAt": _iso(now - 30), "execution_paused": True, "pause_owner": "DEPLOY_MAINTENANCE"}))
        result = lci.run(_args(tmp_path), client=client, now=now)
        assert result["maintenance"] is True
        actions.append({d["key"]: d["action"] for d in result["decisions"]}.get("system_health_red"))
    assert actions == ["suppressed"] * 4 + ["alert"]


def test_main_exits_nonzero_when_github_push_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(lci, "run", lambda args: {"decisions": [], "resolved": [], "synced": False,
                                                  "error": "issue sync failed: HTTPError"})
    assert lci.main(["--state-dir", str(tmp_path)]) == 1
    monkeypatch.setattr(lci, "run", lambda args: {"decisions": [], "resolved": [], "synced": True})
    assert lci.main(["--state-dir", str(tmp_path)]) == 0


def test_dry_run_makes_no_calls_and_writes_no_state(tmp_path):
    _write(tmp_path, _analyzer(3 * 3600), _alerts())
    result = lci.run(_args(tmp_path, dry_run=True), client=None, now=NOW)
    assert [d["key"] for d in result["decisions"]] == ["analyzer_stale"]
    assert not (tmp_path / "laptop-chain-incident.state.json").exists()


def test_gh_cli_maps_http_errors_and_sends_json_body():
    calls = []

    def runner(cmd, **kw):
        calls.append((cmd, kw.get("input")))
        if cmd[-1] == "-" and "labels" in cmd[4]:
            return subprocess.CompletedProcess(cmd, 1, "", "gh: Validation Failed (HTTP 422)")
        return subprocess.CompletedProcess(cmd, 0, '{"number": 7}', "")

    gh = lci.GhCli("o/r", runner=runner)
    assert gh.call("/issues/7", "PATCH", {"state": "closed"}) == {"number": 7}
    assert calls[0][0][:5] == ["gh", "api", "-X", "PATCH", "repos/o/r/issues/7"]
    assert json.loads(calls[0][1]) == {"state": "closed"}
    with pytest.raises(urllib.error.HTTPError) as exc:
        gh.call("/labels", "POST", {"name": "x"})
    assert exc.value.code == 422


def test_notifying_writes_are_relayed_as_github_actions():
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    gh = lci.GhCli("o/r", runner=runner)
    assert gh.call("/issues", "POST", {"title": "t", "labels": [lci.LABEL]}) == {"relayed": True}
    assert gh.call("/issues/3/comments", "POST", {"body": "Alert"}) == {"relayed": True}
    for cmd in calls:
        assert cmd[:4] == ["gh", "workflow", "run", lci.RELAY_WORKFLOW]
    assert calls[0][-3:] == ["path=/issues", "-f", 'body={"title": "t", "labels": ["laptop-chain-incident"]}']
    assert "path=/issues/3/comments" in calls[1]


def test_relay_workflow_is_restricted_to_issue_posts():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".github/workflows/laptop-incident-relay.yml").read_text()
    assert "^/issues(/[0-9]+/comments)?$" in text
    assert 'index("laptop-chain-incident")' in text
    assert "issues: write" in text and "contents: read" in text


# --- supervisor-dead detection on the Fly monitor side --------------------

def test_fly_monitor_flags_silent_laptop_supervisor_after_two_hours():
    assert "unset" in rules.laptop_heartbeat_findings("", NOW)["laptop_silent"]
    assert "unset" in rules.laptop_heartbeat_findings(None, NOW)["laptop_silent"]
    assert rules.laptop_heartbeat_findings(str(int(NOW - 3600)), NOW) == {}
    found = rules.laptop_heartbeat_findings(str(int(NOW - 3 * 3600)), NOW)
    assert "laptop_silent" in found and "3.0h" in found["laptop_silent"]
    assert set(rules.laptop_heartbeat_findings("not-a-number", NOW)) == {"laptop_silent"}
