# Read-only laptop observations for the analyzer Decision page and monitor.
# Writes two small JSON snapshots into the laptop-chain state directory:
#   fly_segment_head_snapshot_v1.json  Fly v2 shipper head from public /health
#   relay_status_snapshot_v1.json      platform ops relay-status (authenticated GET)
# Nothing here can arm, pause or change the relay or the bot. A failed read is
# recorded as ok=false with a bounded error code, never as flat or disarmed.
# Snapshots never contain the token, the scoped user id or upstream error text.
param(
  [string]$RepoRoot = '',
  [string]$CanonicalRoot = '',
  [string]$StateDir = '',
  [int]$TimeoutSec = 20
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'laptop-chain-common.ps1')
. (Join-Path $PSScriptRoot 'home-bot-vault-env.ps1')
$cfg = Get-LaptopChainConfig -RepoRoot $RepoRoot -CanonicalRoot $CanonicalRoot -StateDir $StateDir
$offline = $env:DOXXED_LAPTOP_CHAIN_OFFLINE -eq '1'

function New-Snapshot([string]$Schema) {
  [ordered]@{ schema = $Schema; observedAt = [DateTimeOffset]::UtcNow.ToString('o'); ok = $false }
}

$head = New-Snapshot 'fly_segment_head_snapshot_v1'
if ($offline) {
  $head.error = 'OFFLINE'
} else {
  try {
    $health = Invoke-RestMethod -Method Get -Uri "$($cfg.SourceUrl)/health" -TimeoutSec $TimeoutSec
    $transfer = $health.volume.transfer
    if ($null -eq $transfer) {
      $head.error = 'TRANSFER_BLOCK_MISSING'
    } else {
      $head.ok = $true
      foreach ($name in 'segments_enabled', 'sink', 'shipped_seq', 'laptop_acked_seq', 'unshipped_bytes',
                        'store_bytes', 'max_store_bytes', 'last_segment_at', 'last_error', 'pruning_enabled') {
        $head[$name] = $transfer.$name
      }
    }
  } catch {
    $head.error = 'FLY_HEALTH_HTTP_FAILED'
  }
}
Write-JsonAtomic -Path $cfg.FlySegmentHeadFile -Value $head

$relay = New-Snapshot 'relay_status_snapshot_v1'
Import-HomeBotVaultConfig -VaultEnvPath $cfg.VaultEnv
$apiBaseUrl = $env:PLATFORM_API_BASE_URL
$agentSlug = $env:PLATFORM_RELAY_AGENT_SLUG
$userId = $env:PLATFORM_RELAY_USER_ID
$adminToken = $env:BOT_ADMIN_TOKEN
if ($offline) {
  $relay.error = 'OFFLINE'
} elseif (@($apiBaseUrl, $agentSlug, $userId, $adminToken) | Where-Object { [string]::IsNullOrWhiteSpace($_) }) {
  $relay.error = 'RELAY_STATUS_CONFIG_MISSING'
} else {
  try {
    $statusUri = "$($apiBaseUrl.TrimEnd('/'))/trading-agents/$([uri]::EscapeDataString($agentSlug))/ops/relay-status?userId=$([uri]::EscapeDataString($userId))"
    $status = Invoke-RestMethod -Method Get -Uri $statusUri -Headers @{
      'X-Bot-Admin-Token' = $adminToken
      'Accept' = 'application/json'
    } -TimeoutSec $TimeoutSec
    $relay.ok = $true
    foreach ($name in 'status', 'relayExecutionMode', 'relayArmedAt', 'realTradingConfirmedAt', 'reconciliation',
                      'exchangeOrderAudit', 'relayExecutor', 'relayAllowlist', 'positionMismatchAlert') {
      $relay[$name] = $status.$name
    }
  } catch {
    $relay.error = 'RELAY_STATUS_HTTP_FAILED'
  }
}
Write-JsonAtomic -Path $cfg.RelayStatusSnapshotFile -Value $relay -Depth 8
Write-Output ("SNAPSHOTS fly_head_ok={0} relay_ok={1}" -f $head.ok, $relay.ok)
