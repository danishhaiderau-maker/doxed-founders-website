import json
import re

import pytest

from research import research_dashboard as dashboard


FLY_SYNC_ID = "v31-five-family-score-led-non-tie-paper-v2"
FLY_REGISTRY = "524acf4949ae234e39a3902882a20872dadfe33d1235453696256348bf0c9335"


def _status(tmp_path, monkeypatch, heartbeat=None, health=None, report_sync=None):
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "ROOT", tmp_path)
    if heartbeat is not None:
        (tmp_path / dashboard.SYNC_HEARTBEAT_FILE).write_text(json.dumps(heartbeat), encoding="utf-8")
    if health is not None:
        (tmp_path / dashboard.UPSTREAM_IDENTITY_FILE).write_text(json.dumps(health), encoding="utf-8")
    manifest = {"analyzer_sync_id": report_sync or dashboard.EXPECTED_ANALYZER_SYNC_ID}
    monkeypatch.setattr(
        dashboard, "_read_json",
        lambda name, default=None: manifest if name == dashboard.REPORT_MANIFEST_FILE else (default or {}),
    )
    with dashboard.app.test_client() as client:
        return client.get("/api/status").get_json()


def test_mismatch_against_fly_identity_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "EXPECTED_ANALYZER_SYNC_ID", "v31-five-family-analyzer-hypothesis-paper")
    monkeypatch.setattr(dashboard, "active_tile_registry_signature", lambda: "ab621cf7" + "0" * 56)
    body = _status(
        tmp_path, monkeypatch,
        heartbeat={"tileRegistrySignature": FLY_REGISTRY},
        health={"analyzer_sync_id": FLY_SYNC_ID, "tile_registry_signature": FLY_REGISTRY},
    )
    assert body["analyzer_sync_match"] is False
    assert body["ready"] is False
    assert body["ok"] is False
    assert "UPSTREAM_SYNC_ID_MISMATCH" in body["analyzer_sync_blockers"]
    assert "UPSTREAM_TILE_REGISTRY_MISMATCH" in body["analyzer_sync_blockers"]
    assert body["upstream_sync_id"] == FLY_SYNC_ID
    assert body["upstream_tile_registry_signature_source"] == "sync_heartbeat"
    assert body["upstream_sync_id_source"] == "fly_health_snapshot"


def test_matching_fly_identity_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "EXPECTED_ANALYZER_SYNC_ID", FLY_SYNC_ID)
    monkeypatch.setattr(dashboard, "active_tile_registry_signature", lambda: FLY_REGISTRY)
    body = _status(
        tmp_path, monkeypatch,
        heartbeat={"tileRegistrySignature": FLY_REGISTRY, "botVersion": FLY_SYNC_ID},
    )
    assert body["analyzer_sync_match"] is True
    assert body["analyzer_sync_blockers"] == []


def test_missing_upstream_identity_is_unknown_not_match(tmp_path, monkeypatch):
    body = _status(tmp_path, monkeypatch)
    assert body["analyzer_sync_match"] is None
    assert body["ready"] is False
    assert "UPSTREAM_SYNC_ID_UNAVAILABLE" in body["analyzer_sync_blockers"]


def test_registry_import_failure_is_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "REGISTRY_IMPORT_ERROR", "ImportError: boom")
    monkeypatch.setattr(dashboard, "EXPECTED_ANALYZER_SYNC_ID", FLY_SYNC_ID)
    monkeypatch.setattr(dashboard, "active_tile_registry_signature", lambda: FLY_REGISTRY)
    body = _status(
        tmp_path, monkeypatch,
        heartbeat={"tileRegistrySignature": FLY_REGISTRY, "botVersion": FLY_SYNC_ID},
    )
    assert body["registry_available"] is False
    assert body["analyzer_sync_match"] is not True
    assert body["ready"] is False
    with dashboard.app.test_client() as client:
        html = client.get("/").get_data(as_text=True)
    assert "REGISTRY UNAVAILABLE" in html
    assert "ImportError: boom" in html


def test_registry_fallback_never_invents_identity():
    source = dashboard.__file__
    text = open(source, encoding="utf-8").read()
    fallback = text[text.index("except ImportError as _registry_exc:"):text.index("def is_ai_focused_lane(lane: str) -> bool:\n    return")]
    assert 'EXPECTED_ANALYZER_SYNC_ID = "REGISTRY_UNAVAILABLE"' in fallback
    assert '"unknown"' not in fallback


def _dashboard_script() -> str:
    text = dashboard.DASHBOARD_HTML
    return text[text.index("<script>"):]


def test_evidence_counters_do_not_fabricate_zero():
    script = _dashboard_script()
    offenders = [
        line.strip() for line in script.splitlines()
        if re.search(r"\?\?\s*0(?![.\d])", line)
        and not re.match(r"\s*const \w+ = ", line)
        and not re.search(r"\?\?\s*0\s*\)?\s*(===|!==|>=|<=|>|<)", line)
        and "??0" not in line
    ]
    assert offenders == []
    assert "?? 'NO DATA'" in script


def test_loading_panels_time_out_to_unavailable():
    script = _dashboard_script()
    assert "PANEL_LOADING_TIMEOUT_MS" in script
    assert "sweepStuckLoadingPanels" in script
    assert "setInterval(sweepStuckLoadingPanels" in script
    assert "controller.abort()" in script
    assert "UNAVAILABLE" in script[script.index("function sweepStuckLoadingPanels"):]
