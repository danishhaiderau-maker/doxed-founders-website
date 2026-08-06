param(
  [string]$SourceUrl = "https://doxed-btc-bot.fly.dev",
  [int]$IntervalSec = 60
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "fly-canonical-lock.ps1")
$SourceUrl = Get-CanonicalFlyBotUrl -RequestedUrl $SourceUrl
$agentDir = Join-Path $repoRoot "services\btc-conservative-agent"
$analyzerReport = Join-Path $agentDir "analysis_dashboard.html"
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
$mirrorDir = Join-Path $agentDir "fly-data-mirror"

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

if (-not $env:BOT_ADMIN_TOKEN -and (Test-Path -LiteralPath $vaultEnv)) {
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

try {
  while ($true) {
    $started = Get-Date
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
      }

      # Keep the live analyzer current quickly. The very large lifecycle genome
      # is archival and is intentionally excluded from the minute-by-minute
      # loop; all trading, fills, exits, AI calls, replays, and compact genome
      # files remain included.
      $selected = @(
        $manifest.files |
          Where-Object { [int64]$_.size -le 50MB } |
          ForEach-Object { [string]$_.path }
      )
      $excluded = @($manifest.files | Where-Object { [int64]$_.size -gt 50MB })
      $syncArgs = @{
        SourceUrl = $SourceUrl
        IncludePath = $selected
      }
      # Publish the latest deterministic analyzer HTML back to Fly so admins
      # have an anywhere-access /analysis route. The local :9001 dashboard
      # remains the full interactive report explorer while the PC is online.
      if (Test-Path -LiteralPath $analyzerReport) {
        $syncArgs.PublishAnalyzerReport = $analyzerReport
      }
      $result = & (Join-Path $scriptDir "sync-fly-bot-data.ps1") @syncArgs
      $heartbeat = [ordered]@{
        ok = $true
        syncedAt = (Get-Date).ToUniversalTime().ToString("o")
        source = $SourceUrl
        files = $result.Files
        bytes = $result.Bytes
        sourceRevision = $result.SourceRevision
        analyzerPublished = $result.AnalyzerPublished
        excludedArchiveFiles = $excluded.Count
        excludedArchiveBytes = [int64](($excluded | Measure-Object size -Sum).Sum)
        elapsedSec = [Math]::Round(((Get-Date) - $started).TotalSeconds, 3)
      }
      Add-Content -LiteralPath $logFile -Value (
        "$($heartbeat.syncedAt)`tOK`trev=$($heartbeat.sourceRevision)`tfiles=$($heartbeat.files)`telapsed=$($heartbeat.elapsedSec)s"
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
    Start-Sleep -Seconds ([Math]::Max(15, $IntervalSec))
  }
} finally {
  Remove-Item -LiteralPath $lockFile -Force -ErrorAction SilentlyContinue
  if ($guardStream) { $guardStream.Dispose() }
  Remove-Item -LiteralPath $guardFile -Force -ErrorAction SilentlyContinue
}
