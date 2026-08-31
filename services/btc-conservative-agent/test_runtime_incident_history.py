import json

from runtime_incident_history import build_runtime_incident_history


def test_runtime_history_classifies_only_explicit_application_receipts(tmp_path):
    path = tmp_path / "crash_dump.json"
    rows = [
        {"time": "2026-08-31T00:00:00Z", "watchdog": {
            "schema": "watchdog_crash_context_v1", "trigger": "STRATEGY_PROGRESS_EXIT_75",
            "restart_allowed": True, "exit_code": 75, "source_revision": "abc123",
            "bot_instance_id": "old-instance",
        }},
        {"time": "2026-08-31T01:00:00Z", "edge_score": 1.0},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    result = build_runtime_incident_history(
        path, current_started_at="2026-08-31T02:00:00Z",
        current_instance_id="current", current_revision="def456",
    )

    assert [row["classification"] for row in result["application_incidents"]] == [
        "APPLICATION_WATCHDOG_RESTART_REQUESTED", "APPLICATION_CRASH_DUMP_UNATTRIBUTED"
    ]
    assert result["application_incidents"][0]["exit_code"] == 75
    assert result["platform_events"] == []
    assert result["platform_history_status"] == "UNAVAILABLE_NO_AUTHORITATIVE_PLATFORM_EVENT_RECEIPTS"


def test_runtime_history_skips_malformed_and_is_bounded(tmp_path):
    path = tmp_path / "crash_dump.json"
    path.write_text(
        "not-json\n" + "\n".join(json.dumps({"time": str(i)}) for i in range(6)),
        encoding="utf-8",
    )
    result = build_runtime_incident_history(
        path, current_started_at=None, current_instance_id=None,
        current_revision=None, limit=3,
    )
    assert [row["time"] for row in result["application_incidents"]] == ["3", "4", "5"]
    assert result["malformed_receipts_skipped"] == 1


def test_runtime_history_missing_file_is_honest(tmp_path):
    result = build_runtime_incident_history(
        tmp_path / "missing.jsonl", current_started_at=None,
        current_instance_id=None, current_revision=None,
    )
    assert result["application_incidents"] == []
    assert "not inferred" in result["platform_history_note"]


def test_dashboard_wires_runtime_history_without_platform_inference():
    source = (__import__("pathlib").Path(__file__).with_name("bot.py")).read_text(encoding="utf-8")
    assert 'snapshot["runtime_incident_history"] = build_runtime_incident_history(' in source
    assert 'id="runtimeIncidentTable"' in source
    assert "No retained application incident receipts" in source
    assert "history.platform_history_status" in source
    assert "const incidentText = value =>" in source


def test_research_dashboard_exposes_read_only_runtime_incident_view():
    source = (__import__("pathlib").Path(__file__).parent / "research" / "research_dashboard.py").read_text(encoding="utf-8")
    assert '@app.route("/api/runtime-incidents")' in source
    assert '("runtime-incidents", "Runtime Incidents", None)' in source
    assert 'id="runtime-incidents-body"' in source
    assert "'runtime-incidents': [loadRuntimeIncidents]" in source
    assert "Fly platform and deployment causes remain unavailable" in source
