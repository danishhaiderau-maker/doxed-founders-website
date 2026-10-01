# Fast-forward the analyzer checkout to the revision Fly is running, but only
# after that revision's guarded deploy succeeded and only while no analyzer
# cycle or pass can start (both mutexes are held for the whole step).
# Windows PowerShell 5.1 compatible. Never prints credentials.
#
# Exit codes: 0 already at the deployed revision or fast-forwarded;
# 2 skipped (evidence stale/missing, deploy in progress, analyzer busy);
# 3 refused (dirty tracked files, not a fast-forward, unknown commit, parity
# mismatch after the merge).
param(
  [string]$RepoRoot = '',
  [string]$CanonicalRoot = '',
  [string]$StateDir = '',
  [int]$SnapshotMaxAgeSec = 600
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'laptop-chain-common.ps1')
$cfg = Get-LaptopChainConfig -RepoRoot $RepoRoot -CanonicalRoot $CanonicalRoot -StateDir $StateDir
$logName = 'v2c-auto-ff'
$receipts = Join-Path $cfg.StateDir 'v2c-auto-ff.receipts.jsonl'

function Write-Receipt([string]$Outcome, [hashtable]$Fields) {
  $row = [ordered]@{ schema = 'v2c_auto_ff_receipt_v1'; at = (Get-UtcNowIso); outcome = $Outcome; repoRoot = $cfg.RepoRoot }
  foreach ($key in $Fields.Keys) { $row[$key] = $Fields[$key] }
  $line = ($row | ConvertTo-Json -Compress -Depth 4) + "`n"
  [System.IO.File]::AppendAllText($receipts, $line, (New-Object System.Text.UTF8Encoding $false))
  Write-ChainLog -Config $cfg -Name $logName -Message ("AUTO_FF {0} {1}" -f $Outcome, (($Fields.GetEnumerator() | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join ' '))
}

function Test-Fresh($Snapshot) {
  if (-not $Snapshot -or -not $Snapshot.ok) { return $false }
  $at = ConvertTo-UtcDate $Snapshot.observedAt
  return [bool]($at -and ([datetime]::UtcNow - $at).TotalSeconds -le $SnapshotMaxAgeSec)
}

$runtime = Read-JsonFile (Join-Path $cfg.StateDir 'fly_runtime_snapshot_v1.json')
$deploys = Read-JsonFile (Join-Path $cfg.StateDir 'fly_deploy_runs_snapshot_v1.json')
if (-not (Test-Fresh $runtime) -or -not (Test-Fresh $deploys) -or -not $runtime.git_rev) { exit 2 }
$flyRev = ([string]$runtime.git_rev).Trim().ToLowerInvariant()
$runs = @($deploys.runs | ForEach-Object { if ($_.PSObject.Properties['value']) { $_.value } else { $_ } } |
          Sort-Object { ConvertTo-UtcDate $_.createdAt } -Descending)
if ($runs.Count -eq 0) { exit 2 }
$latest = $runs[0]
# Never follow Fly mid-deploy, after a failed deploy, or to a revision no
# successful guarded deploy produced.
if ([string]$latest.status -ne 'completed' -or [string]$latest.conclusion -ne 'success') { exit 2 }
$target = ([string]$latest.headSha).Trim().ToLowerInvariant()
if ($target -notmatch '^[0-9a-f]{40}$' -or -not $target.StartsWith($flyRev)) { exit 2 }

$head = ((Invoke-NativeQuiet { & git -C $cfg.RepoRoot rev-parse HEAD }) | Select-Object -First 1)
$head = ([string]$head).Trim().ToLowerInvariant()
if ($head -eq $target) { exit 0 }

$cycleLock = Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopSegmentAnalyzerCycle')
if (-not $cycleLock) { exit 2 }
$runLock = $null
try {
  $runLock = Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopAnalyzerRun')
  if (-not $runLock) { exit 2 }
  $fields = @{ from = $head; to = $target; flyRev = $flyRev; deployRun = [string]$latest.databaseId }
  $dirty = @(Invoke-NativeQuiet { & git -C $cfg.RepoRoot status --porcelain --untracked-files=no } | Where-Object { $_ })
  if ($dirty.Count -gt 0) { Write-Receipt 'REFUSED_DIRTY_TRACKED' ($fields + @{ dirty = $dirty.Count }); exit 3 }
  if (-not (Resolve-FullRevision -RepoRoot $cfg.RepoRoot -Revision $target)) {
    Write-Receipt 'REFUSED_UNKNOWN_COMMIT' $fields; exit 3
  }
  # Already containing the deployed revision (for example scripts-only commits
  # on top of it) is not drift.
  Invoke-NativeQuiet { & git -C $cfg.RepoRoot merge-base --is-ancestor $target $head } | Out-Null
  if ($LASTEXITCODE -eq 0) { exit 0 }
  Invoke-NativeQuiet { & git -C $cfg.RepoRoot merge-base --is-ancestor $head $target } | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Receipt 'REFUSED_NOT_FAST_FORWARD' $fields; exit 3 }
  Invoke-NativeQuiet { & git -C $cfg.RepoRoot merge --ff-only --quiet $target } | Out-Null
  $after = ([string]((Invoke-NativeQuiet { & git -C $cfg.RepoRoot rev-parse HEAD }) | Select-Object -First 1)).Trim().ToLowerInvariant()
  if ($after -ne $target -or -not (Test-RevisionPrefixMatch $after $flyRev)) {
    Write-Receipt 'REFUSED_PARITY_AFTER_MERGE' ($fields + @{ after = $after }); exit 3
  }
  Write-Receipt 'FAST_FORWARDED' ($fields + @{ after = $after })
  exit 0
} finally {
  Exit-SingleInstance $runLock
  Exit-SingleInstance $cycleLock
}
