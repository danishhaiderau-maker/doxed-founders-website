import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# The service directory contains a hyphen, so import its small pure module by path.
import importlib.util


MODULE = (
    Path(__file__).resolve().parents[1]
    / "services"
    / "btc-conservative-agent"
    / "research"
    / "mirror_coherence.py"
)
AGENT_ROOT = MODULE.parents[1]
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))
SPEC = importlib.util.spec_from_file_location("mirror_coherence", MODULE)
assert SPEC and SPEC.loader
mirror_coherence = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mirror_coherence
SPEC.loader.exec_module(mirror_coherence)


def _write_heartbeat(root: Path, **updates) -> Path:
    revision = "600ebbcd009b"
    payload = {
        "ok": True,
        "syncedAt": datetime.now(timezone.utc).isoformat(),
        "sourceRevision": revision,
        "observedSourceRevision": revision,
        "mirroredSourceRevision": revision,
        "revisionParity": "MATCH",
    }
    payload.update(updates)
    path = root / ".fly-data-sync-loop.heartbeat.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _write_mirror_identity(mirror: Path) -> Path:
    state_path = mirror / ".fly-sync-state.json"
    state_path.write_text(
        json.dumps(
            {
                "research.db": {
                    "inode": 17,
                    "size": 4096,
                    "mtime_ns": 123456789,
                    "synced_at": datetime.now(timezone.utc).isoformat(),
                }
            }
        ),
        encoding="utf-8",
    )
    (mirror / "research_session.json").write_text(
        json.dumps({"collector_v22_epoch_id": "epoch-current"}),
        encoding="utf-8",
    )
    return state_path


def _check(repo: Path, mirror: Path, **kwargs):
    return mirror_coherence.assert_mirror_coherent(
        repo_root=repo,
        data_root=mirror,
        expected_revision="600ebbcd009b3fa086b41ccd2d2e7f67f36f17f4",
        **kwargs,
    )


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"inProgress": True}, "MIRROR_SYNC_IN_PROGRESS"),
        ({"ok": False}, "MIRROR_SYNC_RECEIPT_FAILED"),
        ({"revisionParity": "MISMATCH"}, "MIRROR_REVISION_PARITY_NOT_MATCH"),
        ({"observedSourceRevision": "deadbeef"}, "MIRROR_REVISION_IDENTITY_MISMATCH"),
    ],
)
def test_preflight_fails_closed_for_incoherent_receipt(tmp_path, updates, reason):
    repo, mirror = tmp_path / "repo", tmp_path / "mirror"
    repo.mkdir(); mirror.mkdir()
    _write_mirror_identity(mirror)
    _write_heartbeat(mirror, **updates)
    with pytest.raises(mirror_coherence.MirrorCoherenceError, match=reason):
        _check(repo, mirror)


def test_preflight_rejects_stale_receipt(tmp_path):
    repo, mirror = tmp_path / "repo", tmp_path / "mirror"
    repo.mkdir(); mirror.mkdir()
    _write_mirror_identity(mirror)
    old = datetime.now(timezone.utc) - timedelta(minutes=11)
    _write_heartbeat(mirror, syncedAt=old.isoformat())
    with pytest.raises(mirror_coherence.MirrorCoherenceError, match="MIRROR_SYNC_RECEIPT_STALE"):
        _check(repo, mirror, max_age_seconds=600)


def test_prepublication_accepts_timestamp_refresh_but_rejects_generation_change(tmp_path):
    repo, mirror = tmp_path / "repo", tmp_path / "mirror"
    repo.mkdir(); mirror.mkdir()
    state_path = _write_mirror_identity(mirror)
    path = _write_heartbeat(mirror)
    token = _check(repo, mirror)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["syncedAt"] = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _check(repo, mirror, previous=token) == token

    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["research.db"]["size"] += 1
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(
        mirror_coherence.MirrorCoherenceError,
        match="MIRROR_SYNC_IDENTITY_CHANGED_DURING_ANALYSIS",
    ):
        _check(repo, mirror, previous=token)


def test_unchanged_coherent_receipt_passes_both_gates(tmp_path):
    repo, mirror = tmp_path / "repo", tmp_path / "mirror"
    repo.mkdir(); mirror.mkdir()
    _write_mirror_identity(mirror)
    _write_heartbeat(mirror)
    token = _check(repo, mirror)
    assert _check(repo, mirror, previous=token) == token


def test_analyzer_requires_repo_canonical_manifest_identity(tmp_path):
    from research.canonical_data_store import append_manifest, initialize_store

    repo = tmp_path / "repo"
    repo.mkdir()
    mirror = initialize_store(
        repo / "services" / "btc-conservative-agent" / "canonical-research-data",
        repo,
    )
    _write_mirror_identity(mirror)
    heartbeat = _write_heartbeat(mirror, tileRegistrySignature="tile-signature")
    payload = json.loads(heartbeat.read_text(encoding="utf-8"))
    append_manifest(
        mirror,
        {
            "dataset_epoch": "epoch-current",
            "source_revision": payload["mirroredSourceRevision"],
            "deployed_revision": payload["mirroredSourceRevision"],
            "tile_config_signature": "tile-signature",
            "collection_started_at": payload["syncedAt"],
            "collection_observed_at": payload["syncedAt"],
            "row_count": 0,
            "opportunity_count": 0,
            "dataset_checksum": "0" * 64,
            "analyzer_status": "PENDING_CANONICAL_ANALYZER_RUN",
            "analyzer_completed_at": None,
            "analyzer_schema_version": "v62",
        },
    )
    assert mirror_coherence.assert_mirror_coherent(
        repo_root=repo,
        data_root=mirror,
        expected_revision="600ebbcd009b3fa086b41ccd2d2e7f67f36f17f4",
        require_canonical_manifest=True,
    ).epoch == "epoch-current"

    current = mirror / "canonical_dataset_current.json"
    current_payload = json.loads(current.read_text(encoding="utf-8"))
    current_payload["tile_config_signature"] = "wrong"
    current.write_text(json.dumps(current_payload), encoding="utf-8")
    with pytest.raises(
        mirror_coherence.MirrorCoherenceError,
        match="CANONICAL_DATASET_MANIFEST_INVALID",
    ):
        mirror_coherence.assert_mirror_coherent(
            repo_root=repo,
            data_root=mirror,
            expected_revision="600ebbcd009b3fa086b41ccd2d2e7f67f36f17f4",
            require_canonical_manifest=True,
        )


def test_analyzer_retry_log_uses_actual_retry_interval_and_reason():
    engine = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "btc-conservative-agent"
        / "analyzer_research_engine_v62.py"
    ).read_text(encoding="utf-8")
    assert "retry_sec, retry_reason = _mirror_coherence_retry_delay_seconds(" in engine
    assert 'return delay, "canonical sync heartbeat backoff"' in engine
    assert "Next run in {retry_label} ({retry_reason})" in engine
    assert "Next run in {interval_min} minutes" not in engine
