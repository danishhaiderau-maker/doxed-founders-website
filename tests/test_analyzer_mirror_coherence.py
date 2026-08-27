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
    _write_heartbeat(repo, **updates)
    with pytest.raises(mirror_coherence.MirrorCoherenceError, match=reason):
        _check(repo, mirror)


def test_preflight_rejects_stale_receipt(tmp_path):
    repo, mirror = tmp_path / "repo", tmp_path / "mirror"
    repo.mkdir(); mirror.mkdir()
    old = datetime.now(timezone.utc) - timedelta(minutes=11)
    _write_heartbeat(repo, syncedAt=old.isoformat())
    with pytest.raises(mirror_coherence.MirrorCoherenceError, match="MIRROR_SYNC_RECEIPT_STALE"):
        _check(repo, mirror, max_age_seconds=600)


def test_prepublication_rejects_sync_identity_change(tmp_path):
    repo, mirror = tmp_path / "repo", tmp_path / "mirror"
    repo.mkdir(); mirror.mkdir()
    path = _write_heartbeat(repo)
    token = _check(repo, mirror)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["syncedAt"] = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(
        mirror_coherence.MirrorCoherenceError,
        match="MIRROR_SYNC_IDENTITY_CHANGED_DURING_ANALYSIS",
    ):
        _check(repo, mirror, previous=token)


def test_unchanged_coherent_receipt_passes_both_gates(tmp_path):
    repo, mirror = tmp_path / "repo", tmp_path / "mirror"
    repo.mkdir(); mirror.mkdir()
    _write_heartbeat(repo)
    token = _check(repo, mirror)
    assert _check(repo, mirror, previous=token) == token
