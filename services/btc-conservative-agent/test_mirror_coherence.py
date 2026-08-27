import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research.mirror_coherence import MirrorCoherenceError, assert_mirror_coherent


REVISION = "1b7c8759823581570367fa73a7d439bdb3477608"
NOW = datetime(2026, 8, 27, 11, 30, tzinfo=timezone.utc)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    mirror = tmp_path / "mirror"
    heartbeat = repo / ".fly-data-sync-loop.heartbeat.json"
    _write_json(
        heartbeat,
        {
            "ok": True,
            "syncedAt": "2026-08-27T11:29:00Z",
            "sourceRevision": REVISION[:12],
            "observedSourceRevision": REVISION[:12],
            "mirroredSourceRevision": REVISION[:12],
            "revisionParity": "MATCH",
        },
    )
    _write_json(
        mirror / ".fly-sync-state.json",
        {
            "research.db": {
                "inode": 7,
                "size": 1024,
                "mtime_ns": 123456,
                "synced_at": "2026-08-27T11:29:00Z",
            }
        },
    )
    _write_json(
        mirror / "research_session.json",
        {"collector_v22_epoch_id": "epoch-current"},
    )
    return repo, mirror, heartbeat


def _assert(repo: Path, mirror: Path, previous=None):
    return assert_mirror_coherent(
        repo_root=repo,
        data_root=mirror,
        expected_revision=REVISION,
        previous=previous,
        now=NOW,
        max_age_seconds=600,
    )


def test_timestamp_only_heartbeat_refresh_does_not_change_generation(tmp_path: Path) -> None:
    repo, mirror, heartbeat = _fixture(tmp_path)
    before = _assert(repo, mirror)
    payload = json.loads(heartbeat.read_text(encoding="utf-8"))
    payload.update(
        {
            "syncedAt": "2026-08-27T11:30:00Z",
            "relayEvidence": {"ok": True, "lastSuccessAt": "2026-08-27T11:30:00Z"},
        }
    )
    _write_json(heartbeat, payload)
    assert _assert(repo, mirror, previous=before) == before


@pytest.mark.parametrize("change", ["state", "epoch"])
def test_actual_completed_mirror_generation_change_is_rejected(
    tmp_path: Path, change: str
) -> None:
    repo, mirror, _heartbeat = _fixture(tmp_path)
    before = _assert(repo, mirror)
    if change == "state":
        state_path = mirror / ".fly-sync-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["research.db"]["size"] += 1
        _write_json(state_path, state)
    else:
        _write_json(
            mirror / "research_session.json",
            {"collector_v22_epoch_id": "epoch-next"},
        )
    with pytest.raises(
        MirrorCoherenceError,
        match="MIRROR_SYNC_IDENTITY_CHANGED_DURING_ANALYSIS",
    ):
        _assert(repo, mirror, previous=before)


def test_revision_or_runtime_sync_state_change_remains_fail_closed(tmp_path: Path) -> None:
    repo, mirror, heartbeat = _fixture(tmp_path)
    before = _assert(repo, mirror)
    payload = json.loads(heartbeat.read_text(encoding="utf-8"))
    payload["inProgress"] = True
    _write_json(heartbeat, payload)
    with pytest.raises(MirrorCoherenceError, match="MIRROR_SYNC_IN_PROGRESS"):
        _assert(repo, mirror, previous=before)

    payload["inProgress"] = False
    payload["mirroredSourceRevision"] = "deadbeef0000"
    _write_json(heartbeat, payload)
    with pytest.raises(MirrorCoherenceError, match="MIRROR_REVISION_IDENTITY_MISMATCH"):
        _assert(repo, mirror, previous=before)
