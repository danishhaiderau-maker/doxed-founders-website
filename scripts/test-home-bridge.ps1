param(
  [int]$Port = 7810
)

$ErrorActionPreference = "Stop"

try {
  $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
  $status = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/status" -TimeoutSec 5
  if (-not $health.ok) { throw "Bridge /health did not report liveness." }
  if ($status.launcher -ne "running") { throw "Bridge /status did not report launcher=running." }
  if ($null -eq $status.bot.online -or $null -eq $status.analyzer.online) {
    throw "Bridge /status is missing bot/analyzer truth fields."
  }
  [pscustomobject]@{
    bridge = "ok"
    stack_status = $status.status
    stack_ready = [bool]$status.ready
    bot_online = [bool]$status.bot.online
    analyzer_online = [bool]$status.analyzer.online
  } | ConvertTo-Json -Compress
  exit 0
} catch {
  Write-Error "Bridge health test failed: $($_.Exception.Message)"
  exit 1
}
