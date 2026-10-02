# Pull sealed research segments into the SHADOW mirror (never the canonical
# mirror), write the incremental laptop ACK, and optionally run the
# shadow-vs-legacy parity check. Idempotent and safe to re-run after sleep.
# Windows PowerShell 5.1 compatible. Never prints credentials.
#
# -Source Http (default): the Fly volume sink (RESEARCH_SEGMENTS_SINK=volume)
#   served at $BaseUrl/api/research-segments/v1/*. Only BOT_ADMIN_TOKEN is
#   read from $HomeBotEnv, into this process's environment.
# -Source Tigris: $VaultEnv holds the scoped laptop key (read seg/man, write acks/laptop only):
#   RESEARCH_SEGMENTS_BUCKET=...
#   RESEARCH_SEGMENTS_ACCESS_KEY_ID=...
#   RESEARCH_SEGMENTS_SECRET_ACCESS_KEY=...
#   RESEARCH_SEGMENTS_ENDPOINT=https://fly.storage.tigris.dev   (optional)
#   RESEARCH_SEGMENTS_PREFIX=v1                                  (optional)
param(
  [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
  [ValidateSet('Http', 'Tigris')][string]$Source = 'Http',
  [string]$BaseUrl = 'https://doxed-btc-bot.fly.dev',
  [string]$ShadowRoot = 'C:\DoxxedCrypto\fly-mirror-segments',
  [string]$ArchiveRoot = 'C:\DoxxedCrypto\fly-segments',
  [string]$VaultEnv = 'C:\DoxxedCrypto\doxedcryptofounder-secrets\vault\research-segments-laptop.env',
  [string]$HomeBotEnv = 'C:\DoxxedCrypto\doxedcryptofounder-secrets\vault\home-bot.env',
  [string]$LegacyTree = 'C:\DoxxedCrypto\btc-v31-current\services\btc-conservative-agent\canonical-research-data',
  [string]$Python = 'python',
  [string]$Prefix = 'v2',
  [int]$MaxSegments = 0,
  [switch]$NoAck,
  [switch]$Parity
)
$ErrorActionPreference = 'Stop'

$credentialFile = if ($Source -eq 'Http') { $HomeBotEnv } else { $VaultEnv }
foreach ($path in @($RepoRoot, $ShadowRoot, $ArchiveRoot, $credentialFile, $LegacyTree)) {
  $full = [System.IO.Path]::GetFullPath($path).ToLowerInvariant()
  if ($full.Contains('\onedrive\')) { throw "RESEARCH_SEGMENTS_ONEDRIVE_REFUSED: $path" }
}
if ([System.IO.Path]::GetFullPath($ShadowRoot).ToLowerInvariant().Contains('canonical-research-data')) {
  throw 'RESEARCH_SEGMENTS_SHADOW_MUST_NOT_BE_CANONICAL_MIRROR'
}
if (-not (Test-Path -LiteralPath $credentialFile)) { throw "RESEARCH_SEGMENTS_VAULT_MISSING: $credentialFile" }

if ($Source -eq 'Http') {
  if (-not $env:BOT_ADMIN_TOKEN) {
    $raw = Get-Content -LiteralPath $HomeBotEnv -Raw
    if ($raw -notmatch '(?m)^BOT_ADMIN_TOKEN=(.+)$') { throw 'BOT_ADMIN_TOKEN_UNAVAILABLE' }
    [Environment]::SetEnvironmentVariable('BOT_ADMIN_TOKEN', $Matches[1].Trim().Trim('"').Trim("'"), 'Process')
  }
} else {
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
}

$logDir = Join-Path $ShadowRoot 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log = Join-Path $logDir ('segment-pull-{0}.log' -f [datetime]::UtcNow.ToString('yyyyMMdd'))
$service = Join-Path $RepoRoot 'services\btc-conservative-agent'

$pullArgs = @((Join-Path $service 'research_segment_puller.py'), '--shadow-root', $ShadowRoot, '--archive-root', $ArchiveRoot, '--prefix', $Prefix)
if ($Source -eq 'Http') { $pullArgs += @('--source', 'http', '--base-url', $BaseUrl) } else { $pullArgs += @('--source', 'store') }
if ($MaxSegments -gt 0) { $pullArgs += @('--max-segments', "$MaxSegments") }
if ($NoAck) { $pullArgs += '--no-ack' }
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try { $pullOutput = & $Python @pullArgs 2>&1 | Out-String } finally { $ErrorActionPreference = $previous }
$pullExit = $LASTEXITCODE
Add-Content -LiteralPath $log -Value ('{0} pull source={1} exit={2} {3}' -f [datetime]::UtcNow.ToString('o'), $Source, $pullExit, ($pullOutput -replace '[\r\n]+', ' ')) -Encoding UTF8
Write-Host $pullOutput.Trim()

$parityExit = 0
if ($Parity -and $pullExit -eq 0) {
  # Fly publishes between pulls; exit 3 (RETRY) means the checkpoint moved
  # ahead of the tree, so catch up and re-check a bounded number of times.
  for ($attempt = 1; $attempt -le 5; $attempt++) {
    $ErrorActionPreference = 'Continue'
    try {
      if ($attempt -gt 1) { $null = & $Python @pullArgs 2>&1 | Out-String }
      $parityOutput = & $Python (Join-Path $RepoRoot 'scripts\research_segment_fly_parity.py') --shadow-root $ShadowRoot --base-url $BaseUrl --prefix $Prefix --report (Join-Path $ShadowRoot 'parity-latest.json') 2>&1 | Out-String
    } finally { $ErrorActionPreference = $previous }
    $parityExit = $LASTEXITCODE
    Add-Content -LiteralPath $log -Value ('{0} parity attempt={1} exit={2} {3}' -f [datetime]::UtcNow.ToString('o'), $attempt, $parityExit, ($parityOutput -replace '[\r\n]+', ' ')) -Encoding UTF8
    if ($parityExit -ne 3) { break }
  }
  Write-Host $parityOutput.Trim()
}
if ($pullExit -ne 0) { exit $pullExit }
if ($parityExit -eq 4) {
  # Lock budget hit: the pull itself succeeded and parity resumes from the hash cache next time.
  # Surfaced through parity-last-attempt.json in :9011 analyzer.parity_checker, not as a failed pull.
  Add-Content -LiteralPath $log -Value ('{0} PARITY_TIMEOUT deferred to the next parity pull' -f [datetime]::UtcNow.ToString('o')) -Encoding UTF8
  exit 0
}
exit $parityExit
