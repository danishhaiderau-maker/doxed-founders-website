# One analyzer cycle on the segment copy: catch the shadow tree up to the Fly
# head, stage a fresh promotion view (fail-closed), migrate it into the
# canonical store of -CanonicalRoot, then run one supervised analyzer pass.
# Windows PowerShell 5.1 compatible. Never prints credentials.
#
# Exit codes: 0 analyzer pass completed; 3 promotion refused after retries;
# 4 migration failed; 5 checkout does not contain the deployed revision and the
# inline fast-forward could not fix it; otherwise run-analyzer-once.ps1's exit
# code.
param(
  [string]$RepoRoot = '',
  [string]$CanonicalRoot = '',
  [string]$StateDir = '',
  [string]$ShadowRoot = 'C:\DoxxedCrypto\fly-mirror-segments',
  [string]$ArchiveRoot = 'C:\DoxxedCrypto\fly-segments',
  [string]$ViewRoot = 'C:\DoxxedCrypto\segment-promotion-view',
  [string]$Prefix = 'v3',
  [string]$Python = 'python',
  [int]$Port = 9001,
  [int]$PromotionAttempts = 6,
  # Must exceed research_segment_fly_parity.DEFAULT_MAX_LOCK_SEC (600 s): a
  # parity scan that starts just before the cycle may hold the lock that long.
  [int]$LockWaitMaxSec = 900,
  [int]$FlyBacklogWaitMaxSec = 900,
  [int]$FlyBacklogPollSec = 60,
  [int]$SyncMaxAgeSec = 1800,
  [int]$InlineFfMaxWaitSec = 900,
  [int]$InlineFfPollSec = 30,
  [string]$GenomeGridReport = 'C:\DoxxedCrypto\analyzer-exports\genome-grid\genome_grid_report.json',
  # Indicator Edge scoreboard + coverage + per-bar labels for Grok Strategist (written by indicator_edge_cycle.py).
  [string]$IndicatorEdgeExport = 'C:\DoxxedCrypto\analyzer-exports\indicator-edge\indicator_edge_export.json',
  [int]$MirrorPublishTimeoutSec = 600,
  [string]$Reason = 'segment-cycle'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'laptop-chain-common.ps1')
$cfg = Get-LaptopChainConfig -RepoRoot $RepoRoot -CanonicalRoot $CanonicalRoot -StateDir $StateDir
$logName = 'segment-analyzer-cycle'
foreach ($path in @($ShadowRoot, $ArchiveRoot, $ViewRoot)) { Assert-NotOneDrive $path }
if ([System.IO.Path]::GetFullPath($ViewRoot).ToLowerInvariant().Contains('canonical-research-data')) {
  throw 'SEGMENT_VIEW_MUST_NOT_BE_CANONICAL_STORE'
}

# The supervisor only takes its dashboard-down path when no cycle is due, so a
# cycle that stops before the analyzer must bring a dead dashboard back itself.
function Set-CycleStatus([string]$Phase, $ExitCode = $null) {
  $script:cycleStatus.phase = $Phase
  $script:cycleStatus.updatedAt = Get-UtcNowIso
  if ($null -ne $ExitCode) { $script:cycleStatus.finishedAt = Get-UtcNowIso; $script:cycleStatus.exitCode = $ExitCode }
  Write-JsonAtomic -Path $cfg.CycleStatus -Value $script:cycleStatus
}

function Stop-Cycle([int]$Code, [string]$StopReason = '', [string]$Detail = '') {
  $script:cycleStatus.stopReason = $StopReason
  $script:cycleStatus.detail = $Detail
  Write-ChainLog -Config $cfg -Name $logName -Message ("CYCLE_STOPPED exit={0} reason={1} {2}" -f $Code, $StopReason, $Detail)
  Set-CycleStatus 'STOPPED' $Code
  $up = $false
  try { $up = [bool](Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 10 -UseBasicParsing) } catch { }
  if (-not $up) {
    & (Join-Path $PSHOME 'powershell.exe') -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'run-analyzer-once.ps1') `
      -RepoRoot $cfg.RepoRoot -CanonicalRoot $cfg.CanonicalRoot -StateDir $cfg.StateDir -Port $Port -Reason 'dashboard-down' -EnsureDashboardOnly
    Write-ChainLog -Config $cfg -Name $logName -Message "DASHBOARD_ENSURED exit=$LASTEXITCODE after cycle stop $Code"
  }
  exit $Code
}

$cycleLock = Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopSegmentAnalyzerCycle')
if (-not $cycleLock) { Write-ChainLog -Config $cfg -Name $logName -Message 'SKIP cycle already running'; exit 0 }
# The supervisor schedules from this cycle start (not the later analyzer start),
# and the pull loop defers a parity pass while the phase is PROMOTION.
$cycleStatus = [ordered]@{ schema = 'segment_analyzer_cycle_status_v1'; pid = $PID; reason = $Reason
                           startedAt = (Get-UtcNowIso); phase = 'PROMOTION'; updatedAt = $null
                           finishedAt = $null; exitCode = $null; stopReason = $null; detail = $null
                           promotionLevel = $null; promotionWarnings = @() }
Set-CycleStatus 'PROMOTION'

if (-not $env:BOT_ADMIN_TOKEN) {
  $raw = Get-Content -LiteralPath $cfg.VaultEnv -Raw
  if ($raw -notmatch '(?m)^\s*BOT_ADMIN_TOKEN\s*=\s*(.+)$') { throw 'BOT_ADMIN_TOKEN_UNAVAILABLE' }
  [Environment]::SetEnvironmentVariable('BOT_ADMIN_TOKEN', $Matches[1].Trim().Trim('"').Trim("'"), 'Process')
}

$service = Join-Path $cfg.RepoRoot 'services\btc-conservative-agent'
$pullArgs = @((Join-Path $service 'research_segment_puller.py'), '--shadow-root', $ShadowRoot, '--archive-root', $ArchiveRoot,
              '--prefix', $Prefix, '--source', 'http', '--base-url', $cfg.SourceUrl)
$promotion = $null
$lockWaitStart = $null
$backlogWaitStart = $null
$previous = $ErrorActionPreference
for ($attempt = 1; $attempt -le $PromotionAttempts; $attempt++) {
  Set-CycleStatus 'PROMOTION'
  # The view is this runner's own staging copy and is updated incrementally;
  # one without the promotion index (legacy or foreign) is cleared first.
  if ((Test-Path -LiteralPath $ViewRoot) -and -not (Test-Path -LiteralPath (Join-Path $ViewRoot '.segment-promotion.index.json'))) {
    Remove-Item -LiteralPath $ViewRoot -Recurse -Force
  }
  $ErrorActionPreference = 'Continue'
  try {
    $null = & $Python @pullArgs 2>&1 | Out-String
    $promotion = & $Python (Join-Path $service 'research_segment_promotion.py') --shadow-root $ShadowRoot --view $ViewRoot `
      --prefix $Prefix --base-url $cfg.SourceUrl 2>&1 | Out-String
  } finally { $ErrorActionPreference = $previous }
  $promotionExit = $LASTEXITCODE
  Write-ChainLog -Config $cfg -Name $logName -Message ("PROMOTION attempt={0} exit={1} {2}" -f $attempt, $promotionExit, $promotion)
  if ($promotionExit -eq 0) { break }
  # The pull loop holds the shadow-root lock for a whole parity pass (~7 min
  # every 30 min), so lock waits have their own budget and do not consume the
  # head-movement attempts.
  if ($promotion -match 'holds the shadow-root lock') {
    if ($null -eq $lockWaitStart) { $lockWaitStart = [datetime]::UtcNow }
    if (([datetime]::UtcNow - $lockWaitStart).TotalSeconds -lt $LockWaitMaxSec) {
      Start-Sleep -Seconds 20
      $attempt--
      continue
    }
    Stop-Cycle 3 'SHADOW_LOCK_WAIT_TIMEOUT' ("lock held > {0}s" -f $LockWaitMaxSec)
  }
  $denied = @([regex]::Matches([string]$promotion, '"([A-Z_]+)(:[^"]*)?"') | ForEach-Object { $_.Groups[1].Value } |
              Where-Object { $_ -match '^(SHADOW_|HEAD_|FLY_|REVISION_|TILE_|RESEARCH_|PULLER_|VIEW_)' } | Select-Object -Unique)
  $denyDetail = ([string]$promotion -replace '\s+', ' ').Trim()
  if ($denyDetail.Length -gt 400) { $denyDetail = $denyDetail.Substring(0, 400) }
  # A Fly shipper backlog after a restart drains on its own (48 -> 36 MB -> OK in
  # ~10 min on 10-02); it is the only guard worth waiting out inside the cycle.
  $onlyBacklog = ($denied.Count -gt 0) -and -not ($denied | Where-Object { $_ -notin @('FLY_UNSHIPPED_BYTES', 'SHADOW_BEHIND_PUBLISHED', 'HEAD_MANIFEST_MISMATCH') }) -and ($denied -contains 'FLY_UNSHIPPED_BYTES')
  if ($onlyBacklog) {
    if ($null -eq $backlogWaitStart) { $backlogWaitStart = [datetime]::UtcNow }
    if (([datetime]::UtcNow - $backlogWaitStart).TotalSeconds -lt $FlyBacklogWaitMaxSec) {
      Set-CycleStatus 'PROMOTION_WAIT_FLY_BACKLOG'
      Start-Sleep -Seconds $FlyBacklogPollSec
      $attempt--
      continue
    }
    Stop-Cycle 3 'FLY_BACKLOG_NOT_DRAINED' $denyDetail
  }
  # Only a moving head is worth retrying; any other refusal is final.
  if ($promotion -notmatch 'SHADOW_BEHIND_PUBLISHED|HEAD_MANIFEST_MISMATCH') { Stop-Cycle 3 ('PROMOTION_DENIED:' + ($denied -join ',')) $denyDetail }
}
if ($promotionExit -ne 0) { Stop-Cycle 3 'PROMOTION_HEAD_KEPT_MOVING' $denyDetail }
# A disclosed promotion warning (e.g. research.db over the Fly SQLite cap ships
# its last copy) degrades the cycle to AMBER but never blocks it.
$promotionHeartbeat = Read-JsonFile (Join-Path $ViewRoot '.segment-promotion.heartbeat.json')
$cycleStatus.promotionLevel = if ($promotionHeartbeat -and $promotionHeartbeat.promotionLevel) { [string]$promotionHeartbeat.promotionLevel } else { 'GREEN' }
$cycleStatus.promotionWarnings = @(if ($promotionHeartbeat) { @($promotionHeartbeat.promotionWarnings) | Where-Object { $_ } | ForEach-Object { [string]$_ } })
if ($cycleStatus.promotionWarnings.Count -gt 0) {
  Write-ChainLog -Config $cfg -Name $logName -Message ("PROMOTION_DEGRADED level={0} warnings={1}" -f $cycleStatus.promotionLevel, ($cycleStatus.promotionWarnings -join ','))
}
Set-CycleStatus 'MIGRATION'

$ErrorActionPreference = 'Continue'
try {
  $migration = & $Python (Join-Path $cfg.RepoRoot 'scripts\migrate_canonical_research_store.py') --source $ViewRoot `
    --heartbeat (Join-Path $ViewRoot '.segment-promotion.heartbeat.json') --destination $cfg.DataRoot 2>&1 | Out-String
} finally { $ErrorActionPreference = $previous }
$migrationExit = $LASTEXITCODE
Write-ChainLog -Config $cfg -Name $logName -Message ("MIGRATION exit={0} {1}" -f $migrationExit, $migration)
if ($migrationExit -ne 0) { Stop-Cycle 4 'MIGRATION_FAILED' }

# The analyzer must run code that contains the revision Fly was running when
# this data was promoted; an older checkout ran the 02:40Z flyMatch=False pass
# that crashed natively. The supervisor's auto fast-forward cannot run while
# this cycle holds its mutex, so a cycle that promoted a just-deployed revision
# fast-forwards here (same guarded rules) instead of discarding its migration.
$heartbeat = Read-JsonFile (Join-Path $ViewRoot '.segment-promotion.heartbeat.json')
$deployedRev = if ($heartbeat) { [string]$heartbeat.deployedRevision } else { '' }
function Test-CheckoutContainsDeployed {
  $deployedFull = if ($deployedRev) { Resolve-FullRevision -RepoRoot $cfg.RepoRoot -Revision $deployedRev } else { $null }
  $script:checkoutHead = ([string]((Invoke-NativeQuiet { & git -C $cfg.RepoRoot rev-parse HEAD }) | Select-Object -First 1)).Trim()
  if (-not $deployedFull) { return $false }
  Invoke-NativeQuiet { & git -C $cfg.RepoRoot merge-base --is-ancestor $deployedFull $checkoutHead } | Out-Null
  return ($LASTEXITCODE -eq 0)
}
if (-not (Test-CheckoutContainsDeployed)) {
  Write-ChainLog -Config $cfg -Name $logName -Message ("ANALYZER_REVISION_MISMATCH checkout={0} deployed={1}" -f $checkoutHead, $deployedRev)
  $autoFfDisabled = Test-Path -LiteralPath (Join-Path $cfg.StateDir 'v2c-auto-ff.disabled')
  if ($autoFfDisabled -or -not $deployedRev) { Stop-Cycle 5 'ANALYZER_REVISION_MISMATCH' ("checkout={0} deployed={1}" -f $checkoutHead, $deployedRev) }
  $ffStart = [datetime]::UtcNow
  $roots = @('-RepoRoot', $cfg.RepoRoot, '-CanonicalRoot', $cfg.CanonicalRoot, '-StateDir', $cfg.StateDir)
  $contains = $false
  while (-not $contains) {
    Set-CycleStatus 'AUTO_FF'
    & (Join-Path $PSHOME 'powershell.exe') -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'laptop-status-snapshots.ps1') @roots | Out-Null
    & (Join-Path $PSHOME 'powershell.exe') -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'v2c-auto-ff.ps1') @roots -CycleLockHeld | Out-Null
    $ffExit = $LASTEXITCODE
    $contains = Test-CheckoutContainsDeployed
    $waited = [int]([datetime]::UtcNow - $ffStart).TotalSeconds
    if ($contains) {
      Write-ChainLog -Config $cfg -Name $logName -Message ("AUTO_FF_INLINE ok exit={0} waited={1}s checkout={2} deployed={3}" -f $ffExit, $waited, $checkoutHead, $deployedRev)
      break
    }
    # 3 is a hard refusal (dirty, not a fast-forward, parity); 2 means the
    # deploy run has not concluded success yet and is worth waiting for.
    if ($ffExit -eq 3 -or $waited -ge $InlineFfMaxWaitSec) {
      Write-ChainLog -Config $cfg -Name $logName -Message ("AUTO_FF_INLINE gave_up exit={0} waited={1}s checkout={2} deployed={3}" -f $ffExit, $waited, $checkoutHead, $deployedRev)
      Stop-Cycle 5 'AUTO_FF_INLINE_GAVE_UP' ("exit={0} waited={1}s" -f $ffExit, $waited)
    }
    Start-Sleep -Seconds $InlineFfPollSec
  }
}
Set-CycleStatus 'ANALYZER'

# Promotion plus migration outlive the default 10-minute receipt SLA as the
# epoch grows; the receipt is still bound to this cycle's applied seq.
$env:ANALYZER_MIRROR_SYNC_MAX_AGE_SEC = "$SyncMaxAgeSec"
& (Join-Path $PSHOME 'powershell.exe') -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'run-analyzer-once.ps1') `
  -RepoRoot $cfg.RepoRoot -CanonicalRoot $cfg.CanonicalRoot -StateDir $cfg.StateDir -Port $Port -Reason $Reason
$analyzerExit = $LASTEXITCODE
Write-ChainLog -Config $cfg -Name $logName -Message "ANALYZER exit=$analyzerExit"
if ($analyzerExit -ne 0) {
  $runStatus = Read-JsonFile $cfg.AnalyzerStatus
  $runDetail = if ($runStatus) { [string]$runStatus.detail } else { '' }
  $knownCause = [regex]::Match($runDetail, 'Error\("([A-Z][A-Z_]+)').Groups[1].Value
  if (-not $knownCause) { $knownCause = [regex]::Match($runDetail, '[A-Z][A-Z_]{6,}(?=\W|$)').Value }
  $cycleStatus.stopReason = if ($knownCause) { "ANALYZER_EXIT_${analyzerExit}:$knownCause" } else { "ANALYZER_EXIT_$analyzerExit" }
  $cycleStatus.detail = if ($runDetail.Length -gt 400) { $runDetail.Substring(0, 400) } else { $runDetail }
}

# Fly's /analysis, /api/analyzer/summary and /api/analyzer/genome serve only an
# uploaded analyzer_mirror_bundle_v2. Publication is a derived mirror step: a
# failure is logged and receipted but never changes the cycle's exit code.
if ($analyzerExit -eq 0) {
  Set-CycleStatus 'MIRROR_PUBLISH'
  $publishArgs = @((Join-Path $cfg.RepoRoot 'scripts\publish_analyzer_mirror.py'),
                   '--report-root', (Join-Path $cfg.DataRoot 'analyzer'), '--base-url', $cfg.SourceUrl,
                   '--vault-env', $cfg.VaultEnv, '--receipt', (Join-Path $cfg.StateDir 'analyzer-mirror-publish.status.json'))
  if ($GenomeGridReport -and (Test-Path -LiteralPath $GenomeGridReport -PathType Leaf)) { $publishArgs += @('--supplemental', $GenomeGridReport) }
  if ($IndicatorEdgeExport -and (Test-Path -LiteralPath $IndicatorEdgeExport -PathType Leaf)) { $publishArgs += @('--supplemental', $IndicatorEdgeExport) }
  $ErrorActionPreference = 'Continue'
  try {
    $publishJob = Start-Job -ScriptBlock { param($Py, $PyArgs) & $Py @PyArgs 2>&1 | Out-String } -ArgumentList $Python, $publishArgs
    if (Wait-Job -Job $publishJob -Timeout $MirrorPublishTimeoutSec) {
      $publish = (Receive-Job -Job $publishJob | Out-String).Trim()
      Write-ChainLog -Config $cfg -Name $logName -Message ("MIRROR_PUBLISH {0}" -f $publish)
    } else {
      Stop-Job -Job $publishJob
      Write-ChainLog -Config $cfg -Name $logName -Message ("MIRROR_PUBLISH timeout after {0}s" -f $MirrorPublishTimeoutSec)
    }
    Remove-Job -Job $publishJob -Force
  } catch {
    Write-ChainLog -Config $cfg -Name $logName -Message ("MIRROR_PUBLISH skipped: {0}" -f $_.Exception.Message)
  } finally {
    $ErrorActionPreference = $previous
  }
}

# Custody-gated retention (120 GB cap) runs only after a successful generation,
# still inside this cycle's mutex so promotion/migration cannot race it. It
# never changes the cycle's exit code; mode comes from bot-data-retention\mode.json.
if ($analyzerExit -eq 0) {
  Set-CycleStatus 'RETENTION'
  $previousToken = $env:BOT_ADMIN_TOKEN
  $ErrorActionPreference = 'Continue'
  try {
    $env:BOT_ADMIN_TOKEN = Get-AdminToken $cfg.VaultEnv
    $retention = & $Python (Join-Path $cfg.RepoRoot 'services\btc-conservative-agent\bot_data_retention.py') `
      --data-root $cfg.DataRoot --mode auto 2>&1 | Out-String
    Write-ChainLog -Config $cfg -Name $logName -Message ("RETENTION exit={0} {1}" -f $LASTEXITCODE, $retention.Trim())
  } catch {
    Write-ChainLog -Config $cfg -Name $logName -Message ("RETENTION skipped: {0}" -f $_.Exception.Message)
  } finally {
    $env:BOT_ADMIN_TOKEN = $previousToken
    $ErrorActionPreference = $previous
  }
}
Set-CycleStatus 'DONE' $analyzerExit
exit $analyzerExit
