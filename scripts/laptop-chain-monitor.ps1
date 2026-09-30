# Laptop research-chain monitor. Positive progress is required: a live
# process is not health. Writes the active alert set to alerts\active-alerts.json,
# appends transitions to a daily JSONL, and notifies (Windows event log, toast,
# optional webhook from DOXXED_ALERT_WEBHOOK_URL) when an alert opens or stays
# open past -RenotifyHours.
#
# Exit code: 0 no active alerts, 10 at least one active alert.
param(
  [string]$RepoRoot = '',
  [string]$CanonicalRoot = '',
  [string]$StateDir = '',
  [int]$AnalyzerMaxAgeMin = 90,
  [int]$InProgressMaxMin = 15,
  [int]$PullStaleMin = 15,
  [int]$RenotifyHours = 6,
  [switch]$NoNotify
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'laptop-chain-common.ps1')
$cfg = Get-LaptopChainConfig -RepoRoot $RepoRoot -CanonicalRoot $CanonicalRoot -StateDir $StateDir
$now = [datetime]::UtcNow
$state = Read-JsonFile $cfg.MonitorState
$previousAlerts = @{}
if ($state -and $state.alerts) {
  foreach ($alert in @($state.alerts)) { $previousAlerts[[string]$alert.code] = $alert }
}
$inProgressSince = if ($state) { ConvertTo-UtcDate $state.inProgressSince } else { $null }
$alerts = New-Object System.Collections.ArrayList

function Add-Alert([string]$Code, [string]$Severity, [string]$Detail) {
  [void]$alerts.Add([ordered]@{ code = $Code; severity = $Severity; detail = $Detail })
}

# 1. Analyzer completion freshness.
$analyzer = Read-JsonFile $cfg.AnalyzerStatus
$lastSuccess = if ($analyzer) { ConvertTo-UtcDate $analyzer.lastSuccessAt } else { $null }
if ($null -eq $lastSuccess -or ($now - $lastSuccess).TotalMinutes -gt $AnalyzerMaxAgeMin) {
  $age = if ($lastSuccess) { '{0:N0} min' -f ($now - $lastSuccess).TotalMinutes } else { 'never' }
  $last = if ($analyzer) { "last run state=$($analyzer.state) exit=$($analyzer.exitCode) detail=$($analyzer.detail)" } else { 'no analyzer run recorded' }
  Add-Alert 'ANALYZER_NO_COMPLETION' 'critical' "No completed analyzer generation for $age (limit $AnalyzerMaxAgeMin min); $last"
}

# 2. Sync heartbeat stuck in progress.
$heartbeat = Read-JsonFile $cfg.HeartbeatFile
if ($heartbeat -and $heartbeat.inProgress -eq $true) {
  if ($null -eq $inProgressSince) {
    $updated = ConvertTo-UtcDate $(if ($heartbeat.updatedAt) { $heartbeat.updatedAt } else { $heartbeat.syncedAt })
    $inProgressSince = if ($updated -and $updated -lt $now) { $updated } else { $now }
  }
  $minutes = ($now - $inProgressSince).TotalMinutes
  if ($minutes -gt $InProgressMaxMin) {
    Add-Alert 'SYNC_HEARTBEAT_IN_PROGRESS_TOO_LONG' 'critical' ("Sync heartbeat inProgress for {0:N0} min (limit {1}); phase={2} updatedAt={3} gen={4}" -f $minutes, $InProgressMaxMin, $heartbeat.phase, $heartbeat.updatedAt, $heartbeat.inventoryGenerationId)
  }
} else {
  $inProgressSince = $null
}

# 3. Segment pull loop liveness: the mutex is released by the OS when the owner dies.
$pullDisabled = Test-Path -LiteralPath (Join-Path $cfg.StateDir 'segment-pull.disabled')
$pull = Read-JsonFile (Join-Path $cfg.StateDir 'segment-pull.status.json')
if (-not $pullDisabled) {
  if (-not (Test-SingleInstanceHeld (Get-ChainMutexName 'LaptopSegmentPull'))) {
    $detail = if ($pull) { "last pid=$($pull.pid) finishedAt=$($pull.finishedAt)" } else { 'no pull status recorded' }
    Add-Alert 'SEGMENT_PULL_DEAD' 'critical' "Segment pull loop is not running; $detail"
  }
  # 4. Positive progress: a finished pull within the window that is not failing.
  $finished = if ($pull) { ConvertTo-UtcDate $pull.finishedAt } else { $null }
  if ($null -eq $finished -or ($now - $finished).TotalMinutes -gt $PullStaleMin) {
    $age = if ($finished) { '{0:N0} min' -f ($now - $finished).TotalMinutes } else { 'never' }
    Add-Alert 'SEGMENT_PULL_STALE' 'critical' "No finished segment pull for $age (limit $PullStaleMin min)"
  } elseif ([string]$pull.error -like '*holds the shadow-root lock*') {
    # The analyzer cycle's own pull holds the shadow lock; staleness above still bounds it.
  } elseif (@(0, 1) -notcontains [int]$pull.exitCode -or $pull.error) {
    Add-Alert 'SEGMENT_PULL_FAILING' 'critical' ("Segment pull exit={0} applied={1} remote={2} error={3}" -f $pull.exitCode, $pull.appliedSeq, $pull.remotePublishedSeq, $pull.error)
  }
}

$notify = New-Object System.Collections.ArrayList
$recorded = @()
foreach ($alert in $alerts) {
  $prior = $previousAlerts[[string]$alert.code]
  $openedAt = if ($prior) { [string]$prior.openedAt } else { $now.ToString('o') }
  $notifiedAt = if ($prior) { ConvertTo-UtcDate $prior.notifiedAt } else { $null }
  if ($null -eq $notifiedAt -or ($now - $notifiedAt).TotalHours -ge $RenotifyHours) {
    [void]$notify.Add($alert)
    $notifiedAt = $now
  }
  $alert['openedAt'] = $openedAt
  $alert['notifiedAt'] = $notifiedAt.ToString('o')
  $recorded += [pscustomobject]$alert
}
$resolved = @($previousAlerts.Keys | Where-Object { $code = $_; -not @($alerts | Where-Object { $_.code -eq $code }).Count })

$active = [ordered]@{
  schema = 'laptop_chain_alerts_v1'
  checkedAt = $now.ToString('o')
  ok = ($alerts.Count -eq 0)
  alerts = $recorded
}
Write-JsonAtomic -Path (Join-Path $cfg.AlertDir 'active-alerts.json') -Value $active
Write-JsonAtomic -Path $cfg.MonitorState -Value ([ordered]@{
  checkedAt = $now.ToString('o')
  inProgressSince = $(if ($inProgressSince) { $inProgressSince.ToString('o') } else { $null })
  alerts = $recorded
})
$journal = Join-Path $cfg.AlertDir ('alerts-{0}.jsonl' -f $now.ToString('yyyyMMdd'))
foreach ($alert in $notify) {
  Add-Content -LiteralPath $journal -Encoding UTF8 -Value (([ordered]@{ at = $now.ToString('o'); event = 'OPEN'; code = $alert.code; severity = $alert.severity; detail = $alert.detail }) | ConvertTo-Json -Compress)
}
foreach ($code in $resolved) {
  Add-Content -LiteralPath $journal -Encoding UTF8 -Value (([ordered]@{ at = $now.ToString('o'); event = 'RESOLVED'; code = $code }) | ConvertTo-Json -Compress)
}

if ($notify.Count -gt 0 -and -not $NoNotify) {
  $text = 'Doxxed laptop chain: ' + (($notify | ForEach-Object { "$($_.code): $($_.detail)" }) -join ' || ')
  if ($text.Length -gt 1800) { $text = $text.Substring(0, 1800) }
  try {
    if (-not [System.Diagnostics.EventLog]::SourceExists('DoxxedLaptopChain')) { throw 'source missing' }
    Write-EventLog -LogName Application -Source 'DoxxedLaptopChain' -EntryType Warning -EventId 4100 -Message $text
  } catch {
    try { Write-EventLog -LogName Application -Source 'Windows PowerShell' -EntryType Warning -EventId 4100 -Message $text } catch { }
  }
  try {
    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
    [void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $escaped = [System.Security.SecurityElement]::Escape($text.Substring(0, [Math]::Min(250, $text.Length)))
    $xml.LoadXml("<toast><visual><binding template=`"ToastGeneric`"><text>Doxxed laptop chain alert</text><text>$escaped</text></binding></visual></toast>")
    $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
  } catch { }
  if ($env:DOXXED_ALERT_WEBHOOK_URL) {
    try {
      Invoke-RestMethod -Method Post -Uri $env:DOXXED_ALERT_WEBHOOK_URL -ContentType 'application/json' -TimeoutSec 15 `
        -Body (@{ text = $text; content = $text } | ConvertTo-Json -Compress) | Out-Null
    } catch { }
  }
}
Write-ChainLog -Config $cfg -Name 'laptop-chain-monitor' -Message ("CHECK ok={0} alerts={1} notified={2}" -f ($alerts.Count -eq 0), (($alerts | ForEach-Object { $_.code }) -join ','), $notify.Count)
if ($alerts.Count -gt 0) { exit 10 }
exit 0
