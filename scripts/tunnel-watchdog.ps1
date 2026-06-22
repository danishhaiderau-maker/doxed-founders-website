# Keeps Cloudflare tunnel alive — restarts cloudflared when process dies or public URL stops responding.
# Started automatically by home-stack-launcher "Start everything".
param(
  [int]$BotPort = 7800,
  [int]$IntervalSec = 45,
  [string]$BridgeUrl = "http://127.0.0.1:7810"
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"
$logFile = Join-Path $repoRoot ".home-tunnel-watchdog.log"
$namedFlag = Join-Path $repoRoot ".home-use-named-tunnel"
$stableUrl = "https://bot.doxxedcrypto.digital"

function Log([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
  Write-Host $line
}

function Read-TunnelUrl {
  if (Test-Path $namedFlag) { return $stableUrl }
  if (-not (Test-Path $tunnelUrlFile)) { return $null }
  $raw = Get-Content $tunnelUrlFile -Raw -ErrorAction SilentlyContinue
  if ($null -eq $raw) { return $null }
  $t = "$raw".Trim()
  if (-not $t) { return $null }
  return $t
}

function Probe([string]$Url, [int]$TimeoutSec = 10) {
  if (-not $Url) { return $false }
  try {
    $r = Invoke-WebRequest -Uri "$Url/api/ping" -UseBasicParsing -TimeoutSec $TimeoutSec
    return $r.StatusCode -eq 200
  } catch {
    return $false
  }
}

function Invoke-Bridge([string]$Path) {
  try {
    $r = Invoke-WebRequest -Uri "$BridgeUrl$Path" -UseBasicParsing -TimeoutSec 120
    return $r.Content
  } catch {
    Log "bridge $Path failed: $($_.Exception.Message)"
    return $null
  }
}

Log "watchdog started (interval ${IntervalSec}s, named=$(Test-Path $namedFlag))"

$lastWireUrl = $null

while ($true) {
  $url = Read-TunnelUrl
  $cfRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
  $live = Probe $url

  if (-not $cfRunning -or -not $live) {
    Log "tunnel unhealthy cloudflared=$cfRunning live=$live url=$url — restarting"
    Invoke-Bridge "/cmd/start-tunnel" | Out-Null
    Start-Sleep -Seconds 20
    $url = Read-TunnelUrl
    $live = Probe $url
    Log "after restart cloudflared=$(@(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0) live=$live url=$url"
  }

  if ($live -and $url -and $url -ne $lastWireUrl) {
    if ($url -match 'trycloudflare\.com' -or (Test-Path $namedFlag)) {
      Log "wiring $url to Neon + Railway"
      Invoke-Bridge "/cmd/wire?url=$([uri]::EscapeDataString($url))" | Out-Null
      $lastWireUrl = $url
    }
  }

  Start-Sleep -Seconds $IntervalSec
}
