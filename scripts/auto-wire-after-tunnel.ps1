param(
  [int]$MaxWaitSec = 180,
  [string]$StableUrl = "https://bot.doxxedcrypto.digital",
  [switch]$Quiet
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"
$namedTunnelFlag = Join-Path $repoRoot ".home-use-named-tunnel"
$logFile = Join-Path $repoRoot ".home-wire.log"
$deadline = (Get-Date).AddSeconds($MaxWaitSec)

function Log([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
  if (-not $Quiet) { Write-Host $msg }
}

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
    $r = Invoke-WebRequest -Uri "$Url/api/ping" -UseBasicParsing -TimeoutSec 10
    return $r.StatusCode -eq 200
  } catch {
    return $false
  }
}

Log "Auto-wire waiting for tunnel URL (max ${MaxWaitSec}s)..."
while ((Get-Date) -lt $deadline) {
  $url = Read-TunnelUrl
  if ($url -match '^https://') {
    Log "Tunnel URL found: $url"
    if (-not (Test-UrlLive $url)) {
      Start-Sleep -Seconds 3
      continue
    }
    Log "Wiring to site..."
    Push-Location $repoRoot
    try {
      & npm.cmd run wire:home-bot -- $url 2>&1 | Tee-Object -FilePath $logFile -Append
      Log "Wire complete"
    } catch {
      Log "Wire error: $($_.Exception.Message)"
    } finally {
      Pop-Location
    }
    exit 0
  }
  Start-Sleep -Seconds 3
}

if (Test-Path $namedTunnelFlag) {
  Log "Trying stable named tunnel: $StableUrl"
  if (Test-UrlLive $StableUrl) {
    Set-Content -Path $tunnelUrlFile -Value $StableUrl -NoNewline
    Push-Location $repoRoot
    try {
      & npm.cmd run wire:home-bot -- $StableUrl 2>&1 | Tee-Object -FilePath $logFile -Append
      Log "Wired stable URL"
    } finally {
      Pop-Location
    }
    exit 0
  }
}

Log "Timed out waiting for tunnel URL"
exit 1
