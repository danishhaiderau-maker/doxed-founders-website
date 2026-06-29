# Diagnostic: find what is spawning powershell.exe repeatedly (the ~5s window flicker).
# Samples all powershell.exe + python.exe + cloudflared.exe processes every 2s for 60s,
# logs each spawn/exit with its parent PID + command line, then prints a summary of the
# most frequent spawners.
#
# Run as Admin for full command lines:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/find-powershell-spawn-loop.ps1
param([int]$SampleSec = 60, [int]$IntervalSec = 2)
$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$logsDir = Join-Path $repoRoot "logs"
if (-not (Test-Path $logsDir)) { New-Item -ItemType Directory -Path $logsDir -Force | Out-Null }
$log = Join-Path $logsDir "powershell-spawn-loop.log"

function Get-Procs {
  Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'pwsh.exe' OR Name = 'python.exe' OR Name = 'cloudflared.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object {
      [pscustomobject]@{ Pid = $_.ProcessId; Name = $_.Name; Ppid = $_.ParentProcessId; Cmd = ($_.CommandLine -replace '\s+', ' ') }
    }
}

"=== spawn-loop sample start $(Get-Date -Format o) ===" | Add-Content $log
$prev = @{}
$spawnCounts = @{}
$end = (Get-Date).AddSeconds($SampleSec)
while ((Get-Date) -lt $end) {
  $now = Get-Date -Format "HH:mm:ss.fff"
  $cur = @{}
  foreach ($p in (Get-Procs)) { $cur[$p.Pid] = $p }
  # spawns
  foreach ($k in $cur.Keys) {
    if (-not $prev.ContainsKey($k)) {
      $p = $cur[$k]
      $parent = ""
      try { $pp = Get-CimInstance Win32_Process -Filter "ProcessId = $($p.Ppid)" -ErrorAction SilentlyContinue; if ($pp) { $parent = "$($pp.Name)($($pp.ProcessId))" } } catch {}
      $line = "$now SPAWN $($p.Name) pid=$($p.Pid) parent=$parent cmd=$($p.Cmd)"
      Add-Content $log -Value $line
      if ($spawnCounts.ContainsKey($parent)) { $spawnCounts[$parent]++ } else { $spawnCounts[$parent] = 1 }
    }
  }
  # exits
  foreach ($k in $prev.Keys) {
    if (-not $cur.ContainsKey($k)) {
      $p = $prev[$k]
      Add-Content $log -Value "$now EXIT  $($p.Name) pid=$($p.Pid)"
    }
  }
  $prev = $cur
  Start-Sleep -Seconds $IntervalSec
}

"`n=== top spawners (parent -> spawn count) ===" | Add-Content $log
$spawnCounts.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 10 |
  ForEach-Object { Add-Content $log -Value ("{0,4}  {1}" -f $_.Value, $_.Key) }
"=== sample end $(Get-Date -Format o) ===" | Add-Content $log
Write-Host "Done. See $log"
Get-Content $log -Tail 20
