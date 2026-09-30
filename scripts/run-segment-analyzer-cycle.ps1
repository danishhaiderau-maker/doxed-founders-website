# One analyzer cycle on the segment copy: catch the shadow tree up to the Fly
# head, stage a fresh promotion view (fail-closed), migrate it into the
# canonical store of -CanonicalRoot, then run one supervised analyzer pass.
# Windows PowerShell 5.1 compatible. Never prints credentials.
#
# Exit codes: 0 analyzer pass completed; 3 promotion refused after retries;
# 4 migration failed; otherwise run-analyzer-once.ps1's exit code.
param(
  [string]$RepoRoot = '',
  [string]$CanonicalRoot = '',
  [string]$StateDir = '',
  [string]$ShadowRoot = 'C:\DoxxedCrypto\fly-mirror-segments',
  [string]$ArchiveRoot = 'C:\DoxxedCrypto\fly-segments',
  [string]$ViewRoot = 'C:\DoxxedCrypto\segment-promotion-view',
  [string]$Prefix = 'v2',
  [string]$Python = 'python',
  [int]$Port = 9001,
  [int]$PromotionAttempts = 6,
  [int]$SyncMaxAgeSec = 1800,
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

$cycleLock = Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopSegmentAnalyzerCycle')
if (-not $cycleLock) { Write-ChainLog -Config $cfg -Name $logName -Message 'SKIP cycle already running'; exit 0 }

if (-not $env:BOT_ADMIN_TOKEN) {
  $raw = Get-Content -LiteralPath $cfg.VaultEnv -Raw
  if ($raw -notmatch '(?m)^\s*BOT_ADMIN_TOKEN\s*=\s*(.+)$') { throw 'BOT_ADMIN_TOKEN_UNAVAILABLE' }
  [Environment]::SetEnvironmentVariable('BOT_ADMIN_TOKEN', $Matches[1].Trim().Trim('"').Trim("'"), 'Process')
}

$service = Join-Path $cfg.RepoRoot 'services\btc-conservative-agent'
$pullArgs = @((Join-Path $service 'research_segment_puller.py'), '--shadow-root', $ShadowRoot, '--archive-root', $ArchiveRoot,
              '--prefix', $Prefix, '--source', 'http', '--base-url', $cfg.SourceUrl)
$promotion = $null
$previous = $ErrorActionPreference
for ($attempt = 1; $attempt -le $PromotionAttempts; $attempt++) {
  # The view is this runner's own staging copy; each cycle starts empty.
  if (Test-Path -LiteralPath $ViewRoot) { Remove-Item -LiteralPath $ViewRoot -Recurse -Force }
  $ErrorActionPreference = 'Continue'
  try {
    $null = & $Python @pullArgs 2>&1 | Out-String
    $promotion = & $Python (Join-Path $service 'research_segment_promotion.py') --shadow-root $ShadowRoot --view $ViewRoot `
      --prefix $Prefix --base-url $cfg.SourceUrl 2>&1 | Out-String
  } finally { $ErrorActionPreference = $previous }
  $promotionExit = $LASTEXITCODE
  Write-ChainLog -Config $cfg -Name $logName -Message ("PROMOTION attempt={0} exit={1} {2}" -f $attempt, $promotionExit, $promotion)
  if ($promotionExit -eq 0) { break }
  # Only a moving head is worth retrying; any other refusal is final.
  if ($promotion -notmatch 'SHADOW_BEHIND_PUBLISHED|HEAD_MANIFEST_MISMATCH') { exit 3 }
}
if ($promotionExit -ne 0) { exit 3 }

$ErrorActionPreference = 'Continue'
try {
  $migration = & $Python (Join-Path $cfg.RepoRoot 'scripts\migrate_canonical_research_store.py') --source $ViewRoot `
    --heartbeat (Join-Path $ViewRoot '.segment-promotion.heartbeat.json') --destination $cfg.DataRoot 2>&1 | Out-String
} finally { $ErrorActionPreference = $previous }
$migrationExit = $LASTEXITCODE
Write-ChainLog -Config $cfg -Name $logName -Message ("MIGRATION exit={0} {1}" -f $migrationExit, $migration)
if ($migrationExit -ne 0) { exit 4 }

# Promotion plus migration outlive the default 10-minute receipt SLA as the
# epoch grows; the receipt is still bound to this cycle's applied seq.
$env:ANALYZER_MIRROR_SYNC_MAX_AGE_SEC = "$SyncMaxAgeSec"
& (Join-Path $PSHOME 'powershell.exe') -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'run-analyzer-once.ps1') `
  -RepoRoot $cfg.RepoRoot -CanonicalRoot $cfg.CanonicalRoot -StateDir $cfg.StateDir -Port $Port -Reason $Reason
$analyzerExit = $LASTEXITCODE
Write-ChainLog -Config $cfg -Name $logName -Message "ANALYZER exit=$analyzerExit"
exit $analyzerExit
