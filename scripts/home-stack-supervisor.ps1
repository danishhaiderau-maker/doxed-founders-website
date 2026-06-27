# 24/7 home stack supervisor - HTTP health checks + auto-recovery with cooldowns.
# Replaces tunnel-watchdog for long runs (48h-1 week). Started by Start everything.
param(
  [int]$BotPort = 7800,
  [int]$AnalyzerPort = 9001,
  [int]$BridgePort = 7810,
  [int]$IntervalSec = 60,
  [int]$FailThreshold = 5,
  [int]$BotFailThreshold = 2,
  [int]$BotCooldownSec = 300,
  [int]$AnalyzerCooldownSec = 600,
  [int]$TunnelCooldownSec = 900,
  [int]$BridgeCooldownSec = 300
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotPort -AnalyzerPort $AnalyzerPort -BridgePort $BridgePort
. (Join-Path $scriptDir "home-stack-health.ps1")

$logFile = Join-Path $repoRoot ".home-stack-supervisor.log"
$lockFile = Join-Path $repoRoot ".home-stack-supervisor.lock"
$namedFlag = Join-Path $repoRoot ".home-use-named-tunnel"

function Log([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
}

function Prevent-Sleep {
  try {
    Add-Type @"
using System.Runtime.InteropServices;
public class HomeStackPower {
  [DllImport("kernel32.dll", CharSet=CharSet.Auto, SetLastError=true)]
  public static extern uint SetThreadExecutionState(uint esFlags);
}
"@
    [HomeStackPower]::SetThreadExecutionState(0x80000002) | Out-Null
  } catch { }
}

function Test-SupervisorLock {
  try {
    $script:LockHandle = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    return $true
  } catch {
    return $false
  }
}

function Stop-DuplicateSupervisors {
  Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and $_.CommandLine -like "*home-stack-supervisor.ps1*" -and $_.ProcessId -ne $PID
    } | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Get-TunnelPublicUrl {
  if ((Test-Path $namedFlag) -and (Use-NamedTunnel)) {
    return "https://bot.doxxedcrypto.digital"
  }
  return Get-TunnelUrl
}

function Restart-BotComponent {
  Log "RECOVER bot - stop + start on :$BotPort (visible window)"
  Stop-PythonMatching "btc_conservative_agent" | Out-Null
  Stop-ListenPortFast $BotPort | Out-Null
  Start-Sleep -Seconds 3
  Start-VisibleConsole (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", "$BotPort") -Title "Doxed Bot :$BotPort"
}

function Restart-AnalyzerComponent {
  Log "RECOVER analyzer - stop + start on :$AnalyzerPort (visible window)"
  Stop-PythonMatching "analyzer_research_engine" | Out-Null
  Stop-ListenPortFast $AnalyzerPort | Out-Null
  Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
  Start-VisibleConsole (Join-Path $scriptDir "start-home-analyzer.ps1") @("-Port", "$AnalyzerPort") -Title "Doxed Analyzer :$AnalyzerPort"
}

function Restart-TunnelComponent {
  param([string]$Reason)
  Log ("RECOVER tunnel - " + $Reason)
  Stop-Cloudflared | Out-Null
  Start-Sleep -Seconds 3
  if ((Test-Path $namedFlag) -and (Use-NamedTunnel)) {
    try {
      Start-CloudflaredNamedHidden -Port $BotPort
    } catch {
      Log ("RECOVER tunnel hidden start failed: " + $_.Exception.Message + " - opening visible tunnel window")
      Start-VisibleConsole (Join-Path $scriptDir "restart-home-tunnel.ps1") @("-Port", "$BotPort", "-Force") -Title "Doxed Cloudflare Tunnel"
    }
  } else {
    Start-VisibleConsole (Join-Path $scriptDir "restart-home-tunnel.ps1") @("-Port", "$BotPort", "-Force") -Title "Doxed Cloudflare Tunnel"
  }
}

function Restart-BridgeComponent {
  Log "RECOVER bridge - restart launcher on :$BridgePort"
  Close-WindowsByTitlePrefix @("Doxed Home Bridge :$BridgePort", "TEST Bridge") | Out-Null
  Stop-ListenPortFast $BridgePort | Out-Null
  Start-Sleep -Seconds 2
  Start-VisibleConsole (Join-Path $scriptDir "home-stack-launcher.ps1") @() -Title "Doxed Home Bridge :$BridgePort"
}

function Invoke-Recovery {
  param(
    [string]$Name,
    [scriptblock]$Action,
    [datetime]$LastAt,
    [int]$CooldownSec
  )
  $since = ((Get-Date) - $LastAt).TotalSeconds
  if ($since -lt $CooldownSec) {
    Log ("RECOVER $Name skipped - cooldown $([int]$since)s/$CooldownSec")
    return $LastAt
  }
  & $Action
  return (Get-Date)
}

Stop-DuplicateSupervisors
if (-not (Test-SupervisorLock)) {
  Log "Another supervisor is already running - exit"
  exit 0
}

Set-Content -Path (Join-Path $repoRoot ".home-stack-supervisor.pid") -Value $PID -NoNewline
Prevent-Sleep
Log "supervisor started bot=:$BotPort analyzer=:$AnalyzerPort interval=${IntervalSec}s threshold=$FailThreshold named=$(Test-Path $namedFlag)"

$hygieneTick = 0

$fail = @{
  bot = 0; analyzer = 0; tunnel = 0; bridge = 0
}
$lastRecover = @{
  bot = [datetime]::MinValue
  analyzer = [datetime]::MinValue
  tunnel = [datetime]::MinValue
  bridge = [datetime]::MinValue
}

while ($true) {
  $hygieneTick++
  if ($hygieneTick -ge 6) {
    $hygieneTick = 0
    try {
      $hygiene = Invoke-HomeTerminalHygiene -BotPort $BotPort -AnalyzerPort $AnalyzerPort
      if ($hygiene -and $hygiene.Count -gt 0) {
        Log ("hygiene " + ($hygiene -join " | "))
      }
    } catch { }
  }

  $tunnelUrl = Get-TunnelPublicUrl
  $botOk = Test-BotHealthy
  $analyzerOk = Test-AnalyzerHealthy
  $bridgeOk = Test-BridgeHealthy
  $cfRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
  $tunnelOk = if ($botOk -and $tunnelUrl) { Test-TunnelPublicHealthy $tunnelUrl } else { $false }

  if ($botOk) { $fail.bot = 0 } else { $fail.bot++ }
  if ($analyzerOk) { $fail.analyzer = 0 } else { $fail.analyzer++ }
  if ($bridgeOk) { $fail.bridge = 0 } else { $fail.bridge++ }
  if ($tunnelOk) { $fail.tunnel = 0 } else { $fail.tunnel++ }

  $botHung = Test-BotHung
  Log ("tick bot=$botOk analyzer=$analyzerOk bridge=$bridgeOk tunnel=$tunnelOk cf=$cfRunning hung=$botHung fails=b$($fail.bot)/a$($fail.analyzer)/t$($fail.tunnel)/br$($fail.bridge) url=$tunnelUrl")

  if ($fail.bridge -ge $FailThreshold) {
    $lastRecover.bridge = Invoke-Recovery "bridge" { Restart-BridgeComponent } $lastRecover.bridge $BridgeCooldownSec
    $fail.bridge = 0
    Start-Sleep -Seconds $IntervalSec
    continue
  }

  # Never restart on a single slow probe — require fail threshold (hung alone is logged only).
  # Bot uses a tighter threshold (BotFailThreshold=2) so a real crash restarts in ~2 min, not 15.
  if ($fail.bot -ge $BotFailThreshold) {
    if (Test-HomeStackUserStopped) {
      Log "RECOVER bot skipped - user stopped stack (.home-stack-user-stopped)"
      $fail.bot = 0
    } else {
      $lastRecover.bot = Invoke-Recovery "bot" { Restart-BotComponent } $lastRecover.bot $BotCooldownSec
      $fail.bot = 0
      $fail.tunnel = 0
      Start-Sleep -Seconds 30
      continue
    }
  }

  if ($fail.analyzer -ge $FailThreshold) {
    if (Test-HomeStackUserStopped) {
      Log "RECOVER analyzer skipped - user stopped stack"
      $fail.analyzer = 0
    } else {
      $lastRecover.analyzer = Invoke-Recovery "analyzer" { Restart-AnalyzerComponent } $lastRecover.analyzer $AnalyzerCooldownSec
      $fail.analyzer = 0
      Start-Sleep -Seconds 20
      continue
    }
  }

  # Zombie cloudflared: process up but public URL dead for multiple checks.
  if ($botOk -and $tunnelUrl -and -not $tunnelOk -and $fail.tunnel -ge $FailThreshold) {
    if (Test-HomeStackUserStopped) {
      Log "RECOVER tunnel skipped - user stopped stack"
      $fail.tunnel = 0
    } else {
      $reason = if ($cfRunning) { "zombie cloudflared (process up, public ping dead)" } else { "cloudflared not running" }
      $lastRecover.tunnel = Invoke-Recovery "tunnel" { Restart-TunnelComponent $reason } $lastRecover.tunnel $TunnelCooldownSec
      $fail.tunnel = 0
      Start-Sleep -Seconds 45
      continue
    }
  }

  Start-Sleep -Seconds $IntervalSec
}
