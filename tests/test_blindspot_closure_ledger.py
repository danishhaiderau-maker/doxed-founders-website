import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "blindspot_closure_ledger.py"
spec = importlib.util.spec_from_file_location("blindspot_closure_ledger", SCRIPT)
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


def _monitor(conclusion="success", *lines):
    return {"gh_monitor": {"databaseId": 1, "headSha": "a" * 40, "createdAt": "2026-10-02T12:25:13Z",
                           "conclusion": conclusion, "lines": list(lines)}}


def test_monitor_rules_need_a_run_on_the_new_code_without_missing_contract_fields():
    state = "state restored=True crashed=False clean_streak=1 incident_close_allowed=False"
    assert ledger.v_monitor_rules(_monitor("success", state, "Fly bot healthy, paper-only"))[0]
    assert ledger.v_monitor_rules(_monitor("failure", state, "::warning title=fly-monitor relay_stale_owner_pending"))[0]
    assert not ledger.v_monitor_rules(_monitor("success", "Fly bot healthy"))[0]
    assert not ledger.v_monitor_rules(_monitor("cancelled", state))[0]
    assert not ledger.v_monitor_rules(_monitor("failure", state, "::warning title=fly-monitor contract_field_missing"))[0]
    assert not ledger.v_monitor_rules({"gh_monitor": None})[0]


def test_monitor_heartbeat_falls_back_to_runs_api_evidence_only_when_present():
    gap = "previous monitor run evidence 2026-10-02T11:50:36Z via Actions runs API; gap 36 min"
    state = "state restored=True crashed=False clean_streak=1 incident_close_allowed=False"
    assert ledger.v_monitor_heartbeat({**_monitor("success", gap, state), "gh_vars": {}})[0]
    assert not ledger.v_monitor_heartbeat({**_monitor("success", state), "gh_vars": {}})[0]
    assert not ledger.v_monitor_heartbeat({"gh_monitor": None, "gh_vars": {}})[0]


def test_check_status_verifiers_never_pass_on_absent_checks():
    live = {"w9011": {"features": ["missing_data_not_green"], "checks": [
        {"id": "railway.relay", "status": "GREEN", "observed": "mode=PAUSED reconciliation=null"}]}}
    verifier = ledger.PLAN["L56"][4]
    assert not verifier(live)[0]
    live["w9011"]["checks"][0]["status"] = "AMBER"
    assert verifier(live)[0]
    assert not verifier({"w9011": {"features": ["missing_data_not_green"], "checks": []}})[0]
