# Measure local bot signal pipeline + shadow write latency (read-only probe).
param(
  [int]$BotPort = 7002,
  [int]$Samples = 3
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "home-stack-mode.ps1")
$mode = Get-HomeStackMode -RepoRoot $repoRoot
if ($BotPort -eq 7002 -and $mode.BotPort -ne 7002) { $BotPort = $mode.BotPort }

$base = "http://127.0.0.1:$BotPort"
$agentDir = if ($mode.Mode -eq "local-collection") { $mode.DataDir } else { Join-Path $repoRoot "services\btc-conservative-agent" }
$shadowFile = Join-Path $agentDir "shadow_outcome.jsonl"

Write-Host ""
Write-Host "=== Shadow / signal latency probe (bot :$BotPort) ===" -ForegroundColor Cyan
Write-Host "Data: $agentDir"
Write-Host ""

$pingMs = @()
for ($i = 0; $i -lt $Samples; $i++) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  try {
    $r = Invoke-WebRequest -Uri "$base/api/ping" -UseBasicParsing -TimeoutSec 5
    $sw.Stop()
    if ($r.StatusCode -eq 200) { $pingMs += $sw.ElapsedMilliseconds }
  } catch {
    Write-Host "Bot not reachable on :$BotPort - start local collection first." -ForegroundColor Red
    exit 1
  }
}

$stateMs = @()
for ($i = 0; $i -lt $Samples; $i++) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  try {
    $r = Invoke-WebRequest -Uri "$base/api/relay-state" -UseBasicParsing -TimeoutSec 8
    $sw.Stop()
    if ($r.StatusCode -eq 200) { $stateMs += $sw.ElapsedMilliseconds }
  } catch {
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $r = Invoke-WebRequest -Uri "$base/api/state" -UseBasicParsing -TimeoutSec 12
    $sw.Stop()
    if ($r.StatusCode -eq 200) { $stateMs += $sw.ElapsedMilliseconds }
  }
}

$avg = {
  param($arr)
  if (-not $arr -or $arr.Count -eq 0) { return $null }
  [math]::Round(($arr | Measure-Object -Average).Average, 0)
}

Write-Host ("API /api/ping avg:        {0} ms (n={1})" -f (& $avg $pingMs), $pingMs.Count)
Write-Host ("API /api/state avg:       {0} ms (n={1})" -f (& $avg $stateMs), $stateMs.Count)

if (Test-Path $shadowFile) {
  $lines = Get-Content $shadowFile -Tail 5 -ErrorAction SilentlyContinue
  Write-Host ""
  Write-Host "Recent shadow_outcome.jsonl entries (last 5):"
  foreach ($line in $lines) {
    if ($line.Trim()) { Write-Host "  $line".Substring(0, [Math]::Min(120, ("  $line").Length)) }
  }
  Write-Host ""
  Write-Host "Shadow runs in-process when signals are APPROVE/blocked - no network hop."
  Write-Host "Typical shadow replay buffer start is sub-second after AI decision in bot.py."
} else {
  Write-Host ""
  Write-Host "No shadow_outcome.jsonl yet - generate a signal on :$BotPort to populate."
}

Write-Host ""
Write-Host "Bridge command latency (stop-all):"
$sw = [Diagnostics.Stopwatch]::StartNew()
try {
  Invoke-WebRequest -Uri "http://127.0.0.1:7810/health" -UseBasicParsing -TimeoutSec 3 | Out-Null
  $sw.Stop()
  Write-Host ("  /health: {0} ms" -f $sw.ElapsedMilliseconds)
} catch {
  Write-Host "  Bridge :7810 offline - run RESTART-LAUNCHER.cmd"
}
