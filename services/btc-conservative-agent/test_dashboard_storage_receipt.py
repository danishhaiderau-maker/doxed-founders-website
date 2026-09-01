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
        "active_lifecycle_bytes": 1048576,
        "completed_unsynchronized_bytes": 2097152,
        "downloaded_unacknowledged_bytes": 3145728,
        "acknowledged_cleanup_eligible_bytes": 0,
        "protected_recovery_bytes": 524288,
        "unknown_unclassified_bytes": 262144,
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
    assert set(storage["categories"]) == {
        "active_lifecycle",
        "completed_unsynchronized",
        "downloaded_unacknowledged",
        "acknowledged_cleanup_eligible",
        "protected_recovery",
        "unknown_unclassified",
    }
    categories = storage["categories"]
    assert categories["active_lifecycle"] == {
        "status": "OBSERVED", "bytes": 1048576, "mb": 1.0,
    }
    assert categories["acknowledged_cleanup_eligible"] == {
        "status": "OBSERVED", "bytes": 0, "mb": 0.0,
    }
    assert categories["unknown_unclassified"]["mb"] == 0.25

    # A receipt without lifecycle classification must stay UNKNOWN rather than
    # fabricating zero-byte categories.
    receipt = {
        key: value for key, value in receipt.items() if not key.endswith("_bytes")
    }
    (tmp_path / dashboard.MIRROR_SIZE_REPORT_FILE).write_text(
        json.dumps(receipt), encoding="utf-8-sig"
    )
    unknown = dashboard.app.test_client().get("/api/summary").get_json()["storage"]
    assert all(
        row["status"] == "UNKNOWN"
        and row["bytes"] is None
        and row["reason"] == "LIFECYCLE_STORAGE_CLASSIFICATION_NOT_IN_SYNC_RECEIPT"
        for row in unknown["categories"].values()
    )
