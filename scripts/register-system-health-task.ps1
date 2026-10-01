# Registers the interim DoxxedSystemHealthWatcher task (every 5 minutes) that
# runs scripts\system-health-tick.ps1 -Interim from a pinned non-OneDrive
# checkout. It defers automatically once the DoxxedLaptopChainSupervisor
# checkout runs system_health.py, so there is never more than one watcher.
# -Unregister removes it.
param(
  [string]$TaskName = 'DoxxedSystemHealthWatcher',
  [string]$RepoRoot = '',
  [string]$StateDir = 'C:\DoxxedCrypto\laptop-chain',
  [switch]$Unregister
)

$ErrorActionPreference = 'Stop'
if ($Unregister) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  "unregistered $TaskName"
  exit 0
}
if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent $PSScriptRoot }
if ($RepoRoot.ToLowerInvariant().Contains('\onedrive\')) { throw 'Refusing a OneDrive checkout.' }
$tick = Join-Path $RepoRoot 'scripts\system-health-tick.ps1'
if (-not (Test-Path -LiteralPath $tick)) { throw "Tick script not found: $tick" }

$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$tick`" -StateDir `"$StateDir`" -Interim"
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $RepoRoot
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$repeat = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($logon, $repeat) -Settings $settings `
  -Principal $principal -Description 'Doxxed system health watcher (interim; defers to the laptop-chain supervisor).' -Force | Out-Null
Enable-ScheduledTask -TaskName $TaskName | Out-Null
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
