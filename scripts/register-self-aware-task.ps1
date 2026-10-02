# Registers DoxxedSelfAware (every 5 minutes + at logon): runs
# scripts\self-aware-tick.ps1 from a pinned non-OneDrive checkout so the
# self-aware daemon on 127.0.0.1:9021 stays up. -Unregister removes it.
param(
  [string]$TaskName = 'DoxxedSelfAware',
  [string]$RepoRoot = 'C:\DoxxedCrypto\self-aware-live',
  [string]$HomeDir = 'C:\DoxxedCrypto\self-aware',
  [switch]$Unregister
)

$ErrorActionPreference = 'Stop'
if ($Unregister) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  "unregistered $TaskName"
  exit 0
}
if ($RepoRoot.ToLowerInvariant().Contains('\onedrive\')) { throw 'Refusing a OneDrive checkout.' }
$tick = Join-Path $RepoRoot 'scripts\self-aware-tick.ps1'
if (-not (Test-Path -LiteralPath $tick)) { throw "Tick script not found: $tick" }
if (-not (Test-Path -LiteralPath (Join-Path $HomeDir 'venv\Scripts\python.exe'))) {
  throw "venv missing: python -m venv --system-site-packages $HomeDir\venv; then pip install duckdb"
}

$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$tick`" -HomeDir `"$HomeDir`""
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory (Join-Path $RepoRoot 'scripts')
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$repeat = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($logon, $repeat) -Settings $settings `
  -Principal $principal -Description 'Doxxed self-aware daemon keeper (laptop-only, 127.0.0.1:9021).' -Force | Out-Null
Enable-ScheduledTask -TaskName $TaskName | Out-Null
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
