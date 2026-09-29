from __future__ import annotations

import json
from datetime import datetime, timezone

import analyzer_research_engine_v62 as analyzer
from research.mirror_coherence import MirrorCoherenceError


NOW = datetime(2026, 8, 31, 2, 22, 13, tzinfo=timezone.utc)


def _heartbeat(tmp_path, **overrides):
    root = tmp_path / "canonical-research-data"
    root.mkdir(parents=True)
    payload = {
        "ok": False,
        "pollOk": False,
        "pollFailedAt": "2026-08-31T12:22:13.0000000+10:00",
        "consecutiveFailures": 14,
        "backoffSec": 1800,
        "nextRetryAt": "2026-08-31T12:52:13.0000000+10:00",
    }
    payload.update(overrides)
    path = root / ".fly-data-sync-loop.heartbeat.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_failed_receipt_honors_remaining_canonical_sync_backoff(tmp_path):
    path = _heartbeat(tmp_path)
    delay, reason = analyzer._mirror_coherence_retry_delay_seconds(
        MirrorCoherenceError("MIRROR_SYNC_RECEIPT_FAILED"),
        1800,
        now=NOW,
        heartbeat_path=path,
    )
    assert delay == 1830
    assert reason == "canonical sync heartbeat backoff"


def test_partial_elapsed_backoff_waits_until_next_retry_plus_grace(tmp_path):
    path = _heartbeat(tmp_path)
    delay, reason = analyzer._mirror_coherence_retry_delay_seconds(
        MirrorCoherenceError("MIRROR_SYNC_RECEIPT_FAILED"),
        1800,
        now=datetime(2026, 8, 31, 2, 42, 13, tzinfo=timezone.utc),
        heartbeat_path=path,
    )
    assert delay == 630
    assert reason == "canonical sync heartbeat backoff"


def test_untrusted_or_elapsed_heartbeat_keeps_one_minute_fail_closed_retry(tmp_path):
    noncanonical = tmp_path / "heartbeat.json"
    noncanonical.write_text("{}", encoding="utf-8")
    assert analyzer._mirror_coherence_retry_delay_seconds(
        MirrorCoherenceError("MIRROR_SYNC_RECEIPT_FAILED"), 1800,
        now=NOW, heartbeat_path=noncanonical,
    ) == (60, "mirror coherence/lease retry")

    elapsed = _heartbeat(
        tmp_path / "elapsed",
        pollFailedAt="2026-08-31T11:22:13+10:00",
        nextRetryAt="2026-08-31T11:52:13+10:00",
    )
    assert analyzer._mirror_coherence_retry_delay_seconds(
        MirrorCoherenceError("MIRROR_SYNC_RECEIPT_FAILED"), 1800,
        now=NOW, heartbeat_path=elapsed,
    ) == (60, "mirror coherence/lease retry")


def test_non_sync_coherence_error_does_not_inherit_outage_backoff(tmp_path):
    path = _heartbeat(tmp_path)
    assert analyzer._mirror_coherence_retry_delay_seconds(
        MirrorCoherenceError("MIRROR_REVISION_IDENTITY_MISMATCH"), 1800,
        now=NOW, heartbeat_path=path,
    ) == (60, "mirror coherence/lease retry")
