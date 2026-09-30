# One scheduled supervisor tick (at logon and every 5 minutes). It returns
# quickly: it ensures exactly one segment pull loop, starts a segment analyzer
# cycle (pull, promote, migrate, analyze) when the 30-minute cadence is due,
# restarts the dashboard if it is down, and runs the monitor. Long-running
# work is detached and guarded by its own mutex.
param(
  [string]$RepoRoot = '',
  [string]$CanonicalRoot = '',
  [string]$StateDir = '',
  [int]$AnalyzerIntervalMin = 30,
  [int]$Port = 9001
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'laptop-chain-common.ps1')
$cfg = Get-LaptopChainConfig -RepoRoot $RepoRoot -CanonicalRoot $CanonicalRoot -StateDir $StateDir
$logName = 'laptop-chain-supervisor'
$powershell = Join-Path $PSHOME 'powershell.exe'

$tick = Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopChainSupervisor')
if (-not $tick) { exit 0 }
try {
  $common = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File')
  $roots = @('-RepoRoot', $cfg.RepoRoot, '-CanonicalRoot', $cfg.CanonicalRoot, '-StateDir', $cfg.StateDir)

  # Segment shadow pull loop (Fly volume sink): pull every 2 min, parity every
  # 30 min. Opt out with <StateDir>\segment-pull.disabled.
  $segmentPullDisabled = Test-Path -LiteralPath (Join-Path $cfg.StateDir 'segment-pull.disabled')
  if (-not $segmentPullDisabled -and -not (Test-SingleInstanceHeld (Get-ChainMutexName 'LaptopSegmentPull'))) {
    $segmentLoop = Start-Process -FilePath $powershell -WorkingDirectory $cfg.RepoRoot -WindowStyle Hidden -PassThru `
      -ArgumentList ($common + @((Join-Path $PSScriptRoot 'research-segment-pull-loop.ps1')) + $roots)
    Write-ChainLog -Config $cfg -Name $logName -Message "SEGMENT_PULL_STARTED pid=$($segmentLoop.Id)"
  }

  if (-not (Test-SingleInstanceHeld (Get-ChainMutexName 'LaptopAnalyzerRun')) -and
      -not (Test-SingleInstanceHeld (Get-ChainMutexName 'LaptopSegmentAnalyzerCycle'))) {
    $analyzer = Read-JsonFile $cfg.AnalyzerStatus
    $lastStart = if ($analyzer) { ConvertTo-UtcDate $analyzer.startedAt } else { $null }
    $due = ($null -eq $lastStart) -or (([datetime]::UtcNow - $lastStart).TotalMinutes -ge $AnalyzerIntervalMin)
    $dashboardUp = $false
    try { $dashboardUp = [bool](Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 10 -UseBasicParsing) } catch { }
    if ($due -or -not $dashboardUp) {
      $runner = if ($due) { 'run-segment-analyzer-cycle.ps1' } else { 'run-analyzer-once.ps1' }
      $runnerArgs = @('-Port', "$Port", '-Reason', $(if ($due) { 'schedule' } else { 'dashboard-down' }))
      if (-not $due) { $runnerArgs += '-EnsureDashboardOnly' }
      $run = Start-Process -FilePath $powershell -WorkingDirectory $cfg.RepoRoot -WindowStyle Hidden -PassThru `
        -ArgumentList ($common + @((Join-Path $PSScriptRoot $runner)) + $roots + $runnerArgs)
      Write-ChainLog -Config $cfg -Name $logName -Message ("ANALYZER_STARTED pid={0} due={1} dashboardUp={2}" -f $run.Id, $due, $dashboardUp)
    }
  }

  # Read-only Fly v2 shipper head and relay-status snapshots; never fails the tick.
  $snapshots = (& $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'laptop-status-snapshots.ps1') @roots 2>&1 |
    Select-Object -Last 1) -as [string]
  & $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'laptop-chain-monitor.ps1') @roots | Out-Null
  $monitorExit = $LASTEXITCODE
  # One GitHub issue per incident (label laptop-chain-incident) plus the
  # supervisor heartbeat the Fly monitor watches. Never fails the tick.
  $incident = 'not-run'
  try {
    $incident = (& python (Join-Path $PSScriptRoot 'laptop_chain_incident.py') --state-dir $cfg.StateDir 2>&1 | Select-Object -Last 1) -as [string]
  } catch {
    $incident = "INCIDENT_ERROR $($_.Exception.Message)"
  }
  Write-ChainLog -Config $cfg -Name $logName -Message "TICK monitorExit=$monitorExit $snapshots $incident"
} catch {
  Write-ChainLog -Config $cfg -Name $logName -Message ("TICK_ERROR {0}" -f $_.Exception.Message)
  exit 1
} finally {
  Exit-SingleInstance $tick
}
exit 0
