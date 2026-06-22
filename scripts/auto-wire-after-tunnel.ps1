# Wait for .home-tunnel-url then wire to Neon + Railway (background after Start everything).
param(
  [int]$MaxWaitSec = 180,
  [string]$StableUrl = "https://bot.doxxedcrypto.digital"
)

$Host.UI.RawUI.WindowTitle = "Doxed Auto-Wire"
$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"
$namedTunnelFlag = Join-Path $repoRoot ".home-use-named-tunnel"
$logFile = Join-Path $repoRoot ".home-wire.log"
$deadline = (Get-Date).AddSeconds($MaxWaitSec)

function Read-TunnelUrl {
  if (-not (Test-Path $tunnelUrlFile)) { return $null }
  $raw = Get-Content $tunnelUrlFile -Raw -ErrorAction SilentlyContinue
  if ($null -eq $raw) { return $null }
  $trimmed = "$raw".Trim()
  if (-not $trimmed) { return $null }
  return $trimmed
}

function Test-UrlLive([string]$Url) {
  if (-not $Url) { return $false }
  try {
    $r = Invoke-WebRequest -Uri "$Url/health" -UseBasicParsing -TimeoutSec 10
    return $r.StatusCode -eq 200
  } catch {
    return $false
  }
}

Write-Host "Waiting for tunnel URL (max ${MaxWaitSec}s)..."
while ((Get-Date) -lt $deadline) {
  $url = Read-TunnelUrl
  if ($url -match '^https://') {
    Write-Host "Tunnel URL found: $url"
    if (-not (Test-UrlLive $url)) {
      Write-Host "Tunnel URL saved but /health not ready yet - retrying..."
      Start-Sleep -Seconds 3
      continue
    }
    Write-Host "Wiring to site..."
    Push-Location $repoRoot
    try {
      & npm.cmd run wire:home-bot -- $url 2>&1 | Tee-Object -FilePath $logFile
      Write-Host "Wire complete. See $logFile"
    } catch {
      Write-Host "Wire error: $($_.Exception.Message)" -ForegroundColor Red
    } finally {
      Pop-Location
    }
    Read-Host "Press Enter to close auto-wire window"
    exit 0
  }
  Start-Sleep -Seconds 3
}

if (Test-Path $namedTunnelFlag) {
  Write-Host "No quick tunnel URL - trying stable named tunnel: $StableUrl"
  if (Test-UrlLive $StableUrl) {
    Set-Content -Path $tunnelUrlFile -Value $StableUrl -NoNewline
    Push-Location $repoRoot
    try {
      & npm.cmd run wire:home-bot -- $StableUrl 2>&1 | Tee-Object -FilePath $logFile
      Write-Host "Wired stable URL. See $logFile"
    } finally {
      Pop-Location
    }
    Read-Host "Press Enter to close auto-wire window"
    exit 0
  }
}

Write-Host "Timed out waiting for tunnel URL in $tunnelUrlFile"
Read-Host "Press Enter to close auto-wire window"
exit 1
