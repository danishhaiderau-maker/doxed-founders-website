# Long-running segment pull loop, kept to exactly one instance by the laptop
# chain supervisor (mutex <prefix>LaptopSegmentPull). Every -PullIntervalSec it
# pulls a bounded batch from the Fly volume sink into the SHADOW mirror and
# ACKs it; every -ParityIntervalMin it also runs shadow-vs-legacy parity.
# This is not the legacy ACK watcher and never touches the canonical mirror.
param(
  [string]$RepoRoot = '',
  [string]$CanonicalRoot = '',
  [string]$StateDir = '',
  [int]$PullIntervalSec = 120,
  [int]$ParityIntervalMin = 60,
  [int]$ParityMaxDeferMin = 120,
  [int]$MaxSegmentsPerPull = 40,
  [int]$CatchUpIntervalSec = 15,
  [long]$CatchUpUnshippedBytes = 8388608,
  [int]$MaxIterations = 0
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'laptop-chain-common.ps1')
$cfg = Get-LaptopChainConfig -RepoRoot $RepoRoot -CanonicalRoot $CanonicalRoot -StateDir $StateDir
$logName = 'segment-pull-loop'
$statusPath = Join-Path $cfg.StateDir 'segment-pull.status.json'
$pullScript = Join-Path $PSScriptRoot 'research-segment-pull.ps1'
$powershell = Join-Path $PSHOME 'powershell.exe'

$instance = Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopSegmentPull')
if (-not $instance) { exit 0 }
try {
  $previous = Read-JsonFile $statusPath
  $lastParity = if ($previous) { ConvertTo-UtcDate $previous.lastParityAt } else { $null }
  $iteration = 0
  Write-ChainLog -Config $cfg -Name $logName -Message "LOOP_STARTED interval=${PullIntervalSec}s parity=${ParityIntervalMin}m batch=$MaxSegmentsPerPull"
  while ($true) {
    $iteration++
    $parityDue = ($null -eq $lastParity) -or (([datetime]::UtcNow - $lastParity).TotalMinutes -ge $ParityIntervalMin)
    # A parity pass holds the shadow-root lock ~7 min and slows promotion, migration
    # and the analyzer when they overlap; run it between analyzer cycles instead.
    # Bounded: a stale cycle marker is ignored and parity is never deferred past
    # -ParityMaxDeferMin since the last pass.
    $cycle = Read-JsonFile $cfg.CycleStatus
    $parityOverdue = ($null -eq $lastParity) -or (([datetime]::UtcNow - $lastParity).TotalMinutes -ge $ParityMaxDeferMin)
    if ($parityDue -and -not $parityOverdue -and $cycle -and $null -eq $cycle.exitCode -and
        @('PROMOTION', 'MIGRATION', 'ANALYZER') -contains $cycle.phase) {
      $cycleStarted = ConvertTo-UtcDate $cycle.startedAt
      if ($cycleStarted -and ([datetime]::UtcNow - $cycleStarted).TotalMinutes -lt 75) { $parityDue = $false }
    }
    $pullArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $pullScript, '-RepoRoot', $cfg.RepoRoot,
                  '-Source', 'Http', '-BaseUrl', $cfg.SourceUrl, '-HomeBotEnv', $cfg.VaultEnv,
                  '-LegacyTree', $cfg.DataRoot, '-MaxSegments', "$MaxSegmentsPerPull")
    if ($parityDue) { $pullArgs += '-Parity' }
    $started = Get-UtcNowIso
    $output = ''
    try {
      $output = (Invoke-NativeQuiet { & $powershell @pullArgs } | Out-String).Trim()
      $code = $LASTEXITCODE
    } catch {
      $code = 1
      $output = $_.Exception.Message
    }
    $pull = $null
    foreach ($line in ($output -split "`r?`n")) {
      if ($line -match '^\{.*"ok"') { try { $pull = $line | ConvertFrom-Json } catch { } }
    }
    if ($parityDue -and (@(0, 1) -contains $code) -and $pull -and $pull.ok) { $lastParity = [datetime]::UtcNow }
    $status = [ordered]@{
      schema = 'laptop_segment_pull_status_v1'
      pid = $PID
      iteration = $iteration
      startedAt = $started
      finishedAt = Get-UtcNowIso
      exitCode = $code
      parityRan = [bool]$parityDue
      lastParityAt = $(if ($lastParity) { $lastParity.ToString('o') } else { $null })
      appliedSeq = $(if ($pull) { $pull.applied_seq } else { $null })
      ackedSeq = $(if ($pull) { $pull.acked_seq } else { $null })
      appliedNow = $(if ($pull) { $pull.applied_now } else { $null })
      remotePublishedSeq = $(if ($pull -and $pull.remote_head) { $pull.remote_head.published_seq } else { $null })
      error = $(if ($pull -and -not $pull.ok) { $pull.error } elseif (-not $pull) { ($output -split "`r?`n" | Select-Object -Last 1) } else { $null })
    }
    Write-JsonAtomic -Path $statusPath -Value $status
    Write-ChainLog -Config $cfg -Name $logName -Message ("PULL exit={0} applied={1} acked={2} now={3} remote={4} parity={5}" -f $code, $status.appliedSeq, $status.ackedSeq, $status.appliedNow, $status.remotePublishedSeq, $parityDue)
    if ($MaxIterations -gt 0 -and $iteration -ge $MaxIterations) { break }
    # Drain a large first backlog in consecutive bounded batches.
    $backlog = $pull -and $pull.ok -and $pull.applied_now -ge $MaxSegmentsPerPull
    if (-not $backlog) { Start-Sleep -Seconds (Get-SegmentPullSleepSeconds -Pull $pull -IntervalSec $PullIntervalSec -CatchUpIntervalSec $CatchUpIntervalSec -CatchUpUnshippedBytes $CatchUpUnshippedBytes) }
  }
} finally {
  Exit-SingleInstance $instance
}
exit 0
