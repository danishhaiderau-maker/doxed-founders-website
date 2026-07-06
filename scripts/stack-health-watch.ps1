# Unified stack health watcher — single instance, 60s loop.
# Writes logs/stack_health.json via stack-monitor.ps1 and logs duplicate PID anomalies.
param(
  [int]$IntervalSec = 60,
  [int]$Hours = 48
)
$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$lockFile = Join-Path $repoRoot ".stack-health-watch.lock"
$logFile = Join-Path $repoRoot "logs\stack-health-watch.log"

try {
  $script:LockHandle = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::Create, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
  Write-Host "stack-health-watch already running ($lockFile) - exit"
  exit 0
}

function WLog([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  if (-not (Test-Path (Split-Path $logFile))) { New-Item -ItemType Directory -Path (Split-Path $logFile) -Force | Out-Null }
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
}

function Get-DuplicatePids {
  $dups = @()
  $bots = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*btc_conservative_agent*" })
  if ($bots.Count -gt 1) { $dups += "duplicate_bot_pids=$($bots.ProcessId -join ',')" }
  $analyzers = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*analyzer_research_engine*" })
  if ($analyzers.Count -gt 1) { $dups += "duplicate_analyzer_pids=$($analyzers.ProcessId -join ',')" }
  $cf = @(Get-Process cloudflared -ErrorAction SilentlyContinue)
  if ($cf.Count -gt 1) { $dups += "duplicate_cloudflared_pids=$($cf.Id -join ',')" }
  $monitors = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*bot-auto-restart*" })
  if ($monitors.Count -gt 1) { $dups += "duplicate_bot_monitors=$($monitors.ProcessId -join ',')" }
  return $dups
}

$endAt = (Get-Date).AddHours($Hours)
WLog "START pid=$PID interval=${IntervalSec}s hours=$Hours"
$monitorScript = Join-Path $scriptDir "stack-monitor.ps1"

while ((Get-Date) -lt $endAt) {
  try {
    & $monitorScript -Quiet
    $dups = Get-DuplicatePids
    if ($dups.Count -gt 0) {
      WLog ("DUPLICATE: " + ($dups -join " | "))
      $healthPath = Join-Path $repoRoot "logs\stack_health.json"
      if (Test-Path $healthPath) {
        try {
          $raw = Get-Content $healthPath -Raw | ConvertFrom-Json
          $abn = @($raw.abnormalities) + $dups
          $raw.abnormality = $true
          $raw.abnormalities = $abn
          $raw | ConvertTo-Json -Depth 5 | Set-Content -Path $healthPath -Encoding UTF8
        } catch { }
      }
    } else {
      WLog "tick OK (no duplicate PIDs)"
    }
  } catch {
    WLog "tick ERROR: $($_.Exception.Message)"
  }
  Start-Sleep -Seconds $IntervalSec
}

WLog "END after $Hours h"
try { $script:LockHandle.Close() } catch { }
Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
