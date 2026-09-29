# Pull sealed research segments from Tigris into the SHADOW mirror (never the
# canonical mirror), write the incremental laptop ACK, and optionally run the
# shadow-vs-legacy parity check. Idempotent and safe to re-run after sleep.
# Windows PowerShell 5.1 compatible. Never prints credentials.
#
# Credentials: $VaultEnv holds the scoped laptop key (read seg/man, write acks/laptop only):
#   RESEARCH_SEGMENTS_BUCKET=...
#   RESEARCH_SEGMENTS_ACCESS_KEY_ID=...
#   RESEARCH_SEGMENTS_SECRET_ACCESS_KEY=...
#   RESEARCH_SEGMENTS_ENDPOINT=https://fly.storage.tigris.dev   (optional)
#   RESEARCH_SEGMENTS_PREFIX=v1                                  (optional)
param(
  [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
  [string]$ShadowRoot = 'C:\DoxxedCrypto\fly-mirror-segments',
  [string]$ArchiveRoot = 'C:\DoxxedCrypto\fly-segments',
  [string]$VaultEnv = 'C:\DoxxedCrypto\doxedcryptofounder-secrets\vault\research-segments-laptop.env',
  [string]$LegacyTree = 'C:\DoxxedCrypto\btc-v31-current\services\btc-conservative-agent\canonical-research-data',
  [string]$Python = 'python',
  [int]$MaxSegments = 0,
  [switch]$NoAck,
  [switch]$Parity
)
$ErrorActionPreference = 'Stop'

foreach ($path in @($RepoRoot, $ShadowRoot, $ArchiveRoot, $VaultEnv, $LegacyTree)) {
  $full = [System.IO.Path]::GetFullPath($path).ToLowerInvariant()
  if ($full.Contains('\onedrive\')) { throw "RESEARCH_SEGMENTS_ONEDRIVE_REFUSED: $path" }
}
if ([System.IO.Path]::GetFullPath($ShadowRoot).ToLowerInvariant().Contains('canonical-research-data')) {
  throw 'RESEARCH_SEGMENTS_SHADOW_MUST_NOT_BE_CANONICAL_MIRROR'
}
if (-not (Test-Path -LiteralPath $VaultEnv)) { throw "RESEARCH_SEGMENTS_VAULT_MISSING: $VaultEnv" }

$allowed = @('RESEARCH_SEGMENTS_BUCKET', 'RESEARCH_SEGMENTS_ACCESS_KEY_ID', 'RESEARCH_SEGMENTS_SECRET_ACCESS_KEY',
             'RESEARCH_SEGMENTS_ENDPOINT', 'RESEARCH_SEGMENTS_REGION', 'RESEARCH_SEGMENTS_PREFIX')
foreach ($line in Get-Content -LiteralPath $VaultEnv) {
  if ($line -match '^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$' -and $allowed -contains $Matches[1]) {
    [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim('"'), 'Process')
  }
}
foreach ($required in @('RESEARCH_SEGMENTS_BUCKET', 'RESEARCH_SEGMENTS_ACCESS_KEY_ID', 'RESEARCH_SEGMENTS_SECRET_ACCESS_KEY')) {
  if (-not [Environment]::GetEnvironmentVariable($required, 'Process')) { throw "RESEARCH_SEGMENTS_VAULT_INCOMPLETE: $required" }
}

$logDir = Join-Path $ShadowRoot 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log = Join-Path $logDir ('segment-pull-{0}.log' -f [datetime]::UtcNow.ToString('yyyyMMdd'))
$service = Join-Path $RepoRoot 'services\btc-conservative-agent'

$pullArgs = @((Join-Path $service 'research_segment_puller.py'), '--shadow-root', $ShadowRoot, '--archive-root', $ArchiveRoot)
if ($MaxSegments -gt 0) { $pullArgs += @('--max-segments', "$MaxSegments") }
if ($NoAck) { $pullArgs += '--no-ack' }
$pullOutput = & $Python @pullArgs 2>&1 | Out-String
$pullExit = $LASTEXITCODE
Add-Content -LiteralPath $log -Value ('{0} pull exit={1} {2}' -f [datetime]::UtcNow.ToString('o'), $pullExit, ($pullOutput -replace '[\r\n]+', ' ')) -Encoding UTF8
Write-Host $pullOutput.Trim()

$parityExit = 0
if ($Parity -and $pullExit -eq 0) {
  $parityOutput = & $Python (Join-Path $service 'research_segment_parity.py') --shadow-tree (Join-Path $ShadowRoot 'tree') --legacy-tree $LegacyTree --report (Join-Path $ShadowRoot 'parity-latest.json') 2>&1 | Out-String
  $parityExit = $LASTEXITCODE
  Add-Content -LiteralPath $log -Value ('{0} parity exit={1} {2}' -f [datetime]::UtcNow.ToString('o'), $parityExit, ($parityOutput -replace '[\r\n]+', ' ')) -Encoding UTF8
  Write-Host $parityOutput.Trim()
}
if ($pullExit -ne 0) { exit $pullExit }
exit $parityExit
