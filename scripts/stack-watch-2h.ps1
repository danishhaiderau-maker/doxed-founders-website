# 2-hour production + home stack watch (every 5 min). Logs to logs/stack-watch-2h.log
param(
  [int]$IntervalSec = 300,
  [int]$DurationMin = 120,
  [int]$BotPort = 7002,
  [int]$AnalyzerPort = 9500,
  [int]$BridgePort = 7810
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$logDir = Join-Path $repoRoot "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir "stack-watch-2h.log"
$deadline = (Get-Date).AddMinutes($DurationMin)
$tunnelUrl = "https://bot.doxxedcrypto.digital"

function Probe([string]$Url, [int]$TimeoutSec = 15) {
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    return ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400)
  } catch {
    return $false
  }
}

Add-Content -Path $logFile -Value "=== watch start $(Get-Date -Format o) duration=${DurationMin}m interval=${IntervalSec}s ==="
while ((Get-Date) -lt $deadline) {
  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  $bot = Probe "http://127.0.0.1:$BotPort/api/ping" 8
  $bridge = Probe "http://127.0.0.1:$BridgePort/health" 5
  $analyzer = Probe "http://127.0.0.1:$AnalyzerPort/" 8
  $tunnel = Probe "$tunnelUrl/api/ping" 20
  $vercel = Probe "https://doxxedcrypto.digital/api/health" 20
  $railway = Probe "https://doxed-founders-website-production.up.railway.app/api/health" 20
  $cf = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
  $allOk = ($bot -and $bridge -and $analyzer -and $tunnel -and $vercel -and $railway)
  $line = "$ts bot=$bot bridge=$bridge analyzer=$analyzer tunnel=$tunnel vercel=$vercel railway=$railway cf=$cf ALL=$allOk"
  Add-Content -Path $logFile -Value $line
  Write-Host $line
  Write-Host "AGENT_LOOP_TICK_stackwatch $line"
  Start-Sleep -Seconds $IntervalSec
}
Add-Content -Path $logFile -Value "=== watch end $(Get-Date -Format o) ==="
Write-Host "AGENT_LOOP_TICK_stackwatch DONE"
