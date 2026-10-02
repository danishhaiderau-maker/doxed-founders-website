# One system-health watcher tick: evaluate every subsystem, open/close
# edge-triggered alarms (RED toast, alarm log feeding the dashboards' Alerts
# section, dashboard banner) and keep the local read-only endpoint up. Alarms and diagnostics
# only: it never pauses, deploys, arms or touches trading.
#
# -Interim is used by the stand-alone DoxxedSystemHealthWatcher task as the
# watcher-of-watchers. It defers only while the supervisor checkout runs the
# watcher AND the supervisor task ran within $FreshMinutes AND the published
# verdict is younger than $FreshMinutes (by timestamp, not file existence).
# Otherwise it ticks itself; the tick's lock + minimum interval keep exactly
# one evaluation at a time.
param(
  [string]$StateDir = 'C:\DoxxedCrypto\laptop-chain',
  [string]$SupervisorRepo = 'C:\DoxxedCrypto\v2c',
  [string]$SupervisorTask = 'DoxxedLaptopChainSupervisor',
  [int]$FreshMinutes = 15,
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

function Get-SupervisorRunAgeMinutes {
  try {
    $info = Get-ScheduledTask -TaskName $SupervisorTask -ErrorAction Stop | Get-ScheduledTaskInfo -ErrorAction Stop
    if ($info.LastRunTime) { return ([datetime]::UtcNow - $info.LastRunTime.ToUniversalTime()).TotalMinutes }
  } catch { }
  return $null
}

function Get-VerdictAgeMinutes {
  $latest = Join-Path $StateDir 'health\system-health-latest.json'
  try {
    $report = Get-Content -LiteralPath $latest -Raw -ErrorAction Stop | ConvertFrom-Json
    $generated = [double]$report.generated_ts
    if ($generated -gt 0) {
      return ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0 - $generated) / 60.0
    }
  } catch { }
  return $null
}

function Write-InterimStatus([string]$Decision, $CarriesWatcher, [string]$RunAge, [string]$VerdictAge) {
  # Read by the watcher's watcher.interim check: a stale file means this task stopped running.
  $path = Join-Path $StateDir 'health\interim-tick.status.json'
  try {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
    $json = [ordered]@{ schema = 'system_health_interim_tick_v1'; at = [datetime]::UtcNow.ToString('o'); decision = $Decision
                        carries_watcher = [bool]$CarriesWatcher; supervisor_run = $RunAge; verdict_age = $VerdictAge } | ConvertTo-Json -Compress
    $tmp = "$path.tmp"
    [System.IO.File]::WriteAllText($tmp, $json, (New-Object System.Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $tmp -Destination $path -Force
  } catch { Write-TickLog ("INTERIM_STATUS_ERROR {0}" -f $_.Exception.Message) }
}

if ($Interim) {
  $supervisor = Join-Path $SupervisorRepo 'scripts\laptop-chain-supervisor.ps1'
  $carriesWatcher = (Test-Path -LiteralPath $supervisor) -and (Select-String -LiteralPath $supervisor -Pattern 'system_health.py' -Quiet)
  $runAge = Get-SupervisorRunAgeMinutes
  $verdictAge = Get-VerdictAgeMinutes
  $fmt = { param($v) if ($null -eq $v) { 'unknown' } else { '{0:N1}m' -f $v } }
  if ($carriesWatcher -and $null -ne $runAge -and $runAge -lt $FreshMinutes -and $null -ne $verdictAge -and $verdictAge -lt $FreshMinutes) {
    Write-TickLog ('DEFERRED supervisor ran {0} ago and published a verdict {1} ago' -f (& $fmt $runAge), (& $fmt $verdictAge))
    Write-InterimStatus 'DEFERRED' $carriesWatcher (& $fmt $runAge) (& $fmt $verdictAge)
    exit 0
  }
  Write-TickLog ('TAKEOVER carriesWatcher={0} supervisorRun={1} verdictAge={2}' -f $carriesWatcher, (& $fmt $runAge), (& $fmt $verdictAge))
  Write-InterimStatus 'TAKEOVER' $carriesWatcher (& $fmt $runAge) (& $fmt $verdictAge)
}

try {
  $line = (& python (Join-Path $PSScriptRoot 'system_health.py') --tick --state-dir $StateDir --analyzer-repo $SupervisorRepo 2>&1 |
    Select-Object -Last 1) -as [string]
  Write-TickLog $line
} catch {
  Write-TickLog ("TICK_ERROR {0}" -f $_.Exception.Message)
}
exit 0
