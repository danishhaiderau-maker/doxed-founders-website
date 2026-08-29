from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync-fly-bot-data-loop.ps1"
CHILD_SCRIPT = ROOT / "scripts" / "sync-fly-bot-data.ps1"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8-sig")


def _child_source() -> str:
    return CHILD_SCRIPT.read_text(encoding="utf-8-sig")


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


def test_full_sync_reuses_authenticated_loop_preflight_without_duplicate_fetch():
    loop_source = _source()
    child_source = _child_source()

    assert "InitialManifest = $manifest" in loop_source
    assert "[object]$InitialManifest = $null" in child_source
    assert "$manifest = $InitialManifest" in child_source
    assert "if ($null -eq $manifest)" in child_source
    assert "$selectedFiles = @($manifest.files)" in child_source
    assert "$manifest.source_git_rev" in child_source

    # Standalone callers still authenticate and fetch their own manifest, and
    # every caller validates its schema before using any rows.
    assert '-Stage "manifest_initial"' in child_source
    assert 'if ($manifest.schema -ne "fly_runtime_incremental_sync_v1")' in child_source

    # Reusing initial metadata must not bypass the authenticated final commit:
    # the checksum acknowledgement remains after all file reconciliation.
    assert '-Stage "acknowledgement"' in child_source
    assert "$ack = Invoke-DataSyncJsonRequest" in child_source
    assert "AckAccepted = $ack.accepted" in child_source
    assert "Canonical manifest commit failed" in child_source


def test_reused_manifest_is_fenced_against_a_fresh_authenticated_identity():
    child_source = _child_source()

    final_fence = child_source.index('-Stage "manifest_final_identity"')
    identity_assertion = child_source.index(
        "Assert-DataSyncManifestIdentity -Initial $manifest -Final $finalManifest"
    )
    acknowledgement = child_source.index('-Stage "acknowledgement"')
    canonical_completion = child_source.index("Canonical manifest commit failed")

    assert "?fresh=1&nonce=" in child_source
    assert final_fence < identity_assertion < acknowledgement < canonical_completion
    for identity_field in (
        "source_git_rev",
        "tile_registry_signature",
        "fresh_collection_signal_ts",
        "dataset_epoch",
        "collection_epoch_id",
        "epoch_id",
        "generation_epoch",
    ):
        assert identity_field in child_source

    # Every required-field absence, availability change, or value mismatch
    # throws before acknowledgement/canonical completion can be reached.
    assert "changed field availability" in child_source
    assert "final identity fence mismatch" in child_source
    assert "missing required field" in child_source


def test_each_identity_mismatch_is_a_fail_closed_throw_before_acknowledgement():
    child_source = _child_source()
    assertion_body = child_source[
        child_source.index("function Assert-DataSyncManifestIdentity"):
        child_source.index("function Write-SyncProgressHeartbeat")
    ]

    # Required identities cannot disappear, optional epoch availability cannot
    # change, and no present identity value may differ. All three branches are
    # terminating throws rather than warnings or boolean results.
    assert assertion_body.count("throw ") == 4
    assert "if ($field.Required -and" in assertion_body
    assert "if ($before.Present -ne $after.Present)" in assertion_body
    assert "if (-not $matches)" in assertion_body
    assert "source_git_rev" in assertion_body
    assert "tile_registry_signature" in assertion_body
    assert "fresh_collection_signal_ts" in assertion_body
    assert "collection_epoch_id" in assertion_body
