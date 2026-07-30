param(
  [string]$SourceUrl = "https://doxed-btc-bot.fly.dev",
  [int]$IntervalSec = 60
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$vaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"
$lockFile = Join-Path $repoRoot ".fly-data-sync-loop.lock"
$heartbeatFile = Join-Path $repoRoot ".fly-data-sync-loop.heartbeat.json"
$logFile = Join-Path $repoRoot "logs\fly-data-sync.log"

if (-not (Test-Path (Split-Path -Parent $logFile))) {
  New-Item -ItemType Directory -Path (Split-Path -Parent $logFile) -Force | Out-Null
}

if (Test-Path -LiteralPath $lockFile) {
  try {
    $existingPid = [int](Get-Content -LiteralPath $lockFile -Raw)
    if (Get-Process -Id $existingPid -ErrorAction SilentlyContinue) { exit 0 }
  } catch { }
}
Set-Content -LiteralPath $lockFile -Value "$PID" -NoNewline -Encoding UTF8

if (Test-Path -LiteralPath $vaultEnv) {
  Get-Content -LiteralPath $vaultEnv | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') {
      Set-Item -Path ("env:" + $matches[1].Trim()) -Value $matches[2].Trim().Trim('"').Trim("'")
    }
  }
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
      $result = & (Join-Path $scriptDir "sync-fly-bot-data.ps1") `
        -SourceUrl $SourceUrl `
        -IncludePath $selected
      $heartbeat = [ordered]@{
        ok = $true
        syncedAt = (Get-Date).ToUniversalTime().ToString("o")
        source = $SourceUrl
        files = $result.Files
        bytes = $result.Bytes
        sourceRevision = $result.SourceRevision
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
}
