# Register (or refresh) the DoxedStackMonitor scheduled task that runs scripts/stack-monitor.ps1
# every 5 minutes. Reproducible from the repo — run after a fresh checkout or PC reset:
#   powershell -ExecutionPolicy Bypass -File scripts/register-stack-monitor.ps1
# Requires admin (schtasks /create needs elevation). Re-running updates the task in place.
#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = Split-Path -Parent $scriptDir
$monitor   = Join-Path $scriptDir 'stack-monitor.ps1'
if (-not (Test-Path $monitor)) { throw "stack-monitor.ps1 not found at $monitor" }

# Detect powershell.exe (PS 5.1) vs pwsh.exe (PS7+) — prefer pwsh if present.
$pwsh = Get-Command pwsh.exe -ErrorAction SilentlyContinue
$exe  = if ($pwsh) { $pwsh.Source } else { (Get-Command powershell.exe).Source }
$arg  = if ($pwsh) { '-NoProfile -ExecutionPolicy Bypass -File' } else { '-NoProfile -ExecutionPolicy Bypass -File' }
$action = New-ScheduledTaskAction -Execute $exe -Argument "$arg `"$monitor`" -Quiet"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 4)

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest

$name = 'DoxedStackMonitor'
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing) {
  Set-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
  Write-Host "Updated scheduled task '$name' (every 5 min) -> $monitor" -ForegroundColor Green
} else {
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'DoxedCrypto stack health monitor (local+cloud+GitHub). Writes logs/stack_health.json; fires webhook on abnormality.' | Out-Null
  Write-Host "Registered scheduled task '$name' (every 5 min) -> $monitor" -ForegroundColor Green
}

# Run once immediately so logs/stack_health.json is fresh.
Write-Host "Running stack-monitor once now..." -ForegroundColor Cyan
& $exe -NoProfile -ExecutionPolicy Bypass -File $monitor -Quiet
Write-Host "Done. Health snapshot: $repoRoot\logs\stack_health.json" -ForegroundColor Green
