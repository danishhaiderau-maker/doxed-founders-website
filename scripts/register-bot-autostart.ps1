# scripts/register-bot-autostart.ps1
#
# Registers the DcfShowcaseBotAutostart scheduled task: launches
# scripts/start-showcase-bot.cmd on every user logon (30s delay) and once
# daily as a safety net. The entry point is deliberately mirror-only:
#   - :7002 compatibility proxy -> canonical Fly bot
#   - incremental Fly data synchronization
#   - :9001 analyzer over the synchronized mirror
# It never starts a Windows AI/strategy bot, relay publisher, supervisor, or
# Cloudflare tunnel. Fly.io remains the sole production owner.
#
# Uptime contract:
#   - Fly process availability       -> Fly machine restart policy
#   - Windows reboot / power failure -> THIS task restores desktop mirror tools
#   - Windows unavailable            -> Fly AI/trading owner remains independent
#
# Administrator is preferred (one-time setup) but no longer mandatory.
#   Run from an Administrator console for Highest process-control privileges,
#   or run normally to install the current-user Limited recovery task.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\register-bot-autostart.ps1            # install
#   powershell -ExecutionPolicy Bypass -File scripts\register-bot-autostart.ps1 -Status    # check
#   powershell -ExecutionPolicy Bypass -File scripts\register-bot-autostart.ps1 -Uninstall # remove
#Requires -Version 5.1
[CmdletBinding()]
param(
  [string]$TaskName = 'DcfShowcaseBotAutostart',
  [int]$LogonDelaySec = 30,
  [int]$SafetyNetIntervalHours = 24,
  [switch]$Status,
  [switch]$Uninstall,
  [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

function Log([string]$msg) {
  if (-not $Quiet) { Write-Host $msg }
}

# Resolve repo paths (works whether invoked by . or & and from any cwd).
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($scriptRoot)) { $scriptRoot = $PSScriptRoot }
if ([string]::IsNullOrWhiteSpace($scriptRoot)) { $scriptRoot = $executionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath('.') }
$repoRoot  = Split-Path -Parent $scriptRoot
$startCmd  = Join-Path $scriptRoot 'start-showcase-bot.cmd'
if (-not (Test-Path $startCmd)) {
  throw "start-showcase-bot.cmd not found at $startCmd"
}

# ---- Status mode ----
if ($Status) {
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if (-not $task) {
    Log "Scheduled task '$TaskName': NOT REGISTERED"
  } else {
    Log ("Scheduled task '{0}': {1}" -f $TaskName, $task.State)
    $info = $task | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
    if ($info) {
      Log ("  LastRun : {0}" -f $(if ($info.LastRunTime) { $info.LastRunTime } else { 'never' }))
      Log ("  LastResult: {0}" -f $(if ($info.LastTaskResult) { '0x{0:X8}' -f $info.LastTaskResult } else { 'n/a' }))
      Log ("  NextRun : {0}" -f $(if ($info.NextRunTime -and $info.NextRunTime.Year -gt 2000) { $info.NextRunTime } else { 'none scheduled' }))
    }
    Log ("  Action   : {0}" -f ($task.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" })[0])
  }
  # Surface the old watchdog so operators can see that it is a separate legacy
  # task. It is not installed or invoked by this mirror-only registration.
  $wd = Get-ScheduledTask -TaskName 'DoxedSupervisorWatchdog' -ErrorAction SilentlyContinue
  Log ("Legacy watchdog 'DoxedSupervisorWatchdog': {0} (not part of mirror autostart)" -f $(if ($wd) { $wd.State } else { 'NOT REGISTERED' }))
  exit 0
}

# ---- Uninstall mode ----
if ($Uninstall) {
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Log "Removed scheduled task '$TaskName'."
  } else {
    Log "Task '$TaskName' was not registered."
  }
  exit 0
}

# ---- Install mode -----------------------------------------------------------
# Prefer Highest when this script is run from an elevated console. A standard
# desktop session can still register a current-user Limited task, which is
# enough to restore the dashboard proxy, data sync, and analyzer. Previously
# we aborted outright for non-admin users, leaving machines with no durable
# recovery task at all.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isAdministrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$runLevel = if ($isAdministrator) { 'Highest' } else { 'Limited' }
if (-not $isAdministrator) {
  Log "Administrator rights are unavailable; registering a current-user recovery task (Limited run level)."
  Log "Run this installer from an Administrator console later to upgrade process-control privileges."
}

# Build the action: cmd /c start-showcase-bot.cmd
$action = New-ScheduledTaskAction `
  -Execute 'cmd.exe' `
  -Argument "/c `"$startCmd`""

# Triggers:
#   (1) At logon, with a 30s delay so the network/DNS stack is ready for Fly
#       proxying and data synchronization.
#   (2) Once-daily safety net at 04:00 local - if the stack died overnight and
#       no one logged in, this brings it back. Repetition covers missed runs.
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn
$logonTrigger.Delay = "PT${LogonDelaySec}S"
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At '4:00 AM'
$dailyTrigger.RandomDelay = 'PT5M'

# Preserve the historical run-level behavior so an existing installation can
# update its action without changing the user's task ownership.
$taskPrincipal = New-ScheduledTaskPrincipal `
  -UserId $identity.Name `
  -LogonType Interactive `
  -RunLevel $runLevel

$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 5) `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
  -MultipleInstances IgnoreNew

# Unregister any prior version, then register fresh.
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
  try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Log "Updated existing task '$TaskName'."
  } catch [System.UnauthorizedAccessException] {
    Log "Existing administrator-owned task '$TaskName' is already installed; preserving it."
    Log "Run this installer from an Administrator console only when you need to update that task."
    exit 0
  } catch {
    if ($_.Exception.Message -match 'Access is denied') {
      Log "Existing administrator-owned task '$TaskName' is already installed; preserving it."
      Log "Run this installer from an Administrator console only when you need to update that task."
      exit 0
    }
    throw
  }
}

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger @($logonTrigger, $dailyTrigger) `
  -Principal $taskPrincipal `
  -Settings $settings `
  -Description "DCF desktop mirror only: :7002 proxies the canonical Fly bot, synchronizes Fly data, and starts the :9001 analyzer. Never starts a Windows AI bot or Cloudflare tunnel." `
  -Force | Out-Null

Log ""
Log "Registered scheduled task '$TaskName'." -ForegroundColor Green
Log "  Entry     : $startCmd"
Log "  Triggers  : AtLogon (+${LogonDelaySec}s delay) + daily 4:00 AM"
Log "  Run level : $runLevel"
Log "  User      : $($identity.Name)"
Log "  Restart   : up to 3 retries @ 5min if the launch fails"
Log ""
Log "Run now     : Start-ScheduledTask -TaskName '$TaskName'"
Log "Verify      : powershell -ExecutionPolicy Bypass -File scripts\register-bot-autostart.ps1 -Status"
Log "Uninstall   : powershell -ExecutionPolicy Bypass -File scripts\register-bot-autostart.ps1 -Uninstall"
