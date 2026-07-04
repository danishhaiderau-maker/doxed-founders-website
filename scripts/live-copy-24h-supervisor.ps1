# 24h Live Copy monitor supervisor: keep watchers alive, hourly jsonl summaries.
# Does not blunt-sync bot, does not enable Bitfinex Live, does not use R2 keys.
param(
  [string]$WindowStartUtc = "2026-07-04T08:28:12Z",
  [int]$DurationHours = 24,
  [int]$PollSec = 90
)

$ErrorActionPreference = "Continue"
$repo = Split-Path $PSScriptRoot -Parent
if (-not (Test-Path (Join-Path $repo "scripts\live-copy-trade-monitor.mjs"))) {
  $repo = "C:\Users\user\Desktop\Final Bots\doxedcryptofounder"
}

$windowStart = [DateTimeOffset]::Parse($WindowStartUtc)
$windowEnd = $windowStart.AddHours($DurationHours)
$summaryPath = Join-Path $repo "logs\live-copy-24h-monitor-summary.jsonl"
$hbPath = Join-Path $repo "logs\live-copy-24h-supervisor-heartbeat.log"
$monLog = Join-Path $repo "logs\live-copy-trade-monitor.log"
$n = 0
$lastHourly = [DateTimeOffset]::UtcNow.AddHours(-2)

function Count-Arr($obj, $name) {
  if ($null -eq $obj) { return 0 }
  $prop = $obj.PSObject.Properties[$name]
  if ($null -eq $prop -or $null -eq $prop.Value) { return 0 }
  return @($prop.Value).Count
}

function Get-SnapCounts($snap) {
  if (-not $snap) {
    return @{
      polls = $null; entries = $null; exits = $null; missEntries = 0; missExits = 0
      orphansSeen = 0; forceCloses = 0; alerts = $null; absentFired = $null; bfxLiveOn = $null; lastStatus = $null
    }
  }
  $entries = if ($null -ne $snap.entryCount) { $snap.entryCount } else { Count-Arr $snap "entries" }
  $exits = if ($null -ne $snap.exitCount) { $snap.exitCount } else { Count-Arr $snap "exits" }
  $alerts = if ($null -ne $snap.alertCount) { $snap.alertCount } else { Count-Arr $snap "alerts" }
  return @{
    polls = $snap.polls
    entries = $entries
    exits = $exits
    missEntries = Count-Arr $snap "missEntries"
    missExits = Count-Arr $snap "missExits"
    orphansSeen = Count-Arr $snap "orphansSeen"
    forceCloses = Count-Arr $snap "forceCloses"
    alerts = $alerts
    absentFired = $snap.showcasePositionAbsentFired
    bfxLiveOn = $snap.bitfinexLiveOn
    lastStatus = $snap.lastStatus
  }
}

function Write-HourlySummary($n, $monPid, $lcPid) {
  $snap = $null
  $sumPath = Join-Path $repo "logs\live-copy-trade-monitor-summary.json"
  if (Test-Path $sumPath) {
    try { $snap = Get-Content $sumPath -Raw | ConvertFrom-Json } catch {}
  }
  $c = Get-SnapCounts $snap
  $lastPoll = $c.lastStatus
  if (-not $lastPoll -and (Test-Path $monLog)) {
    $lastPoll = (Select-String -Path $monLog -Pattern "poll#\d+" | Select-Object -Last 1).Line
  }
  $critical = @()
  if (Test-Path $monLog) {
    $critical = @(Select-String -Path $monLog -Pattern "ORPHAN|FORCE CLOSED|ACTION_MISS|SHOWCASE_POSITION_ABSENT|bot unreachable|Bitfinex Live ON|tick stalled|lastError|PENDING_ENTRY leak" |
      Select-Object -Last 12 | ForEach-Object { $_.Line })
  }
  $rec = [ordered]@{
    type = "hourly_status"
    at = ([DateTimeOffset]::UtcNow).ToString("o")
    heartbeat = $n
    elapsedHours = [math]::Round((([DateTimeOffset]::UtcNow) - $windowStart).TotalHours, 2)
    remainingHours = [math]::Round(($windowEnd - [DateTimeOffset]::UtcNow).TotalHours, 2)
    monPid = $monPid
    lcPid = $lcPid
    polls = $c.polls
    entries = $c.entries
    exits = $c.exits
    missEntries = $c.missEntries
    missExits = $c.missExits
    orphansSeen = $c.orphansSeen
    forceCloses = $c.forceCloses
    alerts = $c.alerts
    absentFired = $c.absentFired
    bfxLiveOn = $c.bfxLiveOn
    lastStatus = $lastPoll
    recentCritical = $critical
  }
  ($rec | ConvertTo-Json -Compress -Depth 6) | Add-Content $summaryPath
  $line = "HOURLY#$n elapsed=$($rec.elapsedHours)h mon=$monPid lc=$lcPid polls=$($rec.polls) entries=$($rec.entries) exits=$($rec.exits) missE=$($rec.missEntries) missX=$($rec.missExits) orphan=$($rec.orphansSeen) force=$($rec.forceCloses) alerts=$($rec.alerts) bfx=$($rec.bfxLiveOn)"
  Add-Content $hbPath $line
  Write-Output $line
}

function Ensure-Process($pattern, $scriptRel, $stdoutRel, $stderrRel, $envMap) {
  $proc = Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -match $pattern } | Select-Object -First 1
  if ($proc) { return $proc }
  $msg = "[$([DateTimeOffset]::UtcNow.ToString('o'))] SUPERVISOR RESTART $scriptRel"
  Add-Content $monLog $msg
  Add-Content $hbPath $msg
  Write-Output $msg
  foreach ($k in $envMap.Keys) { Set-Item -Path "env:$k" -Value $envMap[$k] }
  Start-Process -FilePath "node" -ArgumentList $scriptRel `
    -WorkingDirectory $repo `
    -RedirectStandardOutput (Join-Path $repo $stdoutRel) `
    -RedirectStandardError (Join-Path $repo $stderrRel) `
    -WindowStyle Hidden
  Start-Sleep -Seconds 4
  return (Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object { $_.CommandLine -match $pattern } | Select-Object -First 1)
}

Write-Output "SUPERVISOR START windowEnd=$($windowEnd.ToString('o'))"
Add-Content $hbPath "[$([DateTimeOffset]::UtcNow.ToString('o'))] SUPERVISOR START 24h (script)"

while ([DateTimeOffset]::UtcNow -lt $windowEnd) {
  Start-Sleep -Seconds $PollSec
  $n++

  $lc = Ensure-Process 'watch-live-copy-lifecycle\.mjs' "scripts/watch-live-copy-lifecycle.mjs" `
    "logs/live-copy-lifecycle-watch.stdout.log" "logs/live-copy-lifecycle-watch.stderr.log" @{}

  $remainMs = [math]::Max(60000, [int](($windowEnd - [DateTimeOffset]::UtcNow).TotalMilliseconds))
  $mon = Ensure-Process 'live-copy-trade-monitor\.mjs' "scripts/live-copy-trade-monitor.mjs" `
    "logs/live-copy-trade-monitor.stdout.log" "logs/live-copy-trade-monitor.stderr.log" @{
      MONITOR_DURATION_MS = "$remainMs"
      MONITOR_POLL_MS = "75000"
      FORCE_ORPHAN_CLOSE = "1"
      ORPHAN_FORCE_SEC = "180"
      LIVE_COPY_INSTANCE_ID = "cmq6cfwv4001jli0dqx5r31ve"
    }

  try {
    $st = Invoke-RestMethod -Uri "http://127.0.0.1:7002/api/state" -TimeoutSec 5
    $bfx = $st.bitfinex_live_enabled
    if ($null -eq $bfx) { $bfx = $st.bitfinexLiveEnabled }
    if ($bfx -eq $true -or "$bfx" -eq "true" -or "$bfx" -eq "1") {
      $crit = "[$([DateTimeOffset]::UtcNow.ToString('o'))] CRITICAL Bitfinex Live ON on :7002 - money path violation"
      Add-Content $monLog $crit
      Add-Content $hbPath $crit
      Write-Output $crit
      $rec = [ordered]@{ type = "critical"; at = ([DateTimeOffset]::UtcNow).ToString("o"); msg = "Bitfinex Live ON on showcase :7002" }
      ($rec | ConvertTo-Json -Compress) | Add-Content $summaryPath
    }
  } catch {}

  $monPid = if ($mon) { $mon.ProcessId } else { 0 }
  $lcPid = if ($lc) { $lc.ProcessId } else { 0 }

  if ($n % 10 -eq 0) {
    $hb = "HB#$n mon=$monPid lc=$lcPid $(Get-Date -Format o)"
    Add-Content $hbPath $hb
    Write-Output $hb
  }

  if (([DateTimeOffset]::UtcNow - $lastHourly).TotalHours -ge 1.0) {
    Write-HourlySummary $n $monPid $lcPid
    $lastHourly = [DateTimeOffset]::UtcNow
  }
}

Write-HourlySummary $n $(if ($mon) { $mon.ProcessId } else { 0 }) $(if ($lc) { $lc.ProcessId } else { 0 })
$c = Get-SnapCounts $(if (Test-Path (Join-Path $repo "logs\live-copy-trade-monitor-summary.json")) {
  Get-Content (Join-Path $repo "logs\live-copy-trade-monitor-summary.json") -Raw | ConvertFrom-Json
} else { $null })
$final = [ordered]@{
  type = "window_end"
  at = ([DateTimeOffset]::UtcNow).ToString("o")
  heartbeats = $n
  polls = $c.polls
  entries = $c.entries
  exits = $c.exits
  missEntries = $c.missEntries
  missExits = $c.missExits
  orphansSeen = $c.orphansSeen
  forceCloses = $c.forceCloses
  alerts = $c.alerts
  bfxLiveOn = $c.bfxLiveOn
}
($final | ConvertTo-Json -Compress -Depth 6) | Add-Content $summaryPath
Write-Output "SUPERVISOR DONE $($final | ConvertTo-Json -Compress)"
Add-Content $hbPath "[$([DateTimeOffset]::UtcNow.ToString('o'))] SUPERVISOR DONE"
