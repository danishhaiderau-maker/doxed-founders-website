# Fast-forward the analyzer checkout to the revision Fly is running, but only
# after that revision's guarded deploy succeeded and only while no analyzer
# cycle or pass can start (both mutexes are held for the whole step).
# Windows PowerShell 5.1 compatible. Never prints credentials.
#
# -CycleLockHeld is only for run-segment-analyzer-cycle.ps1, which already
# holds the cycle mutex between migration and the analyzer.
#
# Exit codes: 0 already at the deployed revision or fast-forwarded;
# 2 skipped (evidence stale/missing, deploy in progress, analyzer busy);
# 3 refused (dirty tracked files, not a fast-forward, unknown commit, parity
# mismatch after the merge).
param(
  [string]$RepoRoot = '',
  [string]$CanonicalRoot = '',
  [string]$StateDir = '',
  [int]$SnapshotMaxAgeSec = 600,
  [switch]$CycleLockHeld
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

# Laptop-only follow: [skip ci] merges never trigger a deploy, so without this
# the analyzer would run stale laptop code until the next Fly deploy (48 h in a
# freeze). Only commits on top of the deployed revision whose subjects all carry
# [skip ci] and that touch no Fly runtime path are followed.
$laptopFollowDeny = @(
  '^services/btc-conservative-agent/bot\.py$',
  '^services/btc-conservative-agent/combo_pathway_config\.py$',
  '^services/btc-conservative-agent/Dockerfile',
  '^services/btc-conservative-agent/fly\.toml$',
  '^services/btc-conservative-agent/requirements[^/]*\.txt$',
  '^services/btc-signal-engine/',
  '^fly\.toml$',
  '^Dockerfile',
  '^\.github/'
)
$laptopFollowStamp = Join-Path $cfg.StateDir 'v2c-laptop-follow.fetch.txt'

function Get-LaptopFollowTarget([string]$From, [string]$Deployed) {
  if ($env:DOXXED_V2C_LAPTOP_FOLLOW -eq '0') { return $null }
  $last = $null
  if (Test-Path -LiteralPath $laptopFollowStamp) { $last = ConvertTo-UtcDate ((Get-Content -LiteralPath $laptopFollowStamp -Raw).Trim()) }
  $fetchEverySec = 300
  if ($env:DOXXED_V2C_LAPTOP_FOLLOW_FETCH_SEC -match '^\d+$') { $fetchEverySec = [int]$env:DOXXED_V2C_LAPTOP_FOLLOW_FETCH_SEC }
  if (-not $last -or ([datetime]::UtcNow - $last).TotalSeconds -ge $fetchEverySec) {
    Invoke-NativeQuiet { & git -C $cfg.RepoRoot fetch --quiet origin master } | Out-Null
    Set-Content -LiteralPath $laptopFollowStamp -Value (Get-UtcNowIso) -NoNewline -Encoding ASCII
  }
  $master = ([string]((Invoke-NativeQuiet { & git -C $cfg.RepoRoot rev-parse --verify --quiet 'origin/master^{commit}' }) | Select-Object -First 1)).Trim().ToLowerInvariant()
  if ($master -notmatch '^[0-9a-f]{40}$' -or $master -eq $From) { return $null }
  Invoke-NativeQuiet { & git -C $cfg.RepoRoot merge-base --is-ancestor $Deployed $master } | Out-Null
  if ($LASTEXITCODE -ne 0) { return $null }
  Invoke-NativeQuiet { & git -C $cfg.RepoRoot merge-base --is-ancestor $From $master } | Out-Null
  if ($LASTEXITCODE -ne 0) { return $null }
  $subjects = @(Invoke-NativeQuiet { & git -C $cfg.RepoRoot log --format=%s "$Deployed..$master" } | Where-Object { $_ })
  $notSkip = @($subjects | Where-Object { $_ -notmatch '\[skip ci\]' })
  $paths = @(Invoke-NativeQuiet { & git -C $cfg.RepoRoot diff --name-only $Deployed $master } | Where-Object { $_ })
  $denied = @($paths | Where-Object { $p = $_; @($laptopFollowDeny | Where-Object { $p -match $_ }).Count -gt 0 })
  return [pscustomobject]@{ Master = $master; Commits = $subjects.Count; NotSkip = $notSkip.Count; Denied = $denied }
}

$laptopFollow = $null
Invoke-NativeQuiet { & git -C $cfg.RepoRoot merge-base --is-ancestor $target $head } | Out-Null
$containsDeployed = ($LASTEXITCODE -eq 0)
if ($containsDeployed) {
  $laptopFollow = Get-LaptopFollowTarget -From $head -Deployed $target
  if (-not $laptopFollow) { exit 0 }
  if ($laptopFollow.NotSkip -gt 0 -or $laptopFollow.Denied.Count -gt 0) {
    $skippedFile = Join-Path $cfg.StateDir 'v2c-laptop-follow.skipped.txt'
    $lastSkipped = if (Test-Path -LiteralPath $skippedFile) { (Get-Content -LiteralPath $skippedFile -Raw).Trim() } else { '' }
    if ($lastSkipped -ne $laptopFollow.Master) {
      Write-Receipt 'SKIPPED_LAPTOP_FOLLOW' @{ from = $head; to = $laptopFollow.Master; flyRev = $flyRev; mode = 'laptop_only'
                                               commits = $laptopFollow.Commits; notSkipCi = $laptopFollow.NotSkip
                                               denied = ($laptopFollow.Denied -join ',') }
      Set-Content -LiteralPath $skippedFile -Value $laptopFollow.Master -NoNewline -Encoding ASCII
    }
    exit 0
  }
}

$cycleLock = $null
if (-not $CycleLockHeld) {
  $cycleLock = Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopSegmentAnalyzerCycle')
  if (-not $cycleLock) { exit 2 }
}
$runLock = $null
try {
  $runLock = Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopAnalyzerRun')
  if (-not $runLock) { exit 2 }
  $fields = @{ from = $head; to = $target; flyRev = $flyRev; deployRun = [string]$latest.databaseId
               caller = $(if ($CycleLockHeld) { 'cycle' } else { 'supervisor' }) }
  $dirty = @(Invoke-NativeQuiet { & git -C $cfg.RepoRoot status --porcelain --untracked-files=no } | Where-Object { $_ })
  if ($dirty.Count -gt 0) { Write-Receipt 'REFUSED_DIRTY_TRACKED' ($fields + @{ dirty = $dirty.Count }); exit 3 }
  if (-not (Resolve-FullRevision -RepoRoot $cfg.RepoRoot -Revision $target)) {
    Write-Receipt 'REFUSED_UNKNOWN_COMMIT' $fields; exit 3
  }
  # Already containing the deployed revision (for example scripts-only commits
  # on top of it) is not drift.
  Invoke-NativeQuiet { & git -C $cfg.RepoRoot merge-base --is-ancestor $target $head } | Out-Null
  if ($LASTEXITCODE -eq 0) {
    if (-not $laptopFollow) { exit 0 }
    $followFields = @{} + $fields
    $followFields['to'] = $laptopFollow.Master
    $followFields['mode'] = 'laptop_only'
    $followFields['commits'] = $laptopFollow.Commits
    Invoke-NativeQuiet { & git -C $cfg.RepoRoot merge --ff-only --quiet $laptopFollow.Master } | Out-Null
    $after = ([string]((Invoke-NativeQuiet { & git -C $cfg.RepoRoot rev-parse HEAD }) | Select-Object -First 1)).Trim().ToLowerInvariant()
    if ($after -ne $laptopFollow.Master) { Write-Receipt 'REFUSED_LAPTOP_FOLLOW_MERGE' ($followFields + @{ after = $after }); exit 3 }
    Write-Receipt 'FAST_FORWARDED_LAPTOP_ONLY' ($followFields + @{ after = $after })
    exit 0
  }
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
