# Log home stack health every 60s (run after Start everything).
param(
  [int]$IntervalSec = 60,
  [int]$DurationMin = 120,
  [int]$BotPort = 7800,
  [int]$AnalyzerPort = 9001,
  [int]$BridgePort = 7810
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$logFile = Join-Path $repoRoot ".home-stack-monitor.log"
$deadline = (Get-Date).AddMinutes($DurationMin)
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"

function Probe([string]$Url, [int]$TimeoutSec = 4) {
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    return $r.StatusCode -eq 200
  } catch {
    return $false
  }
}

Write-Host "Monitoring home stack for ${DurationMin}m (every ${IntervalSec}s) -> $logFile"
while ((Get-Date) -lt $deadline) {
  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  $tunnelUrl = $null
  if (Test-Path $tunnelUrlFile) {
    $raw = Get-Content $tunnelUrlFile -Raw -ErrorAction SilentlyContinue
    if ($null -ne $raw -and "$raw".Trim()) { $tunnelUrl = "$raw".Trim() }
  }
  $botOk = Probe "http://127.0.0.1:$BotPort/health"
  $analyzerOk = Probe "http://127.0.0.1:$AnalyzerPort/health"
  $bridgeOk = Probe "http://127.0.0.1:$BridgePort/health"
  $tunnelOk = if ($tunnelUrl) { Probe "$tunnelUrl/health" 8 } else { $false }
  $line = "$ts bot=$botOk analyzer=$analyzerOk bridge=$bridgeOk tunnel=$tunnelOk url=$tunnelUrl"
  Add-Content -Path $logFile -Value $line
  Write-Host $line
  Start-Sleep -Seconds $IntervalSec
}
Write-Host "Monitor complete. Log: $logFile"
