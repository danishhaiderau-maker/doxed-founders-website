# scripts/register-bot-autostart.ps1
#
# Registers the DcfShowcaseBotAutostart scheduled task: launches
# scripts/start-showcase-bot.cmd on every user logon (30s delay) and once
# daily as a safety net, with RunLevel Highest so cloudflared can bind its
# tunnel. This is the missing link that lets the showcase bot survive a
# Windows reboot / Windows Update restart / power failure without a human
# having to click "Start everything".
#
# Uptime contract (see scripts/BOT_UPTIME.md):
#   - Crash / python.exe dies        -> bot-auto-restart.ps1 + home-stack-supervisor.ps1
#   - Windows reboot / power failure -> THIS task (AtLogon) + start-showcase-bot.cmd
#   - Manual Stop (dashboard)        -> .home-stack-user-stopped flag, respected everywhere
#
# MUST BE RUN AS ADMINISTRATOR (one-time setup).
#   Right-click PowerShell -> Run as administrator -> run this script.
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
$supervisorScript = Join-Path $scriptRoot 'home-stack-supervisor.ps1'

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
  # Also surface the supervisor-watchdog task since it is part of the same contract.
  $wd = Get-ScheduledTask -TaskName 'DoxedSupervisorWatchdog' -ErrorAction SilentlyContinue
  Log ("Watchdog task 'DoxedSupervisorWatchdog': {0}" -f $(if ($wd) { $wd.State } else { 'NOT REGISTERED' }))
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

# ---- Install mode: elevation required ----
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Write-Error "This script must be run as Administrator. Right-click PowerShell -> Run as administrator, then re-run."
  exit 1
}

# Build the action: cmd /c start-showcase-bot.cmd
$action = New-ScheduledTaskAction `
  -Execute 'cmd.exe' `
  -Argument "/c `"$startCmd`""

# Triggers:
#   (1) At logon, with a 30s delay so the network/DNS stack is ready (the bot
#       and cloudflared both need outbound HTTPS to come up before they bind).
#   (2) Once-daily safety net at 04:00 local - if the stack died overnight and
#       no one logged in, this brings it back. Repetition covers missed runs.
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn
$logonTrigger.Delay = "PT${LogonDelaySec}S"
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At '4:00 AM'
$dailyTrigger.RandomDelay = 'PT5M'

# Run with highest privilege so cloudflared can bind the tunnel and the
# supervisor can kill / relaunch elevated sibling processes.
$taskPrincipal = New-ScheduledTaskPrincipal `
  -UserId $identity.Name `
  -LogonType Interactive `
  -RunLevel Highest

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
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Log "Updated existing task '$TaskName'."
}

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger @($logonTrigger, $dailyTrigger) `
  -Principal $taskPrincipal `
  -Settings $settings `
  -Description "DCF: start the showcase bot stack (bot :7002 + analyzer :9001 + tunnel + supervisor) at logon and daily. Respects manual Stop via .home-stack-user-stopped flag." `
  -Force | Out-Null

Log ""
Log "Registered scheduled task '$TaskName'." -ForegroundColor Green
Log "  Entry     : $startCmd"
Log "  Triggers  : AtLogon (+${LogonDelaySec}s delay) + daily 4:00 AM"
Log "  Elevation : Highest (RunLevel Highest)"
Log "  User      : $($identity.Name)"
Log "  Restart   : up to 3 retries @ 5min if the launch fails"
Log ""
Log "Run now     : Start-ScheduledTask -TaskName '$TaskName'"
Log "Verify      : powershell -ExecutionPolicy Bypass -File scripts\register-bot-autostart.ps1 -Status"
Log "Uninstall   : powershell -ExecutionPolicy Bypass -File scripts\register-bot-autostart.ps1 -Uninstall"
