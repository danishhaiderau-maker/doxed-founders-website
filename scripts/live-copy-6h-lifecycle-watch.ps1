# 6-hour Live Copy lifecycle watch — single instance, 60-90s jsonl ticks.
# Reuses: live-copy-trade-monitor.mjs, watch-live-copy-lifecycle.mjs,
#         stack-health-watch.ps1, lifecycle-sync-probe.mjs, heal-stuck-copy-participants.mjs
param(
  [int]$DurationHours = 6,
  [int]$PollSec = 75,
  [string]$WindowEndLocal = "2026-07-07T04:36:00+10:00"
)

$ErrorActionPreference = "Continue"
$repo = Split-Path $PSScriptRoot -Parent
$lockFile = Join-Path $repo ".live-copy-6h-watch.lock"
$jsonl = Join-Path $repo "logs\live-copy-6h-watch-20260706.jsonl"
$hbLog = Join-Path $repo "logs\live-copy-6h-watch-heartbeat.log"
$reportPath = Join-Path $repo "tmp\lifecycle-6h-report-20260707.md"
$monLog = Join-Path $repo "logs\live-copy-trade-monitor.log"
$lcLog = Join-Path $repo "logs\live-copy-lifecycle-watch.log"
$probeScript = Join-Path $repo "scripts\lifecycle-sync-probe.mjs"
$healScript = Join-Path $repo "scripts\heal-stuck-copy-participants.mjs"
$INSTANCE_ID = "cmq6cfwv4001jli0dqx5r31ve"

try {
  $script:LockHandle = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::Create, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
} catch {
  Write-Host "live-copy-6h-lifecycle-watch already running ($lockFile) - exit"
  exit 0
}

function WLog([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  $dir = Split-Path $hbLog
  if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
  Add-Content -Path $hbLog -Value $line -ErrorAction SilentlyContinue
  Write-Output $line
}

function Ensure-Dir([string]$p) {
  $d = Split-Path $p
  if ($d -and -not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}

function Get-ProcPid([string]$pattern) {
  $p = Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match $pattern } | Select-Object -First 1
  if ($p) { return $p.ProcessId }
  $p = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match $pattern } | Select-Object -First 1
  if ($p) { return $p.ProcessId }
  return 0
}

function Ensure-Node([string]$pattern, [string]$scriptRel, [hashtable]$envMap) {
  $foundPid = Get-ProcPid $pattern
  if ($foundPid -gt 0) { return $foundPid }
  WLog "RESTART node $scriptRel"
  foreach ($k in $envMap.Keys) { Set-Item -Path "env:$k" -Value $envMap[$k] }
  $stdout = Join-Path $repo ("logs/" + ($scriptRel -replace '[\\/]', '-') + ".stdout.log")
  $stderr = Join-Path $repo ("logs/" + ($scriptRel -replace '[\\/]', '-') + ".stderr.log")
  Start-Process -FilePath "node" -ArgumentList $scriptRel `
    -WorkingDirectory $repo `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden | Out-Null
  Start-Sleep -Seconds 4
  return (Get-ProcPid $pattern)
}

function Ensure-StackHealth {
  $foundPid = Get-ProcPid "stack-health-watch\.ps1"
  if ($foundPid -gt 0) { return $foundPid }
  WLog "RESTART stack-health-watch.ps1"
  Start-Process -FilePath "powershell" `
    -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $repo "scripts\stack-health-watch.ps1"), "-Hours", "8" `
    -WorkingDirectory $repo -WindowStyle Hidden | Out-Null
  Start-Sleep -Seconds 3
  return (Get-ProcPid "stack-health-watch\.ps1")
}

function Read-LogTail([string]$path, [int]$n = 8) {
  if (-not (Test-Path $path)) { return @() }
  try {
    return @(Get-Content $path -Tail $n -ErrorAction SilentlyContinue)
  } catch { return @() }
}

function Parse-MonitorAlerts {
  $alerts = @()
  if (-not (Test-Path $monLog)) { return $alerts }
  $patterns = @("MIRROR_DIFF", "COPY_ORDER_NO_SHOWCASE", "COPY_POSITION_NO_SHOWCASE", "ACTION_MISS", "orphan", "missExit", "missEntry", "SHOWCASE_POSITION_ABSENT", "PENDING_ENTRY leak")
  foreach ($line in (Select-String -Path $monLog -Pattern ($patterns -join "|") -ErrorAction SilentlyContinue | Select-Object -Last 12)) {
    $alerts += $line.Line
  }
  return $alerts
}

function Compute-SyncVerdict($probe) {
  if (-not $probe) { return "FAIL" }
  $showN = [int]($probe.reconcile.showcaseOpen)
  $copyN = [int]($probe.reconcile.copyOpen)
  $matched = [int]($probe.reconcile.matched)
  $lastErr = "$($probe.copy.instance.lastError)"
  $showOk = $probe.showcase.local7002.ok -eq $true
  $recent = @($probe.copy.recentEvents)
  $missEntry = @($recent | Where-Object { $_.type -eq "ACTION_MISS_ENTRY" }).Count
  $missExit = @($recent | Where-Object { $_.type -eq "ACTION_MISS_EXIT" }).Count
  $orphanDiv = @($recent | Where-Object { $_.payload -match "COPY_POSITION_NO_SHOWCASE" }).Count
  $copyNoShow = @($recent | Where-Object { $_.payload -match "COPY_ORDER_NO_SHOWCASE" }).Count

  if (-not $showOk) { return "WARN" }
  if ($missEntry -gt 2 -or $missExit -gt 0 -or $orphanDiv -gt 0) { return "FAIL" }
  if ($showN -gt 0 -and $matched -lt $showN) { return "WARN" }
  if ($copyN -gt $showN -and $orphanDiv -eq 0 -and $copyNoShow -gt 0) { return "WARN" }
  if ($lastErr -match "Insufficient Derivatives margin") { return "WARN" }
  if ($showN -eq 0 -and $copyN -eq 0) { return "PASS" }
  if ($matched -eq $showN -and $showN -gt 0) { return "PASS" }
  return "WARN"
}

function Maybe-AutoHeal($probe) {
  if (-not $probe) { return $null }
  $showIds = @()
  foreach ($p in @($probe.showcase.local7002.positions)) {
    if ($p.trade_id) { $showIds += $p.trade_id }
  }
  if ($showIds.Count -eq 0) { return $null }
  $pending = @($probe.copy.openParticipants | Where-Object { $_.status -eq "PENDING_ENTRY" })
  $openCopy = @($probe.copy.openParticipants | Where-Object { $_.status -eq "OPEN" })
  $blocked = $false
  foreach ($sid in $showIds) {
    $hasOpen = @($openCopy | Where-Object { $_.tradeId -eq $sid }).Count -gt 0
    if ($hasOpen) { continue }
    $pend = @($pending | Where-Object { $_.tradeId -eq $sid })
    if ($pend.Count -gt 0) { $blocked = $true; break }
  }
  if (-not $blocked) {
    $miss = @($probe.copy.recentEvents | Where-Object { $_.type -eq "ACTION_MISS_ENTRY" } | Select-Object -First 1)
    if ($miss -and $pending.Count -gt 0) { $blocked = $true }
  }
  if (-not $blocked) { return $null }
  WLog "AUTO-HEAL heal-stuck-copy-participants.mjs (stale PENDING vs showcase OPEN)"
  try {
    $out = & node $healScript 2>&1 | Out-String
    return $out.Trim().Substring(0, [Math]::Min(400, $out.Trim().Length))
  } catch {
    return "heal error: $($_.Exception.Message)"
  }
}

$endAt = [DateTimeOffset]::Parse($WindowEndLocal)
if ($endAt -lt [DateTimeOffset]::UtcNow) {
  $endAt = [DateTimeOffset]::UtcNow.AddHours($DurationHours)
}
Ensure-Dir $jsonl
Ensure-Dir $reportPath

WLog "START pid=$PID end=$($endAt.ToString('o')) poll=${PollSec}s jsonl=$jsonl"
$tick = 0
$events = New-Object System.Collections.Generic.List[string]
$failTicks = 0
$warnTicks = 0
$healRuns = New-Object System.Collections.Generic.List[string]

while ([DateTimeOffset]::UtcNow -lt $endAt) {
  $tick++
  $remainMs = [math]::Max(60000, [int](($endAt - [DateTimeOffset]::UtcNow).TotalMilliseconds))

  $lcPid = Ensure-Node "watch-live-copy-lifecycle\.mjs" "scripts/watch-live-copy-lifecycle.mjs" @{}
  $monPid = Ensure-Node "live-copy-trade-monitor\.mjs" "scripts/live-copy-trade-monitor.mjs" @{
    MONITOR_DURATION_MS = "$remainMs"
    MONITOR_POLL_MS = "75000"
    LIVE_COPY_INSTANCE_ID = $INSTANCE_ID
  }
  $shPid = Ensure-StackHealth

  $probe = $null
  $verdict = "FAIL"
  $healNote = $null
  try {
    if (Test-Path $probeScript) {
      $raw = & node $probeScript 2>&1 | Out-String
      $probe = $raw | ConvertFrom-Json
      $verdict = Compute-SyncVerdict $probe
      if ($verdict -eq "FAIL") { $failTicks++ } elseif ($verdict -eq "WARN") { $warnTicks++ }
      if ($verdict -ne "PASS") {
        $healNote = Maybe-AutoHeal $probe
        if ($healNote) { $healRuns.Add("[$([DateTimeOffset]::UtcNow.ToString('o'))] $healNote") | Out-Null }
      }
    }
  } catch {
    $verdict = "FAIL"
    $failTicks++
    WLog "probe ERROR $($_.Exception.Message)"
  }

  $alerts = Parse-MonitorAlerts
  $lcTail = Read-LogTail $lcLog 6

  $rec = [ordered]@{
    type = "tick"
    at = ([DateTimeOffset]::UtcNow).ToString("o")
    tick = $tick
    pids = @{ lc = $lcPid; mon = $monPid; stackHealth = $shPid; watch = $PID }
    syncVerdict = $verdict
    showcase = @{
      ok = $probe.showcase.local7002.ok
      open = @($probe.showcase.local7002.positions)
      pendingCount = $probe.showcase.local7002.pending
    }
    copy = @{
      open = @($probe.copy.openParticipants | Where-Object { $_.status -eq "OPEN" })
      pending = @($probe.copy.openParticipants | Where-Object { $_.status -eq "PENDING_ENTRY" })
      closedRecent = @($probe.copy.recentCycles | Where-Object { $_.cycleStatus -eq "CLOSED" } | Select-Object -First 5)
      lastError = $probe.copy.instance.lastError
      lastTickAt = $probe.copy.instance.lastTickAt
      reconcile = $probe.reconcile
    }
    bitfinex = @{
      exchangePositionQty = $probe.copy.instance.reconcileDeltaBtc
      note = "qty from dashboardState.reconcileDeltaBtc; open orders via copy PENDING legs"
      pendingOrders = @($probe.copy.openParticipants | Where-Object { $_.status -eq "PENDING_ENTRY" } | ForEach-Object { $_.tradeId })
    }
    monitorAlerts = $alerts
    lifecycleTail = $lcTail
    autoHeal = $healNote
  }

  ($rec | ConvertTo-Json -Compress -Depth 8) | Add-Content $jsonl
  WLog "tick#$tick verdict=$verdict showOpen=$($rec.showcase.open.Count) copyOpen=$($rec.copy.open.Count) copyPend=$($rec.copy.pending.Count) mon=$monPid lc=$lcPid"

  if ($verdict -eq "FAIL") {
    $events.Add("[$($rec.at)] FAIL tick#$tick alerts=$($alerts.Count)") | Out-Null
  }

  Start-Sleep -Seconds $PollSec
}

# Final report
Ensure-Dir $reportPath
$lines = @(
  "# Live Copy 6h Lifecycle Report",
  "",
  "Window ended: $([DateTimeOffset]::UtcNow.ToString('o'))",
  "Ticks: $tick | PASS=$($tick - $warnTicks - $failTicks) WARN=$warnTicks FAIL=$failTicks",
  "Watch PID: $PID",
  "Log: logs/live-copy-6h-watch-20260706.jsonl",
  "",
  "## Notable events",
  $(if ($events.Count -eq 0) { "- None recorded" } else { $events | ForEach-Object { "- $_" } }),
  "",
  "## Auto-heal runs",
  $(if ($healRuns.Count -eq 0) { "- None" } else { $healRuns | ForEach-Object { "- $_" } }),
  "",
  "## Final probe",
  $(try {
    $final = (& node $probeScript 2>&1 | Out-String) | ConvertFrom-Json
    "- syncVerdict=$(Compute-SyncVerdict $final)"
    "- showcaseOpen=$($final.reconcile.showcaseOpen) copyOpen=$($final.reconcile.copyOpen) matched=$($final.reconcile.matched)"
    "- lastError=$($final.copy.instance.lastError)"
  } catch { "- probe failed: $($_.Exception.Message)" })
)
$lines -join "`n" | Set-Content -Path $reportPath -Encoding UTF8
WLog "DONE report=$reportPath"
try { $script:LockHandle.Close() } catch { }
Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
