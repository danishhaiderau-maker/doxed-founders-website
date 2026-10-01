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

  # Read-only Fly v2 shipper head, runtime, deploy-run and relay-status
  # snapshots; never fails the tick. Refreshed before the fast-forward so a
  # deploy that finished since the last tick is followed before a cycle starts.
  $snapshots = (& $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'laptop-status-snapshots.ps1') @roots 2>&1 |
    Select-Object -Last 1) -as [string]

  # Follow Fly to a successfully deployed revision before any cycle starts, so
  # promotion and the analyzer run the code Fly runs. Opt out with
  # <StateDir>\v2c-auto-ff.disabled. Never fails the tick.
  $autoFf = 'autoFf=off'
  if (-not (Test-Path -LiteralPath (Join-Path $cfg.StateDir 'v2c-auto-ff.disabled'))) {
    try {
      & $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'v2c-auto-ff.ps1') @roots | Out-Null
      $autoFf = "autoFf=$LASTEXITCODE"
    } catch { $autoFf = 'autoFf=error' }
  }

  if (-not (Test-SingleInstanceHeld (Get-ChainMutexName 'LaptopAnalyzerRun')) -and
      -not (Test-SingleInstanceHeld (Get-ChainMutexName 'LaptopSegmentAnalyzerCycle'))) {
    # Cadence runs from the last cycle start: the analyzer itself starts 8-17 min
    # into a cycle, and timing from it spaced generations 44-52 min apart. A
    # cycle that stopped without a generation is retried on the next tick.
    $cycle = Read-JsonFile $cfg.CycleStatus
    $analyzer = Read-JsonFile $cfg.AnalyzerStatus
    $lastStart = if ($cycle) { ConvertTo-UtcDate $cycle.startedAt } elseif ($analyzer) { ConvertTo-UtcDate $analyzer.startedAt } else { $null }
    $cycleFailed = $cycle -and $null -ne $cycle.exitCode -and [int]$cycle.exitCode -ne 0
    $due = ($null -eq $lastStart) -or $cycleFailed -or (([datetime]::UtcNow - $lastStart).TotalMinutes -ge $AnalyzerIntervalMin)
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

  & $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'laptop-chain-monitor.ps1') @roots | Out-Null
  $monitorExit = $LASTEXITCODE
  # Unattended-proof row (at most every 30 min) from the snapshots and alert
  # set above; a no-op unless a proof window was started. Never fails the tick.
  $proof = 'not-run'
  try {
    $proof = (& python (Join-Path $PSScriptRoot 'unattended_proof.py') --check --state-dir $cfg.StateDir 2>&1 | Select-Object -Last 1) -as [string]
  } catch {
    $proof = "PROOF_ERROR $($_.Exception.Message)"
  }
  # Aggregated positive-progress health verdict and edge-triggered alarms
  # (toast, alarm log, webhook if configured, dashboard banners). The single
  # system-health watcher; diagnostics only. Never fails the tick.
  $health = 'not-run'
  try {
    $health = (& python (Join-Path $PSScriptRoot 'system_health.py') --tick --state-dir $cfg.StateDir --analyzer-repo $cfg.RepoRoot 2>&1 |
      Select-Object -Last 1) -as [string]
  } catch {
    $health = "HEALTH_ERROR $($_.Exception.Message)"
  }
  # One GitHub issue per incident (label laptop-chain-incident, including open
  # system-health RED alarms) plus the supervisor heartbeat the Fly monitor
  # watches. Never fails the tick.
  $incident = 'not-run'
  try {
    $incident = (& python (Join-Path $PSScriptRoot 'laptop_chain_incident.py') --state-dir $cfg.StateDir 2>&1 | Select-Object -Last 1) -as [string]
  } catch {
    $incident = "INCIDENT_ERROR $($_.Exception.Message)"
  }
  Write-ChainLog -Config $cfg -Name $logName -Message "TICK monitorExit=$monitorExit $autoFf $snapshots $incident $proof $health"
} catch {
  Write-ChainLog -Config $cfg -Name $logName -Message ("TICK_ERROR {0}" -f $_.Exception.Message)
  exit 1
} finally {
  Exit-SingleInstance $tick
}
exit 0
