# Registers the durable supervisor watchdog task.
#
# This task is deliberately separate from the long-running supervisor.  If the
# supervisor process exits or stops advancing its heartbeat, Task Scheduler
# runs home-stack-supervisor-watchdog.ps1 and restores one exclusive owner.
# The watchdog never starts trading and respects the normal home-stack stop
# sentinel through the supervisor it launches.
#Requires -Version 5.1
[CmdletBinding()]
param(
  [string]$TaskName = "DoxedSupervisorWatchdog",
  [int]$IntervalMinutes = 2,
  [switch]$Status,
  [switch]$Uninstall,
  [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($scriptRoot)) { $scriptRoot = $PSScriptRoot }
$watchdogScript = Join-Path $scriptRoot "home-stack-supervisor-watchdog.ps1"

function Log([string]$Message) {
  if (-not $Quiet) { Write-Host $Message }
}

if (-not (Test-Path -LiteralPath $watchdogScript)) {
  throw "Supervisor watchdog not found at $watchdogScript"
}
if ($IntervalMinutes -lt 1) {
  throw "IntervalMinutes must be at least 1."
}

if ($Status) {
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if (-not $task) {
    Log "Scheduled task '$TaskName': NOT REGISTERED"
    exit 1
  }
  $info = $task | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
  Log ("Scheduled task '{0}': {1}" -f $TaskName, $task.State)
  if ($info) {
    Log ("  LastRun   : {0}" -f $info.LastRunTime)
    Log ("  LastResult: 0x{0:X8}" -f $info.LastTaskResult)
    Log ("  NextRun   : {0}" -f $info.NextRunTime)
  }
  Log ("  Action    : {0}" -f ($task.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" })[0])
  exit 0
}

if ($Uninstall) {
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Log "Removed scheduled task '$TaskName'."
  } else {
    Log "Task '$TaskName' was not registered."
  }
  exit 0
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principalCheck = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdministrator = $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$runLevel = if ($isAdministrator) { "Highest" } else { "Limited" }

$action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$watchdogScript`" -Quiet"

$trigger = New-ScheduledTaskTrigger `
  -Once `
  -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
  -RepetitionDuration (New-TimeSpan -Days 3650)

$taskPrincipal = New-ScheduledTaskPrincipal `
  -UserId $identity.Name `
  -LogonType Interactive `
  -RunLevel $runLevel

$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 1) `
  -MultipleInstances IgnoreNew

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Principal $taskPrincipal `
  -Settings $settings `
  -Description "DCF: every ${IntervalMinutes} minutes, restore one healthy home-stack supervisor from its progress heartbeat." `
  -Force | Out-Null

Log "Registered scheduled task '$TaskName'."
Log "  Watchdog : $watchdogScript"
Log "  Interval : every ${IntervalMinutes} minutes"
Log "  Run level: $runLevel"
Log "Run now    : Start-ScheduledTask -TaskName '$TaskName'"
