# scripts/register-tidy-orphan-shells.ps1
#
# Registers scripts/tidy-orphan-shells.ps1 -Execute as a Windows Task Scheduler
# job that runs every 6 hours WITH ELEVATION. The tidy script can only kill
# orphan shells owned by the same elevation level - non-elevated runs report
# "Access is denied" for processes launched by elevated launchers (which is
# how the bot stack is normally started).
#
# MUST BE RUN AS ADMINISTRATOR (one-time setup).
#   Right-click PowerShell -> Run as administrator -> run this script.
#
# After registration the task runs at logon + every 6h, killing orphan
# cmd.exe and hidden powershell.exe while preserving python.exe (bot/analyzer),
# node.exe (watchers), cloudflared.exe, and the essential watcher scripts.

[CmdletBinding()]
param(
  [string]$TaskName = 'DcfTidyOrphanShells',
  [int]$IntervalHours = 6
)

$ErrorActionPreference = 'Stop'

# Elevation check
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Write-Error "This script must be run as Administrator. Right-click PowerShell -> Run as administrator, then re-run."
  exit 1
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($scriptRoot)) { $scriptRoot = $PSScriptRoot }
$tidyScript = Join-Path $scriptRoot 'tidy-orphan-shells.ps1'
if (-not (Test-Path $tidyScript)) {
  Write-Error "tidy-orphan-shells.ps1 not found at $tidyScript"
  exit 1
}

# Build the scheduled task
$action = New-ScheduledTaskAction `
  -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$tidyScript`" -Execute"

$trigger = @(
  (New-ScheduledTaskTrigger -AtLogOn),
  (New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) -RepetitionDuration ([TimeSpan]::MaxValue))
)

# Run with highest privilege so it can kill cross-session orphans
$principal = New-ScheduledTaskPrincipal `
  -UserId $identity.Name `
  -LogonType Interactive `
  -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

# Unregister if it exists, then register fresh
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "Removed existing task '$TaskName'."
}

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $action `
  -Trigger $trigger `
  -Principal $principal `
  -Settings $settings `
  -Description "DCF: kill orphan cmd.exe and hidden powershell.exe shells every $IntervalHours hours (preserves bot/analyzer/watchers/tunnel)." `
  -Force | Out-Null

Write-Host ""
Write-Host "Registered scheduled task '$TaskName'." -ForegroundColor Green
Write-Host "  Script:    $tidyScript -Execute"
Write-Host "  Interval:  every $IntervalHours hours + at logon"
Write-Host "  Elevation: Highest (RunLevel Highest)"
Write-Host "  User:      $($identity.Name)"
Write-Host ""
Write-Host "To run immediately:    Start-ScheduledTask -TaskName $TaskName"
Write-Host "To view next run:     Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "To remove:            Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
