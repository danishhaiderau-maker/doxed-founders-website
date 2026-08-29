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

function Remove-OrphanedMirrorCandidates {
  param([Parameter(Mandatory = $true)][string]$MirrorPath)
  if (-not (Test-Path -LiteralPath $MirrorPath -PathType Container)) { return }
  foreach ($candidate in @(Get-ChildItem -LiteralPath $MirrorPath -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -match '\.(?<owner>\d+)\.[0-9a-fA-F]{32}\.download(?:\.replace-backup)?$'
  })) {
    $owner = 0
    if ($candidate.Name -match '\.(?<owner>\d+)\.[0-9a-fA-F]{32}\.download(?:\.replace-backup)?$') {
      $owner = [int]$matches['owner']
    }
    # The exclusive loop guard proves this process has no in-flight candidate
    # at the top of a cycle. Candidates owned by this PID are leftovers from a
    # completed/failed prior cycle; candidates from dead PIDs are abandoned.
    $ownerAlive = $owner -gt 0 -and $null -ne (Get-Process -Id $owner -ErrorAction SilentlyContinue)
    if ($owner -eq $PID -or -not $ownerAlive) {
      Remove-Item -LiteralPath $candidate.FullName -Force -ErrorAction SilentlyContinue
    }
  }
}

# Growth trigger (default 50 MB). Override with FLY_VOLUME_SYNC_THRESHOLD_MB.
# Poll cadence is faster than the force-sync interval so large jsonl growth is
# mirrored for the analyzer without waiting a full IntervalSec cycle.
$thresholdMb = 50.0
if ($env:FLY_VOLUME_SYNC_THRESHOLD_MB) {
  try { $thresholdMb = [double]$env:FLY_VOLUME_SYNC_THRESHOLD_MB } catch { $thresholdMb = 50.0 }
}
if ($thresholdMb -lt 5) { $thresholdMb = 5.0 }
$thresholdBytes = [int64]($thresholdMb * 1MB)
$pollSec = [Math]::Max(30, [Math]::Min(60, [int]($IntervalSec / 3)))
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

$preflightManifestAttempts = 5
$preflightManifestTimeoutSec = 90
$relaySyncAttempts = 2

function Get-FlySyncPreflightManifest {
  param([Parameter(Mandatory = $true)][string]$ManifestUri)
  $preflightHeaders = @{ "X-Bot-Admin-Token" = $env:BOT_ADMIN_TOKEN }
  for ($attempt = 1; $attempt -le $preflightManifestAttempts; $attempt++) {
    try {
      return Invoke-RestMethod `
        -Uri $ManifestUri `
        -Headers $preflightHeaders `
        -TimeoutSec $preflightManifestTimeoutSec `
        -ErrorAction Stop
    } catch {
      if ($attempt -ge $preflightManifestAttempts) {
        throw (
          "Fly data-sync stage=loop_manifest_preflight failed after " +
          "$attempt/$preflightManifestAttempts attempt(s): $($_.Exception.Message)"
        )
      }
      Start-Sleep -Seconds ([Math]::Min(15, 2 * $attempt))
    }
  }
}

function Invoke-OptionalRelayEvidenceSync {
  $lastRelayError = $null
  for ($attempt = 1; $attempt -le $relaySyncAttempts; $attempt++) {
    try {
      return & (Join-Path $scriptDir "sync-platform-relay-evidence.ps1")
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

$lastSyncedTotalBytes = [int64]0
$lastSyncAt = [datetime]::SpecifyKind([datetime]'1970-01-01', 'Utc')
$lastSyncedSourceRevision = $null
if (Test-Path -LiteralPath $growthStateFile) {
  try {
    $growthState = Get-Content -LiteralPath $growthStateFile -Raw | ConvertFrom-Json
    if ($growthState.PSObject.Properties.Name -contains "lastSyncedTotalBytes") {
      $lastSyncedTotalBytes = [int64]$growthState.lastSyncedTotalBytes
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
    $observedSourceRevision = $null
    $currentStage = "loop_start"
    $relayEvidenceStatus = [ordered]@{
      ok = $false
      errorCode = "CONFIG_MISSING"
      lastSuccessAt = $relayEvidenceLastSuccessAt
    }
    try {
      $currentStage = "loop_manifest_preflight"
      $manifest = Get-FlySyncPreflightManifest `
        -ManifestUri ($SourceUrl.TrimEnd("/") + "/api/data-sync/manifest")
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
        # The active mirror is now empty. Do not retain a revision receipt for
        # the quarantined generation if the following refresh fails.
        $lastSyncedSourceRevision = $null
      }

      $currentTotalBytes = [int64]0
      if ($manifest.PSObject.Properties.Name -contains "total_bytes") {
        $currentTotalBytes = [int64]$manifest.total_bytes
      } else {
        $currentTotalBytes = [int64](($manifest.files | Measure-Object -Property size -Sum).Sum)
      }
      $growthBytes = [Math]::Max([int64]0, $currentTotalBytes - $lastSyncedTotalBytes)
      $elapsedSec = ([datetime]::UtcNow - $lastSyncAt).TotalSeconds
      # Polling and mutation cadence are deliberately separate. A complete
      # 601-file Fly pass can take much longer than the three-minute poll
      # cadence; forcing one on every poll starves the analyzer generation
      # lease. Polls refresh parity/freshness, while a full pass is due only
      # at the reviewed full-sync interval (or immediately on growth,
      # revision, or fresh-collection changes).
      $forceByTime = $elapsedSec -ge $fullSyncSec
      $forceByGrowth = $growthBytes -ge $thresholdBytes
      $forceFresh = $currentSignal -gt $lastSeenSignal
      # A deployment can change schemas or files without adding 50 MB. The
      # remote revision is only an observation; it becomes the mirrored
      # revision after the complete atomic sync below succeeds.
      $forceByRevision = [bool]$observedSourceRevision -and (
        -not $lastSyncedSourceRevision -or
        -not $observedSourceRevision.Equals($lastSyncedSourceRevision, [StringComparison]::OrdinalIgnoreCase)
      )

      # Relay evidence is optional and may consume two 90-second bounded
      # attempts.  Never put it ahead of a required revision repair: first
      # publish an exact Fly mirror, then refresh relay evidence on the next
      # matched cycle.  This makes the earlier "never blocks the canonical
      # mirror" contract true during deployments and recovery.
      if (
        -not ($forceByTime -or $forceByGrowth -or $forceFresh -or $forceByRevision) -and
        $env:PLATFORM_API_BASE_URL -and
        $env:PLATFORM_RELAY_AGENT_SLUG -and
        $env:PLATFORM_RELAY_USER_ID
      ) {
        $currentStage = "optional_relay_evidence"
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
          reason = "below_threshold"
          growthBytes = $growthBytes
          thresholdBytes = $thresholdBytes
          thresholdMb = $thresholdMb
          currentTotalBytes = $currentTotalBytes
          lastSyncedTotalBytes = $lastSyncedTotalBytes
          elapsedSecSinceSync = [Math]::Round($elapsedSec, 1)
          sourceRevision = $lastSyncedSourceRevision
          observedSourceRevision = $observedSourceRevision
          mirroredSourceRevision = $lastSyncedSourceRevision
          revisionParity = $revisionParity
          botVersion = $(if ($manifest.PSObject.Properties.Name -contains "bot_version") { [string]$manifest.bot_version } else { $null })
          tileRegistrySignature = $(if ($manifest.PSObject.Properties.Name -contains "tile_registry_signature") { [string]$manifest.tile_registry_signature } else { $null })
          activeTiles = $(if ($manifest.PSObject.Properties.Name -contains "active_tiles") { @($manifest.active_tiles) } else { @() })
          relayEvidence = $relayEvidenceStatus
          pollOk = $true
        }
        $heartbeat | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $heartbeatFile -Encoding UTF8
        Add-Content -LiteralPath $logFile -Value (
          "$($heartbeat.syncedAt)`tSKIP`tgrowth=$([Math]::Round($growthBytes/1MB,2))MB < threshold=${thresholdMb}MB"
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
        Add-Content -LiteralPath $logFile -Value (
          "$((Get-Date).ToUniversalTime().ToString('o'))`tDEFER`tanalyzer owns mirror-generation lease"
        )
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
      $lastSyncedTotalBytes = $currentTotalBytes
      $lastSyncAt = [datetime]::UtcNow
      $lastSyncedSourceRevision = $(if ($result.SourceRevision) { [string]$result.SourceRevision } else { $observedSourceRevision })
      @{
        lastSyncedTotalBytes = $lastSyncedTotalBytes
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
        thresholdMb = $thresholdMb
        trigger = $(if ($forceByRevision) { "revision" } elseif ($forceByGrowth) { "growth" } elseif ($forceFresh) { "fresh" } else { "interval" })
        elapsedSec = [Math]::Round(((Get-Date) - $started).TotalSeconds, 3)
        pollOk = $true
      }
      Add-Content -LiteralPath $logFile -Value (
        "$($heartbeat.syncedAt)`tOK`ttrigger=$($heartbeat.trigger)`trev=$($heartbeat.sourceRevision)`tfiles=$($heartbeat.files)`tpruned=$($heartbeat.prunedRotations)`telapsed=$($heartbeat.elapsedSec)s"
      )
    } catch {
      $failureAt = (Get-Date).ToUniversalTime().ToString("o")
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
        }
      }
      Add-Content -LiteralPath $logFile -Value (
        "$failureAt`tERROR`tstage=$currentStage`t$failureMessage"
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
      Start-Sleep -Seconds $pollSec
    } else {
      # A failed preflight must retry at the bounded poll cadence. Sleeping
      # for the full-sync interval would leave a stale heartbeat after a
      # transient 503 and unnecessarily block the analyzer.
      Start-Sleep -Seconds $pollSec
    }
  }
} finally {
  if ($generationLease) { $generationLease.Dispose() }
  Remove-Item -LiteralPath $lockFile -Force -ErrorAction SilentlyContinue
  if ($guardStream) { $guardStream.Dispose() }
  Remove-Item -LiteralPath $guardFile -Force -ErrorAction SilentlyContinue
}
