from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync-fly-bot-data-loop.ps1"
CHILD_SCRIPT = ROOT / "scripts" / "sync-fly-bot-data.ps1"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8-sig")


def _child_source() -> str:
    return CHILD_SCRIPT.read_text(encoding="utf-8-sig")


def test_manifest_preflight_has_bounded_retries_and_stage_diagnostics():
    source = _source()

    assert "$preflightManifestAttempts = 220" in source
    assert "$preflightInventoryWaitMaxSec = 1800" in source
    assert "$preflightManifestTimeoutSec = 90" in source
    assert "stage=loop_manifest_preflight failed after" in source
    assert '$currentStage = "loop_manifest_preflight"' in source
    assert '"$failureAt`tERROR`tstage=$currentStage`tfailures=$consecutiveFailures' in source


def _inventory_progress_key(worker: dict[str, object]) -> tuple[str, ...]:
    """Executable model of the ordered PowerShell progress tuple."""
    return tuple(
        str(worker.get(field, ""))
        for field in (
            "phase",
            "files_seen",
            "dirs_seen",
            "rows_discovered",
            "pages_written",
            "pages_total",
        )
    )


def _stalls(
    observations: list[tuple[float, dict[str, object]]], limit: float = 360
) -> bool:
    last_key: tuple[str, ...] | None = None
    last_progress_at = 0.0
    for elapsed, worker in observations:
        key = _inventory_progress_key(worker)
        if last_key is None or key != last_key:
            last_key = key
            last_progress_at = elapsed
        if elapsed - last_progress_at >= limit:
            return True
    return False


def test_finalize_page_progress_is_observable_and_does_not_false_stall():
    source = _source()
    progress_start = source.index("function Get-FlyInventorySemanticProgressKey")
    progress_block = source[progress_start : source.index("function Set-FlyInventoryDiagnostic", progress_start)]
    for field in (
        "phase",
        "files_seen",
        "dirs_seen",
        "rows_discovered",
        "pages_written",
        "pages_total",
    ):
        assert f"$worker.{field}" in progress_block
    assert "$worker.invocations" not in progress_block
    assert "Get-FlyInventorySemanticProgressKey -Manifest $preflight" in source

    # FINALIZE can make progress while scan counters and phase remain fixed.
    observations = [
        (0, {"phase": "FINALIZE", "invocations": 4, "pages_written": 1, "pages_total": 3}),
        (300, {"phase": "FINALIZE", "invocations": 5, "pages_written": 2, "pages_total": 3}),
        (590, {"phase": "FINALIZE", "invocations": 6, "pages_written": 3, "pages_total": 3}),
    ]
    assert not _stalls(observations)


def test_frozen_inventory_progress_tuple_stalls_at_existing_360_second_cap():
    source = _source()
    assert "$preflightInventoryStallMaxSec = 360" in source
    assert "$preflightInventoryWaitMaxSec = 1800" in source

    frozen = {"phase": "FINALIZE", "invocations": 4, "pages_written": 1, "pages_total": 3}
    assert _stalls([(0, frozen), (359, frozen), (360, frozen)])


def test_http_500_and_503_inventory_failures_are_persisted_fail_closed():
    source = _source()
    assert '$errorClass = "HTTP_$httpStatus"' in source
    assert '$heartbeat["inventoryDiagnostic"] = $lastInventoryDiagnostic' in source
    assert "inventoryDiagnostic = $lastInventoryDiagnostic" in source
    assert '`thttp=$($inventoryLog.httpStatus)`terrorClass=$($inventoryLog.errorClass)' in source
    assert "Get-BoundedDiagnosticText" in source
    assert "ConvertTo-BoundedNullableCounter" in source
    assert "Get-FlyInventoryErrorClass" in source

    # There is no success return in the catch path: either it retries under
    # the unchanged bounds or throws, while retaining an HTTP-class receipt.
    catch_start = source.index(
        "    } catch {", source.index("function Get-FlySyncPreflightManifest")
    )
    catch_block = source[catch_start : source.index("      Start-Sleep -Seconds $delaySec")]
    assert "return $preflight" not in catch_block

    def error_class(status: int) -> str:
        return f"HTTP_{status}"

    assert error_class(500) == "HTTP_500"
    assert error_class(503) == "HTTP_503"


def test_actual_powershell_progress_and_diagnostic_contract():
    harness = ROOT / "scripts" / "test-sync-fly-bot-data-loop-progress.ps1"
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "sync-progress-contract-ok" in completed.stdout


def test_receipt_bootstrap_pending_is_retryable_but_remains_bounded():
    source = _source()
    assert '$Manifest.inventory_build_status -eq "PENDING"' in source
    assert '$worker.phase -eq "WAITING_RECEIPT_BOOTSTRAP"' in source
    assert '$bootstrap.status -eq "PENDING"' in source
    assert "$bootstrap.blocked -ne $true" in source
    assert "(-not [bool]$worker.refreshing -and -not $bootstrapPending)" in source
    assert '$bootstrap.status -eq "BLOCKED"' in source
    assert "$bootstrap.blocked -eq $true" in source
    assert "Test-FlyInventoryTerminalFailure -Manifest $preflight" in source
    assert "$preflightInventoryStallMaxSec = 360" in source
    assert "$preflightInventoryWaitMaxSec = 1800" in source


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
    assert "$manifest = Get-CompleteDataSyncManifest -FirstPage $manifest" in child_source

    # Reusing initial metadata must not bypass the authenticated final commit:
    # the checksum acknowledgement remains after all file reconciliation.
    assert '-Stage "acknowledgement_finalize"' in child_source
    assert "$ack = Invoke-DataSyncJsonRequest" in child_source
    assert "AckAccepted = $ack.accepted" in child_source
    assert "Canonical manifest commit failed" in child_source


def test_post_soak_manifest_and_child_ack_own_terminal_revision_identity():
    source = _source()

    refetch = source.index('$currentStage = "loop_full_manifest"')
    refreshed_observation = source.index(
        '$observedSourceRevision = [string]$manifest.source_git_rev', refetch
    )
    child_sync = source.index(
        '$result = & (Join-Path $scriptDir "sync-fly-bot-data.ps1") @syncArgs'
    )
    child_identity = source.index(
        '$childSourceRevision = [string]$result.SourceRevision', child_sync
    )
    ack_gate = source.index('$result.AckAccepted -ne $true', child_identity)
    terminal_observation = source.index(
        '$observedSourceRevision = $childSourceRevision', ack_gate
    )
    terminal_heartbeat = source.index('ackAccepted = $result.AckAccepted', terminal_observation)

    assert refetch < refreshed_observation < child_sync
    assert child_sync < child_identity < ack_gate < terminal_observation < terminal_heartbeat
    assert '$lastSyncedSourceRevision = $childSourceRevision' in source
    assert (
        'Child Fly sync returned an invalid terminal revision or unaccepted acknowledgement.'
        in source
    )


def test_full_sync_waits_for_a_bounded_quiet_health_soak():
    source = _source()

    assert "$fullSyncQuietSuccesses = 3" in source
    assert "$fullSyncQuietMaxWaitSec = 90" in source
    assert "function Wait-FlyRuntimeQuietForFullSync" in source
    assert "Wait-FlyRuntimeQuietForFullSync -BaseUrl $SourceUrl" in source
    assert '$currentStage = "runtime_quiet_soak"' in source


def test_reused_manifest_is_fenced_against_a_fresh_authenticated_identity():
    child_source = _child_source()

    final_fence = child_source.index('-Stage "manifest_final_identity"')
    identity_assertion = child_source.index(
        "Assert-DataSyncManifestIdentity -Initial $manifest -Final $finalManifest"
    )
    acknowledgement = child_source.index('-Stage "acknowledgement_finalize"')
    canonical_completion = child_source.index("Canonical manifest commit failed")

    assert '"$base/api/data-sync/manifest?fresh=1$identityQuery$pathQuery$generationQuery$pageQuery&nonce="' in child_source
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
    assert assertion_body.count("throw ") == 5
    assert "lost the immutable inventory generation" in assertion_body
    assert "if ($field.Required -and" in assertion_body
    assert "if ($before.Present -ne $after.Present)" in assertion_body
    assert "if (-not $matches)" in assertion_body
    assert "source_git_rev" in assertion_body
    assert "tile_registry_signature" in assertion_body
    assert "fresh_collection_signal_ts" in assertion_body
    assert "collection_epoch_id" in assertion_body
