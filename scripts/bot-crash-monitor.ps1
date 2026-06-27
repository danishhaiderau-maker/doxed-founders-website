# Watches a detached bot PID and writes logs/last_crash.json when it dies.
# Started by start-home-bot.ps1 -NoWait so crashes are captured even with no console.
param([int]$BotPid, [int]$Port = 7002)
$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$agentDir = Join-Path $repoRoot "services\btc-conservative-agent"
$logsDir = Join-Path $repoRoot "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }

$p = Get-Process -Id $BotPid -ErrorAction SilentlyContinue
if (-not $p) { exit 0 }
$p.WaitForExit()
$code = $p.ExitCode
if ($code -eq 0) { exit 0 }

$tail = @(Get-Content (Join-Path $agentDir "bot_runtime.log") -Tail 60 -ErrorAction SilentlyContinue)
$report = [ordered]@{
  ts          = (Get-Date -Format "yyyy-MM-ddTHH:mm:sszzz")
  exit_code   = $code
  message     = "detached bot non-zero exit"
  port        = $Port
  pid         = $BotPid
  bot_version = "v11.1-virtual-chase-known-combos-v1"
  log_tail    = ($tail -join "`n")
}
$json = $report | ConvertTo-Json -Depth 4
Set-Content -Path (Join-Path $logsDir "last_crash.json") -Value $json -Encoding UTF8
Add-Content -Path (Join-Path $logsDir "crash_history.jsonl") -Value ($json -replace "`n"," " -replace "`r","") -Encoding UTF8

$wh = (Get-Item -Path "env:CRASH_NOTIFY_WEBHOOK" -ErrorAction SilentlyContinue).Value
if ($wh) {
  try {
    $body = @{ content = "BOT CRASH exit=$code port=$Port ts=$($report.ts)`n```````n$(($tail | Select-Object -First 30) -join "`n")`n``````" } | ConvertTo-Json
    Invoke-RestMethod -Uri $wh -Method Post -ContentType "application/json" -Body $body -TimeoutSec 5 -ErrorAction Stop | Out-Null
  } catch { }
}
try { msg * /TIME:30 "Doxed bot crashed (exit $code) on port $Port. See logs\last_crash.json" 2>$null } catch { }
