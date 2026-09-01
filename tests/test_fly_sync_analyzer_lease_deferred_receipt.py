import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync-fly-bot-data-loop.ps1"


def _function(source: str, name: str, next_name: str) -> str:
    start = source.index(f"function {name}")
    end = source.index(f"function {next_name}", start)
    return source[start:end]


def test_deferred_branch_is_fenced_and_never_promotes_a_sync_candidate() -> None:
    source = SCRIPT.read_text(encoding="utf-8-sig")
    start = source.index("$deferredReceiptPublished = Publish-AnalyzerLeaseDeferredReceipt")
    branch = source[start:source.index("# Keep the generation lease", start)]

    assert "ObservedSourceRevision $observedSourceRevision" in branch
    assert "LastMirroredSourceRevision $lastSyncedSourceRevision" in branch
    assert "sync-fly-bot-data.ps1" not in branch
    assert "Start-Sleep -Seconds $pollSec" in branch

    helper = _function(source, "Publish-AnalyzerLeaseDeferredReceipt", "Remove-OrphanedMirrorCandidates")
    assert "FileShare.None" in helper
    assert "$LeaseErrorCode -notin @(32, 33)" in helper
    assert helper.count("Get-FileHash -LiteralPath $canonicalPointer") == 2
    assert 'reason"] = "analyzer_lease_immutable_mirror"' in helper
    assert 'syncDeferred"] = $true' in helper
    assert 'canonicalManifestEntryHash"] = $entryHash.ToLowerInvariant()' in helper
    assert 'canonicalDatasetChecksum"] = $datasetChecksum.ToLowerInvariant()' in helper


@pytest.mark.skipif(os.name != "nt", reason="production sync loop is Windows PowerShell")
def test_powershell_deferred_receipt_refreshes_only_for_unchanged_match(tmp_path: Path) -> None:
    source = SCRIPT.read_text(encoding="utf-8-sig")
    hash_fallback = source[source.index("if (-not (Get-Command Get-FileHash"):source.index("$scriptDir =")]
    functions = hash_fallback + _function(source, "Write-Utf8NoBomJsonAtomic", "Remove-OrphanedMirrorCandidates")
    mirror = tmp_path / "canonical-research-data"
    mirror.mkdir()
    heartbeat = mirror / ".fly-data-sync-loop.heartbeat.json"
    pointer = mirror / "canonical_dataset_current.json"
    revision = "abc123def456"
    heartbeat.write_text(json.dumps({
        "ok": True,
        "syncedAt": "2026-01-01T00:00:00Z",
        "sourceRevision": revision,
        "observedSourceRevision": revision,
        "mirroredSourceRevision": revision,
        "revisionParity": "MATCH",
    }), encoding="utf-8")
    pointer.write_text(json.dumps({
        "entry_hash": "a" * 64,
        "dataset_checksum": "b" * 64,
    }), encoding="utf-8")
    harness = tmp_path / "contract.ps1"
    harness.write_text(
        functions
        + "\n$result = Publish-AnalyzerLeaseDeferredReceipt "
        + f"-ObservedSourceRevision '{revision}' -LastMirroredSourceRevision '{revision}' "
        + f"-HeartbeatPath '{heartbeat}' -MirrorPath '{mirror}' -LeaseErrorCode 32\n"
        + "if (-not $result) { exit 7 }\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        check=True,
    )
    receipt = json.loads(heartbeat.read_text(encoding="utf-8-sig"))
    assert receipt["syncDeferred"] is True
    assert receipt["reason"] == "analyzer_lease_immutable_mirror"
    assert receipt["canonicalManifestEntryHash"] == "a" * 64
    assert receipt["canonicalDatasetChecksum"] == "b" * 64
    refreshed_at = receipt["syncedAt"]

    mismatch_harness = tmp_path / "mismatch.ps1"
    mismatch_harness.write_text(
        functions
        + "\n$result = Publish-AnalyzerLeaseDeferredReceipt "
        + f"-ObservedSourceRevision 'different' -LastMirroredSourceRevision '{revision}' "
        + f"-HeartbeatPath '{heartbeat}' -MirrorPath '{mirror}' -LeaseErrorCode 32\n"
        + "if ($result) { exit 8 }\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(mismatch_harness)],
        check=True,
    )
    assert json.loads(heartbeat.read_text(encoding="utf-8-sig"))["syncedAt"] == refreshed_at
