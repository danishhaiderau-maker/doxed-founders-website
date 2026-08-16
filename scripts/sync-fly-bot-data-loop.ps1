param(
  [string]$SourceUrl = "https://doxed-btc-bot.fly.dev",
  [int]$IntervalSec = 180
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "fly-canonical-lock.ps1")
. (Join-Path $scriptDir "fly-data-paths.ps1")
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
$heartbeatFile = Join-Path $repoRoot ".fly-data-sync-loop.heartbeat.json"
$logFile = Join-Path $repoRoot "logs\fly-data-sync.log"
$freshSignalFile = Join-Path $repoRoot ".fly-data-sync-loop.last-fresh.json"
$mirrorDir = Get-DoxxedFlyMirrorDir
$sizeReportFile = Join-Path $mirrorDir "_size_report.json"
$growthStateFile = Join-Path $mirrorDir ".fly-sync-growth-state.json"

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

    $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ReportFile -Encoding UTF8
  } catch {
    Add-Content -LiteralPath $logFile -Value (
      "$((Get-Date).ToUniversalTime().ToString('o'))`tWARN`tsize report failed: $($_.Exception.Message)"
    )
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

if (Test-Path -LiteralPath $vaultEnv) {
  $tokenLine = Get-Content -LiteralPath $vaultEnv | Where-Object {
    $_ -match '^\s*BOT_ADMIN_TOKEN='
  } | Select-Object -Last 1
  if ($tokenLine -match '^\s*BOT_ADMIN_TOKEN=(.*)$') {
    $env:BOT_ADMIN_TOKEN = $matches[1].Trim().Trim('"').Trim("'")
  }
}
if (-not $env:BOT_ADMIN_TOKEN) {
  throw "BOT_ADMIN_TOKEN is required for the canonical Fly data mirror."
}

$lastSyncedTotalBytes = [int64]0
$lastSyncAt = [datetime]::SpecifyKind([datetime]'1970-01-01', 'Utc')
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
  } catch {
    Add-Content -LiteralPath $logFile -Value (
      "$((Get-Date).ToUniversalTime().ToString('o'))`tWARN`tunreadable growth state: $($_.Exception.Message)"
    )
  }
}

try {
  while ($true) {
    $started = Get-Date
    $didSync = $false
    try {
      $headers = @{ "X-Bot-Admin-Token" = $env:BOT_ADMIN_TOKEN }
      $manifest = Invoke-RestMethod `
        -Uri ($SourceUrl.TrimEnd("/") + "/api/data-sync/manifest") `
        -Headers $headers `
        -TimeoutSec 45

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
        Write-Host "[FRESH COLLECTION] Signal received ($currentSignal > $lastSeenSignal). Wiping local mirror before sync."
        Add-Content -LiteralPath $logFile -Value (
          "$((Get-Date).ToUniversalTime().ToString('o'))`tFRESH`tlocal mirror wipe signalled ($currentSignal > $lastSeenSignal)"
        )
        if (Test-Path -LiteralPath $mirrorDir) {
          Get-ChildItem -LiteralPath $mirrorDir -File -Force -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
          # Drop the per-file sync state too so the next pass re-pulls every
          # file from offset 0 against the freshly-wiped Fly volume.
          $syncStatePath = Join-Path $mirrorDir ".fly-sync-state.json"
          if (Test-Path -LiteralPath $syncStatePath) {
            Remove-Item -LiteralPath $syncStatePath -Force -ErrorAction SilentlyContinue
          }
        }
        @{$signal_ts = $currentSignal; signalled_at = (Get-Date).ToUniversalTime().ToString("o") } |
          ConvertTo-Json | Set-Content -LiteralPath $freshSignalFile -Encoding UTF8
        $lastSyncedTotalBytes = 0
      }

      $currentTotalBytes = [int64]0
      if ($manifest.PSObject.Properties.Name -contains "total_bytes") {
        $currentTotalBytes = [int64]$manifest.total_bytes
      } else {
        $currentTotalBytes = [int64](($manifest.files | Measure-Object -Property size -Sum).Sum)
      }
      $growthBytes = [Math]::Max([int64]0, $currentTotalBytes - $lastSyncedTotalBytes)
      $elapsedSec = ([datetime]::UtcNow - $lastSyncAt).TotalSeconds
      $forceByTime = $elapsedSec -ge [Math]::Max(15, $IntervalSec)
      $forceByGrowth = $growthBytes -ge $thresholdBytes
      $forceFresh = $currentSignal -gt $lastSeenSignal

      if (-not ($forceByTime -or $forceByGrowth -or $forceFresh)) {
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
        }
        $heartbeat | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $heartbeatFile -Encoding UTF8
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
      }
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
          Where-Object {
            (Test-Path -LiteralPath (Join-Path $_.FullName "report_manifest.json") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $_.FullName "analysis_dashboard.html") -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $_.FullName "reports") -PathType Container)
          } |
          Select-Object -First 1
        if ($latestCompleteArchive) {
          $publishReport = Join-Path $latestCompleteArchive.FullName "analysis_dashboard.html"
        }
      }
      if ($publishReport) {
        $syncArgs.PublishAnalyzerReport = $publishReport
      }
      $syncArgs.TargetDir = $mirrorDir
      $result = & (Join-Path $scriptDir "sync-fly-bot-data.ps1") @syncArgs
      $didSync = $true
      $lastSyncedTotalBytes = $currentTotalBytes
      $lastSyncAt = [datetime]::UtcNow
      @{
        lastSyncedTotalBytes = $lastSyncedTotalBytes
        lastSyncAt = $lastSyncAt.ToString("o")
        thresholdMb = $thresholdMb
      } | ConvertTo-Json | Set-Content -LiteralPath $growthStateFile -Encoding UTF8

      $heartbeat = [ordered]@{
        ok = $true
        syncedAt = (Get-Date).ToUniversalTime().ToString("o")
        source = $SourceUrl
        files = $result.Files
        bytes = $result.Bytes
        sourceRevision = $result.SourceRevision
        analyzerPublished = $result.AnalyzerPublished
        prunedRotations = $result.PrunedRotations
        growthBytes = $growthBytes
        thresholdMb = $thresholdMb
        trigger = $(if ($forceByGrowth) { "growth" } elseif ($forceFresh) { "fresh" } else { "interval" })
        elapsedSec = [Math]::Round(((Get-Date) - $started).TotalSeconds, 3)
      }
      Add-Content -LiteralPath $logFile -Value (
        "$($heartbeat.syncedAt)`tOK`ttrigger=$($heartbeat.trigger)`trev=$($heartbeat.sourceRevision)`tfiles=$($heartbeat.files)`tpruned=$($heartbeat.prunedRotations)`telapsed=$($heartbeat.elapsedSec)s"
      )
    } catch {
      $heartbeat = [ordered]@{
        ok = $false
        syncedAt = (Get-Date).ToUniversalTime().ToString("o")
        source = $SourceUrl
        error = $_.Exception.Message
        elapsedSec = [Math]::Round(((Get-Date) - $started).TotalSeconds, 3)
      }
      Add-Content -LiteralPath $logFile -Value (
        "$($heartbeat.syncedAt)`tERROR`t$($heartbeat.error)"
      )
    }
    $heartbeat | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $heartbeatFile -Encoding UTF8
    try {
      Write-SizeReport -MirrorPath $mirrorDir -ReportFile $sizeReportFile -FlyApiUrl $SourceUrl -IntervalSec ([Math]::Max(15, $IntervalSec))
    } catch {
      Add-Content -LiteralPath $logFile -Value "$((Get-Date).ToUniversalTime().ToString('o'))`tWARN`tsize report wrapper failed: $($_.Exception.Message)"
    }
    if ($didSync) {
      Start-Sleep -Seconds $pollSec
    } else {
      Start-Sleep -Seconds ([Math]::Max(15, $IntervalSec))
    }
  }
} finally {
  Remove-Item -LiteralPath $lockFile -Force -ErrorAction SilentlyContinue
  if ($guardStream) { $guardStream.Dispose() }
  Remove-Item -LiteralPath $guardFile -Force -ErrorAction SilentlyContinue
}
