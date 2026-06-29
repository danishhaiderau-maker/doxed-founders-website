# 24h lightweight monitor for the Doxxedcrypto showcase stack.
# Logs bot / relay-state / tunnel / website / genome health every 60s to logs/system-watchdog.log
# Anomalies (state_integrity missing, ws down, tunnel dead, website 5xx, bot flap) are flagged.
#
# Run (background, survives session):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/system-watchdog.ps1
# Or with a custom duration:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/system-watchdog.ps1 -Hours 24
param(
  [int]$Hours = 24,
  [int]$IntervalSec = 60,
  [int]$BotPort = 7002,
  [int]$AnalyzerPort = 9001,
  [int]$BridgePort = 7810
)
$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$logsDir = Join-Path $repoRoot "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }
$log = Join-Path $logsDir "system-watchdog.log"
$lock = Join-Path $repoRoot ".home-system-watchdog.lock"

# Single-instance lock
try {
  $script:LockHandle = [System.IO.File]::Open($lock, [System.IO.FileMode]::Create, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
  Write-Host "system-watchdog already running ($lock) - exit"; exit 0
}

function WLog([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $log -Value $line -ErrorAction SilentlyContinue
}

function Probe([string]$url, [int]$timeoutMs = 4000) {
  try {
    $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec ([int]($timeoutMs/1000)) -ErrorAction Stop
    return @{ ok = $true; code = $r.StatusCode; ms = -1 }
  } catch {
    $code = 0
    try { $code = [int]$_.Exception.Response.StatusCode } catch {}
    return @{ ok = $false; code = $code; ms = -1 }
  }
}

function ProbeJson([string]$url, [int]$timeoutMs = 5000) {
  try {
    $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec ([int]($timeoutMs/1000)) -ErrorAction Stop
    $j = $r.Content | ConvertFrom-Json -ErrorAction SilentlyContinue
    return @{ ok = $true; code = $r.StatusCode; json = $j }
  } catch {
    $code = 0
    try { $code = [int]$_.Exception.Response.StatusCode } catch {}
    return @{ ok = $false; code = $code; json = $null }
  }
}

$end = (Get-Date).AddHours($Hours)
WLog "watchdog START hours=$Hours interval=${IntervalSec}s bot=:$BotPort analyzer=:$AnalyzerPort bridge=:$BridgePort pid=$PID"
$prevBotOk = $true
$flapCount = 0

while ((Get-Date) -lt $end) {
  $bot = ProbeJson "http://127.0.0.1:$BotPort/api/relay-state"
  $ping = Probe "http://127.0.0.1:$BotPort/api/ping"
  $analyzer = Probe "http://127.0.0.1:$AnalyzerPort/"
  $bridge = Probe "http://127.0.0.1:$BridgePort/health"
  $tunnel = Probe "https://bot.doxxedcrypto.digital/api/ping" 6000
  $site = Probe "https://doxxedcrypto.digital/" 6000

  $botOk = $bot.ok -and $ping.ok
  if (-not $prevBotOk -and $botOk) { $flapCount++ }
  $prevBotOk = $botOk

  $si = $null
  if ($bot.ok -and $bot.json -and $bot.json.PSObject.Properties.Name -contains 'state_integrity') {
    $si = $bot.json.state_integrity
  }

  $line = "tick bot=$botOk ping=$($ping.ok) analyzer=$($analyzer.ok) bridge=$($bridge.ok) tunnel=$($tunnel.ok) site=$($site.ok) flap=$flapCount"
  if ($si) {
    $line += " | integrity: seq=$($si.snapshot_seq) ws=$($si.ws_connected) rest=$($si.rest_healthy) wsStatus=$($si.ws_status) priceAge=$($si.price_age_sec)s lastFill=$($si.last_fill_sec_ago)s genome=$($si.genome_recorder) relaySeq=$($si.relay_push.seq) liveEnabled=$($si.bitfinex_live_enabled)"
  } else {
    $line += " | integrity: MISSING"
  }
  WLog $line

  # Anomaly flags
  if (-not $botOk) { WLog "  ANOMALY bot offline on :$BotPort" }
  if ($bot.ok -and -not $si) { WLog "  ANOMALY bot reachable but state_integrity block MISSING (old build?)" }
  if ($si -and -not $si.ws_connected) { WLog "  ANOMALY ws not connected (ws_status=$($si.ws_status))" }
  if ($si -and $si.rest_healthy -eq $false) { WLog "  ANOMALY REST not healthy (price_age=$($si.price_age_sec)s)" }
  if (-not $tunnel.ok) { WLog "  ANOMALY tunnel down (https://bot.doxxedcrypto.digital)" }
  if (-not $site.ok) { WLog "  ANOMALY website down (https://doxxedcrypto.digital code=$($site.code))" }
  if (-not $analyzer.ok) { WLog "  ANOMALY :$AnalyzerPort dashboard down (genome outcomes may not render)" }
  if ($flapCount -gt 3) { WLog "  ANOMALY bot flapped $flapCount times - investigate supervisor/bot crash" }

  Start-Sleep -Seconds $IntervalSec
}

WLog "watchdog END after $Hours h. flapCount=$flapCount"
try { $script:LockHandle.Close() } catch {}
Remove-Item $lock -Force -ErrorAction SilentlyContinue
