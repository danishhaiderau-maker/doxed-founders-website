# Overnight architecture guard — 90s health checks + auto-recovery for global showcase stack.
# Watches: bridge :7810, bot :7002, analyzer :9500, tunnel, production API, relay sim.
param(
  [int]$IntervalSec = 90,
  [int]$DurationHours = 12,
  [int]$FailThreshold = 2,
  [int]$BotPort = 0,
  [int]$AnalyzerPort = 0
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "home-stack-mode.ps1")
$mode = Get-HomeStackMode
if ($BotPort -le 0) { $BotPort = $mode.BotPort }
if ($AnalyzerPort -le 0) { $AnalyzerPort = $mode.AnalyzerPort }
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotPort -AnalyzerPort $AnalyzerPort -Port 7810
. (Join-Path $scriptDir "home-stack-health.ps1")

$logDir = Join-Path $repoRoot "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir ("overnight-architecture-guard-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))
$deadline = (Get-Date).AddHours($DurationHours)
$tunnelUrl = "https://bot.doxxedcrypto.digital"
$prodApi = "https://doxxedcrypto.digital"
$railwayApi = "https://doxed-founders-website-production.up.railway.app"
$fail = @{ bot = 0; analyzer = 0; bridge = 0; tunnel = 0; prod = 0; railway = 0 }
$lastRecover = @{ bot = [datetime]::MinValue; analyzer = [datetime]::MinValue; bridge = [datetime]::MinValue; tunnel = [datetime]::MinValue; wire = [datetime]::MinValue; railway = [datetime]::MinValue }
$cooldown = @{ bot = 480; analyzer = 480; bridge = 240; tunnel = 420; wire = 1800; railway = 3600 }

function Log([string]$msg) {
  $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
  Write-Host $line
}

function Prevent-Sleep {
  try {
    Add-Type @"
using System.Runtime.InteropServices;
public class OvernightGuardPower {
  [DllImport("kernel32.dll", CharSet=CharSet.Auto, SetLastError=true)]
  public static extern uint SetThreadExecutionState(uint esFlags);
}
"@
    [OvernightGuardPower]::SetThreadExecutionState(0x80000002) | Out-Null
  } catch { }
}

function Can-Recover([string]$Name) {
  $since = ((Get-Date) - $lastRecover[$Name]).TotalSeconds
  return $since -ge $cooldown[$Name]
}

function Mark-Recover([string]$Name) {
  $script:lastRecover[$Name] = Get-Date
}

function Invoke-BridgeCmd([string]$Path) {
  try {
    $req = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:7810$Path")
    $req.Method = "GET"
    $req.Timeout = 25000
    $req.ReadWriteTimeout = 25000
    $resp = $req.GetResponse()
    $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
    $body = $reader.ReadToEnd()
    $reader.Close()
    $resp.Close()
    return $body
  } catch {
    return $null
  }
}

function Recover-Bridge {
  if (-not (Can-Recover "bridge")) { return }
  Log "RECOVER bridge via ensure-home-bridge -Force"
  Mark-Recover "bridge"
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $scriptDir "ensure-home-bridge.ps1") -Force -Quiet | Out-Null
  Start-Sleep -Seconds 8
}

function Recover-Bot {
  if (-not (Can-Recover "bot")) { return }
  Log "RECOVER bot :$BotPort via bridge /cmd/start-bot"
  Mark-Recover "bot"
  $r = Invoke-BridgeCmd "/cmd/start-bot"
  if ($r) { Log "  bridge: $r" }
  Start-Sleep -Seconds 35
}

function Recover-Analyzer {
  if (-not (Can-Recover "analyzer")) { return }
  Log "RECOVER analyzer :$AnalyzerPort via bridge /cmd/start-analyzer"
  Mark-Recover "analyzer"
  $r = Invoke-BridgeCmd "/cmd/start-analyzer"
  if ($r) { Log "  bridge: $r" }
  Start-Sleep -Seconds 25
}

function Recover-Tunnel {
  if (-not (Can-Recover "tunnel")) { return }
  Log "RECOVER tunnel (named, hidden) on :$BotPort"
  Mark-Recover "tunnel"
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $scriptDir "restart-home-tunnel.ps1") -Port $BotPort -Force -Hidden | Out-Null
  Start-Sleep -Seconds 25
}

function Recover-FullStack {
  Log "RECOVER full stack via /cmd/start-all-global"
  Mark-Recover "bot"
  Mark-Recover "analyzer"
  Mark-Recover "tunnel"
  $r = Invoke-BridgeCmd "/cmd/start-all-global"
  if ($r) { Log "  bridge: $r" }
  Start-Sleep -Seconds 50
}

function Recover-ProdWire {
  if (-not (Can-Recover "wire")) { return }
  if (-not (Test-TunnelPublicHealthy $tunnelUrl 8)) { return }
  Log "RECOVER production wire (Neon + Railway bot URL)"
  Mark-Recover "wire"
  Push-Location $repoRoot
  try {
    & npm run wire:home-bot -- https://bot.doxxedcrypto.digital --skip-health-check 2>&1 | ForEach-Object { Log "  $_" }
  } finally {
    Pop-Location
  }
}

function Recover-RailwayApi {
  if (-not (Can-Recover "railway")) { return }
  Log "RECOVER Railway API (npm run redeploy:railway)"
  Mark-Recover "railway"
  Push-Location $repoRoot
  try {
    & npm run redeploy:railway 2>&1 | ForEach-Object { Log "  $_" }
  } finally {
    Pop-Location
  }
  Start-Sleep -Seconds 90
}

function Get-ProdBotStatus {
  try {
    $r = Invoke-RestMethod -Uri "$prodApi/api/trading-agents/bot/status" -TimeoutSec 15
    return [string]$r.status
  } catch {
    return "fail"
  }
}

function Log-RelaySimSnapshot {
  Push-Location $repoRoot
  try {
    $out = & node scripts/relay-sim-snapshot-once.mjs 2>&1 | Out-String
    $short = ($out -split "`n" | Where-Object { $_ -match "simPnl|participants|PENDING|OPEN|CLOSED|instanceStatus" }) -join " | "
    if ($short) { Log "relay $short" }
  } catch {
    Log "relay snapshot skip: $($_.Exception.Message)"
  } finally {
    Pop-Location
  }
}

Prevent-Sleep
Log "=== overnight guard started === interval=${IntervalSec}s duration=${DurationHours}h bot=:$BotPort analyzer=:$AnalyzerPort log=$logFile"

$tick = 0
while ((Get-Date) -lt $deadline) {
  $tick++
  $botOk = Test-BotHealthy
  $analyzerOk = Test-AnalyzerHealthy
  $bridgeOk = Test-BridgeHealthy
  $cf = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
  $tunnelOk = if ($botOk) { Test-TunnelPublicHealthy $tunnelUrl 10 } else { $false }
  $prodBot = Get-ProdBotStatus
  $prodOk = $prodBot -eq "online"
  $railwayOk = Test-RailwayApiHealthy 10
  $siteApiOk = Test-ProductionSiteApiHealthy 12

  if ($botOk) { $fail.bot = 0 } else { $fail.bot++ }
  if ($analyzerOk) { $fail.analyzer = 0 } else { $fail.analyzer++ }
  if ($bridgeOk) { $fail.bridge = 0 } else { $fail.bridge++ }
  if ($tunnelOk) { $fail.tunnel = 0 } else { $fail.tunnel++ }
  if ($prodOk) { $fail.prod = 0 } else { $fail.prod++ }
  if ($railwayOk -and $siteApiOk) { $fail.railway = 0 } else { $fail.railway++ }

  Log ("tick #{0} bot={1} analyzer={2} bridge={3} tunnel={4} cf={5} prod_bot={6} railway={7} site_api={8} fails=b{9}/a{10}/t{11}/br{12}/p{13}/rw{14}" -f `
    $tick, $botOk, $analyzerOk, $bridgeOk, $tunnelOk, $cf, $prodBot, $railwayOk, $siteApiOk, `
    $fail.bot, $fail.analyzer, $fail.tunnel, $fail.bridge, $fail.prod, $fail.railway)

  if ($fail.bridge -ge $FailThreshold) {
    Recover-Bridge
    if (-not (Test-BridgeHealthy)) { Recover-FullStack }
    Start-Sleep -Seconds $IntervalSec
    continue
  }

  if ($fail.bot -ge $FailThreshold -and $fail.analyzer -ge $FailThreshold) {
    Recover-FullStack
    Start-Sleep -Seconds $IntervalSec
    continue
  }

  if ($fail.bot -ge $FailThreshold) {
    Recover-Bot
  }

  if ($fail.analyzer -ge $FailThreshold) {
    Recover-Analyzer
  }

  if ($botOk -and -not $tunnelOk -and $fail.tunnel -ge $FailThreshold) {
    Recover-Tunnel
  }

  if ($botOk -and $tunnelOk -and -not $prodOk -and $fail.prod -ge $FailThreshold) {
    Recover-ProdWire
  }

  if ($fail.railway -ge $FailThreshold) {
    Recover-RailwayApi
  }

  if ($tick % 5 -eq 0) {
    Log-RelaySimSnapshot
  }

  Start-Sleep -Seconds $IntervalSec
}

Log "=== overnight guard finished ==="
