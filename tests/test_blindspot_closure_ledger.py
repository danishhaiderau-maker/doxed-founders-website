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


def test_round2_verifiers_need_live_evidence():
    state = "state restored=True crashed=False clean_streak=1 incident_close_allowed=False"
    run2 = {"gh_monitor2": {"databaseId": 2, "headSha": "b" * 40, "conclusion": "success", "lines": [state]}}
    assert ledger.v_monitor_rules2(run2)[0] and not ledger.v_monitor_rules2(_monitor("success", state))[0]
    w = {"checks": [{"id": "a"}, {"id": "b"}], "failing": [{"id": "a"}], "acked": []}
    assert ledger.v_unique_check_ids({"w9011": w})[0] and ledger.v_acks_supported({"w9011": w})[0]
    assert not ledger.v_unique_check_ids({"w9011": {**w, "checks": [{"id": "a"}, {"id": "a"}]}})[0]
    assert not ledger.v_acks_supported({"w9011": {"checks": []}})[0]
    ok = {"a9001_insights": {"components": {"transfer": {"status": "STALE", "data": None, "reason": "old"}}}}
    lie = {"a9001_insights": {"components": {"transfer": {"status": "OK", "data": {"applied_seq": None}}}}}
    assert ledger.v_insights_transfer(ok)[0] and not ledger.v_insights_transfer(lie)[0]
    assert not ledger.v_insights_transfer({})[0]
    c = {"w9011": {"checks": [{"id": "laptop.pull_ack", "observed_fields": {"max_run_seconds": None}}]}}
    assert not ledger.v_field_in_check("laptop.pull_ack", "max_run_seconds", not_none=True)(c)[0]
    assert ledger.v_field_in_check("laptop.pull_ack", "max_run_seconds")(c)[0]
    assert ledger.v_secret_history({"gh_secret_history": {"conclusion": "success"}})[0]


def _sa_blockers(*rows):
    return {"findings": [{"id": "selfaware.expected_blockers", "severity": "AMBER", "evidence": {"blockers": list(rows)}}]}


def test_post_freeze_needs_open_draft_prs_and_a_declared_pending_blocker():
    v = ledger.v_post_freeze((365,), "B20")
    live = {"gh_prs": {"365": {"state": "OPEN", "isDraft": True}},
            "sa9021": _sa_blockers({"id": "B20", "eta": "2026-10-04T15:00:00Z", "overdue": False})}
    assert v(live)[0]
    assert not v({**live, "gh_prs": {"365": {"state": "MERGED", "isDraft": False}}})[0]
    assert not v({**live, "sa9021": _sa_blockers({"id": "B20", "eta": "x", "overdue": True})})[0]
    assert not v({**live, "sa9021": _sa_blockers()})[0]


def test_triaged_states_fall_back_to_open_when_their_precondition_fails_and_never_close():
    yes, no = (lambda live: (True, "ok")), (lambda live: (False, "missing"))
    assert ledger.resolve(("o", "-", ledger.POSTF, "p", yes), {}, {})["status"] == ledger.POSTF
    assert ledger.resolve(("o", "-", ledger.POSTF, "p", no), {}, {})["status"] == ledger.OPEN
    assert ledger.resolve(("o", "-", ledger.NEEDS_DANISH, "p", yes), {}, {})["status"] == ledger.NEEDS_DANISH
    assert ledger.resolve(("o", "-", ledger.NEEDS_DANISH, "p", None), {}, {})["status"] == ledger.NEEDS_DANISH
    assert ledger.resolve(("o", "-", ledger.OPEN, "p", yes), {}, {})["status"] == ledger.CLOSED


def test_adhoc_retirement_and_deploy_receipt_verifiers():
    c = {"id": "laptop.adhoc_processes", "observed_fields": {"listeners": [{"port": 7002, "pid": "1"}], "scripts": []}}
    live = {"w9011": {"features": ["adhoc_visibility"], "checks": [c]}}
    assert ledger.v_adhoc("listeners", "7002", present=True)(live)[0]
    assert ledger.v_adhoc("listeners", "9097", present=False)(live)[0]
    assert ledger.v_adhoc("scripts", "watch_queue.ps1", present=False)(live)[0]
    assert not ledger.v_adhoc("listeners", "9097", present=False)({"w9011": {"features": [], "checks": [c]}})[0]
    good = {"sa9021_receipts": {"deploys": [{"databaseId": 1, "status": "completed", "displayTitle": "feat: tiles\u2026"}]}}
    bad = {"sa9021_receipts": {"deploys": [{"databaseId": 1, "status": "completed", "displayTitle": "feat \u0393\u00c7\u00aa"}]}}
    assert ledger.v_deploy_receipts(good)[0] and not ledger.v_deploy_receipts(bad)[0]
    assert not ledger.v_deploy_receipts({})[0]


def test_triage_rows_all_have_a_live_verifier_or_a_danish_default():
    for tid in ledger.TRIAGE_IDS:
        plan = ledger.PLAN.get(tid) or ledger.GAP_PLAN.get(tid)
        assert plan is not None, tid
        assert plan[4] is not None or plan[2] == ledger.NEEDS_DANISH, tid
        if plan[2] == ledger.NEEDS_DANISH:
            assert "Default:" in plan[3], tid
