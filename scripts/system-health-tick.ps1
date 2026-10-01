# One system-health watcher tick: evaluate every subsystem, open/close
# edge-triggered alarms (toast, alarm log, webhook if configured, dashboard
# banner) and keep the local read-only endpoint up. Alarms and diagnostics
# only: it never pauses, deploys, arms or touches trading.
#
# -Interim is used by the stand-alone DoxxedSystemHealthWatcher task while the
# supervisor checkout (v2c) does not carry the watcher yet. Once the supervisor
# runs system_health.py itself, the interim task defers so exactly one watcher
# remains; the tick's lock + minimum interval enforce that regardless.
param(
  [string]$StateDir = 'C:\DoxxedCrypto\laptop-chain',
  [string]$SupervisorRepo = 'C:\DoxxedCrypto\v2c',
  [switch]$Interim
)

$ErrorActionPreference = 'Stop'
if ($StateDir.ToLowerInvariant().Contains('\onedrive\') -or $PSScriptRoot.ToLowerInvariant().Contains('\onedrive\')) { exit 0 }
$logDir = Join-Path $StateDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ('system-health-{0}.log' -f [datetime]::UtcNow.ToString('yyyyMMdd'))
function Write-TickLog([string]$Message) {
  Add-Content -LiteralPath $log -Encoding UTF8 -Value ('{0} pid={1} {2}' -f [datetime]::UtcNow.ToString('o'), $PID, $Message)
}

if ($Interim) {
  $supervisor = Join-Path $SupervisorRepo 'scripts\laptop-chain-supervisor.ps1'
  if ((Test-Path -LiteralPath $supervisor) -and (Select-String -LiteralPath $supervisor -Pattern 'system_health.py' -Quiet)) {
    Write-TickLog 'DEFERRED supervisor checkout runs the watcher'
    exit 0
  }
}

try {
  $line = (& python (Join-Path $PSScriptRoot 'system_health.py') --tick --state-dir $StateDir --analyzer-repo $SupervisorRepo 2>&1 |
    Select-Object -Last 1) -as [string]
  Write-TickLog $line
} catch {
  Write-TickLog ("TICK_ERROR {0}" -f $_.Exception.Message)
}
exit 0
