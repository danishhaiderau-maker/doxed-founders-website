import ast
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
import pytest
from flask import Flask, jsonify, request
from types import SimpleNamespace
from datetime import datetime, timezone
from pathlib import Path
from research.platform_relay_evidence import _validate_platform_relay_evidence_payload as pure_validate_relay


ROOT = Path(__file__).resolve().parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
ENTRYPOINT = (ROOT / "fly-entrypoint.sh").read_text(encoding="utf-8")
SYNC_SCRIPT = (ROOT.parents[1] / "scripts" / "sync-fly-bot-data.ps1").read_text(
    encoding="utf-8"
)
SYNC_LOOP = (ROOT.parents[1] / "scripts" / "sync-fly-bot-data-loop.ps1").read_text(
    encoding="utf-8"
)
SYNC_BACKOFF = ROOT.parents[1] / "scripts" / "fly-sync-backoff.ps1"
DESKTOP_MIRROR_LAUNCHER = (
    ROOT.parents[1] / "scripts" / "start-fly-desktop-mirror.ps1"
).read_text(encoding="utf-8")
RESEARCH_SUPERVISOR_TASK = (
    ROOT.parents[1] / "scripts" / "register-research-stability-supervisor.ps1"
).read_text(encoding="utf-8")
RELAY_SYNC = (ROOT.parents[1] / "scripts" / "sync-platform-relay-evidence.ps1").read_text(
    encoding="utf-8"
)
ATOMIC_HELPER = ROOT.parents[1] / "scripts" / "fly-mirror-atomic.ps1"


def test_analyzer_manifest_timestamp_is_canonicalized_before_publication():
    assert "$analyzerGeneratedAt = [string]$reportManifest.generated_at" not in SYNC_SCRIPT
    assert "$reportManifestRaw = Get-Content -LiteralPath $reportManifestPath -Raw" in SYNC_SCRIPT
    assert "$generatedAtMatch = [regex]::Match(" in SYNC_SCRIPT
    assert "$analyzerGeneratedAt = $generatedAtMatch.Groups['value'].Value" in SYNC_SCRIPT
    assert "[DateTimeOffset]::TryParse(" in SYNC_SCRIPT
    assert "[Globalization.CultureInfo]::InvariantCulture" in SYNC_SCRIPT
    assert "$analyzerCommittedAtValue = [DateTimeOffset]$reportManifestItem.LastWriteTimeUtc" in SYNC_SCRIPT
    assert "$analyzerGeneratedAtValue.AddMinutes(30)" in SYNC_SCRIPT
    assert "$artifactModifiedAt -gt $analyzerCommittedAtValue.AddMinutes(1)" in SYNC_SCRIPT
    assert "$artifactModifiedAt -gt $analyzerGeneratedAtValue.AddMinutes(5)" not in SYNC_SCRIPT


def test_relay_evidence_timestamp_survives_powershell_json_date_conversion():
    assert "[DateTimeOffset]::TryParse([string]$payload.generatedAt" not in RELAY_SYNC
    assert "$generatedAtRaw = $payload.generatedAt" in RELAY_SYNC
    assert "$generatedAtRaw -is [DateTime]" in RELAY_SYNC
    assert "[Globalization.CultureInfo]::InvariantCulture" in RELAY_SYNC


def test_relay_evidence_sync_deduplicates_timestamp_only_envelope_refreshes():
    assert "function Get-RelayEvidenceSemanticDigest" in RELAY_SYNC
    assert "if ([string]$name -ceq 'generatedAt') { continue }" in RELAY_SYNC
    assert "$incomingSemanticDigest = Get-RelayEvidenceSemanticDigest $payload" in RELAY_SYNC
    assert "Get-RelayEvidenceSemanticDigest $existingPayload" in RELAY_SYNC
    dedupe = RELAY_SYNC.index("$incomingSemanticDigest =")
    forward = RELAY_SYNC.index("$forward = Invoke-RestMethod")
    assert dedupe < forward
    assert "Write-Output $destination\n    return" in RELAY_SYNC


def test_fresh_epoch_signal_receipt_has_a_literal_signal_key():
    assert "@{ signal_ts = $currentSignal" in SYNC_LOOP
    assert "@{$signal_ts" not in SYNC_LOOP


def test_sync_loop_has_sha256_fallback_for_minimal_windows_hosts():
    assert "Get-Command Get-FileHash -ErrorAction SilentlyContinue" in SYNC_LOOP
    assert "[System.Security.Cryptography.SHA256]::Create()" in SYNC_LOOP
    assert "[System.IO.File]::OpenRead($resolved)" in SYNC_LOOP


def test_sync_loop_streams_orphan_candidate_discovery_without_materializing_mirror():
    body = SYNC_LOOP.split("function Remove-OrphanedMirrorCandidates", 1)[1]
    body = body.split("# Growth trigger", 1)[0]
    assert "[System.IO.Directory]::EnumerateFiles(" in body
    assert "[System.IO.SearchOption]::AllDirectories" in body
    assert ").GetEnumerator()" in body
    assert "while ($candidateEnumerator.MoveNext())" in body
    assert "$candidatePaths" not in body
    assert "Get-ChildItem -LiteralPath $MirrorPath -Recurse" not in body
    assert "@(" not in body
    assert "orphan candidate enumeration failed closed" in body
    assert "Remove-Item -LiteralPath $candidatePath" in body


def test_long_sync_writes_secret_safe_per_file_and_chunk_progress_heartbeat():
    progress_function = SYNC_SCRIPT.split("function Write-SyncProgressHeartbeat", 1)[1]
    progress_function = progress_function.split("$syncState = @{}", 1)[0]
    assert "[string]$ProgressHeartbeatFile" in SYNC_SCRIPT
    assert "function Write-SyncProgressHeartbeat" in SYNC_SCRIPT
    assert '-Phase "file_start"' in SYNC_SCRIPT
    assert '-Phase "chunk_complete"' in SYNC_SCRIPT
    assert "sourceRevision" in SYNC_SCRIPT
    assert "fileIndex" in SYNC_SCRIPT and "fileCount" in SYNC_SCRIPT
    assert "fileBytes" in SYNC_SCRIPT and "remoteBytes" in SYNC_SCRIPT
    assert 'syncedAt = [DateTimeOffset]::UtcNow.ToString("o")' in progress_function
    assert "tileRegistrySignature" in progress_function
    assert "Invoke-MirrorAtomicReplace" in SYNC_SCRIPT
    assert "ProgressHeartbeatFile = $heartbeatFile" in SYNC_LOOP
    assert "ProgressRelayEvidenceJson" in SYNC_LOOP
    assert '$loopPidFile = Join-Path $repoRoot ".fly-data-sync-loop.lock"' not in SYNC_SCRIPT
    assert '$ProgressHeartbeatFile = Join-Path $targetRoot ".fly-data-sync-loop.heartbeat.json"' in SYNC_SCRIPT
    assert "$previousHeartbeat.relayEvidence" in SYNC_SCRIPT
    assert "AdminToken" not in progress_function


def test_successful_sync_commits_a_completed_revision_normalized_heartbeat():
    progress_function = SYNC_SCRIPT.split("function Write-SyncProgressHeartbeat", 1)[1]
    progress_function = progress_function.split("$syncState = @{}", 1)[0]
    assert "[switch]$Completed" in progress_function
    assert "inProgress = -not [bool]$Completed" in progress_function
    assert ".StartsWith($observedRevision" in progress_function
    assert '-Phase "complete"' in SYNC_SCRIPT
    assert "-Completed" in SYNC_SCRIPT


def test_parent_terminal_heartbeat_carries_authenticated_deployed_revision():
    assert SYNC_LOOP.count("deployedRevision = $observedSourceRevision") == 2
    assert SYNC_LOOP.index("deployedRevision = $observedSourceRevision") < SYNC_LOOP.index(
        "$heartbeat | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $heartbeatFile"
    )


def test_sync_keeps_generation_lease_until_terminal_heartbeat_is_published():
    sync_call = SYNC_LOOP.index('$result = & (Join-Path $scriptDir "sync-fly-bot-data.ps1")')
    publish = SYNC_LOOP.index(
        '$heartbeat | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $heartbeatFile',
        sync_call,
    )
    release = SYNC_LOOP.index("$generationLease.Dispose()", publish)

    assert sync_call < publish < release
    assert SYNC_LOOP.rfind("if ($generationLease) { $generationLease.Dispose() }") > release


def test_sync_transport_retries_are_bounded_and_report_the_failed_stage():
    assert "$transportAttempts = 5" in SYNC_SCRIPT
    assert "$manifestTimeoutSec = 90" in SYNC_SCRIPT
    assert "$chunkTimeoutSec = 240" in SYNC_SCRIPT
    assert "$ackTimeoutSec = 60" in SYNC_SCRIPT
    assert "[TimeSpan]::FromSeconds($chunkTimeoutSec)" in SYNC_SCRIPT
    assert "function Invoke-DataSyncJsonRequest" in SYNC_SCRIPT
    assert '-Stage "manifest_initial"' in SYNC_SCRIPT
    assert '-Stage "manifest_targeted_refresh"' in SYNC_SCRIPT
    assert '-Stage "acknowledgement_page_$([int]$pageReceipt.page_index)"' in SYNC_SCRIPT
    assert '-Stage "acknowledgement_finalize"' in SYNC_SCRIPT
    assert '-Stage "manifest_post_ack_identity"' in SYNC_SCRIPT
    assert "stage=file_chunk failed for path=$rel" in SYNC_SCRIPT
    assert "file=$selectedFileIndex/$selectedFileCount offset=$offset" in SYNC_SCRIPT
    assert "$failureProgress attempt(s)" in SYNC_SCRIPT
    assert "[int]$MaxAttempts = $transportAttempts" in SYNC_SCRIPT
    assert "elapsed_ms=" in SYNC_SCRIPT
    assert "$sqliteSnapshotBuildingMaxAttempts = 35" in SYNC_SCRIPT
    assert "-MaxAttempts $sqliteSnapshotBuildingMaxAttempts" in SYNC_SCRIPT
    assert '"snapshot_status"\\s*:\\s*"BUILDING"' in SYNC_SCRIPT
    assert "$consecutiveNonBuildingPressureFailures = 0" in SYNC_SCRIPT
    assert "$snapshotPressureCircuitOpen" in SYNC_SCRIPT
    assert "$consecutiveNonBuildingPressureFailures -ge $resourcePressureCircuitThreshold" in SYNC_SCRIPT
    # Retry hardening must not weaken the candidate/checksum/atomic contract.
    assert "Get-FileHash -LiteralPath $tmp -Algorithm SHA256" in SYNC_SCRIPT
    assert "Publish-MirrorCandidate -Candidate $candidate -Destination $local" in SYNC_SCRIPT


def _run_sqlite_lease_retry_sequence(sequence):
    function_body = SYNC_SCRIPT[
        SYNC_SCRIPT.index("function Invoke-DataSyncJsonRequest"):
        SYNC_SCRIPT.index("function New-DataSyncManifestUri")
    ]
    encoded = json.dumps(sequence)
    harness = f'''$ErrorActionPreference = "Stop"
$manifestTimeoutSec = 1
$transportAttempts = 5
$resourcePressureCircuitThreshold = 2
$headers = @{{}}
$script:responses = ConvertFrom-Json @'
{encoded}
'@
$script:index = 0
function Start-Sleep {{ param([int]$Seconds) }}
function Test-DataSyncResourcePressureError {{ param([string]$Message) return $true }}
function Get-DataSyncRetryDelaySec {{ param([int]$Attempt, [bool]$ResourcePressure) return 0 }}
function Invoke-RestMethod {{
  $response = [string]$script:responses[$script:index]
  $script:index += 1
  if ($response -eq "CURRENT") {{ return @{{ snapshot_status = "CURRENT" }} }}
  $message = if ($response -eq "BUILDING") {{
    '{{"snapshot_status":"BUILDING","retry_after_seconds":2}}'
  }} elseif ($response -eq "EXPIRED") {{
    '{{"snapshot_status":"EXPIRED","retry_after_seconds":2}}'
  }} else {{
    '{{"ok":false,"error":"dashboard_busy","retry_after_sec":1}}'
  }}
  $record = [System.Management.Automation.ErrorRecord]::new(
    [System.Exception]::new("The remote server returned an error: (503) Server Unavailable."),
    "HTTP503", [System.Management.Automation.ErrorCategory]::ResourceUnavailable, $null
  )
  $record.ErrorDetails = [System.Management.Automation.ErrorDetails]::new($message)
  throw $record
}}
{function_body}
try {{
  $result = Invoke-DataSyncJsonRequest -Stage "sqlite_snapshot_lease" -Uri "https://invalid.test" -MaxAttempts 35
  @{{ ok = $true; calls = $script:index; status = $result.snapshot_status }} | ConvertTo-Json -Compress
}} catch {{
  @{{ ok = $false; calls = $script:index; error = $_.Exception.Message }} | ConvertTo-Json -Compress
}}
'''
    completed = subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", harness],
        text=True, capture_output=True, check=True,
    )
    assert completed.stdout.strip(), completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_sqlite_lease_retry_mixed_building_and_pressure_is_progress_aware():
    result = _run_sqlite_lease_retry_sequence(
        ["BUILDING", "BUSY", "BUILDING", "CURRENT"]
    )
    assert result == {"ok": True, "calls": 4, "status": "CURRENT"}


def test_sqlite_lease_retry_opens_only_after_two_consecutive_pressure_failures():
    result = _run_sqlite_lease_retry_sequence(
        ["BUILDING", "BUSY", "BUILDING", "BUSY", "BUSY", "CURRENT"]
    )
    assert result["ok"] is False
    assert result["calls"] == 5
    assert "2/2 consecutive pressure attempt(s)" in result["error"]


def test_sqlite_lease_retry_stops_immediately_on_terminal_expiry():
    result = _run_sqlite_lease_retry_sequence(["EXPIRED", "BUILDING", "CURRENT"])
    assert result["ok"] is False
    assert result["calls"] == 1
    assert "terminal snapshot lease attempt(s)" in result["error"]


def test_successful_slow_chunks_increase_throttle_instead_of_masking_pressure():
    assert "$chunkRequestWatch = [System.Diagnostics.Stopwatch]::StartNew()" in SYNC_SCRIPT
    assert "$slowSuccessfulChunk = $chunkRequestElapsedMs -ge 2000" in SYNC_SCRIPT
    assert "stage=file_chunk status=slow_success" in SYNC_SCRIPT
    assert "-not $slowSuccessfulChunk -and $adaptiveThrottleMs" in SYNC_SCRIPT


def test_sync_acknowledgement_is_fast_exact_and_followed_by_identity_fence():
    ack_body = SYNC_SCRIPT.index('$ackCommon = [ordered]@{')
    completeness_check = SYNC_SCRIPT.index(
        '$ackByPath.Count -ne [int]$manifest.file_count'
    )
    page_ack = SYNC_SCRIPT.index('-Stage "acknowledgement_page_', ack_body)
    ack_request = SYNC_SCRIPT.index('-Stage "acknowledgement_finalize"', page_ack)
    exact_acceptance = SYNC_SCRIPT.index('$ackAccepted -ne $ackExpected', ack_request)
    post_ack_fence = SYNC_SCRIPT.index(
        '-Stage "manifest_post_ack_identity"', exact_acceptance
    )
    analyzer_publish = SYNC_SCRIPT.index('$analyzerPublished = $false', post_ack_fence)

    assert 'schema = "fly_runtime_incremental_ack_v3"' in SYNC_SCRIPT[ack_body:ack_request]
    assert '$stagePayload.operation = "STAGE_PAGE"' in SYNC_SCRIPT[ack_body:ack_request]
    assert '$finalizePayload.operation = "FINALIZE"' in SYNC_SCRIPT[ack_body:exact_acceptance]
    assert '$manifest.manifest_page_receipts' in SYNC_SCRIPT[ack_body:ack_request]
    assert '$ackByPath.Count -ne [int]$manifest.file_count' in SYNC_SCRIPT
    assert 'inventory_sha256 = $inventorySha256' in SYNC_SCRIPT[ack_body:ack_request]
    assert 'inventory_generated_at = $inventoryGeneratedAt' in SYNC_SCRIPT[ack_body:ack_request]
    assert 'source_git_rev = [string]$manifest.source_git_rev' in SYNC_SCRIPT[ack_body:ack_request]
    assert 'collection_epoch_id = [string]$manifest.collection_epoch_id' in SYNC_SCRIPT[ack_body:ack_request]
    assert 'tile_registry_signature = [string]$manifest.tile_registry_signature' in SYNC_SCRIPT[ack_body:ack_request]
    assert '$requestUrl += "&ack_inventory_sha256=$inventorySha256"' in SYNC_SCRIPT
    assert '$ack.PSObject.Properties.Name -contains "accepted"' in SYNC_SCRIPT
    assert '$ack.PSObject.Properties.Name -contains "rejected_count"' in SYNC_SCRIPT
    assert '$ackRejected -ne 0' in SYNC_SCRIPT[exact_acceptance:post_ack_fence]
    assert "Fly sync acknowledgement was incomplete" in SYNC_SCRIPT
    assert "Fly sync acknowledgement did not bind to the requested inventory generation" in SYNC_SCRIPT
    assert (
        "Assert-DataSyncManifestIdentity -Initial $manifest -Final $postAckManifest"
        in SYNC_SCRIPT[post_ack_fence:analyzer_publish]
    )
    assert completeness_check < ack_body < page_ack < ack_request < exact_acceptance < post_ack_fence < analyzer_publish


def test_paged_ack_stages_every_bounded_page_before_one_complete_generation_commit():
    ack_v3 = BOT[
        BOT.index("def _data_sync_ack_v3(body: dict)"):
        BOT.index("@app.route('/api/data-sync/ack'", BOT.index("def _data_sync_ack_v3(body: dict)"))
    ]
    assert 'operation == "STAGE_PAGE"' in ack_v3
    assert 'operation != "FINALIZE"' in ack_v3
    assert 'for page_index in range(int(generation["page_count"]))' in ack_v3
    assert '"missing_page_index": page_index' in ack_v3
    assert ack_v3.index('operation == "STAGE_PAGE"') < ack_v3.index(
        'for page_index in range(int(generation["page_count"]))'
    ) < ack_v3.index("_write_data_sync_ack(compact_ack)")
    assert '$manifest.manifest_page_receipts' in SYNC_SCRIPT
    assert '$stagePayload.operation = "STAGE_PAGE"' in SYNC_SCRIPT
    assert '$finalizePayload.operation = "FINALIZE"' in SYNC_SCRIPT
    assert SYNC_SCRIPT.index('$stagePayload.operation = "STAGE_PAGE"') < SYNC_SCRIPT.index(
        '$finalizePayload.operation = "FINALIZE"'
    )


def test_revision_refresh_uses_verified_one_read_for_small_hot_reports():
    assert '$consistencyMode -eq "strict_generation_v1"' in SYNC_SCRIPT
    assert '$remoteSize -le $chunkLimit' in SYNC_SCRIPT
    assert '$atomicSnapshotFallback = (' in SYNC_SCRIPT
    assert '$ForceFullRefresh -and' in SYNC_SCRIPT
    assert 'The no-fence endpoint path already proves one exact before/after' in SYNC_SCRIPT
    assert '-not $atomicSnapshotFallback -and' in SYNC_SCRIPT


def test_sync_loop_retries_manifest_preflight_and_keeps_relay_optional():
    assert "$preflightManifestAttempts = 220" in SYNC_LOOP
    assert "$preflightInventoryWaitMaxSec = 1800" in SYNC_LOOP
    assert "$preflightManifestTimeoutSec = 90" in SYNC_LOOP
    assert "function Get-FlySyncPreflightManifest" in SYNC_LOOP
    assert "stage=loop_manifest_preflight failed after" in SYNC_LOOP
    assert "Get-FlySyncPreflightManifest `" in SYNC_LOOP
    assert "$relaySyncAttempts = 2" in SYNC_LOOP
    assert "function Invoke-OptionalRelayEvidenceSync" in SYNC_LOOP
    assert "stage=optional_relay_evidence failed after" in SYNC_LOOP
    manifest_call = SYNC_LOOP.index("$manifest = Get-FlySyncPreflightManifest")
    required_sync_gate = SYNC_LOOP.index("$needsFullInventory =", manifest_call)
    optional_gate = SYNC_LOOP.index("-not $needsFullInventory", required_sync_gate)
    relay_call = SYNC_LOOP.index("$relayEvidencePath = Invoke-OptionalRelayEvidenceSync")
    relay_catch = SYNC_LOOP.index("} catch {", relay_call)
    assert manifest_call < required_sync_gate < optional_gate < relay_call < relay_catch


def test_optional_relay_evidence_is_process_isolated_and_cadence_bounded():
    assert "$relayEvidenceAttemptIntervalSec = 1800" in SYNC_LOOP
    assert "$lastRelayEvidenceAttemptAt = [DateTimeOffset]::UtcNow" in SYNC_LOOP
    relay_body = SYNC_LOOP.split("function Invoke-OptionalRelayEvidenceSync", 1)[1]
    relay_body = relay_body.split("function Wait-FlyRuntimeQuietForFullSync", 1)[0]
    assert "$childHost = (Get-Process -Id $PID -ErrorAction Stop).Path" in relay_body
    assert "& $childHost -NoProfile -ExecutionPolicy Bypass -File $relayScript" in relay_body
    assert "[RELAY_EVIDENCE_$safeCode]" in relay_body
    assert "$relayEvidenceStatus.errorCode = \"DEFERRED_CADENCE\"" in SYNC_LOOP
    assert "$lastRelayEvidenceAttemptAt = [DateTimeOffset]::UtcNow" in SYNC_LOOP


def test_sync_loop_separates_poll_retry_and_full_mutation_cadence():
    assert "[int]$FullSyncIntervalSec = 1800" in SYNC_LOOP
    assert "$fullSyncSec = [Math]::Max(600, $FullSyncIntervalSec)" in SYNC_LOOP
    assert "$forceByTime = $elapsedSec -ge $fullSyncSec" in SYNC_LOOP
    assert "$forceByTime = $elapsedSec -ge [Math]::Max(15, $IntervalSec)" not in SYNC_LOOP
    failure_marker = SYNC_LOOP.index("$failureAt = (Get-Date).ToUniversalTime()")
    catch_start = SYNC_LOOP.rfind("    } catch {", 0, failure_marker)
    loop_tail = SYNC_LOOP[catch_start:]
    assert loop_tail.count("Start-Sleep -Seconds $sleepSec") == 2
    assert "$sleepSec = Get-FlySyncFailureBackoffSeconds" in loop_tail
    assert "Start-Sleep -Seconds ([Math]::Max(15, $IntervalSec))" not in loop_tail


def test_sync_outage_backoff_is_deterministic_bounded_and_reset_on_success():
    helper = SYNC_BACKOFF.read_text(encoding="utf-8")
    assert "function Get-FlySyncFailureBackoffSeconds" in helper
    command = (
        f". '{SYNC_BACKOFF}'; "
        "@(0,1,2,3,4,5,6) | ForEach-Object { "
        "Get-FlySyncFailureBackoffSeconds -ConsecutiveFailures $_ "
        "-NormalPollSeconds 180 -MaximumBackoffSeconds 1800 } | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert json.loads(completed.stdout) == [180, 180, 360, 720, 1440, 1800, 1800]
    assert ". (Join-Path $scriptDir \"fly-sync-backoff.ps1\")" in SYNC_LOOP
    assert "$consecutiveFailures += 1" in SYNC_LOOP
    assert "$consecutiveFailures = 0" in SYNC_LOOP
    assert 'nextRetryAt = $nextRetryAt' in SYNC_LOOP
    assert 'backoffSec = $sleepSec' in SYNC_LOOP
    assert 'Start-Sleep -Seconds $sleepSec' in SYNC_LOOP


def test_chunk_pressure_circuit_breaker_aborts_early_and_resets_deterministically():
    command = (
        f". '{SYNC_BACKOFF}'; $c=0; $out=@(); "
        "$c=Get-FlySyncNextPressureFailureCount -CurrentCount $c -IsResourcePressure $true; $out+=$c; "
        "$c=Get-FlySyncNextPressureFailureCount -CurrentCount $c -IsResourcePressure $true; $out+=$c; "
        "$c=Get-FlySyncNextPressureFailureCount -CurrentCount $c -IsResourcePressure $false; $out+=$c; "
        "$out += [int](Test-FlySyncResourcePressureMessage 'Fly sync HTTP 503'); "
        "$out += [int](Test-FlySyncResourcePressureMessage 'The operation timed out'); "
        "$out += [int](Test-FlySyncResourcePressureMessage 'checksum mismatch'); "
        "$out | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert json.loads(completed.stdout) == [1, 2, 0, 1, 1, 0]
    assert '$resourcePressureCircuitThreshold = 2' in SYNC_SCRIPT
    assert '$consecutiveChunkPressureFailures = 0' in SYNC_SCRIPT
    assert 'stage=file_chunk_resource_pressure_circuit_open' in SYNC_SCRIPT
    circuit = SYNC_SCRIPT.index('stage=file_chunk_resource_pressure_circuit_open')
    final_ack = SYNC_SCRIPT.index('$ackCommon = [ordered]@{')
    assert circuit < final_ack
    assert 'Get-FlySyncNextPressureFailureCount `' in SYNC_SCRIPT


def test_identity_poll_uses_o1_volume_growth_to_trigger_full_inventory():
    assert '$lastSyncedVolumeUsedBytes = [int64]0' in SYNC_LOOP
    assert 'lastSyncedVolumeUsedBytes = $lastSyncedVolumeUsedBytes' in SYNC_LOOP
    assert '$currentVolumeUsedBytes = [int64]$manifest.volume.used' in SYNC_LOOP
    assert '[int64]($currentVolumeUsedBytes - $lastSyncedVolumeUsedBytes)' in SYNC_LOOP
    assert '$volumeGrowthBytes -ge $thresholdBytes' in SYNC_LOOP
    assert (
        '$needsFullInventory = $forceByTime -or $forceFresh -or '
        '$forceByRevision -or $forceByGrowth'
    ) in SYNC_LOOP
    # Decreases and a missing first-run baseline never masquerade as growth.
    assert '$lastSyncedVolumeUsedBytes -gt 0 -and' in SYNC_LOOP
    assert '[Math]::Max(' in SYNC_LOOP
    assert 'growthBasis = "FLY_VOLUME_USED_BYTES_O1"' in SYNC_LOOP


def test_reserved_identity_preflight_is_memory_only_and_full_manifest_retains_usage():
    identity_start = BOT.index("def api_data_sync_identity():")
    identity_end = BOT.index("\n@app.route", identity_start + 10)
    identity_body = BOT[identity_start:identity_end]
    assert "return jsonify(_data_sync_memory_identity_payload())" in identity_body
    for forbidden in (
        "disk_usage", "_data_sync_volume_root", "_load_research_session_meta",
        "_lifecycle_pipeline_runtime_status", "_data_sync_request_async_inventory",
        "state_lock", "trade_lock",
    ):
        assert forbidden not in identity_body

    payload_start = BOT.index("def _data_sync_memory_identity_payload():")
    payload_end = BOT.index("\n\n@app.route('/api/data-sync/identity')", payload_start)
    payload_body = BOT[payload_start:payload_end]
    assert 'payload["inventory_status"] = "IDENTITY_ONLY"' in payload_body
    assert "_data_sync_identity_cache_lock" in payload_body
    assert "_data_sync_identity_epoch_cache" in payload_body
    for forbidden in (
        "disk_usage", "_data_sync_volume_root", "_load_research_session_meta",
        "_lifecycle_pipeline_runtime_status", "_data_sync_request_async_inventory",
        "state_lock", "trade_lock",
    ):
        assert forbidden not in payload_body

    manifest_start = BOT.index("def api_data_sync_manifest():")
    manifest_end = BOT.index("\n@app.route", manifest_start + 10)
    manifest_body = BOT[manifest_start:manifest_end]
    assert 'shutil.disk_usage(_data_sync_volume_root())' in manifest_body
    assert "checks Fly identity and O(1) volume usage every 3 min" in BOT
    assert "or at least every 30 min" in BOT
    assert "ACK-qualified pruning remains deferred" in BOT


def test_reserved_identity_preflight_has_independent_admission_and_auth_contract():
    assert "@app.route('/api/data-sync/identity')" in BOT
    assert 'b"/api/data-sync/identity"' in BOT
    classifier = BOT[BOT.index("def _request_cap(self, request):"):BOT.index(
        "def _reject_overload(", BOT.index("def _request_cap(self, request):")
    )]
    assert classifier.index("_data_sync_identity_paths") < classifier.index(
        "_data_sync_paths"
    )
    assert "_data_sync_identity_thread_cap" in classifier
    guard = BOT[BOT.index("_READ_ONLY_GET_PATHS"):BOT.index("def _client_ip")]
    assert "/api/data-sync/identity" not in guard
    assert "/api/data-sync/manifest" not in guard


def test_identity_epoch_cache_is_primed_at_boot_and_updated_on_fresh_reset():
    main = BOT[BOT.index("def main():"):]
    assert main.index("_ensure_collector_v22_epoch()") < main.index(
        "_prime_data_sync_identity_epoch_cache()"
    )
    fresh = BOT[BOT.index("def _perform_fresh_collection_reset_locked("):BOT.index(
        "replay_buffers:", BOT.index("def _perform_fresh_collection_reset_locked(")
    )]
    signal_read = 'signal_ts = float(state.get("fresh_collection_signal_ts") or 0.0)'
    cache_update = "_update_data_sync_identity_epoch_cache("
    assert fresh.index(signal_read) < fresh.index(cache_update, fresh.index(signal_read))
    assert "collection_epoch_id=_collector_v22_epoch_id()" in fresh


def _load_bot_functions(*names):
    tree = ast.parse(BOT)
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    namespace = {
        "Path": Path,
        "os": os,
        "time": time,
        "_DATA_SYNC_EXTENSIONS": frozenset(
            {".csv", ".json", ".jsonl", ".log", ".db", ".sqlite", ".sqlite3", ".txt"}
        ),
        "_DATA_SYNC_APPEND_PREFIX_NAMES": frozenset({
            "pipeline_events_3factor.csv", "trades_3factor.csv",
            "research_events_v22.jsonl",
        }),
        "_RESEARCH_RAW_JSONL_NEVER_PRUNE": frozenset({"research_events_v22.jsonl"}),
        "_DATA_SYNC_LIFECYCLE_CLEANUP_ENABLED": False,
        "_pure_validate_platform_relay_evidence_payload": pure_validate_relay,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace


def test_data_sync_inventory_excludes_preserved_history_from_active_mirror():
    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_rotation_parts",
        "_data_sync_path_allowed",
        "_data_sync_complete_record_size",
        "_data_sync_consistency_mode",
        "_data_sync_inventory_record",
        "_data_sync_inventory",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        (root / "signal_snapshot.jsonl").write_text("{}\n", encoding="utf-8")
        (root / "research_events_v22.provisional.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (root / "collector_storage_state.json").write_text(
            "{}\n", encoding="utf-8"
        )
        (root / "open_positions.json").write_text("[]\n", encoding="utf-8")
        (root / "paper_lifecycle_v1.json").write_text("{}\n", encoding="utf-8")
        excluded = [
            root / "research_epoch_quarantine" / "epoch_1" / "old.jsonl",
            root / "research_archive" / "session_1" / "old.json",
            root / "research_session_archives" / "old.json",
            root / "object_store" / "old.jsonl",
        ]
        for path in excluded:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
        namespace = {
            "Path": Path,
            "os": os,
            "_DATA_SYNC_EXTENSIONS": frozenset({".json", ".jsonl"}),
            "_DATA_SYNC_APPEND_PREFIX_NAMES": frozenset({"pipeline_events_3factor.csv"}),
            "_DATA_SYNC_EXCLUDED_NAMES": frozenset({
                "manifest.json", "research_events_v22.provisional.json",
                "collector_storage_state.json", "open_positions.json",
            }),
            "_DATA_SYNC_EXCLUDED_DIR_NAMES": frozenset({
                "research_epoch_quarantine", "research_archive",
                "research_session_archives", "archive-v2", "object-store", "object_store",
            }),
            "_DATA_SYNC_TOP_LEVEL_RECEIPT_NAMES": frozenset(),
            "_data_sync_volume_root": lambda: root,
            "_data_sync_runtime_root": lambda: root,
            "_data_sync_allowed_roots": lambda: [root],
            "_data_sync_relpath": lambda path: path.resolve().relative_to(root).as_posix(),
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
        rows = namespace["_data_sync_inventory"]()
        assert [row["path"] for row in rows] == [
            "paper_lifecycle_v1.json", "signal_snapshot.jsonl",
        ]
        for path in excluded:
            assert namespace["_data_sync_path_allowed"](path) is False


def test_data_sync_inventory_and_retrieval_include_only_safe_quarantine_evidence(
    tmp_path, monkeypatch,
):
    root = tmp_path.resolve()
    quarantine = root / "corrupt_evidence_quarantine" / "exact_repair"
    quarantine.mkdir(parents=True)
    expected = {
        "execution_funnel.jsonl": b'{"ok":1}\n',
        "quarantine_manifest.json": b'{}',
        "excluded_lines_unknown.json": b'{}',
        "repair_receipt.json": b'{}',
    }
    for name, payload in expected.items():
        (quarantine / name).write_bytes(payload)
    (quarantine / "operator_secret.json").write_text("{}", encoding="utf-8")
    (quarantine / "credential-copy.json").write_text("{}", encoding="utf-8")
    (quarantine / ".env.json").write_text("{}", encoding="utf-8")
    (quarantine / "raw.bin").write_bytes(b"blocked")

    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_rotation_parts", "_data_sync_path_allowed",
        "_data_sync_is_linked_directory",
        "_data_sync_resolve_relpath", "_data_sync_complete_record_size",
        "_data_sync_consistency_mode", "_data_sync_inventory_record",
        "_data_sync_inventory",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {
        "Path": Path, "os": os, "time": time,
        "_DATA_SYNC_EXTENSIONS": frozenset({".json", ".jsonl"}),
        "_DATA_SYNC_APPEND_PREFIX_NAMES": frozenset(),
        "_DATA_SYNC_EXCLUDED_NAMES": frozenset(),
        "_DATA_SYNC_EXCLUDED_DIR_NAMES": frozenset({"research_archive"}),
        "_DATA_SYNC_TOP_LEVEL_RECEIPT_NAMES": frozenset(),
        "_data_sync_volume_root": lambda: root,
        "_data_sync_runtime_root": lambda: root,
        "_data_sync_allowed_roots": lambda: [root],
        "_data_sync_relpath": lambda path: path.resolve().relative_to(root).as_posix(),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)

    prefix = "corrupt_evidence_quarantine/exact_repair/"
    rows = namespace["_data_sync_inventory"]()
    assert [row["path"] for row in rows] == [prefix + name for name in sorted(expected)]
    for name, payload in expected.items():
        resolved = namespace["_data_sync_resolve_relpath"](prefix + name)
        assert resolved.read_bytes() == payload
    for blocked in ("operator_secret.json", "credential-copy.json", ".env.json", "raw.bin"):
        with pytest.raises(ValueError, match="allowed runtime data file|invalid relative path"):
            namespace["_data_sync_resolve_relpath"](prefix + blocked)

    target = root / "research_archive" / "linked-target.json"
    target.parent.mkdir()
    target.write_text("{}", encoding="utf-8")
    linked = quarantine / "linked.json"
    try:
        linked.symlink_to(target)
    except OSError:
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == linked)
    assert namespace["_data_sync_path_allowed"](linked) is False
    linked_directory = quarantine / "linked-directory"
    target_directory = root / "research_archive" / "linked-directory-target"
    target_directory.mkdir()
    (target_directory / "nested.json").write_text("{}", encoding="utf-8")
    directory_link_created = False
    try:
        linked_directory.symlink_to(target_directory, target_is_directory=True)
        directory_link_created = True
    except OSError:
        pass
    if directory_link_created:
        assert namespace["_data_sync_path_allowed"](
            linked_directory / "nested.json"
        ) is False
    assert "corrupt_evidence_quarantine" not in _excluded_directory_names_from_source()


def _excluded_directory_names_from_source():
    tree = ast.parse(BOT)
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_DATA_SYNC_EXCLUDED_DIR_NAMES"
                for target in node.targets)
    )
    call = assignment.value
    return set(ast.literal_eval(call.args[0]))


def test_data_sync_inventory_never_advertises_a_partial_jsonl_record():
    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_rotation_parts",
        "_data_sync_path_allowed",
        "_data_sync_complete_record_size",
        "_data_sync_consistency_mode",
        "_data_sync_inventory_record",
        "_data_sync_inventory",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        complete = b'{"event":1}\n'
        target = root / "shadow_lane_outcome.jsonl"
        target.write_bytes(complete + b'{"event":2')
        namespace = {
            "Path": Path,
            "os": os,
            "_DATA_SYNC_EXTENSIONS": frozenset({".jsonl"}),
            "_DATA_SYNC_APPEND_PREFIX_NAMES": frozenset({"pipeline_events_3factor.csv"}),
            "_DATA_SYNC_EXCLUDED_NAMES": frozenset(),
            "_DATA_SYNC_EXCLUDED_DIR_NAMES": frozenset(),
            "_DATA_SYNC_TOP_LEVEL_RECEIPT_NAMES": frozenset(),
            "_data_sync_volume_root": lambda: root,
            "_data_sync_runtime_root": lambda: root,
            "_data_sync_allowed_roots": lambda: [root],
            "_data_sync_relpath": lambda path: path.resolve().relative_to(root).as_posix(),
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
        rows = namespace["_data_sync_inventory"]()
        assert rows[0]["size"] == len(complete)

        with target.open("ab") as handle:
            handle.write(b'}\n')
        rows = namespace["_data_sync_inventory"]()
        assert rows[0]["size"] == target.stat().st_size


def test_data_sync_allowed_roots_are_non_overlapping_physical_directories(
    monkeypatch, tmp_path
):
    runtime = tmp_path / "runtime"
    (runtime / "research" / "nested").mkdir(parents=True)
    (runtime / "research_accumulator").mkdir()
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))

    namespace = {"Path": Path, "os": os}
    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_runtime_root", "_data_sync_volume_root",
        "_data_sync_path_is_within", "_data_sync_allowed_roots",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)

    roots = namespace["_data_sync_allowed_roots"]()
    assert roots == [runtime.resolve()]
    for index, root in enumerate(roots):
        for other in roots[index + 1:]:
            assert not namespace["_data_sync_path_is_within"](root, other)
            assert not namespace["_data_sync_path_is_within"](other, root)


def test_data_sync_top_level_research_symlink_is_on_volume_and_round_trips(
    monkeypatch, tmp_path
):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    physical = tmp_path / "durable-research"
    physical.mkdir()
    evidence = physical / "episode.jsonl"
    evidence.write_text('{"episode":1}\n', encoding="utf-8")
    link = runtime / "research"
    link.mkdir()
    original_resolve = Path.resolve

    def mapped_resolve(self, strict=False):
        lexical = Path(os.path.abspath(self))
        try:
            suffix = lexical.relative_to(link)
        except ValueError:
            return original_resolve(self, strict=strict)
        mapped = physical.joinpath(*suffix.parts)
        return original_resolve(mapped, strict=strict)

    monkeypatch.setattr(Path, "resolve", mapped_resolve)
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))

    namespace = {
        "Path": Path,
        "os": os,
        "_DATA_SYNC_EXTENSIONS": frozenset({".jsonl"}),
        "_DATA_SYNC_APPEND_PREFIX_NAMES": frozenset(),
        "_DATA_SYNC_EXCLUDED_NAMES": frozenset(),
        "_DATA_SYNC_EXCLUDED_DIR_NAMES": frozenset(),
        "_DATA_SYNC_TOP_LEVEL_RECEIPT_NAMES": frozenset(),
    }
    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_rotation_parts", "_data_sync_runtime_root",
        "_data_sync_volume_root", "_data_sync_path_is_within",
        "_data_sync_is_linked_directory",
        "_data_sync_allowed_roots", "_data_sync_relpath",
        "_data_sync_path_allowed", "_data_sync_resolve_relpath",
        "_data_sync_complete_record_size", "_data_sync_consistency_mode",
        "_data_sync_inventory_record",
        "_data_sync_inventory",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)

    roots = namespace["_data_sync_allowed_roots"]()
    assert roots == [runtime.resolve(), physical.resolve()]
    rows = namespace["_data_sync_inventory"]()
    assert [row["path"] for row in rows] == ["research/episode.jsonl"]
    assert namespace["_data_sync_relpath"](evidence) == "research/episode.jsonl"
    assert namespace["_data_sync_resolve_relpath"]("research/episode.jsonl") == (
        evidence.resolve()
    )


def test_data_sync_top_level_research_symlink_outside_volume_is_excluded(
    monkeypatch, tmp_path
):
    volume = tmp_path / "volume"
    runtime = volume / "runtime"
    runtime.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.jsonl").write_text("{}\n", encoding="utf-8")
    link = runtime / "research"
    link.mkdir()
    original_resolve = Path.resolve

    def mapped_resolve(self, strict=False):
        lexical = Path(os.path.abspath(self))
        try:
            suffix = lexical.relative_to(link)
        except ValueError:
            return original_resolve(self, strict=strict)
        mapped = outside.joinpath(*suffix.parts)
        return original_resolve(mapped, strict=strict)

    monkeypatch.setattr(Path, "resolve", mapped_resolve)
    monkeypatch.setenv("BOT_DATA_DIR", str(volume))

    namespace = {"Path": Path, "os": os}
    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_runtime_root", "_data_sync_volume_root",
        "_data_sync_path_is_within", "_data_sync_allowed_roots",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)

    assert namespace["_data_sync_allowed_roots"]() == [runtime.resolve()]


def test_data_sync_inventory_never_follows_nested_directory_symlinks(monkeypatch, tmp_path):
    visited = []
    yielded_dirnames = ["linked-research"]

    def fake_walk(root, *, followlinks):
        visited.append((Path(root), followlinks))
        yield str(root), yielded_dirnames, []

    monkeypatch.setattr(os, "walk", fake_walk)
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda self: self.name == "linked-research" or original_is_symlink(self),
    )
    namespace = {
        "Path": Path,
        "os": os,
        "_DATA_SYNC_TOP_LEVEL_RECEIPT_NAMES": frozenset(),
        "_DATA_SYNC_EXCLUDED_DIR_NAMES": frozenset(),
        "_data_sync_volume_root": lambda: tmp_path,
        "_data_sync_allowed_roots": lambda: [tmp_path],
        "_data_sync_is_linked_directory": lambda path: path.name == "linked-research",
    }
    tree = ast.parse(BOT)
    node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_data_sync_inventory"
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)

    assert namespace["_data_sync_inventory"]() == []
    assert visited == [(tmp_path, False)]
    assert yielded_dirnames == []


def test_data_sync_inventory_cache_is_short_ttl_single_flight():
    calls = []
    barrier = threading.Barrier(8)

    def inventory(*, include_sqlite_snapshots):
        assert include_sqlite_snapshots is False
        calls.append(time.monotonic())
        time.sleep(0.05)
        return [{"path": "evidence.json", "size": 1}]

    namespace = {
        "time": time,
        "threading": threading,
        "_data_sync_inventory": inventory,
        "_DATA_SYNC_INVENTORY_CACHE_TTL_SECONDS": 0.2,
        "_data_sync_inventory_cache_condition": threading.Condition(),
        "_data_sync_inventory_cache": {
            "expires_at": 0.0, "refreshing": False, "rows": None,
        },
    }
    tree = ast.parse(BOT)
    node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_data_sync_cached_inventory"
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)

    results = []

    def fetch():
        barrier.wait()
        results.append(namespace["_data_sync_cached_inventory"]())

    workers = [threading.Thread(target=fetch) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2)

    assert len(results) == 8
    assert len(calls) == 1
    assert all(row == [{"path": "evidence.json", "size": 1}] for row in results)
    results[0][0]["size"] = 999
    assert namespace["_data_sync_cached_inventory"]()[0]["size"] == 1
    time.sleep(0.21)
    assert namespace["_data_sync_cached_inventory"]()[0]["size"] == 1
    assert len(calls) == 2


def test_data_sync_inventory_forced_refresh_bypasses_stale_rows_and_serializes():
    calls = []
    barrier = threading.Barrier(6)
    activity_lock = threading.Lock()
    active_scans = 0
    maximum_active_scans = 0

    def inventory(*, include_sqlite_snapshots):
        nonlocal active_scans, maximum_active_scans
        with activity_lock:
            active_scans += 1
            maximum_active_scans = max(maximum_active_scans, active_scans)
        calls.append(len(calls) + 1)
        time.sleep(0.05)
        result = [{"path": "evidence.json", "size": calls[-1]}]
        with activity_lock:
            active_scans -= 1
        return result

    namespace = {
        "time": time,
        "_data_sync_inventory": inventory,
        "_DATA_SYNC_INVENTORY_CACHE_TTL_SECONDS": 30.0,
        "_data_sync_inventory_cache_condition": threading.Condition(),
        "_data_sync_inventory_cache": {
            "expires_at": 0.0, "refreshed_at": 0.0,
            "refreshing": False, "rows": None,
        },
    }
    tree = ast.parse(BOT)
    node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_data_sync_cached_inventory"
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)

    assert namespace["_data_sync_cached_inventory"]()[0]["size"] == 1
    assert namespace["_data_sync_cached_inventory"]()[0]["size"] == 1
    results = []

    def fresh_fetch():
        barrier.wait()
        results.append(
            namespace["_data_sync_cached_inventory"](force_refresh=True)[0]["size"]
        )

    workers = [threading.Thread(target=fresh_fetch) for _ in range(6)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2)

    assert len(results) == 6
    assert sorted(results) == [2] * 6
    assert len(calls) == 2
    assert maximum_active_scans == 1


def test_failed_forced_refresh_never_releases_stale_rows_to_waiter():
    calls = []
    failure_started = threading.Event()
    release_failure = threading.Event()

    def inventory(*, include_sqlite_snapshots):
        call_number = len(calls) + 1
        calls.append(call_number)
        if call_number == 1:
            return [{"path": "evidence.json", "size": 1}]
        if call_number == 2:
            failure_started.set()
            assert release_failure.wait(timeout=2)
            raise OSError("forced inventory scan failed")
        return [{"path": "evidence.json", "size": call_number}]

    cache = {
        "expires_at": 0.0,
        "refreshed_at": 0.0,
        "refreshing": False,
        "rows": None,
    }
    namespace = {
        "time": time,
        "_data_sync_inventory": inventory,
        "_DATA_SYNC_INVENTORY_CACHE_TTL_SECONDS": 30.0,
        "_data_sync_inventory_cache_condition": threading.Condition(),
        "_data_sync_inventory_cache": cache,
    }
    tree = ast.parse(BOT)
    node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_data_sync_cached_inventory"
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    cached_inventory = namespace["_data_sync_cached_inventory"]

    assert cached_inventory() == [{"path": "evidence.json", "size": 1}]
    leader_errors = []
    waiter_results = []

    def failing_leader():
        try:
            cached_inventory(force_refresh=True)
        except OSError as exc:
            leader_errors.append(str(exc))

    def joined_waiter():
        waiter_results.append(cached_inventory(force_refresh=True))

    leader = threading.Thread(target=failing_leader)
    leader.start()
    assert failure_started.wait(timeout=2)
    waiter = threading.Thread(target=joined_waiter)
    waiter.start()
    # The forced waiter is now blocked on the leader's in-flight scan.
    time.sleep(0.05)
    assert waiter.is_alive()
    release_failure.set()
    leader.join(timeout=2)
    waiter.join(timeout=2)

    assert leader_errors == ["forced inventory scan failed"]
    assert waiter_results == [[{"path": "evidence.json", "size": 3}]]
    assert calls == [1, 2, 3]
    assert cache["rows"] == [{"path": "evidence.json", "size": 3}]
    assert cache["refreshing"] is False
    # The successful waiter flight is now the only cacheable generation.
    assert cached_inventory() == [{"path": "evidence.json", "size": 3}]
    assert calls == [1, 2, 3]


def test_data_sync_manifest_route_recognizes_client_cache_bypass_contract():
    tree = ast.parse(BOT)
    node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_data_sync_manifest_force_refresh"
    )
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    should_refresh = namespace["_data_sync_manifest_force_refresh"]

    assert should_refresh({"fresh": "1"}) is True
    assert should_refresh({"fresh": "true"}) is True
    assert should_refresh({"cache_bypass": "9ee00e34-identity-fence"}) is True
    assert should_refresh({}) is False
    assert should_refresh({"fresh": "0", "cache_bypass": ""}) is False

    identity_node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_data_sync_manifest_identity_only"
    )
    identity_namespace = {}
    exec(
        compile(ast.Module(body=[identity_node], type_ignores=[]), "bot.py", "exec"),
        identity_namespace,
    )
    identity_only = identity_namespace["_data_sync_manifest_identity_only"]
    assert identity_only({"identity_only": "1"}) is True
    assert identity_only({"identity_only": "true"}) is True
    assert identity_only({"identity_only": "0"}) is False
    assert identity_only({}) is False

    route_body = BOT[
        BOT.index("def api_data_sync_manifest"):
        BOT.index("@app.route('/api/data-sync/sqlite-snapshot')")
    ]
    assert "_data_sync_manifest_force_refresh(request.args)" in route_body
    assert "_data_sync_manifest_identity_only(request.args)" in route_body
    assert "_data_sync_request_async_inventory(" in route_body
    assert "_data_sync_targeted_inventory(targeted_path)" in route_body
    assert '"inventory_status": inventory_status' in route_body
    assert '"identity_only": identity_only' in route_body


def test_final_identity_fence_skips_full_inventory_without_weakening_file_fences():
    assert "[switch]$IdentityOnly" in SYNC_SCRIPT
    assert "[string]$Path" in SYNC_SCRIPT
    assert '"&identity_only=1"' in SYNC_SCRIPT
    assert "-IdentityOnly `\n    -GenerationId $inventoryGenerationId" in SYNC_SCRIPT
    assert "$Final.inventory_generation_available -ne $true" in SYNC_SCRIPT
    assert 'Assert-DataSyncManifestIdentity -Initial $manifest -Final $finalManifest' in SYNC_SCRIPT
    # Generation refresh is exact-path only; the final authority fence remains
    # identity-only and the initial manifest remains a full CURRENT inventory.
    assert '-Uri (New-DataSyncManifestUri -Path $rel) `' in SYNC_SCRIPT
    # Each file remains independently fenced during download.
    assert '"file generation changed after manifest"' in BOT
    assert '"file generation changed during download"' in BOT


def test_data_sync_includes_canonical_volume_receipts_when_runtime_is_child(monkeypatch, tmp_path):
    receipt_names = {
        "tile_independence_report.json",
        "ai_scan_independence_report.json",
        "ai_scan_role_validation.json",
        "exit_reports_validation.json",
        "lane_memory_validation.json",
        "lane_memory_violation.json",
        "runtime_pathway_integrity.json",
    }
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for name in receipt_names:
        (tmp_path / name).write_text('{"canonical":true}\n', encoding="utf-8")
    # A same-named runtime artifact must not create a duplicate manifest path
    # or override the canonical top-level download target.
    (runtime / "tile_independence_report.json").write_text(
        '{"canonical":false}\n', encoding="utf-8"
    )
    (runtime / "ordinary.json").write_text("{}\n", encoding="utf-8")
    db = sqlite3.connect(runtime / "research.db")
    db.execute("create table evidence(id integer primary key)")
    db.commit()
    db.close()
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(runtime)

    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_rotation_parts", "_data_sync_runtime_root",
        "_data_sync_volume_root", "_data_sync_allowed_roots",
        "_data_sync_relpath", "_data_sync_path_allowed",
        "_data_sync_resolve_relpath", "_data_sync_complete_record_size",
        "_data_sync_consistency_mode", "_data_sync_sqlite_snapshot",
        "_data_sync_inventory_record",
        "_data_sync_inventory",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {
        "Path": Path,
        "os": os,
        "_DATA_SYNC_EXTENSIONS": frozenset({".json", ".db"}),
        "_DATA_SYNC_APPEND_PREFIX_NAMES": frozenset(),
        "_DATA_SYNC_EXCLUDED_NAMES": frozenset(),
        "_DATA_SYNC_EXCLUDED_DIR_NAMES": frozenset(),
        "_DATA_SYNC_TOP_LEVEL_RECEIPT_NAMES": frozenset(receipt_names),
        "sqlite3": sqlite3,
        "uuid": uuid,
        "hashlib": hashlib,
        "time": time,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)

    rows = namespace["_data_sync_inventory"]()
    paths = [row["path"] for row in rows]
    assert receipt_names.issubset(paths)
    assert paths.count("tile_independence_report.json") == 1
    assert "ordinary.json" in paths
    assert "research.db" in paths
    cheap_db_row = next(row for row in rows if row["path"] == "research.db")
    assert cheap_db_row["consistency_mode"] == "sqlite_snapshot_v1"
    assert "snapshot_id" not in cheap_db_row
    assert not (tmp_path / ".data-sync-snapshots").exists()

    snapshot_rows = namespace["_data_sync_inventory"](
        include_sqlite_snapshots=True
    )
    snapshot_db_row = next(
        row for row in snapshot_rows if row["path"] == "research.db"
    )
    assert snapshot_db_row["snapshot_id"]
    assert len(snapshot_db_row["snapshot_sha256"]) == 64
    assert snapshot_db_row["size"] == snapshot_db_row["snapshot_size"]
    assert namespace["_data_sync_resolve_relpath"]("research.db") == (
        runtime / "research.db"
    ).resolve()
    resolved = namespace["_data_sync_resolve_relpath"]("tile_independence_report.json")
    assert resolved == (tmp_path / "tile_independence_report.json").resolve()
    try:
        namespace["_data_sync_resolve_relpath"]("../lane_memory_validation.json")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal must be rejected")


def test_hot_sqlite_is_advertised_as_integrity_checked_snapshot(monkeypatch, tmp_path):
    source = tmp_path / "research.db"
    conn = sqlite3.connect(source)
    conn.execute("create table evidence(id integer primary key, value text)")
    conn.execute("insert into evidence(value) values ('preserved')")
    conn.commit()
    conn.close()
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))

    namespace = _load_bot_functions(
        "_data_sync_volume_root",
        "_data_sync_sqlite_snapshot",
        "_data_sync_resolve_sqlite_snapshot",
    )
    namespace.update({"sqlite3": sqlite3, "uuid": uuid, "hashlib": hashlib, "re": re})
    # Reload with the dependencies used by the selected function bodies.
    tree = ast.parse(BOT)
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_data_sync_volume_root", "_data_sync_sqlite_snapshot",
            "_data_sync_resolve_sqlite_snapshot",
        }
    ]
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    receipt = namespace["_data_sync_sqlite_snapshot"](source)
    snapshot = namespace["_data_sync_resolve_sqlite_snapshot"](receipt["snapshot_id"])
    assert snapshot.stat().st_size == receipt["snapshot_size"]
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == receipt["snapshot_sha256"]
    copied = sqlite3.connect(snapshot)
    try:
        assert copied.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert copied.execute("select value from evidence").fetchone()[0] == "preserved"
    finally:
        copied.close()


def test_sqlite_snapshot_lease_materializes_only_requested_database(tmp_path):
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    tree = ast.parse(BOT)
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "api_data_sync_sqlite_snapshot"
    ]
    app = Flask(__name__)
    calls = []
    namespace = {
        "app": app,
        "request": request,
        "jsonify": jsonify,
        "re": re,
        "sqlite3": sqlite3,
        "_data_sync_resolve_relpath": lambda rel: {
            "first.db": first,
            "second.db": second,
        }.get(rel) or (_ for _ in ()).throw(ValueError("invalid path")),
        "_data_sync_consistency_mode": lambda path: (
            "sqlite_snapshot_v1" if path.suffix == ".db" else "strict_generation_v1"
        ),
        "_data_sync_request_sqlite_snapshot": lambda path, request_id, inventory_generation_id, inventory_sha256, source_identity: (
            calls.append(path.name)
            or {"snapshot_status": "CURRENT", "snapshot_id": "a" * 32,
                "snapshot_size": 5, "snapshot_sha256": "b" * 64,
                "request_id": request_id, "build_id": "c" * 32,
                "inventory_generation_id": inventory_generation_id,
                "inventory_sha256": inventory_sha256}
        ),
        "_data_sync_relpath": lambda path: path.name,
        "_DATA_SYNC_SQLITE_SNAPSHOT_RETRY_SECONDS": 2,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    with app.test_request_context(
            "/api/data-sync/sqlite-snapshot?path=first.db&request_id=" + "d" * 32
            + "&inventory_generation_id=" + "e" * 64
            + "&inventory_sha256=" + "e" * 64):
        payload = namespace["api_data_sync_sqlite_snapshot"]().get_json()
    assert payload["path"] == "first.db"
    assert calls == ["first.db"]
    with app.test_request_context("/api/data-sync/sqlite-snapshot?path=first.db"):
        response, status = namespace["api_data_sync_sqlite_snapshot"]()
    assert status == 400
    assert response.get_json()["error"] == "invalid sqlite snapshot request identity"
    with app.test_request_context("/api/data-sync/sqlite-snapshot?path=../escape.db"):
        response, status = namespace["api_data_sync_sqlite_snapshot"]()
    assert status == 400
    assert response.get_json()["error"] == "invalid path"


def test_sqlite_snapshot_generation_is_single_flight_and_reused(tmp_path):
    source = tmp_path / "single-flight.db"
    source.write_bytes(b"stable-generation")
    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_sqlite_generation", "_data_sync_sqlite_snapshot_worker",
        "_data_sync_request_sqlite_snapshot",
    }
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in wanted]
    started = []
    builds = []

    class DeferredThread:
        def __init__(self, *, target, args, name, daemon):
            self.target, self.args = target, args
            self.started = False

        def start(self):
            self.started = True
            started.append(self)

        def is_alive(self):
            return self.started

    condition = threading.Condition()
    request_id = "c" * 32
    inventory_generation_id = "7" * 64
    inventory_sha256 = "7" * 64
    state = {
        "status": "EMPTY", "generation": None, "path": None, "lease": None,
        "worker": None, "started_at": 0.0, "deadline_at": 0.0,
        "completed_at": 0.0, "error": None,
    }
    lease = {"snapshot_id": "a" * 32, "snapshot_size": 17,
             "snapshot_sha256": "b" * 64}
    namespace = {
        "Path": Path, "time": time, "re": re, "uuid": uuid, "hmac": hmac,
        "threading": SimpleNamespace(Thread=DeferredThread),
        "_data_sync_sqlite_snapshot_condition": condition,
        "_data_sync_sqlite_snapshot_states": {
            (str(source.resolve()), request_id,
             inventory_generation_id, inventory_sha256): state,
        },
        "_data_sync_sqlite_snapshot_build_slots": threading.BoundedSemaphore(1),
        "_DATA_SYNC_SQLITE_SNAPSHOT_RETRY_SECONDS": 2,
        "_DATA_SYNC_SQLITE_SNAPSHOT_CACHE_SECONDS": 900,
        "_data_sync_authorize_sqlite_snapshot_request": lambda path, generation, digest, identity: {"path": path.name},
        "_data_sync_unlink_sqlite_snapshot_artifact": lambda snapshot_id: True,
        "_data_sync_sweep_sqlite_snapshot_artifacts": lambda protected: 0,
        "_data_sync_sqlite_snapshot_deadline_seconds": lambda: 60,
        "_data_sync_sqlite_snapshot": lambda path, deadline_monotonic=None: (
            builds.append(path) or lease.copy()
        ),
        "_data_sync_sqlite_snapshot_subprocess": lambda path, deadline_at=None, snapshot_id=None: (
            builds.append(path) or lease.copy()
        ),
        "_data_sync_resolve_sqlite_snapshot": lambda snapshot_id: source,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    request_snapshot = namespace["_data_sync_request_sqlite_snapshot"]
    first = request_snapshot(source, request_id, inventory_generation_id, inventory_sha256)
    second = request_snapshot(source, request_id, inventory_generation_id, inventory_sha256)
    assert first["snapshot_status"] == "BUILDING"
    assert second["snapshot_status"] == "BUILDING"
    assert len(started) == 1
    assert builds == []
    started[0].target(*started[0].args)
    current = request_snapshot(source, request_id, inventory_generation_id, inventory_sha256)
    assert current["snapshot_status"] == "CURRENT"
    assert current["snapshot_id"] == lease["snapshot_id"]
    assert current["request_id"] == request_id
    assert re.fullmatch(r"[0-9a-f]{32}", current["build_id"])
    assert builds == [source]
    assert len(started) == 1
    Path(f"{source}-wal").write_bytes(b"new-wal-evidence")
    changed_request_id = "d" * 32
    changed = request_snapshot(
        source, changed_request_id, inventory_generation_id, inventory_sha256,
    )
    assert changed["snapshot_status"] == "BUILDING"
    assert len(started) == 2


def test_sqlite_snapshot_single_flight_is_isolated_per_canonical_path(tmp_path):
    first_source = (tmp_path / "first.db").resolve()
    second_source = (tmp_path / "second.db").resolve()
    first_source.write_bytes(b"first-generation")
    second_source.write_bytes(b"second-generation")
    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_sqlite_generation", "_data_sync_sqlite_snapshot_worker",
        "_data_sync_request_sqlite_snapshot",
    }
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in wanted]
    started = []
    builds = []

    class DeferredThread:
        def __init__(self, *, target, args, name, daemon):
            self.target, self.args, self.started = target, args, False

        def start(self):
            self.started = True
            started.append(self)

        def is_alive(self):
            return self.started

    leases = {
        first_source: {"snapshot_id": "a" * 32, "snapshot_size": 16,
                       "snapshot_sha256": "b" * 64},
        second_source: {"snapshot_id": "c" * 32, "snapshot_size": 17,
                        "snapshot_sha256": "d" * 64},
    }
    namespace = {
        "Path": Path, "time": time, "re": re, "uuid": uuid, "hmac": hmac,
        "threading": SimpleNamespace(Thread=DeferredThread),
        "_data_sync_sqlite_snapshot_condition": threading.Condition(),
        "_data_sync_sqlite_snapshot_states": {},
        "_data_sync_sqlite_snapshot_build_slots": threading.BoundedSemaphore(1),
        "_DATA_SYNC_SQLITE_SNAPSHOT_RETRY_SECONDS": 2,
        "_DATA_SYNC_SQLITE_SNAPSHOT_CACHE_SECONDS": 900,
        "_data_sync_authorize_sqlite_snapshot_request": lambda path, generation, digest, identity: {"path": path.name},
        "_data_sync_unlink_sqlite_snapshot_artifact": lambda snapshot_id: True,
        "_data_sync_sweep_sqlite_snapshot_artifacts": lambda protected: 0,
        "_data_sync_sqlite_snapshot_deadline_seconds": lambda: 60,
        "_data_sync_sqlite_snapshot_subprocess": lambda path, deadline_at=None, snapshot_id=None: (
            builds.append(path) or leases[path].copy()
        ),
        "_data_sync_resolve_sqlite_snapshot": lambda snapshot_id: first_source,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    request_snapshot = namespace["_data_sync_request_sqlite_snapshot"]

    first_request = "e" * 32
    second_request = "f" * 32
    inventory_generation_id = "7" * 64
    inventory_sha256 = "7" * 64
    assert request_snapshot(first_source, first_request, inventory_generation_id, inventory_sha256)["snapshot_status"] == "BUILDING"
    assert request_snapshot(first_source, first_request, inventory_generation_id, inventory_sha256)["snapshot_status"] == "BUILDING"
    assert request_snapshot(second_source, second_request, inventory_generation_id, inventory_sha256)["snapshot_status"] == "BUILDING"
    assert len(started) == 2
    assert set(namespace["_data_sync_sqlite_snapshot_states"]) == {
        (str(first_source), first_request, inventory_generation_id, inventory_sha256),
        (str(second_source), second_request, inventory_generation_id, inventory_sha256),
    }
    started[0].target(*started[0].args)
    started[1].target(*started[1].args)
    assert request_snapshot(first_source, first_request, inventory_generation_id, inventory_sha256)["snapshot_id"] == "a" * 32
    assert request_snapshot(second_source, second_request, inventory_generation_id, inventory_sha256)["snapshot_id"] == "c" * 32
    assert builds == [first_source, second_source]


def test_sqlite_snapshot_request_flight_survives_hot_wal_and_replays(tmp_path):
    source = (tmp_path / "hot.db").resolve()
    source.write_bytes(b"database")
    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_sqlite_generation", "_data_sync_sqlite_snapshot_worker",
        "_data_sync_request_sqlite_snapshot",
    }
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in wanted]
    started = []
    builds = []

    class DeferredThread:
        def __init__(self, *, target, args, name, daemon):
            self.target, self.args, self.started = target, args, False

        def start(self):
            self.started = True
            started.append(self)

        def is_alive(self):
            return self.started

    def build(path, deadline_at=None, snapshot_id=None):
        builds.append(path)
        Path(f"{path}-wal").write_bytes(b"wal-advanced-during-backup")
        return {"snapshot_id": "1" * 32, "snapshot_size": 8,
                "snapshot_sha256": "2" * 64}

    namespace = {
        "Path": Path, "time": time, "re": re, "uuid": uuid, "hmac": hmac,
        "threading": SimpleNamespace(Thread=DeferredThread),
        "_data_sync_sqlite_snapshot_condition": threading.Condition(),
        "_data_sync_sqlite_snapshot_states": {},
        "_data_sync_sqlite_snapshot_build_slots": threading.BoundedSemaphore(1),
        "_DATA_SYNC_SQLITE_SNAPSHOT_RETRY_SECONDS": 2,
        "_DATA_SYNC_SQLITE_SNAPSHOT_CACHE_SECONDS": 900,
        "_data_sync_authorize_sqlite_snapshot_request": lambda path, generation, digest, identity: {"path": path.name},
        "_data_sync_unlink_sqlite_snapshot_artifact": lambda snapshot_id: True,
        "_data_sync_sweep_sqlite_snapshot_artifacts": lambda protected: 0,
        "_data_sync_sqlite_snapshot_deadline_seconds": lambda: 60,
        "_data_sync_sqlite_snapshot_subprocess": build,
        "_data_sync_resolve_sqlite_snapshot": lambda snapshot_id: source,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    request_snapshot = namespace["_data_sync_request_sqlite_snapshot"]
    request_id = "3" * 32
    inventory_generation_id = "7" * 64
    inventory_sha256 = "7" * 64

    building = request_snapshot(source, request_id, inventory_generation_id, inventory_sha256)
    assert building["request_id"] == request_id
    build_id = building["build_id"]
    assert len(started) == 1
    started[0].target(*started[0].args)

    first = request_snapshot(source, request_id, inventory_generation_id, inventory_sha256)
    replay = request_snapshot(source, request_id, inventory_generation_id, inventory_sha256)
    assert first == replay
    assert first["snapshot_status"] == "CURRENT"
    assert first["request_id"] == request_id
    assert first["build_id"] == build_id
    assert first["source_changed_during_build"] is True
    assert first["source_generation_start"] != first["source_generation_end"]
    assert builds == [source]

    next_inventory = request_snapshot(
        source, request_id, "9" * 64, "9" * 64,
    )
    assert next_inventory["snapshot_status"] == "BUILDING"
    assert next_inventory["build_id"] != build_id

    # A new logical sync must not silently reuse the prior request's lease.
    next_request = request_snapshot(
        source, "4" * 32, inventory_generation_id, inventory_sha256,
    )
    assert next_request["snapshot_status"] == "BUILDING"
    assert next_request["build_id"] != build_id
    assert len(started) == 3


def test_sqlite_snapshot_failed_request_is_terminal_but_new_request_can_build(tmp_path):
    source = (tmp_path / "failed.db").resolve()
    source.write_bytes(b"database")
    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_sqlite_generation", "_data_sync_sqlite_snapshot_worker",
        "_data_sync_request_sqlite_snapshot",
    }
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in wanted]
    started = []

    class DeferredThread:
        def __init__(self, *, target, args, name, daemon):
            self.target, self.args, self.started = target, args, False

        def start(self):
            self.started = True
            started.append(self)

        def is_alive(self):
            return self.started

    namespace = {
        "Path": Path, "time": time, "re": re, "uuid": uuid, "hmac": hmac,
        "threading": SimpleNamespace(Thread=DeferredThread),
        "_data_sync_sqlite_snapshot_condition": threading.Condition(),
        "_data_sync_sqlite_snapshot_states": {},
        "_data_sync_sqlite_snapshot_build_slots": threading.BoundedSemaphore(1),
        "_DATA_SYNC_SQLITE_SNAPSHOT_RETRY_SECONDS": 2,
        "_DATA_SYNC_SQLITE_SNAPSHOT_CACHE_SECONDS": 900,
        "_data_sync_authorize_sqlite_snapshot_request": lambda path, generation, digest, identity: {"path": path.name},
        "_data_sync_unlink_sqlite_snapshot_artifact": lambda snapshot_id: True,
        "_data_sync_sweep_sqlite_snapshot_artifacts": lambda protected: 0,
        "_data_sync_sqlite_snapshot_deadline_seconds": lambda: 60,
        "_data_sync_sqlite_snapshot_subprocess": lambda path, deadline_at=None, snapshot_id=None: (
            (_ for _ in ()).throw(TimeoutError("bounded failure"))
        ),
        "_data_sync_resolve_sqlite_snapshot": lambda snapshot_id: source,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    request_snapshot = namespace["_data_sync_request_sqlite_snapshot"]
    request_id = "5" * 32
    inventory_generation_id = "7" * 64
    inventory_sha256 = "7" * 64
    request_snapshot(source, request_id, inventory_generation_id, inventory_sha256)
    started[0].target(*started[0].args)

    failed = request_snapshot(source, request_id, inventory_generation_id, inventory_sha256)
    failed_replay = request_snapshot(source, request_id, inventory_generation_id, inventory_sha256)
    assert failed == failed_replay
    assert failed["snapshot_status"] == "FAILED"
    assert failed["request_id"] == request_id
    assert "bounded failure" in failed["error"]
    assert len(started) == 1
    assert request_snapshot(source, "6" * 32, inventory_generation_id, inventory_sha256)["snapshot_status"] == "BUILDING"
    assert len(started) == 2


def test_sqlite_snapshot_expiry_is_stable_until_new_nonce_and_inactive_eviction(tmp_path):
    source = (tmp_path / "expiry.db").resolve()
    source.write_bytes(b"database")
    request_id, generation_id = "a" * 32, "b" * 64
    build_id, snapshot_id = "c" * 32, "d" * 32
    lease = {
        "request_id": request_id, "build_id": build_id,
        "inventory_generation_id": generation_id,
        "inventory_sha256": generation_id, "source_path": str(source),
        "snapshot_id": snapshot_id, "snapshot_size": 8,
        "snapshot_sha256": "e" * 64,
    }
    state_key = (str(source), request_id, generation_id, generation_id)
    states = {state_key: {
        "status": "CURRENT", "lease": lease, "build_id": build_id,
        "worker": None, "completed_at": time.monotonic() - 1000,
        "last_accessed_at": time.monotonic() - 1000,
    }}
    started, removed = [], []

    class DeferredThread:
        def __init__(self, *, target, args, name, daemon):
            self.started = False
        def start(self):
            self.started = True
            started.append(self)
        def is_alive(self):
            return self.started

    tree = ast.parse(BOT)
    wanted = {"_data_sync_sqlite_generation", "_data_sync_request_sqlite_snapshot"}
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {
        "Path": Path, "time": time, "re": re, "uuid": uuid, "hmac": hmac,
        "threading": SimpleNamespace(Thread=DeferredThread),
        "_data_sync_sqlite_snapshot_condition": threading.Condition(),
        "_data_sync_sqlite_snapshot_states": states,
        "_DATA_SYNC_SQLITE_SNAPSHOT_RETRY_SECONDS": 2,
        "_DATA_SYNC_SQLITE_SNAPSHOT_CACHE_SECONDS": 900,
        "_data_sync_authorize_sqlite_snapshot_request": lambda path, generation, digest, identity: {"path": path.name},
        "_data_sync_unlink_sqlite_snapshot_artifact": lambda token: removed.append(token) or True,
        "_data_sync_sweep_sqlite_snapshot_artifacts": lambda protected: 0,
        "_data_sync_sqlite_snapshot_deadline_seconds": lambda: 60,
        "_data_sync_resolve_sqlite_snapshot": lambda token: source,
        "_data_sync_sqlite_snapshot_worker": lambda *args: None,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    request_snapshot = namespace["_data_sync_request_sqlite_snapshot"]
    expired = request_snapshot(source, request_id, generation_id, generation_id)
    replay = request_snapshot(source, request_id, generation_id, generation_id)
    assert expired["snapshot_status"] == replay["snapshot_status"] == "EXPIRED"
    assert expired["build_id"] == replay["build_id"] == build_id
    assert states[state_key]["lease"]["snapshot_id"] == snapshot_id
    assert removed == [] and started == []

    fresh = request_snapshot(source, "f" * 32, generation_id, generation_id)
    assert fresh["snapshot_status"] == "BUILDING"
    assert fresh["build_id"] != build_id
    assert len(started) == 1
    assert removed == []  # recent EXPIRED access protects the old artifact

    states[state_key]["last_accessed_at"] = time.monotonic() - 1000
    request_snapshot(source, "1" * 32, generation_id, generation_id)
    assert removed == [snapshot_id]


def test_sqlite_snapshot_client_binds_one_request_and_validates_server_identity():
    lease_body = SYNC_SCRIPT[
        SYNC_SCRIPT.index("function Set-SqliteSnapshotLease"):
        SYNC_SCRIPT.index("# The long-running loop already performs",)
    ]
    assert '$requestId = [guid]::NewGuid().ToString("N")' in lease_body
    assert '"&request_id=$requestId"' in lease_body
    assert '"&inventory_generation_id=$inventoryGenerationId"' in lease_body
    assert '"&inventory_sha256=$inventorySha256"' in lease_body
    assert '"&source_physical_size=$([int64]$Row.physical_size)"' in lease_body
    assert '"&source_mtime_ns=$([int64]$Row.mtime_ns)"' in lease_body
    assert '"&source_inode=$([int64]$Row.inode)"' in lease_body
    assert '"&source_consistency_mode=$([string]$Row.consistency_mode)"' in lease_body
    assert "[string]$lease.request_id -cne $requestId" in lease_body
    assert "[string]$lease.build_id -notmatch '^[0-9a-f]{32}$'" in lease_body
    assert "[string]$lease.inventory_generation_id -cne $inventoryGenerationId" in lease_body
    assert "[string]$lease.inventory_sha256 -cne $inventorySha256" in lease_body
    assert "snapshot_request_id" in lease_body
    assert "snapshot_build_id" in lease_body
    assert lease_body.count("[guid]::NewGuid()") == 1


def test_sqlite_snapshot_file_resolver_rejects_cross_flight_identity(tmp_path):
    source = (tmp_path / "source.db").resolve()
    snapshot = (tmp_path / "snapshot.db").resolve()
    snapshot.write_bytes(b"immutable")
    request_id, build_id = "1" * 32, "2" * 32
    inventory_generation_id, inventory_sha256 = "3" * 64, "3" * 64
    snapshot_id, snapshot_sha256 = "5" * 32, "6" * 64
    lease = {
        "request_id": request_id, "build_id": build_id,
        "inventory_generation_id": inventory_generation_id,
        "inventory_sha256": inventory_sha256,
        "source_path": str(source), "snapshot_id": snapshot_id,
        "snapshot_size": snapshot.stat().st_size,
        "snapshot_sha256": snapshot_sha256,
    }
    state_key = (
        str(source), request_id, inventory_generation_id, inventory_sha256,
    )
    tree = ast.parse(BOT)
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "_data_sync_resolve_sqlite_snapshot_flight"]
    namespace = {
        "Path": Path, "re": re, "hmac": hmac, "time": time,
        "_data_sync_sqlite_snapshot_condition": threading.Condition(),
        "_data_sync_sqlite_snapshot_states": {
            state_key: {"status": "CURRENT", "lease": lease,
                        "completed_at": time.monotonic() - 1000,
                        "last_accessed_at": time.monotonic() - 1000},
        },
        "_data_sync_resolve_sqlite_snapshot": lambda token: snapshot,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    resolve = namespace["_data_sync_resolve_sqlite_snapshot_flight"]
    args = [source, request_id, build_id, inventory_generation_id,
            inventory_sha256, snapshot_id, snapshot.stat().st_size, snapshot_sha256]
    assert resolve(*args) == snapshot
    assert namespace["_data_sync_sqlite_snapshot_states"][state_key][
        "last_accessed_at"
    ] > time.monotonic() - 2
    for index, replacement in (
        (1, "7" * 32), (2, "8" * 32), (3, "9" * 64),
        (4, "a" * 64), (5, "b" * 32), (7, "c" * 64),
    ):
        mismatched = list(args)
        mismatched[index] = replacement
        with pytest.raises(ValueError, match="identity mismatch"):
            resolve(*mismatched)


def test_sqlite_snapshot_file_chunks_carry_complete_flight_identity():
    assert '&snapshot_request_id=$([string]$row.snapshot_request_id)' in SYNC_SCRIPT
    assert '&snapshot_build_id=$([string]$row.snapshot_build_id)' in SYNC_SCRIPT
    assert '&inventory_generation_id=$([string]$row.snapshot_inventory_generation_id)' in SYNC_SCRIPT
    assert '&inventory_sha256=$([string]$row.snapshot_inventory_sha256)' in SYNC_SCRIPT
    file_body = BOT[BOT.index("def api_data_sync_file"):BOT.index("def _data_sync_ack_v3_identity_matches")]
    assert "_data_sync_resolve_sqlite_snapshot_flight(" in file_body
    assert "sqlite snapshot acknowledgement identity mismatch" in file_body
    assert "X-Data-Snapshot-Request-Id" in file_body
    assert "X-Data-Snapshot-Build-Id" in file_body
    assert "X-Data-Inventory-Generation-Id" in file_body
    assert "X-Data-Inventory-Sha256" in file_body
    assert '$returnedRequestId -ne [string]$row.snapshot_request_id' in SYNC_SCRIPT
    assert '$returnedBuildId -ne [string]$row.snapshot_build_id' in SYNC_SCRIPT


def test_sqlite_snapshot_authority_requires_exact_current_inventory_row(tmp_path):
    source = (tmp_path / "authority.db").resolve()
    source.write_bytes(b"authority")
    stat = source.stat()
    generation_id = "d" * 64
    row = {
        "path": "authority.db", "physical_size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "inode": int(getattr(stat, "st_ino", 0) or 0),
        "consistency_mode": "sqlite_snapshot_v1",
    }
    generation = {
        "status": "CURRENT", "ack_eligible": True,
        "storage": "memory_v1", "rows": [row],
    }
    tree = ast.parse(BOT)
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "_data_sync_authorize_sqlite_snapshot_request"]
    namespace = {
        "Path": Path, "hmac": hmac,
        "_data_sync_inventory_generation": lambda digest: generation if digest == generation_id else None,
        "_data_sync_disk_manifest_page": lambda retained, raw_cursor="": {"rows": [row]},
        "_data_sync_manifest_cursor": lambda digest, page: f"{digest}.{page}",
        "_data_sync_relpath": lambda path: path.name,
        "_data_sync_generation_matches": lambda observed, size, mtime_ns, inode: (
            observed.st_size == size and observed.st_mtime_ns == mtime_ns
            and int(getattr(observed, "st_ino", 0) or 0) == inode
        ),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    authorize = namespace["_data_sync_authorize_sqlite_snapshot_request"]
    assert authorize(source, generation_id, generation_id, row)["path"] == "authority.db"
    with pytest.raises(ValueError, match="unavailable"):
        authorize(source, "e" * 64, "e" * 64, row)
    generation["rows"] = [{**row, "path": "other.db"}]
    with pytest.raises(ValueError, match="absent"):
        authorize(source, generation_id, generation_id, row)
    generation["rows"] = [{**row, "consistency_mode": "strict_generation_v1"}]
    with pytest.raises(ValueError, match="not a SQLite snapshot"):
        authorize(source, generation_id, generation_id, row)
    generation["rows"] = [{**row, "physical_size": stat.st_size + 1}]
    with pytest.raises(ValueError, match="identity mismatch"):
        authorize(source, generation_id, generation_id, row)
    generation.update({
        "status": "CURRENT", "ack_eligible": True,
        "storage": "disk_pages_v2", "page_count": 1,
    })
    generation.pop("rows", None)
    assert authorize(source, generation_id, generation_id, row)["path"] == "authority.db"
    generation.update({"status": "STALE", "ack_eligible": False, "rows": [row]})
    with pytest.raises(ValueError, match="unavailable"):
        authorize(source, generation_id, generation_id, row)


def test_sqlite_snapshot_eviction_and_orphan_sweep_preserve_active(tmp_path):
    snapshot_root = tmp_path / ".data-sync-snapshots"
    snapshot_root.mkdir()
    expired_id, orphan_id, active_id = "1" * 32, "2" * 32, "3" * 32
    for token in (expired_id, orphan_id, active_id):
        target = snapshot_root / f"{token}.db"
        target.write_bytes(token.encode("ascii"))
        os.utime(target, (time.time() - 2000, time.time() - 2000))
    linked_id = "8" * 32
    linked_target = tmp_path / "must-survive.db"
    linked_target.write_bytes(b"outside")
    linked_path = snapshot_root / f"{linked_id}.db"
    try:
        linked_path.symlink_to(linked_target)
    except OSError:
        linked_path = None
    source = (tmp_path / "source.db").resolve()
    source.write_bytes(b"source")
    request_id, generation_id = "4" * 32, "5" * 64

    class AliveWorker:
        def is_alive(self):
            return True

    class DeferredThread:
        def __init__(self, *, target, args, name, daemon):
            self.started = False
        def start(self):
            self.started = True
        def is_alive(self):
            return self.started

    old = time.monotonic() - 2000
    states = {
        (str(source), "6" * 32, generation_id, generation_id): {
            "status": "CURRENT", "lease": {"snapshot_id": expired_id},
            "worker": None, "completed_at": old,
        },
        (str(source), "7" * 32, generation_id, generation_id): {
            "status": "BUILDING", "lease": None, "snapshot_id": active_id,
            "worker": AliveWorker(), "completed_at": 0.0,
        },
    }
    wanted = {
        "_data_sync_sqlite_generation", "_data_sync_unlink_sqlite_snapshot_artifact",
        "_data_sync_sweep_sqlite_snapshot_artifacts", "_data_sync_request_sqlite_snapshot",
    }
    tree = ast.parse(BOT)
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {
        "Path": Path, "time": time, "os": os, "re": re, "uuid": uuid,
        "hmac": hmac, "threading": SimpleNamespace(Thread=DeferredThread),
        "_data_sync_volume_root": lambda: tmp_path,
        "_data_sync_sqlite_snapshot_condition": threading.Condition(),
        "_data_sync_sqlite_snapshot_states": states,
        "_DATA_SYNC_SQLITE_SNAPSHOT_RETRY_SECONDS": 2,
        "_DATA_SYNC_SQLITE_SNAPSHOT_CACHE_SECONDS": 900,
        "_data_sync_authorize_sqlite_snapshot_request": lambda path, generation, digest, identity: {"path": path.name},
        "_data_sync_sqlite_snapshot_deadline_seconds": lambda: 60,
        "_data_sync_resolve_sqlite_snapshot": lambda snapshot_id: snapshot_root / f"{snapshot_id}.db",
        "_data_sync_sqlite_snapshot_worker": lambda *args: None,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    namespace["_data_sync_request_sqlite_snapshot"](
        source, request_id, generation_id, generation_id,
    )
    assert not (snapshot_root / f"{expired_id}.db").exists()
    assert not (snapshot_root / f"{orphan_id}.db").exists()
    assert (snapshot_root / f"{active_id}.db").is_file()
    assert linked_target.read_bytes() == b"outside"
    if linked_path is not None:
        assert linked_path.is_symlink()


def test_manifest_is_metadata_only_and_snapshot_hash_is_streamed():
    manifest_body = BOT[BOT.index("def api_data_sync_manifest"):BOT.index("@app.route('/api/data-sync/sqlite-snapshot')")]
    cache_body = BOT[BOT.index("def _data_sync_cached_inventory"):BOT.index("def _data_sync_optional_file_audit")]
    snapshot_body = BOT[BOT.index("def _data_sync_sqlite_snapshot"):BOT.index("def _data_sync_resolve_sqlite_snapshot")]
    assert "_data_sync_request_async_inventory(" in manifest_body
    assert "_data_sync_targeted_inventory(targeted_path)" in manifest_body
    assert "force_refresh=force_refresh" in manifest_body
    assert "_data_sync_inventory(include_sqlite_snapshots=False)" in cache_body
    assert 'session = _load_research_session_meta() or {}' in manifest_body
    assert '"collection_epoch_id": collection_epoch_id or None' in manifest_body
    assert '"collection_epoch_status": "BOUND" if collection_epoch_id else "UNAVAILABLE"' in manifest_body
    assert '"sqlite_snapshots_materialized": False' in manifest_body
    assert "read_bytes()" not in snapshot_body
    assert 'handle.read(1024 * 1024)' in snapshot_body
    assert "deadline_monotonic" in snapshot_body
    assert "set_progress_handler" in snapshot_body


def test_powershell_client_binds_every_sqlite_chunk_to_one_snapshot():
    assert 'requested_mode == "sqlite_snapshot_v1"' in BOT
    assert '"snapshot_id": token' in BOT
    assert 'X-Data-Snapshot-Id' in BOT
    assert '$consistencyMode -eq "sqlite_snapshot_v1"' in SYNC_SCRIPT
    assert '&snapshot_id=$([uri]::EscapeDataString([string]$row.snapshot_id))' in SYNC_SCRIPT
    assert 'SQLite snapshot identity changed while downloading $rel.' in SYNC_SCRIPT
    assert '/api/data-sync/manifest?include_snapshots=1' not in SYNC_SCRIPT
    assert "Get-CompleteDataSyncManifest -FirstPage $manifest" in SYNC_SCRIPT
    assert '/api/data-sync/sqlite-snapshot?path=' in SYNC_SCRIPT
    assert 'Set-SqliteSnapshotLease -Row $row' in SYNC_SCRIPT
    assert '$chunkLimit = 1MB' in SYNC_SCRIPT
    assert '$baseInterChunkThrottleMs = 1000' in SYNC_SCRIPT
    assert '$baseInterFileThrottleMs = 1500' in SYNC_SCRIPT
    assert '$maxAdaptiveThrottleMs = 5000' in SYNC_SCRIPT
    assert 'Start-Sleep -Milliseconds $adaptiveThrottleMs' in SYNC_SCRIPT
    assert 'Start-Sleep -Milliseconds $fileThrottleMs' in SYNC_SCRIPT


def test_sync_resource_pressure_uses_adaptive_pacing_and_fail_closed_backoff():
    helper = SYNC_BACKOFF.read_text(encoding="utf-8")
    assert "function Test-DataSyncResourcePressureError" in SYNC_SCRIPT
    assert "function Get-DataSyncRetryDelaySec" in SYNC_SCRIPT
    assert "(?:502|503)" in helper
    assert "boot(?:ing)?" in helper
    assert "15 * [Math]::Max(1, $Attempt)" in SYNC_SCRIPT
    assert "$adaptiveThrottleMs * 2" in SYNC_SCRIPT
    assert "$adaptiveThrottleMs - 100" in SYNC_SCRIPT
    assert SYNC_SCRIPT.count(
        "Get-DataSyncRetryDelaySec `"
    ) >= 2, "JSON and file transports must both back off under pressure"
    assert SYNC_SCRIPT.index("$adaptiveThrottleMs = [Math]::Min(") < SYNC_SCRIPT.index(
        '"Fly data-sync stage=file_chunk failed for path=$rel "'
    )
    assert 'SQLite snapshot checksum mismatch for $rel.' in SYNC_SCRIPT
    assert '/api/data-sync/manifest?include_snapshots=1' not in SYNC_LOOP
    assert '"sqlite_snapshots_materialized": False' in BOT
    assert 'b"/api/data-sync/sqlite-snapshot"' in BOT


def test_ephemeral_open_positions_is_explicitly_optional_not_required():
    assert '"open_positions.json",' in BOT[BOT.index("_DATA_SYNC_EXCLUDED_NAMES"):BOT.index("_DATA_SYNC_EXCLUDED_DIR_NAMES")]
    assert '"classification": "OPTIONAL_OPERATIONAL_PROJECTION"' in BOT
    assert '"canonical_replacement": "paper_lifecycle_v1.json"' in BOT
    assert '"optional_files": _data_sync_optional_file_audit()' in BOT
    # The client remains fail-closed for every row in the required file list;
    # optional classification is a server-manifest concern, not a skip rule.
    assert "$selectedFiles = @($manifest.files)" in SYNC_SCRIPT
    assert "Fly data-sync stage=file_chunk failed for path=$rel" in SYNC_SCRIPT
    assert "optional_files" not in SYNC_SCRIPT


def test_data_sync_generation_fence_rejects_every_generation_change():
    namespace = _load_bot_functions("_data_sync_generation_matches")
    matches = namespace["_data_sync_generation_matches"]
    original = SimpleNamespace(st_size=100, st_mtime_ns=200, st_ino=300)
    assert matches(original, size=100, mtime_ns=200, inode=300)
    assert not matches(original, size=101, mtime_ns=200, inode=300)
    assert not matches(original, size=100, mtime_ns=201, inode=300)
    assert not matches(original, size=100, mtime_ns=200, inode=301)


def test_only_declared_append_ledgers_use_append_prefix_mode():
    mode = _load_bot_functions("_data_sync_consistency_mode")["_data_sync_consistency_mode"]
    assert mode(Path("pipeline_events_3factor.csv")) == "append_prefix_v1"
    assert mode(Path("signal_snapshot.csv")) == "strict_generation_v1"
    assert mode(Path("research_events_v22.jsonl")) == "append_prefix_v1"


def test_v22_prefix_growth_is_allowed_but_inode_replacement_is_fenced():
    namespace = _load_bot_functions("_data_sync_append_prefix_matches")
    matches = namespace["_data_sync_append_prefix_matches"]
    current = SimpleNamespace(st_size=12_000_000, st_ino=41)
    assert matches(current, minimum_size=10_000_000, inode=41)
    assert not matches(current, minimum_size=10_000_000, inode=42)


def test_serialized_jsonl_writer_targets_are_append_prefix_eligible(tmp_path):
    namespace = _load_bot_functions("_data_sync_consistency_mode")
    target = (tmp_path / "reversal_study.jsonl").resolve()
    namespace["_jsonl_serialized_append_targets"] = {str(target)}
    assert namespace["_data_sync_consistency_mode"](target) == "append_prefix_v1"
    assert namespace["_data_sync_consistency_mode"](
        tmp_path / "unregistered.jsonl"
    ) == "strict_generation_v1"


def test_signal_snapshot_rewrite_stays_strict_even_when_append_registered(tmp_path):
    namespace = _load_bot_functions("_data_sync_consistency_mode")
    target = (tmp_path / "signal_snapshot.jsonl").resolve()
    namespace["SIGNAL_SNAPSHOT_FILE"] = str(target)
    namespace["_jsonl_serialized_append_targets"] = {str(target)}
    assert namespace["_data_sync_consistency_mode"](target) == "strict_generation_v1"


def test_signal_snapshot_patch_and_append_share_the_canonical_path_lock():
    patch_body = BOT[
        BOT.index("def patch_signal_snapshot_outcome("):
        BOT.index("def log_signal_snapshot(")
    ]
    assert "with _jsonl_path_lock(SIGNAL_SNAPSHOT_FILE), signal_snapshot_lock:" in patch_body


def test_signal_snapshot_concurrent_append_survives_outcome_patch(tmp_path):
    tree = ast.parse(BOT)
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "patch_signal_snapshot_outcome"
    )
    target = tmp_path / "signal_snapshot.jsonl"
    target.write_text('{"trade_id":"first","executed":false}\n', encoding="utf-8")
    path_lock = threading.RLock()
    replacement_started = threading.Event()

    def atomic_replace(path, write_fn, _file_lock, _label):
        temp = str(path) + ".test.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            write_fn(handle)
        replacement_started.set()
        time.sleep(0.05)
        os.replace(temp, path)
        return True

    namespace = {
        "os": os,
        "json": json,
        "time": time,
        "SIGNAL_SNAPSHOT_FILE": str(target),
        "signal_snapshot_lock": threading.RLock(),
        "_jsonl_path_lock": lambda _path: path_lock,
        "_atomic_file_replace": atomic_replace,
        "logger": SimpleNamespace(error=lambda *_args: None, warning=lambda *_args: None),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), "bot.py", "exec"), namespace)

    patch_thread = threading.Thread(
        target=namespace["patch_signal_snapshot_outcome"],
        args=("first",),
        kwargs={"executed": True},
    )

    def append_second():
        assert replacement_started.wait(1)
        with path_lock, target.open("a", encoding="utf-8") as handle:
            handle.write('{"trade_id":"second","executed":false}\n')

    append_thread = threading.Thread(target=append_second)
    patch_thread.start()
    append_thread.start()
    patch_thread.join(2)
    append_thread.join(2)
    assert not patch_thread.is_alive()
    assert not append_thread.is_alive()
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert rows == [
        {"trade_id": "first", "executed": True, "outcome": rows[0]["outcome"]},
        {"trade_id": "second", "executed": False},
    ]


def test_every_static_serialized_jsonl_target_is_declared_before_first_write():
    tree = ast.parse(BOT)
    declared_constants = set()
    declared_literals = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Tuple):
            continue
        values = {
            item.value for item in node.value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
        if target.id == "_JSONL_SERIALIZED_APPEND_CONSTANTS":
            declared_constants = values
        elif target.id == "_JSONL_SERIALIZED_APPEND_LITERALS":
            declared_literals = values

    observed_constants = set()
    observed_literals = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "_safe_append_jsonl" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Name) and first.id != "output_file":
            observed_constants.add(first.id)
        elif isinstance(first, ast.Constant) and isinstance(first.value, str):
            observed_literals.add(first.value)

    assert observed_constants <= declared_constants
    assert observed_literals <= declared_literals
    assert "FILL_QUALITY_FILE" in declared_constants
    assert "TYPE_B_RESEARCH_V2_EVENT_FILE" not in declared_constants
    assert "execution_funnel.jsonl" in declared_literals
    assert "retired_lane_violations.jsonl" not in declared_literals


def test_dynamic_csv_schema_expansion_is_an_atomic_inode_change():
    tree = ast.parse(BOT)
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in {
            "_atomic_write_csv_rows", "_dynamic_csv_writer_once"
        }
    ]
    namespace = {
        "os": os, "csv": __import__("csv"), "threading": threading,
        "safe_csv_row": lambda row: dict(row),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "pipeline_events_3factor.csv"
        namespace["_dynamic_csv_writer_once"](str(target), {"a": 1})
        first_inode = target.stat().st_ino
        namespace["_dynamic_csv_writer_once"](str(target), {"a": 2, "b": 3})
        assert target.stat().st_ino != first_inode
        rows = list(__import__("csv").DictReader(target.open(encoding="utf-8")))
        assert rows == [{"a": "1", "b": ""}, {"a": "2", "b": "3"}]


def test_trend_health_and_golden_stack_use_serialized_ledger_writers():
    assert "dynamic_csv_writer(TREND_HEALTH_CSV_FILE" in BOT
    assert '_safe_append_jsonl(GOLDEN_STACK_REJECTIONS_FILE, row, label="GS_REJECTION")' in BOT
    assert 'label="GS_REJECTION_OUTCOME"' in BOT
    assert '"trend_health.csv"' in BOT.split("_DATA_SYNC_APPEND_PREFIX_NAMES", 1)[1].split("})", 1)[0]


def test_append_prefix_mode_is_narrow_and_allows_only_same_inode_growth():
    namespace = _load_bot_functions(
        "_data_sync_consistency_mode", "_data_sync_append_prefix_matches"
    )
    mode = namespace["_data_sync_consistency_mode"]
    matches = namespace["_data_sync_append_prefix_matches"]
    assert mode(Path("bot_runtime.log")) == "append_prefix_v1"
    assert mode(Path("research_events_v22.jsonl")) == "append_prefix_v1"
    assert mode(Path("trades_3factor.csv")) == "append_prefix_v1"
    assert mode(Path("state.json")) == "strict_generation_v1"
    grown = SimpleNamespace(st_size=130, st_mtime_ns=999, st_ino=7)
    assert matches(grown, minimum_size=100, inode=7)
    assert not matches(grown, minimum_size=131, inode=7)
    assert not matches(grown, minimum_size=100, inode=8)


def _load_jsonl_writer(tmp_path):
    tree = ast.parse(BOT)
    wanted = {
        "_jsonl_path_lock", "_jsonl_validation_signature",
        "_jsonl_validation_tail_sha256", "_jsonl_validation_receipt_path",
        "_fsync_jsonl_validation_parent",
        "_persist_jsonl_validation_receipt", "_jsonl_validation_receipt_matches",
        "_validate_or_quarantine_jsonl", "_safe_append_jsonl",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]

    class _Logger:
        def __init__(self):
            self.messages = []

        def error(self, message):
            self.messages.append(str(message))

        def warning(self, message, *args):
            self.messages.append(str(message) % args if args else str(message))

    namespace = {
        "os": os,
        "json": json,
        "time": time,
        "uuid": uuid,
        "hashlib": hashlib,
        "threading": threading,
        "datetime": datetime,
        "timezone": timezone,
        "CSV_WRITE_RETRIES": 3,
        "CSV_WRITE_RETRY_BASE_SEC": 0,
        "_jsonl_append_locks_guard": threading.Lock(),
        "_jsonl_append_locks": {},
        "_jsonl_validated_targets": {},
        "_JSONL_VALIDATION_TAIL_BYTES": 64 * 1024,
        "_jsonl_serialized_append_targets": set(),
        "rotate_log": lambda _path: None,
        "_transient_csv_lock_error": lambda _error: False,
        "_csv_write_fallback": lambda *_args: None,
        "_data_sync_runtime_root": lambda: tmp_path,
        "emergency_admission": lambda **_kwargs: {
            "allowed": True,
            "reason": None,
            "threshold": 0.90,
        },
        "utc_iso": lambda: datetime.now(timezone.utc).isoformat(),
        "logger": _Logger(),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace


def test_jsonl_writer_serializes_concurrent_rows(tmp_path):
    namespace = _load_jsonl_writer(tmp_path)
    append = namespace["_safe_append_jsonl"]
    target = tmp_path / "shadow_lane_outcome.jsonl"
    threads = [
        threading.Thread(target=append, args=(str(target), {"row": index}, "SHADOW"))
        for index in range(32)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 32
    assert {row["row"] for row in rows} == set(range(32))


def test_jsonl_writer_distinguishes_admission_suppression_from_write_failure(tmp_path):
    suppressed = _load_jsonl_writer(tmp_path)
    suppressed["emergency_admission"] = lambda **_kwargs: {
        "allowed": False,
        "reason": "NEW_NONESSENTIAL_RESEARCH_BLOCKED_AT_STORAGE_EMERGENCY",
        "threshold": 0.90,
    }
    admission_outcome = {}
    assert not suppressed["_safe_append_jsonl"](
        str(tmp_path / "suppressed.jsonl"), {"row": 1},
        "MARKET_MICROSTRUCTURE_1S", outcome=admission_outcome,
    )
    assert admission_outcome == {"status": "ADMISSION_SUPPRESSED"}

    failed = _load_jsonl_writer(tmp_path)
    failed["rotate_log"] = lambda _path: (_ for _ in ()).throw(OSError("disk fault"))
    write_outcome = {}
    assert not failed["_safe_append_jsonl"](
        str(tmp_path / "failed.jsonl"), {"row": 1},
        "MARKET_MICROSTRUCTURE_1S", fallback_on_error=False,
        outcome=write_outcome,
    )
    assert write_outcome == {"status": "WRITE_FAILED"}


def test_jsonl_writer_quarantines_corrupt_bytes_with_receipt(tmp_path):
    namespace = _load_jsonl_writer(tmp_path)
    target = tmp_path / "shadow_lane_outcome.jsonl"
    corrupt = b'{"row": 1}\n{"row":'
    target.write_bytes(corrupt)

    assert namespace["_safe_append_jsonl"](str(target), {"row": 2}, "SHADOW")
    assert [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()] == [
        {"row": 2}
    ]

    quarantine_dirs = list((tmp_path / "corrupt_evidence_quarantine").iterdir())
    assert len(quarantine_dirs) == 1
    preserved = quarantine_dirs[0] / target.name
    receipt = json.loads(
        (quarantine_dirs[0] / "quarantine_manifest.json").read_text(encoding="utf-8")
    )
    assert preserved.read_bytes() == corrupt
    assert receipt["complete"] is True
    assert receipt["bad_line"] == 2
    assert receipt["size_bytes"] == len(corrupt)
    assert receipt["sha256"] == hashlib.sha256(corrupt).hexdigest()
    assert receipt["preserved_path"] == target.name


def test_jsonl_writer_restart_uses_durable_bounded_validation_receipt(tmp_path):
    target = tmp_path / "ai_input_log.jsonl"
    target.write_text(
        "".join(json.dumps({"row": index}) + "\n" for index in range(5000)),
        encoding="utf-8",
    )
    first = _load_jsonl_writer(tmp_path)
    assert first["_safe_append_jsonl"](str(target), {"row": 5000}, "AI_INPUT")
    assert Path(str(target) + ".validation.json").is_file()

    # A fresh namespace models a watchdog/process restart with an empty memory
    # cache. Loading the small receipt may decode once; historical rows must
    # not be decoded again.
    restarted = _load_jsonl_writer(tmp_path)
    original_loads = restarted["json"].loads
    decoded = []

    def _counting_loads(value, *args, **kwargs):
        decoded.append(len(value))
        return original_loads(value, *args, **kwargs)

    restarted["json"].loads = _counting_loads
    try:
        assert restarted["_safe_append_jsonl"](
            str(target), {"row": 5001}, "AI_INPUT"
        )
    finally:
        restarted["json"].loads = original_loads
    assert len(decoded) <= 2
    assert len(target.read_text(encoding="utf-8").splitlines()) == 5002


def test_jsonl_writer_external_mutation_invalidates_receipt_and_quarantines(tmp_path):
    namespace = _load_jsonl_writer(tmp_path)
    target = tmp_path / "ai_input_log.jsonl"
    assert namespace["_safe_append_jsonl"](str(target), {"row": 1}, "AI_INPUT")

    target.write_bytes(b'{"row": X}\n')
    assert namespace["_safe_append_jsonl"](str(target), {"row": 2}, "AI_INPUT")
    assert [json.loads(line) for line in target.read_text().splitlines()] == [{"row": 2}]
    quarantine_dirs = list((tmp_path / "corrupt_evidence_quarantine").iterdir())
    assert len(quarantine_dirs) == 1
    receipt = json.loads(
        (quarantine_dirs[0] / "quarantine_manifest.json").read_text()
    )
    assert receipt["bad_line"] == 1


def test_jsonl_writer_external_truncation_invalidates_receipt(tmp_path):
    namespace = _load_jsonl_writer(tmp_path)
    target = tmp_path / "ai_input_log.jsonl"
    assert namespace["_safe_append_jsonl"](str(target), {"row": 1}, "AI_INPUT")
    assert namespace["_safe_append_jsonl"](str(target), {"row": 2}, "AI_INPUT")

    target.write_bytes(b'{"row":')
    assert namespace["_safe_append_jsonl"](str(target), {"row": 3}, "AI_INPUT")
    assert [json.loads(line) for line in target.read_text().splitlines()] == [{"row": 3}]
    quarantine_dirs = list((tmp_path / "corrupt_evidence_quarantine").iterdir())
    assert len(quarantine_dirs) == 1
    assert (quarantine_dirs[0] / target.name).read_bytes() == b'{"row":'


def test_jsonl_writer_restored_mtime_prefix_mutation_invalidates_on_ctime(tmp_path):
    namespace = _load_jsonl_writer(tmp_path)
    target = tmp_path / "ai_input_log.jsonl"
    target.write_text(
        "".join(json.dumps({"row": index, "payload": "x" * 80}) + "\n"
                for index in range(2000)),
        encoding="utf-8",
    )
    assert namespace["_safe_append_jsonl"](
        str(target), {"row": 2000, "payload": "x" * 80}, "AI_INPUT"
    )
    before = target.stat()
    content = bytearray(target.read_bytes())
    mutation_at = content.find(b'"row": 100')
    assert 0 <= mutation_at < len(content) - 64 * 1024
    content[mutation_at + len(b'"row": ')] = ord("X")
    target.write_bytes(content)
    os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

    # Windows exposes creation time as st_ctime, while production Linux exposes
    # inode change time. Model the production stat transition explicitly when
    # the host cannot provide it so this regression remains cross-platform.
    real_signature = namespace["_jsonl_validation_signature"]
    current = real_signature(str(target))
    if current[4] == before.st_ctime_ns:
        namespace["_jsonl_validation_signature"] = (
            lambda path: real_signature(path)[:4] + (before.st_ctime_ns + 1,)
        )

    assert namespace["_safe_append_jsonl"](
        str(target), {"row": 2001, "payload": "x" * 80}, "AI_INPUT"
    )
    quarantine_dirs = list((tmp_path / "corrupt_evidence_quarantine").iterdir())
    assert len(quarantine_dirs) == 1
    receipt = json.loads(
        (quarantine_dirs[0] / "quarantine_manifest.json").read_text()
    )
    assert receipt["bad_line"] == 101
    assert [json.loads(line) for line in target.read_text().splitlines()] == [
        {"row": 2001, "payload": "x" * 80}
    ]


def test_jsonl_receipt_failure_never_retries_a_durable_row(tmp_path):
    namespace = _load_jsonl_writer(tmp_path)
    target = tmp_path / "ai_input_log.jsonl"
    original_persist = namespace["_persist_jsonl_validation_receipt"]
    persist_calls = []

    def _fail_receipt(*_args):
        persist_calls.append(True)
        raise OSError("simulated receipt failure")

    namespace["_persist_jsonl_validation_receipt"] = _fail_receipt
    assert namespace["_safe_append_jsonl"](str(target), {"row": 1}, "AI_INPUT")
    assert persist_calls == [True]
    assert [json.loads(line) for line in target.read_text().splitlines()] == [{"row": 1}]

    # Receipt failure invalidates the memory cache. The next append must parse
    # the existing row before proceeding, then restore the durable receipt.
    namespace["_persist_jsonl_validation_receipt"] = original_persist
    original_loads = namespace["json"].loads
    decoded = []

    def _counting_loads(value, *args, **kwargs):
        decoded.append(value)
        return original_loads(value, *args, **kwargs)

    namespace["json"].loads = _counting_loads
    try:
        assert namespace["_safe_append_jsonl"](str(target), {"row": 2}, "AI_INPUT")
    finally:
        namespace["json"].loads = original_loads
    assert decoded
    assert [json.loads(line) for line in target.read_text().splitlines()] == [
        {"row": 1}, {"row": 2}
    ]


def test_jsonl_writer_is_serialized_and_corruption_is_preserved_not_deleted():
    assert "with _jsonl_path_lock(path):" in BOT
    assert "_validate_or_quarantine_jsonl(path, label)" in BOT
    assert '"schema": "corrupt_jsonl_quarantine_v1"' in BOT
    assert '"sha256": digest.hexdigest()' in BOT
    assert "os.replace(key, target)" in BOT
    assert '"corrupt_evidence_quarantine"' in BOT
    assert "os.fsync(f.fileno())" in BOT


def test_all_direct_append_opens_are_bounded_writer_internals_or_diagnostics():
    """Research ledgers must use the shared serialized JSONL/CSV writers."""
    tree = ast.parse(BOT)
    direct_append_functions = set()
    for function in (
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            if not isinstance(call.func, ast.Name) or call.func.id != "open":
                continue
            mode = None
            if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
                mode = call.args[1].value
            for keyword in call.keywords:
                if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
                    mode = keyword.value.value
            if isinstance(mode, str) and "a" in mode:
                direct_append_functions.add(function.name)
    assert direct_append_functions == {
        "_agent_dbg",                 # bounded ordinary diagnostic log
        "_dynamic_csv_writer_once",   # serialized by dynamic_csv_writer/csv_lock
        "_safe_append_jsonl",         # serialized by the per-path JSONL lock
        "dump_system_state",          # crash diagnostics, not research evidence
    }


def test_csv_fallback_uses_non_recursive_serialized_jsonl_writer():
    assert '_safe_append_jsonl(\n            CSV_FALLBACK_JSONL' in BOT
    assert 'label="CSV_FALLBACK", fallback_on_error=False' in BOT
    assert "if fallback_on_error:\n        _csv_write_fallback(path, row, last_err)" in BOT
    assert "_jsonl_serialized_append_targets.add(os.path.abspath(path))" in BOT


def test_sync_refetches_only_a_bounded_changed_generation_from_byte_zero():
    assert "$generationRefreshCount = 0" in SYNC_SCRIPT
    assert "$generationRefreshCount -lt 3" in SYNC_SCRIPT
    assert "-match 'generation changed'" in SYNC_SCRIPT
    assert '-Stage "manifest_targeted_refresh"' in SYNC_SCRIPT
    assert '-Uri (New-DataSyncManifestUri -Path $rel) `' in SYNC_SCRIPT
    assert "Get-CompleteDataSyncManifest -FirstPage $manifest" in SYNC_SCRIPT
    assert 'Where-Object { [string]$_.path -eq $rel }' in SYNC_SCRIPT
    assert "$sameGeneration = $false" in SYNC_SCRIPT
    assert "$fullReplaceRetry = $true" in SYNC_SCRIPT
    assert "restarting the candidate from byte zero" in SYNC_SCRIPT
    # Other 409s (mode mismatch, unsafe path, malformed request) must continue
    # to fail closed rather than being hidden by a broad retry.
    assert "-match '^Fly sync HTTP 409 '" in SYNC_SCRIPT


def test_sync_prioritizes_and_can_refresh_short_lived_sqlite_snapshot_leases():
    assert '$selectedFiles | Sort-Object' in SYNC_SCRIPT
    assert 'if ([string]$_.consistency_mode -eq "sqlite_snapshot_v1") { 0 } else { 1 }' in SYNC_SCRIPT
    assert '$sqliteLeaseRefreshRequired = (' in SYNC_SCRIPT
    assert '$consistencyMode -eq "sqlite_snapshot_v1"' in SYNC_SCRIPT
    assert "-match '^Fly sync HTTP 409 '" in SYNC_SCRIPT
    assert 'sqlite snapshot (?:is unavailable or expired|flight identity mismatch|' in SYNC_SCRIPT
    assert 'acknowledgement identity mismatch)' in SYNC_SCRIPT
    assert '($generationChanged -or $sqliteLeaseRefreshRequired)' in SYNC_SCRIPT
    assert 'Set-SqliteSnapshotLease -Row $row' in SYNC_SCRIPT


def test_shadow_jsonl_is_validated_before_startup_collection_and_sync():
    assert "def _validate_research_ledgers_on_startup():" in BOT
    assert '(SHADOW_LANE_OUTCOME_FILE, "SHADOW_LANE_STARTUP")' in BOT
    assert '(SIGNAL_REPLAY_FILE, "SIGNAL_REPLAY_STARTUP")' in BOT
    assert '_safe_append_jsonl(SIGNAL_REPLAY_FILE, replay, label="SIGNAL_REPLAY")' in BOT
    main_start = BOT.index("def main():")
    assert BOT.index("_validate_research_ledgers_on_startup()", main_start) < BOT.index(
        "_restore_collector_v22_provisionals()", main_start
    )


def test_fly_runtime_cwd_is_volume_backed():
    assert 'RUNTIME_DIR="$DATA_DIR/runtime"' in ENTRYPOINT
    assert 'export BOT_SINGLETON_DIR="$DATA_DIR/locks"' in ENTRYPOINT
    assert 'cd "$RUNTIME_DIR"' in ENTRYPOINT
    assert "python /app/btc_conservative_agent.py" in ENTRYPOINT
    assert "python btc_conservative_agent.py" not in ENTRYPOINT


def test_incremental_sync_is_authenticated_and_chunk_verified():
    assert "@app.route('/api/data-sync/manifest')" in BOT
    assert "@app.route('/api/data-sync/file')" in BOT
    assert "@app.route('/api/data-sync/ack', methods=['POST'])" in BOT
    assert 'response.headers["X-Chunk-Sha256"]' in BOT
    assert '"physical_size": int(stat.st_size)' in BOT
    assert '"file generation changed after manifest"' in BOT
    assert '"file generation changed during download"' in BOT
    assert "/api/data-sync/manifest" not in BOT[BOT.index("_READ_ONLY_GET_PATHS"):BOT.index("def _client_ip")]
    assert '"X-Bot-Admin-Token" = $AdminToken' in SYNC_SCRIPT
    assert "Chunk checksum mismatch" in SYNC_SCRIPT
    assert "Fly data-sync stage=file_chunk failed for path=$rel" in SYNC_SCRIPT
    assert "offset=$offset" in SYNC_SCRIPT and "limit=$limit" in SYNC_SCRIPT
    assert "$attempt/$transportAttempts attempt(s): $($_.Exception.Message)" in SYNC_SCRIPT
    assert "expected_physical_size=$expectedPhysicalSize" in SYNC_SCRIPT
    assert "expected_published_size=$expectedPublishedSize" in SYNC_SCRIPT
    assert "consistency_mode=$consistencyMode" in SYNC_SCRIPT
    assert "expected_mtime_ns=$expectedMtime" in SYNC_SCRIPT
    assert "expected_inode=$expectedInode" in SYNC_SCRIPT
    assert "$chunkLimit = 1MB" in SYNC_SCRIPT
    assert "Start-Sleep -Milliseconds $adaptiveThrottleMs" in SYNC_SCRIPT
    assert '$appendOnly = $extension -in @(".jsonl", ".csv", ".log", ".txt")' in SYNC_SCRIPT
    assert "[int64]$previous.mtime_ns -eq [int64]$row.mtime_ns" in SYNC_SCRIPT
    assert "def _data_sync_rotation_parts" in BOT
    assert '"consistency_mode": _data_sync_consistency_mode(resolved)' in BOT
    assert 'path.name in _DATA_SYNC_APPEND_PREFIX_NAMES' in BOT
    assert 'return "append_prefix_v1" if append_prefix' in BOT
    assert 'limit = min(limit, max(0, published_boundary - offset))' in BOT
    assert "_data_sync_rotation_parts(resolved.name) is not None" in BOT
    assert 'path.startswith("/api/data-sync/")' in BOT
    assert "and not is_authenticated_data_sync" in BOT
    assert "@app.route('/api/data-sync/platform-relay-evidence', methods=['POST'])" in BOT
    assert "def _validate_platform_relay_evidence_payload" in BOT
    assert "os.replace(staged, destination)" in BOT


def test_local_mirror_download_is_validated_then_atomically_published():
    assert '. (Join-Path $scriptDir "fly-mirror-atomic.ps1")' in SYNC_SCRIPT
    assert "Test-MirrorCandidate `" in SYNC_SCRIPT
    assert "-Path $candidate `" in SYNC_SCRIPT
    assert "Publish-MirrorCandidate -Candidate $candidate -Destination $local" in SYNC_SCRIPT
    assert "[System.IO.File]::Copy($local, $candidate, $true)" in SYNC_SCRIPT
    assert "if (-not ($sameGeneration -and $localSize -eq $remoteSize))" in SYNC_SCRIPT
    assert "retrying as a complete atomic replace without deleting prior valid records." in SYNC_SCRIPT
    assert "$fullReplaceRetry = $true" in SYNC_SCRIPT
    assert "[System.IO.File]::Replace($Candidate, $Destination" in ATOMIC_HELPER.read_text(encoding="utf-8")
    assert "Remove-Item -LiteralPath $local -Force" not in SYNC_SCRIPT


def test_hot_small_strict_document_uses_verified_single_read_snapshot_only():
    assert '$atomicSnapshotFallback = (' in SYNC_SCRIPT
    assert '$ForceFullRefresh -and' in SYNC_SCRIPT
    assert '$generationRefreshCount -ge 3' not in SYNC_SCRIPT
    assert '$consistencyMode -eq "strict_generation_v1"' in SYNC_SCRIPT
    assert '$remoteSize -le $chunkLimit' in SYNC_SCRIPT
    assert 'if (-not $atomicSnapshotFallback)' in SYNC_SCRIPT
    assert 'X-Data-Size' in SYNC_SCRIPT
    assert 'X-Data-Mtime-Ns' in SYNC_SCRIPT
    assert 'X-Data-Inode' in SYNC_SCRIPT
    assert '$payload.Length -ne $snapshotSize' in SYNC_SCRIPT
    # Raw/multi-chunk evidence is never admitted to the relaxed one-read path.
    assert 'Never use this for multi-chunk/raw evidence streams.' in SYNC_SCRIPT


def test_invalid_jsonl_candidate_preserves_previous_mirror_and_valid_candidate_replaces_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        destination = root / "signal_snapshot.jsonl"
        invalid = root / "invalid.download"
        valid = root / "valid.download"
        destination.write_text('{"trade_id":"old"}\n', encoding="utf-8")
        invalid.write_text('{"trade_id":"partial"}', encoding="utf-8")
        valid.write_text('{"trade_id":"new"}\n', encoding="utf-8")
        command = (
            f". '{ATOMIC_HELPER}'; "
            f"$dest='{destination}'; $invalid='{invalid}'; $valid='{valid}'; "
            "$failed=$false; try { Test-MirrorCandidate -Path $invalid -RelativePath 'signal_snapshot.jsonl'; "
            "Publish-MirrorCandidate -Candidate $invalid -Destination $dest } catch { $failed=$true }; "
            "$before=[IO.File]::ReadAllText($dest); "
            "Test-MirrorCandidate -Path $valid -RelativePath 'signal_snapshot.jsonl'; "
            "Publish-MirrorCandidate -Candidate $valid -Destination $dest; "
            "$after=[IO.File]::ReadAllText($dest); "
            "@{failed=$failed;before=$before;after=$after}|ConvertTo-Json -Compress"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(completed.stdout.strip())
    result = {key: value.replace("\r\n", "\n") if isinstance(value, str) else value for key, value in result.items()}
    assert result == {
        "after": '{"trade_id":"new"}\n',
        "before": '{"trade_id":"old"}\n',
        "failed": True,
    }


def test_corrupt_quarantine_first_transfer_accepts_contiguous_authenticated_chunks():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        candidate = root / "quarantine.download"
        candidate.write_bytes(b'{"valid":true}\n{"incomplete":')
        data = candidate.read_bytes()
        first = hashlib.sha256(data[:8]).hexdigest()
        second = hashlib.sha256(data[8:]).hexdigest()
        relative = "corrupt_evidence_quarantine/repair-1/execution_funnel.jsonl"
        command = (
            f". '{ATOMIC_HELPER}'; "
            f"$receipts=@(@{{offset=0;length=8;sha256='{first}'}},"
            f"@{{offset=8;length={len(data)-8};sha256='{second}'}}); "
            f"$full=Test-OpaqueMirrorChunkReceipts -Path '{candidate}' "
            f"-ExpectedSize {len(data)} -Receipts $receipts; "
            f"Test-MirrorCandidate -Path '{candidate}' -RelativePath '{relative}' "
            f"-ExpectedSize {len(data)}"
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
        )


def test_same_incomplete_jsonl_outside_corrupt_quarantine_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "active.download"
        candidate.write_bytes(b'{"valid":true}\n{"incomplete":')
        command = (
            f". '{ATOMIC_HELPER}'; $failed=$false; try {{ "
            f"Test-MirrorCandidate -Path '{candidate}' -RelativePath 'execution_funnel.jsonl' "
            f"-ExpectedSize {candidate.stat().st_size} "
            "} catch { $failed=$true }; if(-not $failed){ exit 9 }"
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
        )


@pytest.mark.parametrize("defect", ["size", "tamper", "gap", "overlap", "missing_hash"])
def test_corrupt_quarantine_chunk_receipts_fail_closed(defect):
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / "quarantine.download"
        candidate.write_bytes(b'{"incomplete":')
        size = candidate.stat().st_size
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        offset, length = 0, size
        if defect == "size":
            size += 1
        elif defect == "tamper":
            digest = "0" * 64
        elif defect == "missing_hash":
            digest = ""
        elif defect == "gap":
            offset = 1
            length -= 1
        elif defect == "overlap":
            offset = -1
            length += 1
        command = (
            f". '{ATOMIC_HELPER}'; $failed=$false; try {{ "
            f"$receipts=@(@{{offset={offset};length={length};sha256='{digest}'}}); "
            f"Test-OpaqueMirrorChunkReceipts -Path '{candidate}' -ExpectedSize {size} -Receipts $receipts "
            "} catch { $failed=$true }; if(-not $failed){ exit 9 }"
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
        )


def test_quarantine_transfer_rebuilds_from_zero_and_reuse_requires_local_full_hash():
    quarantine_flag = SYNC_SCRIPT.index("$opaqueQuarantineEvidence =")
    candidate_copy = SYNC_SCRIPT.index("[System.IO.File]::Copy($local, $candidate", quarantine_flag)
    assert "-not $opaqueQuarantineEvidence -and" in SYNC_SCRIPT[quarantine_flag:candidate_copy]
    assert "$storedFullSha256 = [string]$previous.full_sha256" in SYNC_SCRIPT
    assert "Get-FileHash -LiteralPath $local -Algorithm SHA256" in SYNC_SCRIPT
    assert "$sameGeneration = $false" in SYNC_SCRIPT


def test_quarantine_candidate_receipts_and_full_hash_precede_publish_and_ack():
    validation = SYNC_SCRIPT.index("Test-OpaqueMirrorChunkReceipts")
    publication = SYNC_SCRIPT.index("Publish-MirrorCandidate", validation)
    acknowledgement = SYNC_SCRIPT.index('$ackSessionId = [guid]::NewGuid()', publication)
    assert validation < publication < acknowledgement
    assert "$chunkReceipts.Add" in SYNC_SCRIPT
    assert "offset = [int64]$chunkOffset" in SYNC_SCRIPT
    assert "length = [int64]$payload.Length" in SYNC_SCRIPT
    assert "$syncState[$rel].full_sha256 = $verifiedFullSha256" in SYNC_SCRIPT
    assert "$ackRow.full_sha256 = $verifiedFullSha256" in SYNC_SCRIPT
    assert "-ExpectedSize $remoteSize" in SYNC_SCRIPT


def test_legacy_crash_dump_json_is_validated_as_jsonl():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        valid = root / "valid-crash.download"
        invalid = root / "invalid-crash.download"
        valid.write_text('{"time":"first"}\n{"time":"second"}\n', encoding="utf-8")
        invalid.write_text('{"time":"first"}\n{"time":', encoding="utf-8")
        command = (
            f". '{ATOMIC_HELPER}'; "
            f"Test-MirrorCandidate -Path '{valid}' -RelativePath 'crash_dump.json'; "
            "$failed=$false; try { "
            f"Test-MirrorCandidate -Path '{invalid}' -RelativePath 'crash_dump.json' "
            "} catch { $failed=$true }; if(-not $failed){ exit 9 }"
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
        )


def test_atomic_publish_retries_a_transient_windows_reader_lock_without_losing_either_file():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        destination = root / "locked.jsonl"
        candidate = root / "candidate.download"
        marker = root / "locked.marker"
        destination.write_text('{"version":"old"}\n', encoding="utf-8")
        candidate.write_text('{"version":"new"}\n', encoding="utf-8")
        command = (
            f". '{ATOMIC_HELPER}'; $dest='{destination}'; $candidate='{candidate}'; $marker='{marker}'; "
            "$job=Start-Job -ScriptBlock { param($p,$m) "
            "$s=[IO.File]::Open($p,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read); "
            "[IO.File]::WriteAllText($m,'locked'); Start-Sleep -Milliseconds 550; $s.Dispose() "
            "} -ArgumentList $dest,$marker; "
            "$until=(Get-Date).AddSeconds(5); while (!(Test-Path -LiteralPath $marker) -and (Get-Date) -lt $until) { Start-Sleep -Milliseconds 20 }; "
            "Publish-MirrorCandidate -Candidate $candidate -Destination $dest -ReplaceAttempts 8; "
            "$job|Wait-Job|Remove-Job; [IO.File]::ReadAllText($dest)"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    assert completed.stdout.replace("\r\n", "\n").strip() == '{"version":"new"}'


def test_atomic_publish_lock_failure_names_the_destination_and_preserves_candidate():
    helper = ATOMIC_HELPER.read_text(encoding="utf-8")
    assert "Atomic mirror publish failed after $attempt attempt(s): destination=$Destination" in helper
    assert "retry only the atomic publish operation" in helper
    assert "-Attempts 12" in SYNC_SCRIPT


def test_platform_relay_evidence_validation_rejects_wrong_scope_and_duplicate_events():
    validate = _load_bot_functions("_validate_platform_relay_evidence_payload")[
        "_validate_platform_relay_evidence_payload"
    ]
    base = {
        "schema": "relay_lifecycle_evidence_v1",
        "generatedAt": "2026-08-16T00:00:00Z",
        "generatingRevision": "a" * 40,
        "runIdentity": "run-1",
        "agentSlug": "conservative-btc",
        "userId": "user-1",
        "records": [{
            "canonicalTradeId": "cont-1",
            "lifecycleId": "cycle-1",
            "participantId": "participant-1",
            "events": [{"id": "event-1", "eventType": "FILLED", "createdAt": "2026-08-16T00:00:01Z"}],
        }],
    }
    assert validate(base) == (True, "OK")
    wrong = json.loads(json.dumps(base))
    wrong["agentSlug"] = "other-agent"
    assert validate(wrong) == (False, "SCOPE_INVALID")
    duplicate = json.loads(json.dumps(base))
    duplicate["records"].append({
        "canonicalTradeId": "cont-2", "lifecycleId": "cycle-2", "participantId": "participant-2",
        "events": [{"id": "event-1", "eventType": "EXIT", "createdAt": "2026-08-16T00:00:02Z"}],
    })
    assert validate(duplicate) == (False, "DUPLICATE_EVENT")


def test_local_sync_has_fail_closed_30_gib_admission_guard():
    assert "[int]$MaxLocalMirrorGiB = 30" in SYNC_SCRIPT
    assert "$currentMirrorBytes + $incomingGrowth" in SYNC_SCRIPT
    assert "Local Fly mirror hard cap would be exceeded" in SYNC_SCRIPT
    assert "fingerprinted receipts" in SYNC_SCRIPT


def test_local_sync_archives_only_manifest_absent_top_level_raw_research_files():
    assert "stale local Fly research file" in SYNC_SCRIPT
    assert "\\.(jsonl|log|csv)(?:\\.\\d+)?$" in SYNC_SCRIPT
    assert "$manifestPaths.Contains($candidate.Name)" in SYNC_SCRIPT
    assert "Get-ChildItem -LiteralPath $targetRoot -File" in SYNC_SCRIPT
    assert "[System.IO.File]::Copy($resolvedCandidate, $temporaryArchive, $false)" in SYNC_SCRIPT
    assert "$stableSourceSha256 -cne $sourceSha256" in SYNC_SCRIPT
    assert "Promoted archive verification failed; source retained" in SYNC_SCRIPT
    assert "Remove-Item -LiteralPath $resolvedCandidate" in SYNC_SCRIPT
    assert SYNC_SCRIPT.index("Promoted archive verification failed; source retained") < SYNC_SCRIPT.index(
        "Remove-Item -LiteralPath $resolvedCandidate"
    )
    assert 'schema = "canonical_research_cleanup_receipt_v1"' in SYNC_SCRIPT
    assert 'verification = "COPY_AND_SOURCE_STABILITY_SHA256_VERIFIED_BEFORE_REMOVAL"' in SYNC_SCRIPT
    assert 'recoverable = $true' in SYNC_SCRIPT
    assert "[System.IO.File]::Delete($resolvedCandidate)" not in SYNC_SCRIPT
    assert "[void]$syncState.Remove($candidate.Name)" in SYNC_SCRIPT


def test_local_sync_never_archives_the_append_only_canonical_manifest():
    assert '$canonicalLocalFiles = [System.Collections.Generic.HashSet[string]]::new(' in SYNC_SCRIPT
    assert '@("canonical_dataset_manifest.jsonl")' in SYNC_SCRIPT
    assert "$canonicalLocalFiles.Contains($candidate.Name)" in SYNC_SCRIPT
    assert SYNC_SCRIPT.index("$canonicalLocalFiles.Contains($candidate.Name)") < SYNC_SCRIPT.index(
        "$manifestPaths.Contains($candidate.Name)"
    )


def test_sync_commits_append_first_canonical_manifest_after_completed_heartbeat():
    assert "migrate_canonical_research_store.py" in SYNC_SCRIPT
    assert "--record-existing" in SYNC_SCRIPT
    assert SYNC_SCRIPT.index("-Completed") < SYNC_SCRIPT.index("--record-existing")
    assert '$ProgressHeartbeatFile = Join-Path $targetRoot ".fly-data-sync-loop.heartbeat.json"' in SYNC_SCRIPT


def test_retention_never_removes_active_or_unacknowledged_files():
    assert "active/unacked files retained" in BOT
    assert "newest_kept = frozenset(sorted(generations)[-keep_newest:])" in BOT
    assert "if rotation_index in newest_kept" in BOT
    assert "int(ack.get(\"size\") or -1) == int(stat.st_size)" in BOT
    assert "int(ack.get(\"mtime_ns\") or -1) == int(stat.st_mtime_ns)" in BOT
    assert "volume_used_pct" in BOT


def test_ack_validation_uses_validated_inventory_without_filesystem_calls():
    validate = _load_bot_functions("_data_sync_validate_ack_rows")[
        "_data_sync_validate_ack_rows"
    ]
    inventory = {
        f"v3/ledgers/row-{index}.jsonl": {
            "path": f"v3/ledgers/row-{index}.jsonl",
            "size": index + 10,
            "mtime_ns": index + 100,
        }
        for index in range(5000)
    }
    received = [
        {"path": path, "size": row["size"], "mtime_ns": row["mtime_ns"]}
        for path, row in inventory.items()
    ]
    accepted, rejected = validate(received, inventory)
    assert len(accepted) == 5000
    assert sum(rejected.values()) == 0

    mixed = [
        received[0],
        dict(received[0]),
        {"path": received[1]["path"], "size": -1, "mtime_ns": received[1]["mtime_ns"]},
        {"path": "outside.jsonl", "size": 1, "mtime_ns": 2},
        {"path": "", "size": 1, "mtime_ns": 2},
        "invalid",
    ]
    accepted, rejected = validate(mixed, inventory)
    assert list(accepted) == [received[0]["path"]]
    assert rejected == {
        "INVALID_ROW": 2,
        "PATH_NOT_IN_VALIDATED_INVENTORY": 1,
        "GENERATION_MISMATCH": 1,
        "DUPLICATE_PATH": 1,
    }


def test_ack_http_path_is_bounded_and_never_prunes_synchronously():
    body = BOT[BOT.index("def api_data_sync_ack"):BOT.index(
        "def _data_sync_validate_lifecycle_ack_bundle"
    )]
    assert "_data_sync_validated_inventory_index(" in body
    assert "requested_inventory_sha256, requested_inventory_generated_at" in body
    assert "_data_sync_validate_ack_rows(" in body
    assert 'body.get("schema") != "fly_runtime_incremental_ack_v2"' in body
    assert 'body.get("defer_retention") is not True' in body
    assert "_data_sync_resolve_relpath(" not in body
    assert "path.stat()" not in body
    assert "_prune_acknowledged_rotations(" not in body
    assert "_schedule_data_sync_retention_cleanup()" not in body
    assert '"cleanup_status": "ELIGIBILITY_MODEL_ONLY_SOURCE_RETAINED"' in body
    assert '"accepted": len(accepted_rows)' in body
    assert '"rejected_count": rejected' in body


def test_deferred_retention_is_delayed_single_flight_and_fail_closed():
    worker = BOT[BOT.index("_data_sync_retention_schedule_lock ="):BOT.index(
        "@app.route('/api/data-sync/manifest')"
    )]
    assert 'max(\n    60, int(os.getenv("DATA_SYNC_RETENTION_DELAY_SECONDS", "300"))' in worker
    assert "time.sleep(_DATA_SYNC_RETENTION_DELAY_SECONDS)" in worker
    assert "_read_data_sync_ack()" in worker
    assert "_prune_acknowledged_rotations(acks)" in worker
    assert "if _data_sync_retention_scheduled:" in worker
    assert 'name="data-sync-retention"' in worker
    assert "daemon=True" in worker
    assert "except BaseException as exc:" in worker
    ack_body = BOT[BOT.index("def api_data_sync_ack"):BOT.index(
        "_PLATFORM_RELAY_EVIDENCE_MAX_BYTES"
    )]
    assert "they must never\n    # schedule source cleanup" in ack_body
    assert "_schedule_data_sync_retention_cleanup()" not in ack_body


def test_long_sync_ack_can_select_the_exact_retained_initial_generation():
    namespace = _load_bot_functions(
        "_data_sync_inventory_rows_sha256",
        "_data_sync_retain_inventory_generation",
        "_data_sync_register_served_ack_generation",
        "_data_sync_validated_inventory_index",
        "_data_sync_validate_ack_rows",
    )
    namespace.update({
        "hashlib": hashlib,
        "json": json,
        "re": re,
        "hmac": __import__("hmac"),
        "threading": threading,
        "_data_sync_inventory_cache_condition": threading.Condition(),
        "_data_sync_inventory_generations": {},
        "_DATA_SYNC_INVENTORY_GENERATION_TTL_SECONDS": 7200,
        "_DATA_SYNC_INVENTORY_GENERATION_MAX": 8,
    })
    initial = [{"path": "v3/ledgers/order.jsonl", "size": 10, "mtime_ns": 100}]
    evolved = [{"path": "v3/ledgers/order.jsonl", "size": 20, "mtime_ns": 200}]
    initial_sha = namespace["_data_sync_retain_inventory_generation"](
        initial, "2026-08-31T00:00:00Z"
    )
    namespace["_data_sync_retain_inventory_generation"](
        evolved, "2026-08-31T00:05:00Z"
    )
    # Prove retained generations are immutable copies rather than aliases.
    initial[0]["size"] = 999
    index, generated_at, selected_sha = namespace[
        "_data_sync_validated_inventory_index"
    ](initial_sha, "2026-08-31T00:00:00Z")
    assert index["v3/ledgers/order.jsonl"]["size"] == 10
    assert generated_at == "2026-08-31T00:00:00Z"
    assert selected_sha == initial_sha
    assert namespace["_data_sync_validated_inventory_index"](
        initial_sha, "2026-08-31T00:05:00Z"
    )[0] == {}
    assert namespace["_data_sync_validated_inventory_index"](
        "f" * 64, "2026-08-31T00:00:00Z"
    )[0] == {}
    # A small hot strict file may be safely served from a newer exact
    # before/after generation. It is accepted only when the file endpoint
    # bound that exact tuple to this initial manifest digest.
    namespace["_data_sync_register_served_ack_generation"](
        initial_sha, "v3/ledgers/order.jsonl", 15, 150
    )
    index = namespace["_data_sync_validated_inventory_index"](
        initial_sha, "2026-08-31T00:00:00Z"
    )[0]
    accepted, rejected = namespace["_data_sync_validate_ack_rows"](
        [{"path": "v3/ledgers/order.jsonl", "size": 15, "mtime_ns": 150}],
        index,
    )
    assert list(accepted) == ["v3/ledgers/order.jsonl"]
    assert sum(rejected.values()) == 0
    accepted, rejected = namespace["_data_sync_validate_ack_rows"](
        [{"path": "v3/ledgers/order.jsonl", "size": 16, "mtime_ns": 151}],
        index,
    )
    assert accepted == {}
    assert rejected["GENERATION_MISMATCH"] == 1


def test_manifest_publishes_and_retains_its_exact_inventory_generation():
    manifest = BOT[BOT.index("def api_data_sync_manifest"):BOT.index(
        "@app.route('/api/data-sync/sqlite-snapshot')"
    )]
    assert "_data_sync_retain_inventory_generation(" in manifest
    assert '"inventory_sha256": inventory_sha256' in manifest
    assert 'inventory_status == "CURRENT" and not targeted_path' in manifest


def test_atomic_ack_write_preserves_previous_receipt_on_replace_failure(tmp_path):
    namespace = _load_bot_functions("_write_data_sync_ack")
    target = tmp_path / "sync_ack.json"
    target.write_text('{"old":true}', encoding="utf-8")
    namespace.update({"json": json, "uuid": uuid})
    namespace["_data_sync_ack_path"] = lambda: target
    original_replace = os.replace

    def fail_replace(source, destination):
        assert Path(destination) == target
        raise OSError("modeled replace failure")

    namespace["os"].replace = fail_replace
    try:
        try:
            namespace["_write_data_sync_ack"]({"new": True})
            raise AssertionError("replace failure should propagate")
        except OSError as exc:
            assert str(exc) == "modeled replace failure"
    finally:
        namespace["os"].replace = original_replace
    assert target.read_text(encoding="utf-8") == '{"old":true}'
    assert list(tmp_path.glob("sync_ack.json.*.tmp")) == []


def test_inventory_excludes_only_internal_lock_directories_from_evidence_walk():
    excluded_block = BOT[BOT.index("_DATA_SYNC_EXCLUDED_DIR_NAMES"):BOT.index(
        "_DATA_SYNC_CHUNK_MAX"
    )]
    assert '".locks"' in excluded_block
    assert '"ledgers"' not in excluded_block
    assert '"market_segments"' not in excluded_block


def test_legacy_file_ack_rotations_are_all_retained_until_lifecycle_cleanup_exists():
    namespace = _load_bot_functions(
        "_data_sync_rotation_parts",
        "_prune_acknowledged_rotations",
    )
    rotation_parts = namespace["_data_sync_rotation_parts"]
    assert rotation_parts("signal_replay.jsonl.28") == ("signal_replay.jsonl", 28)
    assert rotation_parts("bot_runtime.log.3") == ("bot_runtime.log", 3)
    assert rotation_parts("credential.bin.4") is None
    assert rotation_parts("signal_replay.jsonl.tmp") is None

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        active = root / "signal_replay.jsonl"
        active.write_text("active\n", encoding="utf-8")
        old_stamp = time.time() - (48 * 3600)
        acks = {}
        for generation in (2, 7, 9, 11):
            path = root / f"signal_replay.jsonl.{generation}"
            path.write_text(f"rotation-{generation}\n", encoding="utf-8")
            os.utime(path, (old_stamp, old_stamp))
            if generation != 11:  # newest rotation deliberately remains unacknowledged
                stat = path.stat()
                acks[path.name] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}

        namespace["_data_sync_resolve_relpath"] = lambda rel: root / rel
        namespace["_data_sync_path_allowed"] = lambda path: path.is_file()
        removed = namespace["_prune_acknowledged_rotations"](acks)

        assert removed == []
        assert active.read_text(encoding="utf-8") == "active\n"
        assert (root / "signal_replay.jsonl.2").is_file()
        assert (root / "signal_replay.jsonl.7").is_file()
        assert (root / "signal_replay.jsonl.9").is_file()
        assert (root / "signal_replay.jsonl.11").is_file()


def _load_lifecycle_cleanup_model():
    namespace = _load_bot_functions(
        "_data_sync_iso8601_utc",
        "_data_sync_lifecycle_manifest_sha256",
        "_data_sync_lifecycle_identity_sha256",
        "_data_sync_lifecycle_cleanup_eligibility",
    )
    namespace.update({
        "datetime": datetime,
        "timezone": timezone,
        "json": json,
        "hashlib": hashlib,
        "hmac": __import__("hmac"),
        "re": re,
        "_DATA_SYNC_LIFECYCLE_CLEANUP_ACK_SCHEMA": "lifecycle_bundle_cleanup_ack_v1",
        "_DATA_SYNC_LIFECYCLE_CLEANUP_ENABLED": False,
        "_DATA_SYNC_TERMINAL_OUTCOMES": frozenset({
            "FULL_FILL", "PARTIAL_FILL", "NO_FILL", "UNKNOWN",
        }),
    })
    return namespace


def _complete_lifecycle_cleanup_receipt(namespace):
    files = [{
        "path": "lifecycle/bundle-001/events.jsonl",
        "sha256": hashlib.sha256(b'{"event":"closed"}\n').hexdigest(),
        "size": 19,
        "mtime_ns": 1788134400000000000,
        "row_count": 1,
        "first_timestamp": "2026-08-31T00:00:00Z",
        "last_timestamp": "2026-08-31T02:10:00Z",
    }]
    manifest_sha = namespace["_data_sync_lifecycle_manifest_sha256"](files)
    receipt = {
        "schema": "lifecycle_bundle_cleanup_ack_v1",
        "bundle_id": "bundle-001",
        "lifecycle_id": "episode-001",
        "source_git_rev": "a" * 40,
        "deployed_git_rev": "b" * 40,
        "collection_epoch_id": "epoch-001",
        "tile_registry_signature": "c" * 64,
        "terminal_outcome": "UNKNOWN",
        "terminal_at": "2026-08-31T02:10:00Z",
        "pending_order_ids": [],
        "open_position_ids": [],
        "files": files,
        "manifest_sha256": manifest_sha,
    }
    receipt["immutable_identity_sha256"] = namespace[
        "_data_sync_lifecycle_identity_sha256"
    ](receipt)
    receipt["laptop_acknowledgement"] = {
        copy_name: {
            "complete": True,
            "bundle_id": receipt["bundle_id"],
            "lifecycle_id": receipt["lifecycle_id"],
            "sha256": hashlib.sha256(copy_name.encode("utf-8")).hexdigest(),
            "manifest_sha256": manifest_sha,
            "acknowledged_at": "2026-08-31T02:15:00Z",
        }
        for copy_name in ("canonical", "archive", "index")
    }
    return receipt


def test_complete_lifecycle_proof_is_recognized_but_cleanup_remains_disabled():
    namespace = _load_lifecycle_cleanup_model()
    receipt = _complete_lifecycle_cleanup_receipt(namespace)

    result = namespace["_data_sync_lifecycle_cleanup_eligibility"](receipt)

    assert result == {
        "schema": "lifecycle_bundle_cleanup_eligibility_v1",
        "bundle_id": "bundle-001",
        "lifecycle_id": "episode-001",
        "proof_complete": True,
        "cleanup_authorized": False,
        "status": "ELIGIBLE_BUT_CLEANUP_DISABLED",
        "reasons": ["LIFECYCLE_ROTATION_CLEANUP_NOT_IMPLEMENTED"],
    }


def test_lifecycle_cleanup_fails_closed_for_integrity_identity_and_laptop_gaps():
    namespace = _load_lifecycle_cleanup_model()
    receipt = _complete_lifecycle_cleanup_receipt(namespace)
    receipt["files"][0]["sha256"] = "f" * 64
    receipt["pending_order_ids"] = ["paper-order-1"]
    receipt["laptop_acknowledgement"]["archive"]["complete"] = False

    result = namespace["_data_sync_lifecycle_cleanup_eligibility"](receipt)

    assert result["status"] == "INELIGIBLE_RETAIN_SOURCE"
    assert result["proof_complete"] is False
    assert result["cleanup_authorized"] is False
    assert "PENDING_ORDER_REFERENCE_NOT_CLEARED" in result["reasons"]
    assert "MANIFEST_SHA256_MISMATCH" in result["reasons"]
    assert "LAPTOP_ARCHIVE_ACK_INCOMPLETE" in result["reasons"]


def test_lifecycle_cleanup_excludes_active_runtime_sync_and_analyzer_leases():
    namespace = _load_lifecycle_cleanup_model()
    receipt = _complete_lifecycle_cleanup_receipt(namespace)

    result = namespace["_data_sync_lifecycle_cleanup_eligibility"](
        receipt,
        active_order_or_position_refs=["episode-001"],
        active_sync_leases=["bundle-001"],
        active_analyzer_leases=["bundle-001"],
    )

    assert result["cleanup_authorized"] is False
    assert set(result["reasons"]) >= {
        "ACTIVE_RUNTIME_REFERENCE",
        "ACTIVE_SYNC_LEASE",
        "ACTIVE_ANALYZER_LEASE",
    }


def test_lifecycle_cleanup_requires_explicit_unknown_instead_of_missing_outcome():
    namespace = _load_lifecycle_cleanup_model()
    receipt = _complete_lifecycle_cleanup_receipt(namespace)
    receipt["terminal_outcome"] = ""
    receipt["immutable_identity_sha256"] = namespace[
        "_data_sync_lifecycle_identity_sha256"
    ](receipt)

    result = namespace["_data_sync_lifecycle_cleanup_eligibility"](receipt)

    assert "TERMINAL_OR_EXPLICIT_UNKNOWN_MISSING" in result["reasons"]
    assert result["status"] == "INELIGIBLE_RETAIN_SOURCE"


def test_remote_analyzer_mirror_is_read_only_and_admin_gated():
    assert "@app.route('/api/data-sync/analyzer-report', methods=['POST'])" in BOT
    assert "@app.route('/analysis')" in BOT
    assert "@app.route('/analysis/')" in BOT
    assert "@app.route('/analysis/<path:artifact_path>')" in BOT
    assert "if not _admin_authed_strict()" in BOT
    assert '$form.Add($content, "bundle", "analyzer_bundle.zip")' in SYNC_SCRIPT
    assert '"reports/$name"' in SYNC_SCRIPT
    assert '"report_manifest.json"' in SYNC_SCRIPT
    assert "$reportManifest.text_artifacts" in SYNC_SCRIPT
    assert "Required analyzer artifact is missing" in SYNC_SCRIPT
    assert "outside the committed run window" in SYNC_SCRIPT
    assert "metadata does not match the snapshotted file" in SYNC_SCRIPT
    assert "analysis_provenance.cohort_schema" in SYNC_SCRIPT
    assert 'schema = "analyzer_mirror_bundle_v2"' in SYNC_SCRIPT
    assert 'app.config["MAX_CONTENT_LENGTH"]' in BOT


def _load_analyzer_bundle_validators():
    namespace = {
        "Path": Path,
        "zipfile": zipfile,
        "json": json,
        "re": re,
        "datetime": datetime,
        "hashlib": hashlib,
        "_ANALYZER_BUNDLE_ALLOWED_SUFFIXES": frozenset((".html", ".txt", ".json", ".log")),
        "_ANALYZER_BUNDLE_MAX_EXPANDED_BYTES": 150 * 1024 * 1024,
        "_ANALYZER_BUNDLE_MAX_MEMBERS": 256,
        "_ANALYZER_BUNDLE_MAX_MEMBER_BYTES": 50 * 1024 * 1024,
        "_ANALYZER_BUNDLE_MAX_COMPRESSION_RATIO": 1000,
        "_ANALYZER_BUNDLE_MANIFEST": "bundle_manifest.json",
        "_ANALYZER_BUNDLE_SCHEMA": "analyzer_mirror_bundle_v2",
    }
    tree = ast.parse(BOT)
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_safe_analyzer_bundle_members", "_validated_analyzer_bundle_manifest"}
    ]
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace


def _ensure_source_report_manifest(files):
    if files.get("report_manifest.json") not in (None, b"{}"):
        return
    text_artifacts = sorted(
        path for path in files
        if path != "report_manifest.json" and not path.startswith("reports/")
    )
    reports = [
        {"file": path.removeprefix("reports/")}
        for path in sorted(files)
        if path.startswith("reports/")
    ]
    files["report_manifest.json"] = json.dumps(
        {
            "schema": "report_manifest_v1",
            "analyzer_sync_id": "analyzer-v1",
            "analyzer_version": "analyzer-v1",
            "generated_at": "2026-08-16T00:00:00+00:00",
            "data_scope": "session",
            "session_scope": "SESSION",
            "analysis_provenance": {
                "cohort_schema": "analysis_cohorts_v1",
                "generation_revision": "b" * 40,
            },
            "report_count": len(reports),
            "reports": reports,
            "text_artifacts": text_artifacts,
        },
        sort_keys=True,
    ).encode()


def _bundle_manifest(files):
    _ensure_source_report_manifest(files)
    return {
        "schema": "analyzer_mirror_bundle_v2",
        "snapshot_id": "fixture-run-1",
        "analyzer_run_id": "analyzer-v1",
        "analyzer_version": "analyzer-v1",
        "analyzer_generated_at": "2026-08-16T00:00:00+00:00",
        "source_data_revision": "a" * 40,
        "analyzer_generation_revision": "b" * 40,
        "cohort_schema": "analysis_cohorts_v1",
        "data_scope": "session",
        "session_scope": "SESSION",
        "source_report_manifest_sha256": hashlib.sha256(files["report_manifest.json"]).hexdigest(),
        "files": [
            {
                "path": path,
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path, content in files.items()
        ],
    }


def _zip_bundle(files, manifest=None):
    _ensure_source_report_manifest(files)
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
        archive.writestr(
            "bundle_manifest.json",
            json.dumps(manifest if manifest is not None else _bundle_manifest(files)),
        )
    return payload.getvalue()


def test_analyzer_bundle_validation_fails_closed_for_missing_dashboard_and_unsafe_paths():
    namespace = _load_analyzer_bundle_validators()
    validate = namespace["_safe_analyzer_bundle_members"]

    missing = io.BytesIO()
    with zipfile.ZipFile(missing, "w") as archive:
        archive.writestr("executive_summary.txt", "summary")
    with zipfile.ZipFile(io.BytesIO(missing.getvalue()), "r") as archive:
        try:
            validate(archive)
        except ValueError as exc:
            assert "missing bundle_manifest.json" in str(exc)
        else:
            raise AssertionError("bundle without dashboard must fail closed")

    traversal = io.BytesIO()
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("analysis_dashboard.html", "dashboard")
        archive.writestr("bundle_manifest.json", "{}")
        archive.writestr("../secret.txt", "no")
    with zipfile.ZipFile(io.BytesIO(traversal.getvalue()), "r") as archive:
        try:
            validate(archive)
        except ValueError as exc:
            assert "unsafe path" in str(exc)
        else:
            raise AssertionError("path traversal must fail closed")


def test_analyzer_bundle_accepts_complete_read_only_report_tree():
    namespace = _load_analyzer_bundle_validators()
    files = {
        "analysis_dashboard.html": b'<a href="executive_summary.txt">summary</a>',
        "executive_summary.txt": b"summary",
        "report_manifest.json": b"{}",
        "reports/ai_calibration_report.json": b"{}",
    }
    payload = _zip_bundle(files)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        members = namespace["_safe_analyzer_bundle_members"](archive)
        manifest = namespace["_validated_analyzer_bundle_manifest"](archive, members)
    assert manifest["snapshot_id"] == "fixture-run-1"
    assert {str(rel).replace("\\", "/") for _, rel in members} == {
        "analysis_dashboard.html",
        "executive_summary.txt",
        "report_manifest.json",
        "reports/ai_calibration_report.json",
        "bundle_manifest.json",
    }


def test_analyzer_bundle_rejects_missing_extra_duplicate_and_bad_hash_members():
    namespace = _load_analyzer_bundle_validators()
    files = {"analysis_dashboard.html": b"dashboard", "executive_summary.txt": b"summary"}
    manifest = _bundle_manifest(files)
    manifest["files"][0]["sha256"] = "0" * 64
    payload = _zip_bundle(files, manifest)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        members = namespace["_safe_analyzer_bundle_members"](archive)
        # Structural validation succeeds; extraction performs the final content hash check.
        parsed = namespace["_validated_analyzer_bundle_manifest"](archive, members)
        assert parsed["files"][0]["sha256"] == "0" * 64

    missing_manifest = _bundle_manifest(files)
    missing_manifest["files"] = missing_manifest["files"][:-1]
    payload = _zip_bundle(files, missing_manifest)
    with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
        members = namespace["_safe_analyzer_bundle_members"](archive)
        try:
            namespace["_validated_analyzer_bundle_manifest"](archive, members)
        except ValueError as exc:
            assert "membership" in str(exc)
        else:
            raise AssertionError("undeclared archive member must fail closed")

    duplicate = io.BytesIO()
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("analysis_dashboard.html", "one")
        archive.writestr("ANALYSIS_DASHBOARD.HTML", "two")
        archive.writestr("bundle_manifest.json", "{}")
    with zipfile.ZipFile(io.BytesIO(duplicate.getvalue()), "r") as archive:
        try:
            namespace["_safe_analyzer_bundle_members"](archive)
        except ValueError as exc:
            assert "duplicate path" in str(exc)
        else:
            raise AssertionError("case-colliding archive members must fail closed")


def test_analyzer_bundle_install_is_atomic_and_bad_hash_preserves_current_generation():
    tree = ast.parse(BOT)
    wanted = {
        "_valid_analyzer_generation",
        "_recover_latest_analyzer_generation",
        "_active_analyzer_mirror_dir",
        "_safe_analyzer_bundle_members",
        "_validated_analyzer_bundle_manifest",
        "_install_analyzer_bundle",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {
        "Path": Path,
        "zipfile": zipfile,
        "json": json,
        "re": re,
        "io": io,
        "os": os,
        "time": time,
        "shutil": shutil,
        "hashlib": hashlib,
        "datetime": datetime,
        "_ANALYZER_BUNDLE_ALLOWED_SUFFIXES": frozenset((".html", ".txt", ".json", ".log")),
        "_ANALYZER_BUNDLE_MAX_EXPANDED_BYTES": 150 * 1024 * 1024,
        "_ANALYZER_BUNDLE_MAX_MEMBERS": 256,
        "_ANALYZER_BUNDLE_MAX_MEMBER_BYTES": 50 * 1024 * 1024,
        "_ANALYZER_BUNDLE_MAX_COMPRESSION_RATIO": 1000,
        "_ANALYZER_BUNDLE_MANIFEST": "bundle_manifest.json",
        "_ANALYZER_BUNDLE_SCHEMA": "analyzer_mirror_bundle_v2",
        "_ANALYZER_INSTALL_LOCK": threading.RLock(),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        generations = root / "generations"
        pointer = root / "current.json"
        namespace["_analyzer_generations_dir"] = lambda: generations
        namespace["_analyzer_current_pointer_path"] = lambda: pointer
        namespace["_analyzer_mirror_dir"] = lambda: root / "legacy"
        namespace["_prune_analyzer_generations"] = lambda generation: None
        files = {
            "analysis_dashboard.html": b"dashboard-v1",
            "executive_summary.txt": b"summary-v1",
        }
        installed = namespace["_install_analyzer_bundle"](_zip_bundle(files), {"uploaded_at": "now"})
        assert installed["complete"] is True
        first_pointer = json.loads(pointer.read_text(encoding="utf-8"))
        first_generation = generations / first_pointer["generation"]
        assert (first_generation / "analysis_dashboard.html").read_bytes() == b"dashboard-v1"
        assert namespace["_active_analyzer_mirror_dir"]() == first_generation

        pointer.write_text("not-json", encoding="utf-8")
        assert namespace["_active_analyzer_mirror_dir"]() == first_generation
        pointer.write_text(json.dumps(first_pointer), encoding="utf-8")

        bad_manifest = _bundle_manifest(files)
        bad_manifest["files"][0]["sha256"] = "0" * 64
        try:
            namespace["_install_analyzer_bundle"](_zip_bundle(files, bad_manifest), {})
        except ValueError as exc:
            assert "integrity mismatch" in str(exc)
        else:
            raise AssertionError("bad artifact hash must fail installation")
        assert json.loads(pointer.read_text(encoding="utf-8")) == first_pointer
        assert (first_generation / "analysis_dashboard.html").read_bytes() == b"dashboard-v1"

        summary_path = first_generation / "executive_summary.txt"
        summary_path.write_bytes(b"tampered!")
        assert namespace["_active_analyzer_mirror_dir"]() is None
        summary_path.write_bytes(b"summary-v1")
        assert namespace["_active_analyzer_mirror_dir"]() == first_generation

        shutil.rmtree(first_generation)
        assert namespace["_active_analyzer_mirror_dir"]() is None
        (root / "legacy").mkdir()
        (root / "legacy" / "analysis_dashboard.html").write_text("legacy", encoding="utf-8")
        assert namespace["_active_analyzer_mirror_dir"]() is None


def test_flask_snapshot_routes_require_auth_serve_links_and_reject_traversal():
    from flask import Flask, jsonify, make_response, request, send_file

    tree = ast.parse(BOT)
    wanted = {
        "analyzer_mirror_dashboard",
        "analyzer_mirror_dashboard_index",
        "analyzer_mirror_artifact",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    with tempfile.TemporaryDirectory() as tmp:
        mirror = Path(tmp)
        (mirror / "analysis_dashboard.html").write_text(
            '<a href="executive_summary.txt">summary</a>', encoding="utf-8"
        )
        (mirror / "executive_summary.txt").write_text("summary", encoding="utf-8")
        outside = mirror.parent / "secret.txt"
        outside.write_text("secret", encoding="utf-8")
        app = Flask("analyzer-route-fixture")
        auth = {"allowed": False}
        namespace = {
            "app": app,
            "request": request,
            "jsonify": jsonify,
            "make_response": make_response,
            "send_file": send_file,
            "_analyzer_view_authed": lambda: auth["allowed"],
            "_active_analyzer_mirror_dir": lambda: mirror,
            "_ANALYZER_BUNDLE_ALLOWED_SUFFIXES": frozenset((".html", ".txt", ".json", ".log")),
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
        client = app.test_client()
        unauthenticated = client.get("/analysis/")
        assert unauthenticated.status_code == 303
        assert unauthenticated.headers["Location"] == "/analysis/login"

        auth["allowed"] = True
        dashboard = client.get("/analysis/")
        assert dashboard.status_code == 200
        assert b"executive_summary.txt" in dashboard.data
        assert "default-src 'none'" in dashboard.headers["Content-Security-Policy"]
        summary = client.get("/analysis/executive_summary.txt")
        assert summary.status_code == 200
        assert summary.data == b"summary"
        traversal = client.get("/analysis/%2e%2e/secret.txt")
        assert traversal.status_code == 400
        unauthenticated.close()
        dashboard.close()
        summary.close()
        traversal.close()
        outside.unlink(missing_ok=True)


def test_flask_snapshot_routes_fail_closed_without_complete_generation():
    from flask import Flask, jsonify, make_response, request, send_file

    tree = ast.parse(BOT)
    wanted = {"analyzer_mirror_dashboard_index", "analyzer_mirror_artifact"}
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    app = Flask("analyzer-fail-closed-fixture")
    namespace = {
        "app": app,
        "request": request,
        "jsonify": jsonify,
        "make_response": make_response,
        "send_file": send_file,
        "_analyzer_view_authed": lambda: True,
        "_active_analyzer_mirror_dir": lambda: None,
        "_ANALYZER_BUNDLE_ALLOWED_SUFFIXES": frozenset((".html", ".txt", ".json", ".log")),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    client = app.test_client()
    dashboard = client.get("/analysis/")
    artifact = client.get("/analysis/executive_summary.txt")
    assert dashboard.status_code == 503
    assert artifact.status_code == 503
    assert b"complete validated analyzer bundle" in dashboard.data


def test_legacy_html_publication_is_rejected_and_status_discloses_quarantine():
    from flask import Flask, jsonify, request

    tree = ast.parse(BOT)
    wanted = {"api_data_sync_analyzer_report", "api_analyzer_mirror_status"}
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    with tempfile.TemporaryDirectory() as tmp:
        legacy = Path(tmp) / "legacy"
        legacy.mkdir()
        (legacy / "analysis_dashboard.html").write_text("forensic legacy", encoding="utf-8")
        app = Flask("analyzer-publication-fixture")
        namespace = {
            "app": app,
            "request": request,
            "jsonify": jsonify,
            "_admin_authed_strict": lambda: True,
            "_active_analyzer_mirror_dir": lambda: None,
            "_analyzer_mirror_dir": lambda: legacy,
            "_ANALYZER_BUNDLE_SCHEMA": "analyzer_mirror_bundle_v2",
            "_ANALYZER_BUNDLE_MAX_COMPRESSED_BYTES": 50 * 1024 * 1024,
        }
        exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
        client = app.test_client()
        response = client.post(
            "/api/data-sync/analyzer-report",
            data={"report": (io.BytesIO(b"new legacy"), "analysis_dashboard.html")},
        )
        assert response.status_code == 410
        assert response.json["required_schema"] == "analyzer_mirror_bundle_v2"
        assert (legacy / "analysis_dashboard.html").read_text(encoding="utf-8") == "forensic legacy"
        status = client.get("/api/analyzer-mirror/status")
        assert status.status_code == 404
        assert status.json["available"] is False
        assert status.json["legacy_data_preserved"] is True


def test_v3_normalized_ledgers_use_prefix_sync_but_segments_are_strict():
    namespace = _load_bot_functions("_data_sync_consistency_mode")
    namespace["_jsonl_serialized_append_targets"] = set()
    namespace["SIGNAL_SNAPSHOT_FILE"] = "signal_snapshot.jsonl"
    mode = namespace["_data_sync_consistency_mode"]
    assert mode(Path("v3/ledgers/opportunity.jsonl")) == "append_prefix_v1"
    assert mode(Path("v3/ledgers/lifecycle.jsonl")) == "append_prefix_v1"
    assert mode(Path("v3/market_segments/ab/abcdef.json")) == "strict_generation_v1"


def test_optional_analyzer_publication_failure_does_not_invalidate_canonical_sync():
    sync = (ROOT.parent.parent / "scripts" / "sync-fly-bot-data.ps1").read_text(
        encoding="utf-8"
    )
    assert "$analyzerPublished = $false" in sync
    assert '$analyzerPublishErrorCode = "ANALYZER_PUBLICATION_FAILED"' in sync
    assert "canonical evidence sync remains valid" in sync
    assert "AnalyzerPublished = [bool]$analyzerPublished" in sync
    assert "AnalyzerPublished = [bool]$PublishAnalyzerReport" not in sync
    ack_pos = sync.index('$ack = Invoke-DataSyncJsonRequest')
    publication_try_pos = sync.index("if ($PublishAnalyzerReport)")
    assert ack_pos < publication_try_pos


def test_sync_batches_state_checkpoints_and_skips_unchanged_state_rewrites():
    sync = (ROOT.parent.parent / "scripts" / "sync-fly-bot-data.ps1").read_text(
        encoding="utf-8"
    )
    assert "$pendingStateWrites = 0" in sync
    assert "$stateMetadataChanged" in sync
    assert "$previous.size -ne $remoteSize" in sync
    assert "if ($downloadedGeneration -or -not $previous -or $stateMetadataChanged)" in sync
    assert "if ($pendingStateWrites -ge 10)" in sync
    assert "At most nine changed files need replay after interruption" in sync


def test_hot_small_strict_file_uses_atomic_snapshot_after_first_generation_conflict():
    sync = (ROOT.parent.parent / "scripts" / "sync-fly-bot-data.ps1").read_text(
        encoding="utf-8"
    )
    fallback = sync[sync.index("if ($refreshGeneration) {") : sync.index("$freshManifest = Invoke-DataSyncJsonRequest")]
    assert '$consistencyMode -eq "strict_generation_v1"' in fallback
    assert "$remoteSize -le $chunkLimit" in fallback
    assert "$generationRefreshCount -ge 3" not in fallback


def test_desktop_mirror_launcher_loads_canonical_path_helper_before_use():
    helper_import = '. (Join-Path $scriptDir "fly-data-paths.ps1")'
    assert helper_import in DESKTOP_MIRROR_LAUNCHER
    assert DESKTOP_MIRROR_LAUNCHER.index(helper_import) < DESKTOP_MIRROR_LAUNCHER.index(
        "$canonicalMirror = Get-DoxxedFlyMirrorDir"
    )


def test_unattended_research_supervisor_is_local_repair_only():
    assert "start-research-stability-supervisor.ps1" in RESEARCH_SUPERVISOR_TASK
    assert '"-Once"' not in RESEARCH_SUPERVISOR_TASK
    assert '"-RepairMissingLocal"' in RESEARCH_SUPERVISOR_TASK
    assert "-MultipleInstances IgnoreNew" in RESEARCH_SUPERVISOR_TASK
    assert "-RestartCount 3" in RESEARCH_SUPERVISOR_TASK
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in RESEARCH_SUPERVISOR_TASK
    assert 'supervisionMode = "CONTINUOUS_LOOP_WITH_SCHEDULED_RESTART"' in RESEARCH_SUPERVISOR_TASK
    assert 'repairAuthority = "LOCAL_SYNC_OR_MISSING_OR_REVISION_STALE_ANALYZER_ONLY"' in RESEARCH_SUPERVISOR_TASK
    for forbidden in ("fly deploy", "fly machine restart", "fresh-reset", "live-armed"):
        assert forbidden not in RESEARCH_SUPERVISOR_TASK.lower()


if __name__ == "__main__":
    test_fly_runtime_cwd_is_volume_backed()
    test_incremental_sync_is_authenticated_and_chunk_verified()
    test_local_sync_has_fail_closed_30_gib_admission_guard()
    test_retention_never_removes_active_or_unacknowledged_files()
    test_legacy_file_ack_rotations_are_all_retained_until_lifecycle_cleanup_exists()
    test_remote_analyzer_mirror_is_read_only_and_admin_gated()
    test_analyzer_bundle_validation_fails_closed_for_missing_dashboard_and_unsafe_paths()
    test_analyzer_bundle_accepts_complete_read_only_report_tree()
    print("Fly data sync contract checks passed")
def test_persisted_inventory_snapshot_is_atomic_and_tamper_evident(tmp_path):
    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_inventory_snapshot_path",
        "_data_sync_inventory_rows_sha256",
        "_data_sync_persist_inventory_snapshot",
        "_data_sync_load_persisted_inventory_snapshot",
    }
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {
        "Path": Path, "json": json, "hashlib": hashlib, "hmac": __import__("hmac"),
        "os": os, "uuid": uuid,
        "_DATA_SYNC_INVENTORY_SNAPSHOT_NAME": "sync_inventory_current.json",
        "_DATA_SYNC_INVENTORY_SNAPSHOT_SCHEMA": "fly_runtime_inventory_snapshot_v1",
        "_DATA_SYNC_INVENTORY_SNAPSHOT_SCHEMA_V2": "fly_runtime_inventory_snapshot_v2",
        "_data_sync_validate_disk_inventory_generation": lambda payload, root: payload,
        "_data_sync_inventory_work_root": lambda: tmp_path,
        "_data_sync_volume_root": lambda: tmp_path,
        "_runtime_git_rev": lambda: "a" * 40,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    rows = [{"path": "evidence.jsonl", "size": 12, "inode": 7}]
    namespace["_data_sync_persist_inventory_snapshot"](rows, "2026-08-30T00:00:00Z")
    snapshot = namespace["_data_sync_load_persisted_inventory_snapshot"]()
    assert snapshot["rows"] == rows
    assert snapshot["file_count"] == 1
    target = tmp_path / "sync_inventory_current.json"
    tampered = json.loads(target.read_text(encoding="utf-8"))
    tampered["rows"][0]["size"] = 13
    target.write_text(json.dumps(tampered), encoding="utf-8")
    assert namespace["_data_sync_load_persisted_inventory_snapshot"]() is None


def test_async_inventory_cold_start_is_nonblocking_single_flight():
    tree = ast.parse(BOT)
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_data_sync_request_async_inventory")
    started = []

    class FakeThread:
        def __init__(self, **kwargs): self.kwargs = kwargs
        def start(self): started.append(self.kwargs)

    state = {"status": "EMPTY", "rows": None, "generated_at": None, "expires_at": 0.0, "served_since_refresh": False, "refreshing": False, "error": None}
    namespace = {
        "time": time, "threading": SimpleNamespace(Thread=FakeThread),
        "_data_sync_inventory_cache_condition": threading.Condition(),
        "_data_sync_async_inventory": state,
        "_data_sync_load_persisted_inventory_snapshot": lambda: None,
        "_data_sync_retain_inventory_generation": lambda *args, **kwargs: "f" * 64,
        "_data_sync_inventory_refresh_worker": lambda: None,
        "_DATA_SYNC_INVENTORY_CACHE_TTL_SECONDS": 30.0,
        "hmac": hmac,
        "uuid": uuid,
        "utc_iso": lambda: "2026-09-01T00:00:00Z",
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    request_inventory = namespace["_data_sync_request_async_inventory"]
    assert request_inventory()["status"] == "BUILDING"
    assert request_inventory()["status"] == "BUILDING"
    assert len(started) == 1
    assert started[0]["daemon"] is True
    assert "_data_sync_inventory(" not in ast.unparse(node)
    state.update({"status": "CURRENT", "rows": [{"path": "a.json", "size": 1}], "generated_at": "now", "expires_at": time.monotonic() + 10, "served_since_refresh": False, "refreshing": False})
    assert request_inventory()["status"] == "CURRENT"
    revalidating = request_inventory(force_refresh=True)
    assert revalidating["status"] == "STALE_REVALIDATING"
    assert revalidating["rows"] == [{"path": "a.json", "size": 1}]
    assert len(started) == 2

    state.update({"status": "EMPTY", "rows": None, "generated_at": None, "expires_at": 0.0, "served_since_refresh": False, "refreshing": False})
    namespace["_data_sync_load_persisted_inventory_snapshot"] = lambda: {
        "rows": [{"path": "prior.json", "size": 1}],
        "generated_at": "prior",
    }
    stale = request_inventory()
    assert stale["status"] == "STALE_REVALIDATING"
    assert stale["rows"] == [{"path": "prior.json", "size": 1}]


def test_completed_inventory_is_delivered_once_after_outer_backoff_exceeds_ttl():
    tree = ast.parse(BOT)
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_data_sync_request_async_inventory")
    started = []

    class FakeThread:
        def __init__(self, **kwargs): self.kwargs = kwargs
        def start(self): started.append(self.kwargs)

    state = {
        "status": "CURRENT", "rows": [{"path": "sealed.json", "size": 7}],
        "generated_at": "completed-before-backoff", "expires_at": 0.0,
        "served_since_refresh": False, "refreshing": False, "error": None,
    }
    namespace = {
        "time": time, "threading": SimpleNamespace(Thread=FakeThread),
        "_data_sync_inventory_cache_condition": threading.Condition(),
        "_data_sync_async_inventory": state,
        "_data_sync_load_persisted_inventory_snapshot": lambda: None,
        "_data_sync_retain_inventory_generation": lambda *args, **kwargs: "f" * 64,
        "_data_sync_inventory_refresh_worker": lambda: None,
        "hmac": hmac,
        "uuid": uuid,
        "utc_iso": lambda: "2026-09-01T00:00:00Z",
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    request_inventory = namespace["_data_sync_request_async_inventory"]

    delivered = request_inventory()
    assert delivered["status"] == "CURRENT"
    assert delivered["rows"] == [{"path": "sealed.json", "size": 7}]
    assert delivered["generated_at"] == "completed-before-backoff"
    assert delivered["error"] is None
    assert state["served_since_refresh"] is True
    assert started == []
    revalidating = request_inventory()
    assert revalidating["status"] == "STALE_REVALIDATING"
    assert revalidating["rows"] == [{"path": "sealed.json", "size": 7}]
    assert len(started) == 1


def test_same_refresh_nonce_consumes_completed_generation_without_restarting_worker():
    tree = ast.parse(BOT)
    node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_data_sync_request_async_inventory"
    )
    started = []

    class FakeThread:
        def __init__(self, **kwargs): self.kwargs = kwargs
        def start(self): started.append(self.kwargs)

    refresh_nonce = "b" * 32
    state = {
        "status": "CURRENT",
        "rows": [{"path": "sealed.json", "size": 7}],
        "generation": None,
        "generation_id": "a" * 64,
        "generated_at": "completed",
        "expires_at": 0.0,
        "served_since_refresh": True,
        "refreshing": False,
        "completed_refresh_nonce": refresh_nonce,
        "error": None,
    }
    namespace = {
        "time": time,
        "threading": SimpleNamespace(Thread=FakeThread),
        "_data_sync_inventory_cache_condition": threading.Condition(),
        "_data_sync_async_inventory": state,
        "_data_sync_load_persisted_inventory_snapshot": lambda: None,
        "_data_sync_retain_inventory_generation": lambda *args, **kwargs: "f" * 64,
        "_data_sync_inventory_refresh_worker": lambda *args: None,
        "hmac": hmac,
        "uuid": uuid,
        "utc_iso": lambda: "2026-09-01T00:00:00Z",
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    result = namespace["_data_sync_request_async_inventory"](
        force_refresh=True,
        refresh_nonce=refresh_nonce,
    )
    assert result["status"] == "CURRENT"
    assert result["refresh_nonce"] == refresh_nonce
    assert started == []


def test_inventory_refresh_uses_standalone_nonce_bound_worker_contract():
    worker = ROOT / "data_sync_inventory_worker.py"
    assert worker.is_file()
    worker_source = worker.read_text(encoding="utf-8")
    assert "import bot" not in worker_source
    assert "from bot" not in worker_source
    assert '_DATA_SYNC_INVENTORY_WORKER_NAME = "data_sync_inventory_worker.py"' in BOT
    assert 'work_root / f"inventory-request-{nonce}.json"' in BOT
    assert 'work_root / f"inventory-result-{nonce}.json"' in BOT
    assert '"--nonce", nonce' in BOT
    assert "_DATA_SYNC_INVENTORY_WORKER_RESULT_SCHEMA" in BOT
    assert 'float(result.get("generated_unix") or 0.0) < launched_unix' in BOT
    assert 'completed.returncode == 75 and result.get("status") == "BUILDING"' in BOT
    assert "_data_sync_validate_disk_inventory_generation(" in BOT
    assert "_data_sync_persist_disk_inventory_snapshot(disk_generation, generated_at)" in BOT
    assert '"rows": None' in BOT
    assert 'result.get("rows")' not in BOT[
        BOT.index("def _data_sync_inventory_refresh_worker"):
        BOT.index("def _data_sync_request_async_inventory")
    ]


def test_parent_validates_and_serves_disk_generation_one_bounded_page_at_a_time(tmp_path):
    tree = ast.parse(BOT)
    wanted = {
        "_data_sync_inventory_rows_sha256",
        "_data_sync_file_sha256",
        "_data_sync_validate_disk_inventory_generation",
        "_data_sync_manifest_cursor",
        "_data_sync_disk_page_descriptor",
        "_data_sync_disk_manifest_page",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    namespace = {
        "Path": Path,
        "hashlib": hashlib,
        "hmac": hmac,
        "json": json,
        "re": re,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    work = tmp_path / ".data-sync-snapshots"
    generation_id = "a" * 64
    generation_dir = work / "inventory-generations" / generation_id
    generation_dir.mkdir(parents=True)
    pages = [
        [{"path": "a.json", "size": 1}, {"path": "b.json", "size": 2}],
        [{"path": "c.json", "size": 3}],
    ]
    descriptors = []
    for index, rows in enumerate(pages):
        payload = {
            "schema": "fly_runtime_inventory_page_v1",
            "page_index": index,
            "file_count": len(rows),
            "total_bytes": sum(row["size"] for row in rows),
            "rows_sha256": namespace["_data_sync_inventory_rows_sha256"](rows),
            "rows": rows,
        }
        raw = json.dumps(
            payload, separators=(",", ":"), sort_keys=True, ensure_ascii=True,
        ).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        name = f"p{index:08d}-{digest[:24]}.json"
        (generation_dir / name).write_bytes(raw)
        descriptors.append({
            "page_index": index,
            "file_count": len(rows),
            "total_bytes": sum(row["size"] for row in rows),
            "page_sha256": digest,
            "file_name": name,
        })
    index_path = generation_dir / "page-index.jsonl"
    index_path.write_text(
        "".join(
            json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
            for row in descriptors
        ),
        encoding="utf-8",
    )
    result = {
        "generation_id": generation_id,
        "generation_dir": str(generation_dir),
        "page_index_path": str(index_path),
        "page_index_sha256": hashlib.sha256(index_path.read_bytes()).hexdigest(),
        "page_count": 2,
        "page_size": 2,
        "file_count": 3,
        "total_bytes": 6,
    }
    generation = namespace["_data_sync_validate_disk_inventory_generation"](result, work)
    assert "rows" not in generation
    first = namespace["_data_sync_disk_manifest_page"](generation)
    second = namespace["_data_sync_disk_manifest_page"](
        generation, raw_cursor=first["next_cursor"]
    )
    assert [row["path"] for row in first["rows"]] == ["a.json", "b.json"]
    assert [row["path"] for row in second["rows"]] == ["c.json"]
    assert second["is_last_page"] is True and second["next_cursor"] is None
    (generation_dir / descriptors[1]["file_name"]).write_bytes(b"tampered")
    with __import__("pytest").raises(ValueError, match="page hash mismatch"):
        namespace["_data_sync_disk_manifest_page"](
            generation,
            raw_cursor=first["next_cursor"],
        )


def test_disk_generation_gc_is_confined_bounded_and_preserves_current_or_leased(tmp_path):
    tree = ast.parse(BOT)
    node = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_data_sync_gc_disk_inventory_generations"
    )
    work = tmp_path / ".data-sync-snapshots"
    generations = work / "inventory-generations"
    generations.mkdir(parents=True)
    ids = [f"{index:064x}" for index in range(10)]
    for generation_id in ids:
        path = generations / generation_id
        path.mkdir()
        (path / "page-index.jsonl").write_text("", encoding="utf-8")
        os.utime(path, (1, 1))
    snapshot_path = tmp_path / "sync_inventory_current.json"
    snapshot_path.write_text(json.dumps({
        "generation": {"generation_id": ids[9]},
    }), encoding="utf-8")
    leased = work / "inventory-acks" / ids[8]
    leased.mkdir(parents=True)
    os.utime(leased, (999, 999))
    namespace = {
        "Path": Path,
        "json": json,
        "os": os,
        "re": re,
        "shutil": shutil,
        "time": time,
        "_DATA_SYNC_INVENTORY_GENERATION_TTL_SECONDS": 100,
        "_DATA_SYNC_INVENTORY_GENERATION_MAX": 2,
        "_data_sync_inventory_snapshot_path": lambda: snapshot_path,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    removed = namespace["_data_sync_gc_disk_inventory_generations"](
        work,
        protected_generation_ids={ids[7]},
        now=1000,
    )
    assert ids[9] not in removed and (generations / ids[9]).is_dir()
    assert ids[8] not in removed and (generations / ids[8]).is_dir()
    assert ids[7] not in removed and (generations / ids[7]).is_dir()
    assert removed
    assert all(not (generations / value).exists() for value in removed)


def test_sync_requires_current_inventory_and_targeted_refresh_never_walks_volume():
    assert '[string]$FirstPage.inventory_status -ne "CURRENT"' in SYNC_SCRIPT
    assert "Get-CompleteDataSyncManifest -FirstPage $manifest" in SYNC_SCRIPT
    assert '$expectedInventoryStatus = if ($IdentityOnly) { "IDENTITY_ONLY" } else { "CURRENT" }' in SYNC_LOOP
    assert '[string]$preflight.inventory_status -ne $expectedInventoryStatus' in SYNC_LOOP
    assert '-Uri (New-DataSyncManifestUri -Path $rel)' in SYNC_SCRIPT
    assert '[string]$freshManifest.targeted_path -ne $rel' in SYNC_SCRIPT
    tree = ast.parse(BOT)
    targeted = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_data_sync_targeted_inventory")
    targeted_source = ast.unparse(targeted)
    assert "_data_sync_resolve_relpath" in targeted_source
    assert "_data_sync_inventory_record" in targeted_source
    assert "os.walk" not in targeted_source


def test_guarded_workflow_has_exact_paper_only_flatten_recovery_mode():
    workflow = (ROOT.parents[1] / ".github" / "workflows" / "fly-bot-deploy.yml").read_text(encoding="utf-8")
    assert "- flatten-paper-exposure" in workflow
    block = workflow.split("  flatten-paper-exposure:", 1)[1].split("\n  restart-only:", 1)[0]
    assert 'health.get("force_paper_mode") is True' in block
    assert 'health.get("live_armed") is False' in block
    assert 'health.get("bitfinex_live_enabled") is False' in block
    assert '"/api/reconcile/phantom-cancel"' in block
    assert '"/api/orders/cancel"' in block
    assert "AUTHORIZED_PAPER_UPGRADE_BOUNDARY_UNKNOWN" in block
