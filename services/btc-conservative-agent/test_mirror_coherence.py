import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from research.mirror_coherence import MirrorCoherenceError, assert_mirror_coherent
from research.canonical_data_store import append_manifest, initialize_store
from research.mirror_generation_lease import (
    MirrorGenerationLease,
    MirrorGenerationLeaseTimeout,
)


REVISION = "1b7c8759823581570367fa73a7d439bdb3477608"
NOW = datetime(2026, 8, 27, 11, 30, tzinfo=timezone.utc)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    mirror = tmp_path / "mirror"
    heartbeat = mirror / ".fly-data-sync-loop.heartbeat.json"
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


def _canonical_fixture(tmp_path: Path, *, deployed_revision: str | None) -> tuple[Path, Path]:
    repo, old_mirror, _ = _fixture(tmp_path)
    mirror = repo / "canonical-research-data"
    initialize_store(mirror, repo)
    for name in (".fly-sync-state.json", "research_session.json"):
        (mirror / name).write_bytes((old_mirror / name).read_bytes())
    heartbeat = json.loads(
        (old_mirror / ".fly-data-sync-loop.heartbeat.json").read_text(encoding="utf-8")
    )
    heartbeat["tileRegistrySignature"] = "tile-signature"
    if deployed_revision is not None:
        heartbeat["deployedRevision"] = deployed_revision
    _write_json(mirror / ".fly-data-sync-loop.heartbeat.json", heartbeat)
    append_manifest(
        mirror,
        {
            "dataset_epoch": "epoch-current",
            "source_revision": REVISION[:12],
            "deployed_revision": REVISION[:12],
            "tile_config_signature": "tile-signature",
            "collection_started_at": "2026-08-27T11:00:00Z",
            "collection_observed_at": "2026-08-27T11:29:00Z",
            "file_count": 1,
            "byte_count": 1024,
            "row_count": 1,
            "opportunity_count": 1,
            "dataset_checksum": "a" * 64,
            "analyzer_status": "PENDING_CANONICAL_ANALYZER_RUN",
            "analyzer_completed_at": None,
            "analyzer_schema_version": "v62",
        },
    )
    return repo, mirror


@pytest.mark.parametrize("deployed_revision", [REVISION[:12], None])
def test_current_terminal_heartbeat_admits_canonical_manifest(
    tmp_path: Path, deployed_revision: str | None
) -> None:
    repo, mirror = _canonical_fixture(tmp_path, deployed_revision=deployed_revision)

    token = assert_mirror_coherent(
        repo_root=repo,
        data_root=mirror,
        expected_revision=REVISION,
        now=NOW,
        max_age_seconds=600,
        require_canonical_manifest=True,
    )

    assert token.revision == REVISION[:12]


def test_mismatched_terminal_deployed_revision_is_rejected(tmp_path: Path) -> None:
    repo, mirror = _canonical_fixture(tmp_path, deployed_revision="deadbeef0000")

    with pytest.raises(
        MirrorCoherenceError, match="CANONICAL_DATASET_MANIFEST_INVALID"
    ):
        assert_mirror_coherent(
            repo_root=repo,
            data_root=mirror,
            expected_revision=REVISION,
            now=NOW,
            max_age_seconds=600,
            require_canonical_manifest=True,
        )


def test_publication_time_rejects_changed_manifest_entry_or_checksum(tmp_path: Path) -> None:
    repo, mirror = _canonical_fixture(tmp_path, deployed_revision=REVISION[:12])
    current = json.loads(
        (mirror / "canonical_dataset_current.json").read_text(encoding="utf-8")
    )
    before = assert_mirror_coherent(
        repo_root=repo,
        data_root=mirror,
        expected_revision=REVISION,
        expected_deployed_revision=REVISION,
        expected_manifest_entry_hash=current["entry_hash"],
        expected_dataset_checksum=current["dataset_checksum"],
        now=NOW,
        max_age_seconds=600,
        require_canonical_manifest=True,
    )
    for field, value, error in (
        ("expected_manifest_entry_hash", "b" * 64, "MIRROR_MANIFEST_ENTRY_IDENTITY_MISMATCH"),
        ("expected_dataset_checksum", "c" * 64, "MIRROR_DATASET_CHECKSUM_IDENTITY_MISMATCH"),
    ):
        kwargs = {
            "repo_root": repo,
            "data_root": mirror,
            "expected_revision": REVISION,
            "expected_deployed_revision": REVISION,
            "expected_manifest_entry_hash": current["entry_hash"],
            "expected_dataset_checksum": current["dataset_checksum"],
            "previous": before,
            "now": NOW,
            "max_age_seconds": 600,
            "require_canonical_manifest": True,
        }
        kwargs[field] = value
        with pytest.raises(MirrorCoherenceError, match=error):
            assert_mirror_coherent(**kwargs)


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


def test_stale_receipt_is_waived_only_for_same_token_with_held_lease(tmp_path: Path) -> None:
    repo, mirror, heartbeat = _fixture(tmp_path)
    before = _assert(repo, mirror)
    payload = json.loads(heartbeat.read_text(encoding="utf-8"))
    payload["syncedAt"] = "2026-08-27T10:00:00Z"
    _write_json(heartbeat, payload)

    with pytest.raises(MirrorCoherenceError, match="MIRROR_SYNC_RECEIPT_STALE"):
        _assert(repo, mirror, previous=before)

    lease = MirrorGenerationLease(mirror, owner="test-analyzer").acquire(timeout_seconds=0)
    try:
        assert assert_mirror_coherent(
            repo_root=repo, data_root=mirror, expected_revision=REVISION,
            previous=before, now=NOW, max_age_seconds=600, held_lease=lease,
        ) == before

        state_path = mirror / ".fly-sync-state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["research.db"]["size"] += 1
        _write_json(state_path, state)
        with pytest.raises(
            MirrorCoherenceError,
            match="MIRROR_SYNC_IDENTITY_CHANGED_DURING_ANALYSIS",
        ):
            assert_mirror_coherent(
                repo_root=repo, data_root=mirror, expected_revision=REVISION,
                previous=before, now=NOW, max_age_seconds=600, held_lease=lease,
            )
    finally:
        lease.release()


def test_generation_lease_is_exclusive_and_dead_owner_file_is_recoverable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    first = MirrorGenerationLease(repo, owner="first").acquire(timeout_seconds=0)
    try:
        with pytest.raises(MirrorGenerationLeaseTimeout):
            MirrorGenerationLease(repo, owner="second").acquire(timeout_seconds=0)
    finally:
        first.release()

    # The diagnostic file intentionally remains.  OS handle ownership, not
    # pathname deletion, provides stale-owner recovery.
    assert (repo / ".fly-mirror-generation.lease").is_file()
    recovered = MirrorGenerationLease(repo, owner="recovered").acquire(timeout_seconds=0)
    assert recovered.held
    recovered.release()


@pytest.mark.skipif(os.name != "nt", reason="PowerShell FileShare interop is Windows-specific")
def test_python_lease_blocks_powershell_sync_style_exclusive_open(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    ready = tmp_path / "ready"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib,time; "
                "from research.mirror_generation_lease import MirrorGenerationLease; "
                f"lease=MirrorGenerationLease({str(repo)!r},owner='subprocess').acquire(timeout_seconds=0); "
                f"pathlib.Path({str(ready)!r}).write_text('ready'); "
                "time.sleep(10)"
            ),
        ],
        cwd=Path(__file__).parent,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists()
        lease_path = repo / ".fly-mirror-generation.lease"
        command = (
            f"try {{$h=[IO.File]::Open('{lease_path}',"
            "[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,"
            "[IO.FileShare]::None);$h.Dispose();exit 0}catch{exit 3}"
        )
        attempted = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command], check=False
        )
        assert attempted.returncode == 3
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def test_sync_defers_before_child_can_publish_in_progress() -> None:
    repo = Path(__file__).resolve().parents[2]
    source = (repo / "scripts" / "sync-fly-bot-data-loop.ps1").read_text(encoding="utf-8")
    acquire_at = source.index("$generationLease = [System.IO.File]::Open")
    invoke_at = source.index('$result = & (Join-Path $scriptDir "sync-fly-bot-data.ps1")')
    assert acquire_at < invoke_at
    assert "analyzer owns mirror-generation lease" in source


def test_analyzer_uses_bounded_lease_wait_and_short_coherence_retry() -> None:
    source = Path(__file__).with_name("analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    assert 'ANALYZER_MIRROR_LEASE_WAIT_SEC", "1200"' in source
    assert "with lease.acquire(timeout_seconds=wait_sec)" in source
    assert "_mirror_coherence_retry_delay_seconds(" in source
    assert 'fallback = min(60, max(1, int(scheduled_delay_seconds)))' in source
    assert '"canonical sync heartbeat backoff"' in source
    assert 'held_lease=globals().get("_CURRENT_MIRROR_GENERATION_LEASE")' in source
