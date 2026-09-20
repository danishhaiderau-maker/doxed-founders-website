import json
import os

import pytest

_saved_data_root = os.environ.pop("BTC_AGENT_DATA_DIR", None)
_saved_report_root = os.environ.pop("BTC_AGENT_REPORT_DIR", None)
try:
    from research import research_dashboard as dashboard
finally:
    if _saved_data_root is not None:
        os.environ["BTC_AGENT_DATA_DIR"] = _saved_data_root
    if _saved_report_root is not None:
        os.environ["BTC_AGENT_REPORT_DIR"] = _saved_report_root


def _fence_payload():
    return {
        "schema": "local_research_generation_fence_v1",
        "state": "BLOCKED_PENDING_VERIFIED_IMPORT",
        "operation_id": "reset-test",
        "local_generation": "retired-test-generation",
        "tombstone_id": "tombstone-test",
    }


def _tree_snapshot(root):
    return sorted(
        (str(path.relative_to(root)), path.read_bytes())
        for path in root.rglob("*") if path.is_file()
    )


@pytest.mark.parametrize("malformed", [False, True])
def test_fenced_view_never_reads_reports_exports_or_writes(tmp_path, monkeypatch, malformed):
    fence = tmp_path / ".local-generation-fence.json"
    fence.write_text("{" if malformed else json.dumps(_fence_payload()), encoding="utf-8")
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    monkeypatch.setenv("SOURCE_GIT_REV", "a" * 40)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("fenced viewer attempted research/report/archive access")

    for name in (
        "_read_report", "_read_json", "_best_report_path", "_archives_index",
        "_past_analysis_index", "_manifest_reports", "_build_bundle_manifest",
    ):
        if hasattr(dashboard, name):
            monkeypatch.setattr(dashboard, name, forbidden)

    before = _tree_snapshot(tmp_path)
    client = dashboard.app.test_client()
    expected = "LOCAL_GENERATION_FENCE_INVALID" if malformed else "BLOCKED_PENDING_VERIFIED_IMPORT"
    expected_status = 503 if malformed else 200

    home = client.get("/")
    assert home.status_code == expected_status
    assert expected.encode() in home.data
    assert b"No current local generation" in home.data
    assert b"Ready: NO" in home.data
    assert b"pending a separate verified import" in home.data
    assert b"stale" not in home.data.lower()

    for path in ("/api/health", "/api/status", "/api/integrity"):
        response = client.get(path)
        assert response.status_code == expected_status
        payload = response.get_json()
        assert payload["status"] == expected
        assert payload["local_reset"] == (
            "INVALID_FENCE_FAIL_CLOSED" if malformed else "FENCED_PENDING_VERIFIED_IMPORT"
        )
        assert payload["source_revision"] == "a" * 40
        assert payload["current_generation"] is None
        assert payload["ready"] is False
        assert payload["qualification_allowed"] is False
        assert payload["report_access_allowed"] is False

    for path in ("/api/report/stale.json", "/api/archives", "/download/reports", "/download/all-sessions"):
        response = client.get(path)
        assert response.status_code == (503 if malformed else 423)
        payload = response.get_json()
        assert payload["status"] == expected
        assert payload["archive_export_allowed"] is False
        assert payload["analysis_allowed"] is False

    assert _tree_snapshot(tmp_path) == before


def test_unfenced_requests_keep_existing_dashboard_behavior(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    with dashboard.app.test_request_context("/"):
        assert dashboard._serve_local_reset_view() is None
