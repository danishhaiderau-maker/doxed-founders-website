# Registers the DoxxedGenomeGrid task (every 2 hours) that runs
# scripts\run-genome-grid.ps1 against the analyzer checkout (C:\DoxxedCrypto\v2c).
# The self-aware "sections" job flags the /details genome grid AMBER after 3 h
# and RED after 12 h without a refresh. -Unregister removes it.
param(
  [string]$TaskName = 'DoxxedGenomeGrid',
  [string]$RepoRoot = 'C:\DoxxedCrypto\v2c',
  [switch]$Unregister
)

$ErrorActionPreference = 'Stop'
if ($Unregister) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  "unregistered $TaskName"
  exit 0
}
if ($RepoRoot.ToLowerInvariant().Contains('\onedrive\')) { throw 'Refusing a OneDrive checkout.' }
$runner = Join-Path $RepoRoot 'scripts\run-genome-grid.ps1'
if (-not (Test-Path -LiteralPath $runner)) { throw "Runner not found: $runner" }

$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -RepoRoot `"$RepoRoot`""
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $RepoRoot
$repeat = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(2)) -RepetitionInterval (New-TimeSpan -Hours 2)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 90) -Priority 7
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $repeat -Settings $settings `
  -Principal $principal -Description 'Doxxed analyzer policy genome grid (research only, laptop-side).' -Force | Out-Null
Enable-ScheduledTask -TaskName $TaskName | Out-Null
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State