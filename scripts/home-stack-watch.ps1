# Monitor home stack for N hours — logs bot/tunnel/analyzer/bridge health.
param(
  [int]$Hours = 2,
  [int]$IntervalSec = 300
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$logFile = Join-Path $repoRoot ".home-stack-watch.log"
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"
$deadline = (Get-Date).AddHours($Hours)

function Read-TunnelUrl {
  if (-not (Test-Path $tunnelUrlFile)) { return $null }
  $raw = Get-Content $tunnelUrlFile -Raw -ErrorAction SilentlyContinue
  if ($null -eq $raw) { return $null }
  $t = "$raw".Trim()
  if (-not $t) { return $null }
  return $t
}

function Probe([string]$Url, [int]$TimeoutSec = 5) {
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    return $r.StatusCode -eq 200
  } catch {
    return $false
  }
}

Add-Content -Path $logFile -Value "`n=== watch started $(Get-Date -Format o) for ${Hours}h every ${IntervalSec}s ==="

while ((Get-Date) -lt $deadline) {
  $tunnel = Read-TunnelUrl
  $bot = Probe "http://127.0.0.1:7800/api/ping"
  $bridge = Probe "http://127.0.0.1:7810/health"
  $analyzer = Probe "http://127.0.0.1:9001/api/status"
  $tunnelLive = if ($tunnel) { Probe "$tunnel/api/ping" 12 } else { $false }
  $cf = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
  $urlLabel = if ($tunnel) { $tunnel } else { "none" }
  $line = "{0} bot={1} bridge={2} analyzer={3} cloudflared={4} tunnel_live={5} url={6}" -f (
    (Get-Date -Format "HH:mm:ss"),
    $bot, $bridge, $analyzer, $cf, $tunnelLive, $urlLabel
  )
  Add-Content -Path $logFile -Value $line
  Write-Host $line
  Start-Sleep -Seconds $IntervalSec
}

Add-Content -Path $logFile -Value "=== watch ended $(Get-Date -Format o) ==="
Write-Host "Watch complete. Log: $logFile"
