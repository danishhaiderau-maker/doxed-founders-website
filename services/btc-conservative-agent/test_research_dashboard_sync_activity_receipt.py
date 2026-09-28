from datetime import datetime, timezone

import pytest

from research import research_dashboard as dashboard


NOW = datetime(2026, 9, 5, 12, tzinfo=timezone.utc).timestamp()


def timestamp(delta):
    return datetime.fromtimestamp(NOW + delta, timezone.utc).isoformat()


@pytest.mark.parametrize("delta,freshness", [(0, "FRESH"), (-600, "FRESH"), (-601, "STALE"), (1, "FUTURE_TIMESTAMP")])
def test_receipt_age_never_proves_owner(delta, freshness):
    result = dashboard._mirror_sync_activity_meta({"inProgress": True, "updatedAt": timestamp(delta)}, now_ts=NOW)
    assert result["mirror_sync_receipt_freshness"] == freshness
    assert result["mirror_sync_owner_verified"] is False
    assert result["mirror_sync_receipt_age_seconds"] == -delta
    if freshness == "FRESH":
        assert result["mirror_sync_activity_status"] == "REPORTED_IN_PROGRESS_OWNER_UNVERIFIED"
    else:
        assert result["mirror_sync_activity_status"].startswith("UNKNOWN_")


@pytest.mark.parametrize("stamp,freshness", [(None, "MISSING_TIMESTAMP"), ("", "MISSING_TIMESTAMP"),
    ("not-a-time", "INVALID_TIMESTAMP"), (True, "INVALID_TIMESTAMP"), (12345, "INVALID_TIMESTAMP"),
    ("2026-09-05T12:00:00", "INVALID_TIMESTAMP")])
def test_missing_invalid_and_unzoned_receipts_have_unknown_activity(stamp, freshness):
    result = dashboard._mirror_sync_activity_meta({"updatedAt": stamp, "inProgress": True}, now_ts=NOW)
    assert result["mirror_sync_receipt_freshness"] == freshness
    assert result["mirror_sync_activity_status"] == "UNKNOWN_" + freshness


def test_missing_timestamp_does_not_use_filesystem_mtime_or_secondary_invalid_fallback():
    assert dashboard._mirror_sync_activity_meta({}, now_ts=NOW)["mirror_sync_activity_status"] == "UNKNOWN_MISSING_TIMESTAMP"
    result = dashboard._mirror_sync_activity_meta({"updatedAt": "invalid", "syncedAt": timestamp(0)}, now_ts=NOW)
    assert result["mirror_sync_receipt_freshness"] == "INVALID_TIMESTAMP"


def test_documented_completed_receipt_timestamp_alias_is_supported():
    result = dashboard._mirror_sync_activity_meta({"syncedAt": timestamp(-10), "inProgress": False}, now_ts=NOW)
    assert result["mirror_sync_activity_status"] == "REPORTED_IDLE_OWNER_UNVERIFIED"


def test_truthy_string_is_not_a_current_activity_claim():
    result = dashboard._mirror_sync_activity_meta({"updatedAt": timestamp(0), "inProgress": "false"}, now_ts=NOW)
    assert result["mirror_sync_activity_status"] == "UNKNOWN_ACTIVITY_FLAG"


def test_fresh_string_flag_is_not_reported_as_running(monkeypatch):
    monkeypatch.setattr(dashboard.time, "time", lambda: NOW)
    monkeypatch.setattr(dashboard, "_load_bot_session", lambda: {})
    monkeypatch.setattr(dashboard, "_mirror_source_revision", lambda: None)
    monkeypatch.setattr(dashboard, "_mirror_sync_receipt", lambda: {"updatedAt": timestamp(0), "inProgress": "false"})
    result = dashboard._generation_freshness_meta({})
    assert any("activity flag is invalid" in reason for reason in result["reasons"])
    assert not any("synchronization is in progress" in reason for reason in result["reasons"])


@pytest.mark.parametrize("stamp", [timestamp(-601), timestamp(30), None, "bad"])
def test_expired_in_progress_never_unlocks_qualification(monkeypatch, stamp):
    monkeypatch.setattr(dashboard.time, "time", lambda: NOW)
    monkeypatch.setattr(dashboard, "_load_bot_session", lambda: {"epoch_id": "epoch-one"})
    monkeypatch.setattr(dashboard, "_mirror_source_revision", lambda: "revision-one")
    monkeypatch.setattr(dashboard, "_mirror_sync_receipt", lambda: {
        "inProgress": True, "updatedAt": stamp, "ok": True, "pollOk": True,
        "revisionParity": "MATCH", "observedSourceRevision": "revision-one",
    })
    result = dashboard._generation_freshness_meta({"source_revision": "revision-one", "fresh_epoch": {"epoch_id": "epoch-one"}})
    assert result["revision_parity"] == result["epoch_parity"] == "MATCH"
    assert result["mirror_sync_in_progress"] is True  # compatibility/safety, not proof of a process
    assert result["current"] is False and result["qualification_allowed"] is False
    assert result["mirror_sync_activity_status"].startswith("UNKNOWN_")
    assert any("current downloader activity is unknown" in reason for reason in result["reasons"])
    assert "Canonical Fly mirror synchronization is in progress" not in result["reasons"]
