# Explicit one-shot batch transport. Run only after the prior owner is terminal.
# Existing sync owns canonical URL/path validation, vault authentication,
# transfer integrity, checkpointing and ACK. No serial fallback is introduced.
[CmdletBinding()]
param(
  [string]$SourceUrl = 'https://doxed-btc-bot.fly.dev',
  [string]$TargetDir = '',
  [string]$MirroredSourceRevision = '',
  [object]$InitialManifest = $null,
  [string]$ProgressHeartbeatFile = '',
  [int]$MaxLocalMirrorGiB = 30
)

$ErrorActionPreference = 'Stop'
$batchPreviousOptIn = [Environment]::GetEnvironmentVariable('FLY_SYNC_TRANSPORT_BUNDLES', 'Process')
try {
  [Environment]::SetEnvironmentVariable('FLY_SYNC_TRANSPORT_BUNDLES', '1', 'Process')
  # Do not pass credentials on a command line. The existing sync script uses
  # Import-CanonicalBotAdminToken from home-bot-vault-env.ps1 itself.
  & (Join-Path $PSScriptRoot 'sync-fly-bot-data.ps1') `
    -SourceUrl $SourceUrl -TargetDir $TargetDir `
    -MirroredSourceRevision $MirroredSourceRevision -InitialManifest $InitialManifest `
    -ProgressHeartbeatFile $ProgressHeartbeatFile -MaxLocalMirrorGiB $MaxLocalMirrorGiB
}
finally {
  [Environment]::SetEnvironmentVariable('FLY_SYNC_TRANSPORT_BUNDLES', $batchPreviousOptIn, 'Process')
}
