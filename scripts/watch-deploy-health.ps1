# scripts/watch-deploy-health.ps1
#
# Continuous post-deploy health watcher. Runs verify-stack-deployed.mjs on a
# loop, writes a JSONL line per tick to logs/deploy_health.jsonl, and emits a
# prominent RED_ALERT when any surface has been continuously RED for more than
# one hour (4 consecutive ticks at the default 15min interval).
#
# Single-instance enforced via an exclusive file lock (same pattern as
# stack-health-watch.ps1).
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/watch-deploy-health.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/watch-deploy-health.ps1 -IntervalSec 300 -Hours 24
#
# npm run watch:deploy-health

param(
  [int]$IntervalSec = 900,   # 15 min
  [int]$Hours = 48,          # 2 days
  [int]$RedAlertTicks = 4    # 4 consecutive red ticks (~1h at default interval) => RED_ALERT
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$lockFile = Join-Path $repoRoot ".deploy-health-watch.lock"
$logFile = Join-Path $repoRoot "logs\deploy-health-watch.log"
$jsonlFile = Join-Path $repoRoot "logs\deploy_health.jsonl"
$verifyScript = Join-Path $scriptDir "verify-stack-deployed.mjs"

# ─── Single-instance lock ─────────────────────────────────────────────────
try {
  $script:LockHandle = [System.IO.File]::Open(
    $lockFile,
    [System.IO.FileMode]::Create,
    [System.IO.FileAccess]::ReadWrite,
    [System.IO.FileShare]::None
  )
} catch {
  Write-Host "watch-deploy-health already running ($lockFile) - exit"
  exit 0
}

# ─── Logging helpers ──────────────────────────────────────────────────────
function WLog([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  $dir = Split-Path $logFile
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
}

function Write-Jsonl([string]$json) {
  $dir = Split-Path $jsonlFile
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  Add-Content -Path $jsonlFile -Value $json -ErrorAction SilentlyContinue
}

# Track consecutive-red counts per surface so we only alert on sustained outages.
$consecutiveRed = @{}

# ─── Main loop ────────────────────────────────────────────────────────────
$endAt = (Get-Date).AddHours($Hours)
WLog "START pid=$PID interval=${IntervalSec}s hours=$Hours redAlertTicks=$RedAlertTicks"
Write-Host "watch-deploy-health: every ${IntervalSec}s for ${Hours}h. Logs: $logFile"

while ((Get-Date) -lt $endAt) {
  $tickStart = Get-Date
  try {
    # Run the verifier in JSON mode and capture stdout. Using & with the
    # script path as a single variable correctly handles paths containing
    # spaces (e.g. "Final Bots"); Start-Process -ArgumentList would split on
    # the space and node couldn't find the module.
    $raw = & node $verifyScript --json 2>$null | Out-String
    $exitCode = $LASTEXITCODE

    $payload = $null
    if ($raw) {
      try { $payload = $raw | ConvertFrom-Json } catch { WLog "tick parse error: $($_.Exception.Message)" }
    }

    if ($null -eq $payload) {
      WLog "tick ERROR: verifier produced no JSON (exit=$exitCode)"
      $tickRecord = [pscustomobject]@{
        ts             = ($tickStart | Get-Date -Format "o")
        ok             = $false
        error          = "no JSON output (exit=$exitCode)"
        overall        = "UNKNOWN"
        surfaces       = @()
      }
      Write-Jsonl ($tickRecord | ConvertTo-Json -Compress -Depth 6)
    } else {
      # Build a compact per-surface status map.
      $surfaceStatus = @{}
      $redSurfaces = @()
      foreach ($s in $payload.surfaces) {
        $surfaceStatus[$s.surface] = $s.status
        if ($s.status -eq "RED") { $redSurfaces += $s.surface }
      }

      # Update consecutive-red counters.
      foreach ($surf in @("GITHUB", "RAILWAY", "VERCEL", "NEON")) {
        if ($surfaceStatus[$surf] -eq "RED") {
          if ($consecutiveRed.ContainsKey($surf)) {
            $consecutiveRed[$surf] = [int]$consecutiveRed[$surf] + 1
          } else {
            $consecutiveRed[$surf] = 1
          }
        } else {
          $consecutiveRed[$surf] = 0
        }
      }

      $overall = $payload.overall
      $ts = $tickStart | Get-Date -Format "o"
      $tickRecord = [pscustomobject]@{
        ts       = $ts
        ok       = ($overall -ne "FAIL")
        overall  = $overall
        exitCode = $exitCode
        surfaces = $payload.surfaces
      }
      Write-Jsonl ($tickRecord | ConvertTo-Json -Compress -Depth 6)

      # Logging
      $summary = ($payload.surfaces | ForEach-Object { "$($_.surface)=$($_.status)" }) -join " "
      WLog "tick $overall ($summary)"

      # RED_ALERT: any surface continuously red for >= threshold ticks.
      $alertSurfaces = @()
      foreach ($kv in $consecutiveRed.GetEnumerator()) {
        if ([int]$kv.Value -ge $RedAlertTicks) { $alertSurfaces += "$($kv.Key)($($kv.Value)ticks)" }
      }
      if ($alertSurfaces.Count -gt 0) {
        $alertMsg = "RED_ALERT: sustained outage - $($alertSurfaces -join ', ') - has been RED for >= $RedAlertTicks ticks ($([math]::Round($RedAlertTicks * $IntervalSec / 3600.0, 1))h). Fix before declaring any task done."
        WLog $alertMsg
        Write-Host ""
        Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Red
        Write-Host "  $alertMsg" -ForegroundColor Red
        Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Red
        # Also drop an alert line into the jsonl so it's queryable.
        $alertRecord = [pscustomobject]@{
          ts      = $ts
          event   = "RED_ALERT"
          message = $alertMsg
          surfaces = ($alertSurfaces -join ", ")
        }
        Write-Jsonl ($alertRecord | ConvertTo-Json -Compress -Depth 4)
      }
    }
  } catch {
    WLog "tick EXCEPTION: $($_.Exception.Message)"
  }

  Start-Sleep -Seconds $IntervalSec
}

WLog "END after $Hours h"
try { $script:LockHandle.Close() } catch { }
Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
