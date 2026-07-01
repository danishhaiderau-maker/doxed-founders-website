# Register (or refresh) the DoxxedBridgeWatch scheduled task that runs bridge-watchdog.ps1
# every 1 minute. The watchdog polls :7810/health every ~10s and relaunches the bridge if
# it dies (e.g. when the user closes the bridge console window). Survives terminal closure,
# logoff, and reboot.
#
# One-command install (run once on the home PC, needs admin for the scheduled task):
#   powershell -ExecutionPolicy Bypass -File scripts\register-bridge-watchdog.ps1
#
# Verify (no admin needed):
#   powershell -ExecutionPolicy Bypass -File scripts\register-bridge-watchdog.ps1 -Status
#
# Uninstall:
#   powershell -ExecutionPolicy Bypass -File scripts\register-bridge-watchdog.ps1 -Uninstall
#
# Re-running updates the task in place. If run without admin, the scheduled task registration
# is skipped but a detached hidden watchdog loop is still launched immediately so coverage
# starts now (survives terminal closure, but not logoff/reboot — re-run as admin for that).
#Requires -Version 5.1
param(
  [switch]$Quiet,
  [switch]$Status,
  [switch]$Uninstall
)

$ErrorActionPreference = 'Continue'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = Split-Path -Parent $scriptDir
$watchdog  = Join-Path $scriptDir 'bridge-watchdog.ps1'
$lockFile  = Join-Path $repoRoot '.home-bridge-watchdog.lock'
$pidFile   = Join-Path $repoRoot '.home-bridge-watchdog.pid'
$taskName  = 'DoxxedBridgeWatch'

function Wd-Log([string]$msg) {
  if (-not $Quiet) { Write-Host $msg }
}

function Test-WatchdogLoopAlive {
  if (-not (Test-Path $pidFile)) { return $false }
  $otherPid = 0
  [int]::TryParse((Get-Content $pidFile -Raw -ErrorAction SilentlyContinue), [ref]$otherPid) | Out-Null
  if ($otherPid -le 0) { return $false }
  $p = Get-Process -Id $otherPid -ErrorAction SilentlyContinue
  if (-not $p) { return $false }
  return ($p.Name -match 'powershell|pwsh')
}

if ($Status) {
  $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  Wd-Log ("Scheduled task '{0}': {1}" -f $taskName, $(if ($task) { $task.State } else { 'NOT REGISTERED' }))
  $loopState = if (Test-WatchdogLoopAlive) { "RUNNING (pid=" + ((Get-Content $pidFile -Raw -ErrorAction SilentlyContinue).Trim()) + ")" } else { 'NOT RUNNING' }
  Wd-Log ("Hidden watchdog loop: {0}" -f $loopState)
  $logFile = Join-Path $repoRoot '.home-bridge-watchdog.log'
  if (Test-Path $logFile) {
    Wd-Log "Last 5 log lines:"
    Get-Content $logFile -Tail 5 | ForEach-Object { Wd-Log "  $_" }
  }
  exit 0
}

if ($Uninstall) {
  $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  if ($task) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Wd-Log "Uninstalled scheduled task '$taskName'."
  } else {
    Wd-Log "Scheduled task '$taskName' was not registered."
  }
  if (Test-WatchdogLoopAlive) {
    $otherPid = [int](Get-Content $pidFile -Raw)
    Stop-Process -Id $otherPid -Force -ErrorAction SilentlyContinue
    Wd-Log "Stopped live watchdog loop pid=$otherPid."
  }
  Remove-Item $lockFile,$pidFile -Force -ErrorAction SilentlyContinue
  exit 0
}

if (-not (Test-Path $watchdog)) { throw "bridge-watchdog.ps1 not found at $watchdog" }

# 1) Register / refresh the scheduled task (needs admin). Best-effort on a non-admin shell.
$taskRegistered = $false
try {
  $pwsh = Get-Command pwsh.exe -ErrorAction SilentlyContinue
  $exe  = if ($pwsh) { $pwsh.Source } else { (Get-Command powershell.exe).Source }
  $arg  = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File'
  $action = New-ScheduledTaskAction -Execute $exe -Argument "$arg `"$watchdog`" -DurationMin 5 -Quiet"
  # Every 1 min — the watchdog's inner loop covers the 10s polling; the task guarantees a
  # fresh watchdog is spawned if the previous one exited (so coverage survives logoff/reboot).
  $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
  $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest

  $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
  if ($existing) {
    Set-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
    Wd-Log "Updated scheduled task '$taskName' (every 1 min) -> $watchdog"
  } else {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description 'DoxedCrypto bridge :7810 auto-respawn watchdog. Polls /health every ~10s and relaunches the bridge if it dies.' | Out-Null
    Wd-Log "Registered scheduled task '$taskName' (every 1 min) -> $watchdog"
  }
  $taskRegistered = $true
} catch {
  Wd-Log "Scheduled task registration skipped (need admin): $($_.Exception.Message)"
}

# 2) Ensure a hidden watchdog loop is running RIGHT NOW (regardless of task registration)
#    so coverage starts immediately without waiting for the next task tick. Long duration
#    so a single launch keeps covering until the task takes over or re-runs.
if (-not (Test-WatchdogLoopAlive)) {
  Wd-Log "Launching hidden watchdog loop (24h duration)..."
  Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile","-WindowStyle","Hidden","-ExecutionPolicy","Bypass","-File",
    $watchdog,"-DurationMin","1440","-Quiet"
  ) -WorkingDirectory $repoRoot -WindowStyle Hidden | Out-Null
  Start-Sleep -Seconds 2
  if (Test-WatchdogLoopAlive) {
    $wPid = (Get-Content $pidFile -Raw -ErrorAction SilentlyContinue).Trim()
    Wd-Log "Watchdog loop RUNNING (pid=$wPid)."
  } else {
    Wd-Log "Watchdog loop may have failed to start - see .home-bridge-watchdog.log"
  }
} else {
  $wPid = (Get-Content $pidFile -Raw -ErrorAction SilentlyContinue).Trim()
  Wd-Log "Watchdog loop already RUNNING (pid=$wPid)."
}

Wd-Log ""
Wd-Log "Verify:  powershell -ExecutionPolicy Bypass -File scripts\register-bridge-watchdog.ps1 -Status"
Wd-Log "Uninstall: powershell -ExecutionPolicy Bypass -File scripts\register-bridge-watchdog.ps1 -Uninstall"
