"""Regression coverage for Windows PowerShell UTF-8 storage receipts."""
import json

from research import research_dashboard as dashboard


def test_summary_accepts_utf8_bom_size_report(tmp_path, monkeypatch):
    receipt = {
        "local_size_mb": 9.25,
        "local_file_count": 43,
        "fly_size_mb": 66.0,
        "fly_volume_pct": 3.3,
        "computed_at": "2026-08-23T18:36:48Z",
        "sync_interval_seconds": 180,
        "sync_threshold_mb": 50,
    }
    (tmp_path / dashboard.MIRROR_SIZE_REPORT_FILE).write_text(
        json.dumps(receipt), encoding="utf-8-sig"
    )
    monkeypatch.setattr(dashboard, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(dashboard, "_read_json", lambda *args, **kwargs: {})
    monkeypatch.setattr(dashboard, "_read_text", lambda *args, **kwargs: "")
    monkeypatch.setattr(dashboard, "_integrity_payload", lambda: {})

    response = dashboard.app.test_client().get("/api/summary")

    assert response.status_code == 200
    storage = response.get_json()["storage"]
    assert storage["local_size_mb"] == 9.25
    assert storage["local_file_count"] == 43
    assert storage["fly_size_mb"] == 66.0
    assert storage["fly_volume_pct"] == 3.3
