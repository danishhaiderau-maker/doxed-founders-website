from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync-fly-bot-data-loop.ps1"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8-sig")


def test_manifest_preflight_has_bounded_retries_and_stage_diagnostics():
    source = _source()

    assert "$preflightManifestAttempts = 5" in source
    assert "$preflightManifestTimeoutSec = 90" in source
    assert "stage=loop_manifest_preflight failed after" in source
    assert '$currentStage = "loop_manifest_preflight"' in source
    assert '"$failureAt`tERROR`tstage=$currentStage`t$failureMessage"' in source


def test_transient_poll_failure_retains_only_a_qualified_completed_match():
    source = _source()

    assert "$candidateHeartbeat.ok -eq $true" in source
    assert "$candidateHeartbeat.inProgress -ne $true" in source
    assert '[string]$candidateHeartbeat.revisionParity -eq "MATCH"' in source
    assert (
        "[string]$candidateHeartbeat.mirroredSourceRevision "
        "-eq $lastSyncedSourceRevision"
    ) in source
    assert '$heartbeat["pollOk"] = $false' in source
    assert '$heartbeat["pollStage"] = $currentStage' in source
    assert '$heartbeat["pollError"] = $failureMessage' in source


def test_successful_poll_clears_transient_poll_failure_state():
    source = _source()

    # Both the below-threshold observation and a completed atomic sync publish
    # an explicitly healthy poll receipt.
    assert source.count("pollOk = $true") >= 2
