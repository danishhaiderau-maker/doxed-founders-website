param(
  [string]$SourceUrl = "https://doxed-btc-bot.fly.dev",
  [int]$IntervalSec = 180,
  [int]$FullSyncIntervalSec = 1800
)

$ErrorActionPreference = "Continue"

# Windows PowerShell hosts launched without the Microsoft.PowerShell.Utility
# module may not expose Get-FileHash. Keep sync receipts fail-closed while
# providing the same SHA-256 contract via the framework implementation.
if (-not (Get-Command Get-FileHash -ErrorAction SilentlyContinue)) {
  function Get-FileHash {
    param(
      [Parameter(Mandatory = $true)][string]$LiteralPath,
      [ValidateSet("SHA256")][string]$Algorithm = "SHA256"
    )
    $resolved = [System.IO.Path]::GetFullPath($LiteralPath)
    $stream = [System.IO.File]::OpenRead($resolved)
    try {
      $sha = [System.Security.Cryptography.SHA256]::Create()
      try { $hex = [System.BitConverter]::ToString($sha.ComputeHash($stream)).Replace("-", "") }
      finally { $sha.Dispose() }
    } finally {
      $stream.Dispose()
    }
    [pscustomobject]@{ Algorithm = "SHA256"; Hash = $hex; Path = $resolved }
  }
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "fly-canonical-lock.ps1")
. (Join-Path $scriptDir "fly-data-paths.ps1")
. (Join-Path $scriptDir "fly-mirror-quarantine.ps1")
. (Join-Path $scriptDir "fly-sync-backoff.ps1")
$SourceUrl = Get-CanonicalFlyBotUrl -RequestedUrl $SourceUrl
$agentDir = Join-Path $repoRoot "services\btc-conservative-agent"
$analyzerReport = Join-Path $agentDir "analysis_dashboard.html"
$analyzerArchiveRoot = Join-Path $agentDir "research_session_archives"
$vaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"
$lockFile = Join-Path $repoRoot ".fly-data-sync-loop.lock"
$machineStateBase = if ($env:LOCALAPPDATA) {
  $env:LOCALAPPDATA
} else {
  [System.IO.Path]::GetTempPath()
}
$machineLockDir = Join-Path $machineStateBase "DoxxedCrypto\locks"
New-Item -ItemType Directory -Path $machineLockDir -Force | Out-Null
$guardFile = Join-Path $machineLockDir ".fly-data-sync-loop.guard"
$mirrorDir = Get-DoxxedFlyMirrorDir
$heartbeatFile = Join-Path $mirrorDir ".fly-data-sync-loop.heartbeat.json"
$logFile = Join-Path $mirrorDir "logs\fly-data-sync.log"
$freshSignalFile = Join-Path $mirrorDir ".fly-data-sync-loop.last-fresh.json"
$relayEvidenceDestination = Join-Path $mirrorDir "relay_lifecycle_evidence_v1.json"
$sizeReportFile = Join-Path $mirrorDir "_size_report.json"
$growthStateFile = Join-Path $mirrorDir ".fly-sync-growth-state.json"
$generationLeaseFile = Join-Path $mirrorDir ".fly-mirror-generation.lease"
New-Item -ItemType Directory -Path (Split-Path -Parent $logFile) -Force | Out-Null
$relayEvidenceLastSuccessAt = if (Test-Path -LiteralPath $relayEvidenceDestination -PathType Leaf) {
  (Get-Item -LiteralPath $relayEvidenceDestination).LastWriteTimeUtc.ToString("o")
} else { $null }
$env:PLATFORM_RELAY_EVIDENCE_FILE = $relayEvidenceDestination
$env:PLATFORM_SOURCE_BOT_URL = $SourceUrl
$consecutiveFailures = 0
$maximumFailureBackoffSec = 1800

function Write-Utf8NoBomJsonAtomic {
  param(
    [Parameter(Mandatory = $true)]$Value,
    [Parameter(Mandatory = $true)][string]$LiteralPath,
    [int]$Depth = 6
  )
  $target = [System.IO.Path]::GetFullPath($LiteralPath)
  $temporary = "$target.tmp-$PID-$([Guid]::NewGuid().ToString('N'))"
  $json = $Value | ConvertTo-Json -Depth $Depth
  $encoding = New-Object System.Text.UTF8Encoding($false)
  try {
    [System.IO.File]::WriteAllText($temporary, $json + [Environment]::NewLine, $encoding)
    Move-Item -LiteralPath $temporary -Destination $target -Force
  } finally {
    if (Test-Path -LiteralPath $temporary) {
      Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
  }
}

function Publish-AnalyzerLeaseDeferredReceipt {
  param(
    [Parameter(Mandatory = $true)][string]$ObservedSourceRevision,
    [Parameter(Mandatory = $true)][string]$LastMirroredSourceRevision,
    [Parameter(Mandatory = $true)][string]$HeartbeatPath,
    [Parameter(Mandatory = $true)][string]$MirrorPath,
    [Parameter(Mandatory = $true)][int]$LeaseErrorCode
  )
  # Failure to acquire FileShare.None is the authoritative proof that another
  # live process still owns the OS lease. The JSON lease body is diagnostic
  # only and cannot safely be read while that exclusive handle is held.
  if (
    $LeaseErrorCode -notin @(32, 33) -or
    [string]::IsNullOrWhiteSpace($ObservedSourceRevision) -or
    [string]::IsNullOrWhiteSpace($LastMirroredSourceRevision) -or
    -not $ObservedSourceRevision.Equals($LastMirroredSourceRevision, [StringComparison]::OrdinalIgnoreCase)
  ) { return $false }

  $canonicalPointer = Join-Path $MirrorPath "canonical_dataset_current.json"
  if (
    -not (Test-Path -LiteralPath $HeartbeatPath -PathType Leaf) -or
    -not (Test-Path -LiteralPath $canonicalPointer -PathType Leaf)
  ) { return $false }

  try {
    $beforePointerHash = (Get-FileHash -LiteralPath $canonicalPointer -Algorithm SHA256).Hash
    $canonical = Get-Content -LiteralPath $canonicalPointer -Raw | ConvertFrom-Json
    $entryHash = [string]$canonical.entry_hash
    $datasetChecksum = [string]$canonical.dataset_checksum
    if ($entryHash -notmatch '^[0-9a-fA-F]{64}$' -or $datasetChecksum -notmatch '^[0-9a-fA-F]{64}$') {
      return $false
    }

    $candidate = Get-Content -LiteralPath $HeartbeatPath -Raw | ConvertFrom-Json
    if (
      $candidate.ok -ne $true -or
      $candidate.inProgress -eq $true -or
      [string]$candidate.revisionParity -ne "MATCH" -or
      -not ([string]$candidate.sourceRevision).Equals($ObservedSourceRevision, [StringComparison]::OrdinalIgnoreCase) -or
      -not ([string]$candidate.observedSourceRevision).Equals($ObservedSourceRevision, [StringComparison]::OrdinalIgnoreCase) -or
      -not ([string]$candidate.mirroredSourceRevision).Equals($ObservedSourceRevision, [StringComparison]::OrdinalIgnoreCase)
    ) { return $false }

    # Re-read the O(1) canonical pointer immediately before publication. The
    # held analyzer lease prevents file promotion; this double read also fails
    # closed if an unexpected writer disregards the lease contract.
    $afterPointerHash = (Get-FileHash -LiteralPath $canonicalPointer -Algorithm SHA256).Hash
    if ($beforePointerHash -ne $afterPointerHash) { return $false }

    $receipt = [ordered]@{}
    foreach ($property in $candidate.PSObject.Properties) {
      $receipt[$property.Name] = $property.Value
    }
    $receipt["syncedAt"] = [DateTimeOffset]::UtcNow.ToString("o")
    $receipt["skipped"] = $true
    $receipt["reason"] = "analyzer_lease_immutable_mirror"
    $receipt["syncDeferred"] = $true
    $receipt["deferredBy"] = "live_analyzer_generation_lease"
    $receipt["canonicalManifestEntryHash"] = $entryHash.ToLowerInvariant()
    $receipt["canonicalDatasetChecksum"] = $datasetChecksum.ToLowerInvariant()
    $receipt["pollOk"] = $true
    $receipt["consecutiveFailures"] = 0
    $receipt["backoffSec"] = 0
    $receipt["nextRetryAt"] = $null
    Write-Utf8NoBomJsonAtomic -Value $receipt -LiteralPath $HeartbeatPath -Depth 8
    return $true
  } catch {
    Write-Warning "Deferred immutable-mirror receipt rejected: $($_.Exception.Message)"
    return $false
  }
}

function Remove-OrphanedMirrorCandidates {
  param([Parameter(Mandatory = $true)][string]$MirrorPath)
  if (-not (Test-Path -LiteralPath $MirrorPath -PathType Container)) { return }
  # Stream candidate names instead of materializing every FileInfo object in
  # the multi-gigabyte canonical mirror.  The old Get-ChildItem pipeline made
  # loop startup memory proportional to the complete mirror and delayed the
  # first terminal heartbeat, which in turn blocked the analyzer fail-closed.
  $candidatePattern = '\.(?<owner>\d+)\.[0-9a-fA-F]{32}\.download(?:\.replace-backup)?$'
  $candidateEnumerator = $null
  try {
    # PowerShell otherwise auto-enumerates an IEnumerable assigned from a
    # method call and materializes the full result. Hold the .NET enumerator
    # explicitly so memory stays O(1) as the mirror grows.
    $candidateEnumerator = [System.IO.Directory]::EnumerateFiles(
      [System.IO.Path]::GetFullPath($MirrorPath),
      '*',
      [System.IO.SearchOption]::AllDirectories
    ).GetEnumerator()
  } catch {
    Add-Content -LiteralPath $logFile -Value (
      "$((Get-Date).ToUniversalTime().ToString('o'))`tWARN`t" +
      "orphan candidate enumeration failed closed: $($_.Exception.GetType().Name)"
    )
    return
  }
  try {
    while ($candidateEnumerator.MoveNext()) {
      $candidatePath = [string]$candidateEnumerator.Current
      $candidateName = [System.IO.Path]::GetFileName($candidatePath)
      if ($candidateName -notmatch $candidatePattern) { continue }
      $owner = [int]$matches['owner']
      # The exclusive loop guard proves this process has no in-flight candidate
      # at the top of a cycle. Candidates owned by this PID are leftovers from a
      # completed/failed prior cycle; candidates from dead PIDs are abandoned.
      $ownerAlive = $owner -gt 0 -and $null -ne (Get-Process -Id $owner -ErrorAction SilentlyContinue)
      if ($owner -eq $PID -or -not $ownerAlive) {
        Remove-Item -LiteralPath $candidatePath -Force -ErrorAction SilentlyContinue
      }
    }
  } catch {
    Add-Content -LiteralPath $logFile -Value (
      "$((Get-Date).ToUniversalTime().ToString('o'))`tWARN`t" +
      "orphan candidate traversal failed closed: $($_.Exception.GetType().Name)"
    )
  } finally {
    if ($candidateEnumerator -is [System.IDisposable]) { $candidateEnumerator.Dispose() }
  }
}

# Growth trigger (default 50 MB). Override with FLY_VOLUME_SYNC_THRESHOLD_MB.
# Each ordinary poll obtains a freshly completed inventory generation. Polling
# faster than the configured interval caused immediate re-scans after long
# syncs and starved the runtime control plane.
$thresholdMb = 50.0
if ($env:FLY_VOLUME_SYNC_THRESHOLD_MB) {
  try { $thresholdMb = [double]$env:FLY_VOLUME_SYNC_THRESHOLD_MB } catch { $thresholdMb = 50.0 }
}
if ($thresholdMb -lt 5) { $thresholdMb = 5.0 }
$thresholdBytes = [int64]($thresholdMb * 1MB)
$minimumInventoryPollSec = 180
$pollSec = [Math]::Max($minimumInventoryPollSec, [int]$IntervalSec)
$fullSyncSec = [Math]::Max(600, $FullSyncIntervalSec)

# Compute local mirror size + merge Fly /api/data_size numbers into one report.
# Written after every sync cycle so the dashboard always has fresh local info.
function Write-SizeReport {
  param(
    [string]$MirrorPath,
    [string]$ReportFile,
    [string]$FlyApiUrl,
    [int]$IntervalSec
  )
  try {
    if (-not (Test-Path -LiteralPath $MirrorPath)) {
      New-Item -ItemType Directory -Path $MirrorPath -Force | Out-Null
    }
    $localSizeBytes = 0
    $localFileCount = 0
    $mirrorItems = Get-ChildItem -LiteralPath $MirrorPath -Recurse -File -Force -ErrorAction SilentlyContinue
    if ($mirrorItems) {
      $localFileCount = @($mirrorItems).Count
      $sum = ($mirrorItems | Measure-Object Length -Sum).Sum
      if ($sum) { $localSizeBytes = [int64]$sum }
    }
    $localSizeMb = [Math]::Round($localSizeBytes / 1MB, 2)

    $report = [ordered]@{
      local_size_mb        = $localSizeMb
      local_file_count     = $localFileCount
      sync_interval_seconds = $IntervalSec
      sync_threshold_mb    = $thresholdMb
      computed_at          = (Get-Date).ToUniversalTime().ToString("o")
      fly_size_mb          = $null
      fly_volume_pct       = $null
      fly_top_files        = $null
      fly_source           = $null
      fly_error            = $null
    }

    # Pull Fly-side numbers so one file has both local + Fly data.
    if ($env:BOT_ADMIN_TOKEN) {
      try {
        $flyHeaders = @{ "X-Bot-Admin-Token" = $env:BOT_ADMIN_TOKEN }
        $flyRes = Invoke-RestMethod `
          -Uri ($FlyApiUrl.TrimEnd("/") + "/api/data_size") `
          -Headers $flyHeaders `
          -TimeoutSec 15 `
          -ErrorAction Stop
        $report.fly_size_mb = $flyRes.runtime_size_mb
        $report.fly_volume_pct = $flyRes.volume_pct
        $report.fly_top_files = $flyRes.top_files
        $report.fly_source = $flyRes.source
        $report.fly_runtime_path = $flyRes.runtime_path
        $report.fly_volume_total_mb = $flyRes.volume_total_mb
        $report.fly_cleanup_status = $flyRes.cleanup_status
        $report.fly_computed_at = $flyRes.computed_at
      } catch {
        $report.fly_error = $_.Exception.Message
      }
    }

    Write-Utf8NoBomJsonAtomic -Value $report -LiteralPath $ReportFile -Depth 6
  } catch {
    Add-Content -LiteralPath $logFile -Value (
      "$((Get-Date).ToUniversalTime().ToString('o'))`tWARN`tsize report failed: $($_.Exception.Message)"
    )
  }
}

function Test-CompleteAnalyzerArchive {
  param([Parameter(Mandatory=$true)][string]$ArchivePath)
  try {
    $root = [IO.Path]::GetFullPath($ArchivePath).TrimEnd('\', '/')
    $manifestPath = Join-Path $root "archive_manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { return $false }
    $archive = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($archive.schema -ne "research_session_archive_v2" -or $archive.complete -ne $true) { return $false }
    if ([string]$archive.analyzer_revision -notmatch '^[0-9a-fA-F]{40}$') { return $false }
    if ([string]$archive.source_data_revision -notmatch '^[0-9a-fA-F]{64}$') { return $false }
    if (-not [string]$archive.cohort_schema) { return $false }
    if (-not $archive.report_manifest_sha256 -or $null -eq $archive.files) { return $false }
    $declared = @{}
    foreach ($row in @($archive.files)) {
      $relative = ([string]$row.path).Replace('\', '/')
      if (-not $relative -or $relative.StartsWith('/') -or $relative -match '(^|/)\.\.(/|$)' -or $declared.ContainsKey($relative.ToLowerInvariant())) { return $false }
      $target = [IO.Path]::GetFullPath((Join-Path $root $relative))
      if (-not $target.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { return $false }
      if (-not (Test-Path -LiteralPath $target -PathType Leaf)) { return $false }
      $item = Get-Item -LiteralPath $target
      if ([int64]$row.size_bytes -ne [int64]$item.Length) { return $false }
      if ([string]$row.sha256 -notmatch '^[0-9a-fA-F]{64}$') { return $false }
      if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ine [string]$row.sha256) { return $false }
      $declared[$relative.ToLowerInvariant()] = $true
    }
    foreach ($required in @("report_manifest.json", "analysis_dashboard.html", "session_meta.json")) {
      if (-not $declared.ContainsKey($required)) { return $false }
    }
    $actual = @(Get-ChildItem -LiteralPath $root -Recurse -File -Force | ForEach-Object {
      $_.FullName.Substring($root.Length + 1).Replace('\', '/').ToLowerInvariant()
    } | Where-Object { $_ -ne "archive_manifest.json" })
    if (@($actual).Count -ne $declared.Count) { return $false }
    foreach ($relative in $actual) { if (-not $declared.ContainsKey($relative)) { return $false } }
    $reportHash = (Get-FileHash -LiteralPath (Join-Path $root "report_manifest.json") -Algorithm SHA256).Hash
    return $reportHash -ieq [string]$archive.report_manifest_sha256
  } catch {
    return $false
  }
}


if (-not (Test-Path (Split-Path -Parent $logFile))) {
  New-Item -ItemType Directory -Path (Split-Path -Parent $logFile) -Force | Out-Null
}

# The PID marker is useful for recovery, but checking then writing it was not an
# atomic lock. Hold an exclusive file handle for the lifetime of this loop so
# two simultaneous Start desktop tools requests cannot create competing syncs.
$guardStream = $null
try {
  $guardStream = [System.IO.File]::Open(
    $guardFile,
    [System.IO.FileMode]::OpenOrCreate,
    [System.IO.FileAccess]::ReadWrite,
    [System.IO.FileShare]::None
  )
} catch {
  exit 0
}
Set-Content -LiteralPath $lockFile -Value "$PID" -NoNewline -Encoding UTF8

. (Join-Path $scriptDir "home-bot-vault-env.ps1")
Import-HomeBotVaultConfig -VaultEnvPath $vaultEnv
if (-not $env:BOT_ADMIN_TOKEN) {
  throw "BOT_ADMIN_TOKEN is required for the canonical Fly data mirror."
}

$preflightManifestAttempts = 220
$preflightManifestTimeoutSec = 90
# One inventory generation may require several resumable child invocations;
# 300 seconds is the bound for each child, not for the complete generation.
# Join one nonce-bound build while its bounded counters advance. A stalled
# build and the total join both retain independent hard limits, and the prior
# canonical mirror remains published throughout.
$preflightInventoryStallMaxSec = 360
$preflightInventoryWaitMaxSec = 1800
$preflightInventoryElapsedProvider = $null
$relaySyncAttempts = 2
$relayEvidenceAttemptIntervalSec = 1800
# Core mirror ownership and its terminal receipt must become available first.
# A fresh loop waits one optional cadence before the first relay collection;
# this avoids a 7 MB nested payload expansion delaying analyzer admission at
# every launcher/recovery boundary.
$lastRelayEvidenceAttemptAt = [DateTimeOffset]::UtcNow
$fullSyncQuietSuccesses = 3
$fullSyncQuietProbeTimeoutSec = 8
$fullSyncQuietMaxWaitSec = 90

function Get-FlySyncPreflightManifest {
  param(
    [Parameter(Mandatory = $true)][string]$ManifestUri,
    [switch]$IdentityOnly
  )
  $preflightHeaders = @{ "X-Bot-Admin-Token" = $env:BOT_ADMIN_TOKEN }
  $requestUri = $ManifestUri
  if (-not $IdentityOnly) {
    $refreshNonce = [guid]::NewGuid().ToString("N")
    $separator = if ($requestUri.Contains("?")) { "&" } else { "?" }
    $requestUri = "$requestUri${separator}fresh=1&nonce=$refreshNonce"
  }
  $preflightWait = [System.Diagnostics.Stopwatch]::StartNew()
  $lastProgressKey = $null
  $lastProgressAtSec = 0.0
  for ($attempt = 1; $attempt -le $preflightManifestAttempts; $attempt++) {
    $terminalInventoryFailure = $false
    try {
      $preflight = Invoke-RestMethod `
        -Uri $requestUri `
        -Headers $preflightHeaders `
        -TimeoutSec $preflightManifestTimeoutSec `
        -ErrorAction Stop
      $expectedInventoryStatus = if ($IdentityOnly) { "IDENTITY_ONLY" } else { "CURRENT" }
      if ([string]$preflight.inventory_status -ne $expectedInventoryStatus) {
        $elapsedSec = if ($preflightInventoryElapsedProvider) {
          [double](& $preflightInventoryElapsedProvider)
        } else {
          [double]$preflightWait.Elapsed.TotalSeconds
        }
        $worker = $preflight.inventory_worker
        $progressKey = @(
          [string]$worker.phase,
          [string]$worker.files_seen,
          [string]$worker.dirs_seen,
          [string]$worker.rows_discovered
        ) -join ":"
        if ($null -eq $lastProgressKey -or $progressKey -cne $lastProgressKey) {
          $lastProgressKey = $progressKey
          $lastProgressAtSec = $elapsedSec
        }
        $refreshing = [bool]$worker.refreshing
        $terminalInventoryFailure = (
          -not $refreshing -or
          -not [string]::IsNullOrWhiteSpace([string]$preflight.inventory_error) -or
          [string]$preflight.inventory_build_status -eq "FAILED"
        )
        if ($terminalInventoryFailure) {
          throw (
            "Fly data-sync inventory terminated without CURRENT " +
            "(status=$([string]$preflight.inventory_status), " +
            "build=$([string]$preflight.inventory_build_status), " +
            "error=$([string]$preflight.inventory_error))."
          )
        }
        if (($elapsedSec - $lastProgressAtSec) -ge $preflightInventoryStallMaxSec) {
          $terminalInventoryFailure = $true
          throw "Fly data-sync inventory made no bounded progress before the stall deadline."
        }
        throw (
          "Fly data-sync inventory is not CURRENT " +
          "(status=$([string]$preflight.inventory_status), expected=$expectedInventoryStatus)."
        )
      }
      return $preflight
    } catch {
      if ($terminalInventoryFailure) { throw }
      $elapsedSec = if ($preflightInventoryElapsedProvider) {
        [double](& $preflightInventoryElapsedProvider)
      } else {
        [double]$preflightWait.Elapsed.TotalSeconds
      }
      if (($elapsedSec - $lastProgressAtSec) -ge $preflightInventoryStallMaxSec) {
        throw "Fly data-sync preflight made no observable progress before the stall deadline."
      }
      $remainingWaitSec = [Math]::Floor($preflightInventoryWaitMaxSec - $elapsedSec)
      if ($attempt -ge $preflightManifestAttempts -or $remainingWaitSec -le 0) {
        throw (
          "Fly data-sync stage=loop_manifest_preflight failed after " +
          "$attempt/$preflightManifestAttempts attempt(s): $($_.Exception.Message)"
        )
      }
      # BUILDING/STALE_REVALIDATING is a single-flight resumable scan, not a
      # reason to launch another worker. Respect the server's Retry-After as a
      # minimum while bounding the complete join window just beyond the
      # bounded child invocation. Progress may span several such invocations;
      # the independent stall and absolute deadlines above prevent starvation.
      $retryAfterSec = 0
      try {
        $response = $_.Exception.Response
        if ($response -and $response.Headers) {
          if ($response.Headers.RetryAfter -and $response.Headers.RetryAfter.Delta) {
            $retryAfterSec = [int][Math]::Ceiling($response.Headers.RetryAfter.Delta.TotalSeconds)
          } elseif ($response.Headers["Retry-After"]) {
            [void][int]::TryParse([string]$response.Headers["Retry-After"], [ref]$retryAfterSec)
          }
        }
      } catch { $retryAfterSec = 0 }
      $backoffSec = [Math]::Min(10, 2 * $attempt)
      $delaySec = [Math]::Min(
        $remainingWaitSec,
        [Math]::Max(1, [Math]::Max($retryAfterSec, $backoffSec))
      )
      Start-Sleep -Seconds $delaySec
    }
  }
}

function Invoke-OptionalRelayEvidenceSync {
  $lastRelayError = $null
  for ($attempt = 1; $attempt -le $relaySyncAttempts; $attempt++) {
    try {
      # The validated relay payload can be several megabytes and expands into
      # a large nested PowerShell object graph. Run it outside the long-lived
      # core sync owner so that memory is reclaimed when the optional attempt
      # exits and cannot accumulate across three-minute identity polls.
      $childHost = (Get-Process -Id $PID -ErrorAction Stop).Path
      $relayScript = Join-Path $scriptDir "sync-platform-relay-evidence.ps1"
      $childOutput = @(
        & $childHost -NoProfile -ExecutionPolicy Bypass -File $relayScript 2>&1 |
          ForEach-Object { [string]$_ }
      )
      if ($LASTEXITCODE -ne 0) {
        $safeCode = "CHILD_FAILED"
        $boundedDiagnostic = ($childOutput -join "`n")
        if ($boundedDiagnostic -match '\[RELAY_EVIDENCE_([A-Z0-9_]+)\]') {
          $safeCode = $matches[1]
        }
        throw "[RELAY_EVIDENCE_$safeCode]"
      }
      return @($childOutput | Where-Object { $_ })[-1]
    } catch {
      $lastRelayError = $_
      if ($attempt -lt $relaySyncAttempts) { Start-Sleep -Seconds 2 }
    }
  }
  throw (
    "Fly data-sync stage=optional_relay_evidence failed after " +
    "$relaySyncAttempts/$relaySyncAttempts attempt(s): $($lastRelayError.Exception.Message)"
  )
}

function Wait-FlyRuntimeQuietForFullSync {
  param([Parameter(Mandatory = $true)][string]$BaseUrl)
  $quietWatch = [System.Diagnostics.Stopwatch]::StartNew()
  $consecutive = 0
  $attempt = 0
  while ($quietWatch.Elapsed.TotalSeconds -lt $fullSyncQuietMaxWaitSec) {
    $attempt += 1
    $probeWatch = [System.Diagnostics.Stopwatch]::StartNew()
    try {
      $health = Invoke-RestMethod `
        -Uri ($BaseUrl.TrimEnd("/") + "/health") `
        -TimeoutSec $fullSyncQuietProbeTimeoutSec `
        -ErrorAction Stop
      if ($health.process_alive -ne $true -or $health.status -eq "starting") {
        throw "runtime health is not ready"
      }
      $consecutive += 1
      Add-Content -LiteralPath $logFile -Value (
        "$((Get-Date).ToUniversalTime().ToString('o'))`tQUIET_PROBE`t" +
        "attempt=$attempt consecutive=$consecutive " +
        "elapsed_ms=$([Math]::Round($probeWatch.Elapsed.TotalMilliseconds))"
      )
      if ($consecutive -ge $fullSyncQuietSuccesses) { return }
    } catch {
      $consecutive = 0
      Add-Content -LiteralPath $logFile -Value (
        "$((Get-Date).ToUniversalTime().ToString('o'))`tQUIET_PROBE_FAILED`t" +
        "attempt=$attempt elapsed_ms=$([Math]::Round($probeWatch.Elapsed.TotalMilliseconds)) " +
        "error=$($_.Exception.Message)"
      )
    }
    Start-Sleep -Seconds 5
  }
  throw (
    "Fly data-sync stage=runtime_quiet_soak failed: fewer than " +
    "$fullSyncQuietSuccesses consecutive healthy probes within $fullSyncQuietMaxWaitSec seconds."
  )
}

$lastSyncedTotalBytes = [int64]0
$lastSyncedVolumeUsedBytes = [int64]0
$lastSyncAt = [datetime]::SpecifyKind([datetime]'1970-01-01', 'Utc')
$lastSyncedSourceRevision = $null
if (Test-Path -LiteralPath $growthStateFile) {
  try {
    $growthState = Get-Content -LiteralPath $growthStateFile -Raw | ConvertFrom-Json
    if ($growthState.PSObject.Properties.Name -contains "lastSyncedTotalBytes") {
      $lastSyncedTotalBytes = [int64]$growthState.lastSyncedTotalBytes
    }
    if ($growthState.PSObject.Properties.Name -contains "lastSyncedVolumeUsedBytes") {
      $lastSyncedVolumeUsedBytes = [int64]$growthState.lastSyncedVolumeUsedBytes
    }
    if ($growthState.PSObject.Properties.Name -contains "lastSyncAt") {
      $parsed = [datetime]::MinValue
      if ([datetime]::TryParse($growthState.lastSyncAt, [ref]$parsed)) {
        $lastSyncAt = $parsed.ToUniversalTime()
      }
    }
    if ($growthState.PSObject.Properties.Name -contains "lastSyncedSourceRevision") {
      $lastSyncedSourceRevision = [string]$growthState.lastSyncedSourceRevision
      if (-not $lastSyncedSourceRevision) { $lastSyncedSourceRevision = $null }
    }
  } catch {
    Add-Content -LiteralPath $logFile -Value (
      "$((Get-Date).ToUniversalTime().ToString('o'))`tWARN`tunreadable growth state: $($_.Exception.Message)"
    )
  }
}

try {
  while ($true) {
    Remove-OrphanedMirrorCandidates -MirrorPath $mirrorDir
    $started = Get-Date
    $didSync = $false
    $sleepSec = $pollSec
    $observedSourceRevision = $null
    $currentStage = "loop_start"
    $relayEvidenceStatus = [ordered]@{
      ok = $null
      errorCode = "NOT_ATTEMPTED"
      lastSuccessAt = $relayEvidenceLastSuccessAt
    }
    try {
      $currentStage = "loop_manifest_preflight"
      # Ordinary three-minute checks need only the cheap authority identity.
      # A complete volume inventory is requested below only when a revision,
      # fresh-collection signal, or the reviewed 30-minute interval makes an
      # atomic mirror pass due.  This prevents the 180-second desktop poll
      # from continuously restarting a metadata-heavy Fly-volume scan after
      # its 150-second cache expires.
      $manifest = Get-FlySyncPreflightManifest `
        -ManifestUri ($SourceUrl.TrimEnd("/") + "/api/data-sync/identity") `
        -IdentityOnly
      if ($manifest.PSObject.Properties.Name -contains "source_git_rev") {
        $observedSourceRevision = [string]$manifest.source_git_rev
        if (-not $observedSourceRevision) { $observedSourceRevision = $null }
      }

      # Fresh Collection signal: when the Fly dashboard's Fresh Collection
      # toggle wipes Fly, it bumps manifest.fresh_collection_signal_ts. The
      # operational 'Wipe Fly Data Only' button leaves this field untouched
      # so we keep the local mirror. Compare against the last-seen value
      # persisted on disk so the wipe survives loop restarts and so a single
      # signal is only honoured once.
      $currentSignal = 0.0
      if ($manifest.PSObject.Properties.Name -contains "fresh_collection_signal_ts") {
        $currentSignal = [double]$manifest.fresh_collection_signal_ts
      }
      $lastSeenSignal = 0.0
      if (Test-Path -LiteralPath $freshSignalFile) {
        try {
          $lastSeenRaw = Get-Content -LiteralPath $freshSignalFile -Raw | ConvertFrom-Json
          if ($lastSeenRaw.PSObject.Properties.Name -contains "signal_ts") {
            $lastSeenSignal = [double]$lastSeenRaw.signal_ts
          }
        } catch {
          Add-Content -LiteralPath $logFile -Value (
            "$((Get-Date).ToUniversalTime().ToString('o'))`tWARN`tunreadable fresh-signal state: $($_.Exception.Message)"
          )
        }
      }
      if ($currentSignal -gt $lastSeenSignal) {
        Write-Host "[FRESH COLLECTION] Signal received ($currentSignal > $lastSeenSignal). Quarantining local mirror before sync."
        Add-Content -LiteralPath $logFile -Value (
          "$((Get-Date).ToUniversalTime().ToString('o'))`tFRESH`tlocal mirror quarantine signalled ($currentSignal > $lastSeenSignal)"
        )
        $quarantineResult = Invoke-FlyMirrorEpochQuarantine `
          -MirrorPath $mirrorDir `
          -QuarantineRoot (Join-Path (Split-Path -Parent $mirrorDir) 'fly-data-quarantine') `
          -FreshCollectionSignalTs $currentSignal
        Add-Content -LiteralPath $logFile -Value (
          "$((Get-Date).ToUniversalTime().ToString('o'))`tFRESH`tquarantine complete files=$($quarantineResult.FileCount) path=$($quarantineResult.Destination)"
        )
        # A signal is acknowledged only after recursive preservation, hash
        # verification and active-mirror emptying all succeed. Any lock or
        # verification failure leaves this receipt untouched and blocks sync.
        @{ signal_ts = $currentSignal; signalled_at = (Get-Date).ToUniversalTime().ToString("o") } |
          ConvertTo-Json | Set-Content -LiteralPath $freshSignalFile -Encoding UTF8
        $lastSyncedTotalBytes = 0
        $lastSyncedVolumeUsedBytes = 0
        # The active mirror is now empty. Do not retain a revision receipt for
        # the quarantined generation if the following refresh fails.
        $lastSyncedSourceRevision = $null
      }

      $elapsedSec = ([datetime]::UtcNow - $lastSyncAt).TotalSeconds
      # Polling and mutation cadence are deliberately separate. A complete
      # 601-file Fly pass can take much longer than the three-minute poll
      # cadence; forcing one on every poll starves the Fly HTTP/control plane.
      # Identity polls refresh revision/freshness and O(1) filesystem usage.
      # The usage delta can trigger a physical inventory without repeatedly
      # walking every file on the volume.
      $forceByTime = $elapsedSec -ge $fullSyncSec
      $forceFresh = $currentSignal -gt $lastSeenSignal
      # A deployment can change schemas or files without adding 50 MB. The
      # remote revision is only an observation; it becomes the mirrored
      # revision after the complete atomic sync below succeeds.
      $forceByRevision = [bool]$observedSourceRevision -and (
        -not $lastSyncedSourceRevision -or
        -not $observedSourceRevision.Equals($lastSyncedSourceRevision, [StringComparison]::OrdinalIgnoreCase)
      )

      $currentVolumeUsedBytes = [int64]0
      if (
        $manifest.PSObject.Properties.Name -contains "volume" -and
        $null -ne $manifest.volume -and
        $manifest.volume.PSObject.Properties.Name -contains "used"
      ) {
        $currentVolumeUsedBytes = [int64]$manifest.volume.used
      }
      $volumeGrowthBytes = [int64]0
      if ($currentVolumeUsedBytes -gt 0 -and $lastSyncedVolumeUsedBytes -gt 0) {
        # A volume decrease (cleanup/rotation) is not growth and cannot force
        # a sync. The first run establishes a baseline at successful publish.
        $volumeGrowthBytes = [Math]::Max(
          [int64]0,
          [int64]($currentVolumeUsedBytes - $lastSyncedVolumeUsedBytes)
        )
      }
      $forceByGrowth = (
        $lastSyncedVolumeUsedBytes -gt 0 -and
        $volumeGrowthBytes -ge $thresholdBytes
      )

      $needsFullInventory = $forceByTime -or $forceFresh -or $forceByRevision -or $forceByGrowth
      $relayEvidenceConfigMissing = -not (
        $env:PLATFORM_API_BASE_URL -and
        $env:PLATFORM_RELAY_AGENT_SLUG -and
        $env:PLATFORM_RELAY_USER_ID
      )
      if ($relayEvidenceConfigMissing) {
        # CONFIG_MISSING is reserved for an explicit configuration result. It
        # must not describe a configured optional stage that was merely
        # deferred while a mandatory mirror repair takes priority.
        $relayEvidenceStatus.ok = $false
        $relayEvidenceStatus.errorCode = "CONFIG_MISSING"
      } elseif ($needsFullInventory) {
        $relayEvidenceStatus.errorCode = "DEFERRED_REQUIRED_SYNC"
      }
      $relayEvidenceDue = (
        ([DateTimeOffset]::UtcNow - $lastRelayEvidenceAttemptAt).TotalSeconds -ge
        $relayEvidenceAttemptIntervalSec
      )
      if (-not $relayEvidenceConfigMissing -and -not $needsFullInventory -and -not $relayEvidenceDue) {
        $relayEvidenceStatus.errorCode = "DEFERRED_CADENCE"
      }
      $currentTotalBytes = $lastSyncedTotalBytes
      $growthBytes = $volumeGrowthBytes
      if ($needsFullInventory) {
        $currentStage = "runtime_quiet_soak"
        Wait-FlyRuntimeQuietForFullSync -BaseUrl $SourceUrl
        $currentStage = "loop_full_manifest"
        $manifest = Get-FlySyncPreflightManifest `
          -ManifestUri ($SourceUrl.TrimEnd("/") + "/api/data-sync/manifest")
        if ($manifest.PSObject.Properties.Name -contains "total_bytes") {
          $currentTotalBytes = [int64]$manifest.total_bytes
        } else {
          $currentTotalBytes = [int64](($manifest.files | Measure-Object -Property size -Sum).Sum)
        }
      }

      # Relay evidence is optional and may consume two 90-second bounded
      # attempts.  Never put it ahead of a required revision repair: first
      # publish an exact Fly mirror, then refresh relay evidence on the next
      # matched cycle.  This makes the earlier "never blocks the canonical
      # mirror" contract true during deployments and recovery.
      if (
        -not $needsFullInventory -and
        -not $relayEvidenceConfigMissing -and
        $relayEvidenceDue
      ) {
        $currentStage = "optional_relay_evidence"
        $lastRelayEvidenceAttemptAt = [DateTimeOffset]::UtcNow
        try {
          $relayEvidencePath = Invoke-OptionalRelayEvidenceSync
          if ($relayEvidencePath -and (Test-Path -LiteralPath $relayEvidenceDestination -PathType Leaf)) {
            $relayEvidenceLastSuccessAt = [DateTimeOffset]::UtcNow.ToString("o")
            $relayEvidenceStatus = [ordered]@{
              ok = $true
              errorCode = $null
              lastSuccessAt = $relayEvidenceLastSuccessAt
            }
          } else {
            $relayEvidenceStatus.errorCode = "ARTIFACT_MISSING"
          }
        } catch {
          $safeCode = "SYNC_FAILED"
          if ($_.Exception.Message -match '^\[RELAY_EVIDENCE_([A-Z0-9_]+)\]$') { $safeCode = $matches[1] }
          $relayEvidenceStatus.errorCode = $safeCode
          Add-Content -LiteralPath $logFile -Value (
            "$((Get-Date).ToUniversalTime().ToString('o'))`tWARN`trelay-evidence=$safeCode"
          )
        }
      }

      if (-not ($forceByTime -or $forceByGrowth -or $forceFresh -or $forceByRevision)) {
        $consecutiveFailures = 0
        $revisionParity = $(
          if (-not $observedSourceRevision -or -not $lastSyncedSourceRevision) { "UNKNOWN" }
          elseif ($observedSourceRevision.Equals($lastSyncedSourceRevision, [StringComparison]::OrdinalIgnoreCase)) { "MATCH" }
          else { "MISMATCH" }
        )
        $heartbeat = [ordered]@{
          ok = $true
          syncedAt = (Get-Date).ToUniversalTime().ToString("o")
          source = $SourceUrl
          skipped = $true
          reason = "identity_match_before_full_interval"
          growthBytes = $volumeGrowthBytes
          growthBasis = "FLY_VOLUME_USED_BYTES_O1"
          thresholdBytes = $thresholdBytes
          thresholdMb = $thresholdMb
          currentTotalBytes = $null
          lastSyncedTotalBytes = $lastSyncedTotalBytes
          currentVolumeUsedBytes = $currentVolumeUsedBytes
          lastSyncedVolumeUsedBytes = $lastSyncedVolumeUsedBytes
          elapsedSecSinceSync = [Math]::Round($elapsedSec, 1)
          sourceRevision = $lastSyncedSourceRevision
          observedSourceRevision = $observedSourceRevision
          mirroredSourceRevision = $lastSyncedSourceRevision
          deployedRevision = $observedSourceRevision
          revisionParity = $revisionParity
          botVersion = $(if ($manifest.PSObject.Properties.Name -contains "bot_version") { [string]$manifest.bot_version } else { $null })
          tileRegistrySignature = $(if ($manifest.PSObject.Properties.Name -contains "tile_registry_signature") { [string]$manifest.tile_registry_signature } else { $null })
          activeTiles = $(if ($manifest.PSObject.Properties.Name -contains "active_tiles") { @($manifest.active_tiles) } else { @() })
          relayEvidence = $relayEvidenceStatus
          pollOk = $true
          consecutiveFailures = 0
          backoffSec = 0
          nextRetryAt = $null
        }
        $heartbeat | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $heartbeatFile -Encoding UTF8
        Add-Content -LiteralPath $logFile -Value (
          "$($heartbeat.syncedAt)`tSKIP`tidentity match; full inventory not due"
        )
        Start-Sleep -Seconds $pollSec
        continue
      }

      # Sync ALL manifest files. A prior filter excluded files >50MB, which
      # blocked the exact append-only research logs that fill the Fly volume
      # (signal_replay / ai_reason_research). Incremental chunk sync already
      # downloads only new bytes for those files.
      $syncArgs = @{
        SourceUrl = $SourceUrl
        # Reuse this cycle's authenticated preflight. The child remains safe
        # for standalone use because it fetches when InitialManifest is null.
        InitialManifest = $manifest
        ProgressHeartbeatFile = $heartbeatFile
        ProgressRelayEvidenceJson = ($relayEvidenceStatus | ConvertTo-Json -Compress)
        MirroredSourceRevision = $(if ($lastSyncedSourceRevision) { $lastSyncedSourceRevision } else { "" })
      }
      if ($forceByRevision) { $syncArgs.ForceFullRefresh = $true }
      # Publish the latest deterministic analyzer HTML back to Fly so admins
      # have an anywhere-access /analysis route. The local :9001 dashboard
      # remains the full interactive report explorer while the PC is online.
      # Publish only an immutable, completed analyzer generation. The live
      # report directory is also read by :9001 and can change between manifest
      # validation and archive creation, producing a mixed-generation bundle.
      $publishReport = $null
      if (Test-Path -LiteralPath $analyzerArchiveRoot) {
        $latestCompleteArchive = Get-ChildItem -LiteralPath $analyzerArchiveRoot -Directory -ErrorAction SilentlyContinue |
          Sort-Object Name -Descending |
          Where-Object { Test-CompleteAnalyzerArchive -ArchivePath $_.FullName } |
          Select-Object -First 1
        if ($latestCompleteArchive) {
          $publishReport = Join-Path $latestCompleteArchive.FullName "analysis_dashboard.html"
        }
      }
      if ($publishReport) {
        $syncArgs.PublishAnalyzerReport = $publishReport
      }
      $syncArgs.TargetDir = $mirrorDir
      # A complete analyzer iteration and a mirror mutation are mutually
      # exclusive.  Acquire before the child can publish inProgress so a
      # deferred sync never masks the last completed MATCH receipt.
      $generationLease = $null
      try {
        $generationLease = [System.IO.File]::Open(
          $generationLeaseFile,
          [System.IO.FileMode]::OpenOrCreate,
          [System.IO.FileAccess]::ReadWrite,
          [System.IO.FileShare]::None
        )
      } catch {
        $leaseErrorCode = $_.Exception.HResult -band 0xFFFF
        $deferredReceiptPublished = Publish-AnalyzerLeaseDeferredReceipt `
          -ObservedSourceRevision $observedSourceRevision `
          -LastMirroredSourceRevision $lastSyncedSourceRevision `
          -HeartbeatPath $heartbeatFile `
          -MirrorPath $mirrorDir `
          -LeaseErrorCode $leaseErrorCode
        Add-Content -LiteralPath $logFile -Value (
          "$((Get-Date).ToUniversalTime().ToString('o'))`tDEFER`tanalyzer owns mirror-generation lease; " +
          "immutable-receipt-published=$deferredReceiptPublished"
        )
        # Fly preflight succeeded; local analyzer ownership is a normal defer,
        # not another outage failure.
        $consecutiveFailures = 0
        Start-Sleep -Seconds $pollSec
        continue
      }
      # Keep the generation lease until the completed/failed heartbeat is
      # durably published below. Releasing it immediately after file copying
      # let a waiting analyzer acquire the lease while the prior inProgress
      # receipt was still visible, fail MIRROR_SYNC_IN_PROGRESS, and then
      # collide with the next sync on its one-minute retry.
      $currentStage = "atomic_mirror_sync"
      $result = & (Join-Path $scriptDir "sync-fly-bot-data.ps1") @syncArgs
      $didSync = $true
      $consecutiveFailures = 0
      $lastSyncedTotalBytes = $currentTotalBytes
      $lastSyncedVolumeUsedBytes = $currentVolumeUsedBytes
      $lastSyncAt = [datetime]::UtcNow
      $lastSyncedSourceRevision = $(if ($result.SourceRevision) { [string]$result.SourceRevision } else { $observedSourceRevision })
      @{
        lastSyncedTotalBytes = $lastSyncedTotalBytes
        lastSyncedVolumeUsedBytes = $lastSyncedVolumeUsedBytes
        lastSyncAt = $lastSyncAt.ToString("o")
        lastSyncedSourceRevision = $lastSyncedSourceRevision
        thresholdMb = $thresholdMb
      } | ConvertTo-Json | Set-Content -LiteralPath $growthStateFile -Encoding UTF8

      $heartbeat = [ordered]@{
        ok = $true
        syncedAt = (Get-Date).ToUniversalTime().ToString("o")
        source = $SourceUrl
        files = $result.Files
        bytes = $result.Bytes
        sourceRevision = $lastSyncedSourceRevision
        observedSourceRevision = $observedSourceRevision
        mirroredSourceRevision = $lastSyncedSourceRevision
        deployedRevision = $observedSourceRevision
        revisionParity = $(
          if (-not $observedSourceRevision -or -not $lastSyncedSourceRevision) { "UNKNOWN" }
          elseif ($observedSourceRevision.Equals($lastSyncedSourceRevision, [StringComparison]::OrdinalIgnoreCase)) { "MATCH" }
          else { "MISMATCH" }
        )
        botVersion = $(if ($manifest.PSObject.Properties.Name -contains "bot_version") { [string]$manifest.bot_version } else { $null })
        tileRegistrySignature = $(if ($manifest.PSObject.Properties.Name -contains "tile_registry_signature") { [string]$manifest.tile_registry_signature } else { $null })
        activeTiles = $(if ($manifest.PSObject.Properties.Name -contains "active_tiles") { @($manifest.active_tiles) } else { @() })
        analyzerPublished = $result.AnalyzerPublished
        relayEvidence = $relayEvidenceStatus
        prunedRotations = $result.PrunedRotations
        growthBytes = $growthBytes
        growthBasis = "FLY_VOLUME_USED_BYTES_O1"
        currentVolumeUsedBytes = $currentVolumeUsedBytes
        lastSyncedVolumeUsedBytes = $lastSyncedVolumeUsedBytes
        thresholdMb = $thresholdMb
        trigger = $(if ($forceByRevision) { "revision" } elseif ($forceByGrowth) { "growth" } elseif ($forceFresh) { "fresh" } else { "interval" })
        elapsedSec = [Math]::Round(((Get-Date) - $started).TotalSeconds, 3)
        pollOk = $true
        consecutiveFailures = 0
        backoffSec = 0
        nextRetryAt = $null
      }
      Add-Content -LiteralPath $logFile -Value (
        "$($heartbeat.syncedAt)`tOK`ttrigger=$($heartbeat.trigger)`trev=$($heartbeat.sourceRevision)`tfiles=$($heartbeat.files)`tpruned=$($heartbeat.prunedRotations)`telapsed=$($heartbeat.elapsedSec)s"
      )
    } catch {
      $consecutiveFailures += 1
      $sleepSec = Get-FlySyncFailureBackoffSeconds `
        -ConsecutiveFailures $consecutiveFailures `
        -NormalPollSeconds $pollSec `
        -MaximumBackoffSeconds $maximumFailureBackoffSec
      $failureAt = (Get-Date).ToUniversalTime().ToString("o")
      $nextRetryAt = [datetime]::UtcNow.AddSeconds($sleepSec).ToString("o")
      $failureMessage = $_.Exception.Message
      $retainedHeartbeat = $null
      if (Test-Path -LiteralPath $heartbeatFile -PathType Leaf) {
        try {
          $candidateHeartbeat = Get-Content -LiteralPath $heartbeatFile -Raw | ConvertFrom-Json
          if ($candidateHeartbeat.ok -eq $true -and
              $candidateHeartbeat.inProgress -ne $true -and
              [string]$candidateHeartbeat.revisionParity -eq "MATCH" -and
              [string]$candidateHeartbeat.mirroredSourceRevision -eq $lastSyncedSourceRevision) {
            $retainedHeartbeat = $candidateHeartbeat
          }
        } catch { $retainedHeartbeat = $null }
      }
      if ($retainedHeartbeat) {
        # A failed observation does not invalidate or rewrite the immutable
        # mirror generation that already committed successfully. Preserve its
        # completion timestamp and MATCH receipt, while exposing the failed
        # poll separately. Staleness checks still fail closed if polling does
        # not recover; a transient timeout no longer fabricates mirror drift.
        $heartbeat = [ordered]@{}
        foreach ($property in $retainedHeartbeat.PSObject.Properties) {
          $heartbeat[$property.Name] = $property.Value
        }
        $heartbeat["pollOk"] = $false
        $heartbeat["pollFailedAt"] = $failureAt
        $heartbeat["pollStage"] = $currentStage
        $heartbeat["pollError"] = $failureMessage
        $heartbeat["relayEvidence"] = $relayEvidenceStatus
        $heartbeat["consecutiveFailures"] = $consecutiveFailures
        $heartbeat["backoffSec"] = $sleepSec
        $heartbeat["nextRetryAt"] = $nextRetryAt
      } else {
        $heartbeat = [ordered]@{
          ok = $false
          syncedAt = $failureAt
          source = $SourceUrl
          error = $failureMessage
          pollOk = $false
          pollFailedAt = $failureAt
          pollStage = $currentStage
          pollError = $failureMessage
          sourceRevision = $lastSyncedSourceRevision
          observedSourceRevision = $observedSourceRevision
          mirroredSourceRevision = $lastSyncedSourceRevision
          revisionParity = $(
            if (-not $observedSourceRevision -or -not $lastSyncedSourceRevision) { "UNKNOWN" }
            elseif ($observedSourceRevision.Equals($lastSyncedSourceRevision, [StringComparison]::OrdinalIgnoreCase)) { "MATCH" }
            else { "MISMATCH" }
          )
          relayEvidence = $relayEvidenceStatus
          elapsedSec = [Math]::Round(((Get-Date) - $started).TotalSeconds, 3)
          consecutiveFailures = $consecutiveFailures
          backoffSec = $sleepSec
          nextRetryAt = $nextRetryAt
        }
      }
      Add-Content -LiteralPath $logFile -Value (
        "$failureAt`tERROR`tstage=$currentStage`tfailures=$consecutiveFailures`tbackoff=${sleepSec}s`tnextRetry=$nextRetryAt`t$failureMessage"
      )
    }
    $heartbeat | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $heartbeatFile -Encoding UTF8
    if ($generationLease) {
      $generationLease.Dispose()
      $generationLease = $null
    }
    try {
      Write-SizeReport -MirrorPath $mirrorDir -ReportFile $sizeReportFile -FlyApiUrl $SourceUrl -IntervalSec ([Math]::Max(15, $IntervalSec))
    } catch {
      Add-Content -LiteralPath $logFile -Value "$((Get-Date).ToUniversalTime().ToString('o'))`tWARN`tsize report wrapper failed: $($_.Exception.Message)"
    }
    if ($didSync) {
      # A full copy can run for several minutes. Give the control plane one
      # complete configured interval before requesting another recursive
      # inventory; append/new-file detection remains bounded by IntervalSec.
      Start-Sleep -Seconds $sleepSec
    } else {
      # Healthy no-sync iterations keep the normal cadence. Consecutive
      # failures use the deterministic bounded backoff selected above, which
      # prevents an outage from repeatedly pressuring Fly.
      Start-Sleep -Seconds $sleepSec
    }
  }
} finally {
  if ($generationLease) { $generationLease.Dispose() }
  Remove-Item -LiteralPath $lockFile -Force -ErrorAction SilentlyContinue
  if ($guardStream) { $guardStream.Dispose() }
  Remove-Item -LiteralPath $guardFile -Force -ErrorAction SilentlyContinue
}
