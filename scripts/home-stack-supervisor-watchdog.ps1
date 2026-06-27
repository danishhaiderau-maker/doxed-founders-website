# Supervisor watchdog — re-launches home-stack-supervisor.ps1 if it has died.
# Designed to run as a Windows Scheduled Task every 5 minutes so the supervisor
# survives screen-lock / sleep (the most common cause of hidden-process death).
#
# Detection: the supervisor writes a tick line to .home-stack-supervisor.log every
# 180s. If that log is missing or stale (>5 min old), the supervisor is dead and we
# relaunch it hidden. No WMI required (fast + reliable under load).
#
# Register with: scripts\register-supervisor-watchdog.ps1
param([int]$StaleSeconds = 300, [switch]$Quiet)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$log = Join-Path $repoRoot ".home-stack-supervisor.log"
$watchdogLog = Join-Path $repoRoot ".home-supervisor-watchdog.log"
$now = Get-Date

function Watchdog-Log([string]$msg) {
  $line = "{0} {1}" -f ($now.ToString("yyyy-MM-dd HH:mm:ss")), $msg
  Add-Content -Path $watchdogLog -Value $line -ErrorAction SilentlyContinue
  if (-not $Quiet) { Write-Host $line }
}

$stale = $true
if (Test-Path $log) {
  $age = ($now - (Get-Item $log).LastWriteTime).TotalSeconds
  if ($age -lt $StaleSeconds) { $stale = $false }
}

if (-not $stale) {
  exit 0
}

Watchdog-Log "supervisor log stale/missing - relaunching hidden..."
try {
  $argList = @("-NoProfile","-ExecutionPolicy","Bypass","-WindowStyle","Hidden","-File", (Join-Path $scriptDir "home-stack-supervisor.ps1"))
  $ps = Start-Process -FilePath "powershell.exe" -ArgumentList $argList -PassThru -WindowStyle Hidden
  Watchdog-Log ("relaunched supervisor PID=" + $ps.Id)
} catch {
  $msg = $_.Exception.Message
  Watchdog-Log ("relaunch FAILED: " + $msg)
}
