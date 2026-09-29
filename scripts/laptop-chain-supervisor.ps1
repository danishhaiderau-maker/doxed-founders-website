# One scheduled supervisor tick (at logon and every 5 minutes). It returns
# quickly: it ensures exactly one ACK watcher, starts an analyzer pass when
# the 30-minute cadence is due (or the dashboard is down), and runs the
# monitor. Long-running work is detached and guarded by its own mutex.
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

  if (-not (Test-SingleInstanceHeld (Get-ChainMutexName 'LaptopAckWatcher'))) {
    $watcher = Start-Process -FilePath $powershell -WorkingDirectory $cfg.RepoRoot -WindowStyle Hidden -PassThru `
      -ArgumentList ($common + @((Join-Path $PSScriptRoot 'laptop-ack-watcher.ps1')) + $roots)
    Write-ChainLog -Config $cfg -Name $logName -Message "WATCHER_STARTED pid=$($watcher.Id)"
  }

  if (-not (Test-SingleInstanceHeld (Get-ChainMutexName 'LaptopAnalyzerRun'))) {
    $analyzer = Read-JsonFile $cfg.AnalyzerStatus
    $lastStart = if ($analyzer) { ConvertTo-UtcDate $analyzer.startedAt } else { $null }
    $due = ($null -eq $lastStart) -or (([datetime]::UtcNow - $lastStart).TotalMinutes -ge $AnalyzerIntervalMin)
    $dashboardUp = $false
    try { $dashboardUp = [bool](Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 10 -UseBasicParsing) } catch { }
    if ($due -or -not $dashboardUp) {
      $runnerArgs = @('-Port', "$Port", '-Reason', $(if ($due) { 'schedule' } else { 'dashboard-down' }))
      if (-not $due) { $runnerArgs += '-EnsureDashboardOnly' }
      $run = Start-Process -FilePath $powershell -WorkingDirectory $cfg.RepoRoot -WindowStyle Hidden -PassThru `
        -ArgumentList ($common + @((Join-Path $PSScriptRoot 'run-analyzer-once.ps1')) + $roots + $runnerArgs)
      Write-ChainLog -Config $cfg -Name $logName -Message ("ANALYZER_STARTED pid={0} due={1} dashboardUp={2}" -f $run.Id, $due, $dashboardUp)
    }
  }

  & $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'laptop-chain-monitor.ps1') @roots | Out-Null
  Write-ChainLog -Config $cfg -Name $logName -Message "TICK monitorExit=$LASTEXITCODE"
} catch {
  Write-ChainLog -Config $cfg -Name $logName -Message ("TICK_ERROR {0}" -f $_.Exception.Message)
  exit 1
} finally {
  Exit-SingleInstance $tick
}
exit 0
