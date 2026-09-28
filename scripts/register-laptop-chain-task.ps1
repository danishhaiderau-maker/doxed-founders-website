# Registers the single laptop-chain supervisor task (system powershell.exe,
# at logon + every 5 minutes). Other Doxxed tasks are left untouched.
param(
  [string]$TaskName = 'DoxxedLaptopChainSupervisor',
  [string]$RepoRoot = '',
  [string]$CanonicalRoot = '',
  [string]$StateDir = ''
)

$ErrorActionPreference = 'Stop'
if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent $PSScriptRoot }
$supervisor = Join-Path $RepoRoot 'scripts\laptop-chain-supervisor.ps1'
if (-not (Test-Path -LiteralPath $supervisor)) { throw "Supervisor not found: $supervisor" }
if ($RepoRoot.ToLowerInvariant().Contains('\onedrive\')) { throw 'Refusing a OneDrive checkout.' }

$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$supervisor`" -RepoRoot `"$RepoRoot`""
if ($CanonicalRoot) { $arguments += " -CanonicalRoot `"$CanonicalRoot`"" }
if ($StateDir) { $arguments += " -StateDir `"$StateDir`"" }

$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $RepoRoot
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$repeat = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($logon, $repeat) -Settings $settings `
  -Principal $principal -Description 'Doxxed laptop research chain: one ACK watcher, 30-minute analyzer, monitor.' -Force | Out-Null
Enable-ScheduledTask -TaskName $TaskName | Out-Null
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
