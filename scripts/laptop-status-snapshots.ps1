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
$health = $null
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
                        'store_bytes', 'max_store_bytes', 'last_segment_at', 'last_error', 'pruning_enabled',
                        'prune_mode', 'pruned_through_seq', 'custody_through_seq', 'prune_deleted_bytes_total') {
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

# Paper/tile/AI/WS state for the unattended proof: public /health and /ready,
# plus only the tile toggle map from the admin-authenticated /api/state.
$runtime = New-Snapshot 'fly_runtime_snapshot_v1'
if ($offline) {
  $runtime.error = 'OFFLINE'
} elseif ($null -eq $health) {
  $runtime.error = 'FLY_HEALTH_HTTP_FAILED'
} else {
  foreach ($name in 'execution_paused', 'pause_owner', 'execution_reason', 'manual_admin_pause', 'live_armed',
                    'bitfinex_live_enabled', 'force_paper_mode', 'git_rev', 'tile_registry_signature') {
    $runtime[$name] = $health.$name
  }
  try {
    $ready = Invoke-RestMethod -Method Get -Uri "$($cfg.SourceUrl)/ready" -TimeoutSec $TimeoutSec
    $progress = $ready.strategy_progress
    $cycle = $progress.scheduled_ai_cycle
    $runtime.ready_status = $ready.status
    $runtime.ws_age = $ready.ws_age
    $runtime.active_tile_lanes = @($ready.active_tiles | ForEach-Object { [string]$_.lane })
    $inputHealth = $ready.ai_input_health
    if ($inputHealth) {
      $runtime.ai_input_health = [ordered]@{
        status = $inputHealth.status; prompt_id = $inputHealth.prompt_id
        observed_calls = $inputHealth.observed_calls; threshold_calls = $inputHealth.threshold_calls
        dead_fields = @($inputHealth.dead_fields | ForEach-Object { [ordered]@{ path = [string]$_.path; kind = [string]$_.kind; calls = $_.calls } })
      }
    }
    $crossVenue = $ready.cross_venue_health
    if ($crossVenue) {
      $runtime.cross_venue_health = [ordered]@{
        status = [string]$crossVenue.status; reason = $crossVenue.reason
        collector_age_s = $crossVenue.collector_age_s
        stale_venues = @($crossVenue.stale_venues | ForEach-Object { [string]$_ })
      }
    }
    $xvl = $ready.xvl_evaluator_health
    if ($xvl) {
      $runtime.xvl_evaluator_health = [ordered]@{
        status = [string]$xvl.status; reason = $xvl.reason
        tick_age_s = $xvl.tick_age_s; write_failures = $xvl.write_failures
        tick_errors = $xvl.tick_errors; rows_written = $xvl.rows_written
      }
    }
    $provider = $progress.ai_provider
    $runtime.strategy_progress = [ordered]@{
      ai_progressing = $progress.ai_progressing; ai_age_sec = $progress.ai_age_sec
      last_ai_success_at = $progress.last_ai_success_at; ai_consecutive_failures = $progress.ai_consecutive_failures
      ai_provider = if ($provider) { [ordered]@{
        last_ai_success_at = $provider.last_ai_success_at; successes_since_boot = $provider.successes_since_boot
        consecutive_failures = $provider.consecutive_failures; alert = $provider.alert
        last_model_echo = $provider.last_model_echo; configured_model = $provider.configured_model
        last_system_fingerprint = $provider.last_system_fingerprint
      } } else { $null }
      ai_stale_after_sec = $progress.ai_stale_after_sec; evaluation_age_sec = $progress.evaluation_age_sec
      process_startup_age_sec = $progress.process_startup_age_sec
      ws_age_sec = $progress.ws_age_sec; ws_progressing = $progress.ws_progressing
      scheduled_ai_cycle = [ordered]@{
        completed_ts = $cycle.completed_ts; last_poll_ts = $cycle.last_poll_ts
        last_poll_entry_eligible = $cycle.last_poll_entry_eligible; stage = $cycle.stage
      }
    }
    $runtime.ok = $true
  } catch {
    $runtime.error = 'FLY_READY_HTTP_FAILED'
  }
  if ([string]::IsNullOrWhiteSpace($adminToken)) {
    $runtime.toggles_error = 'ADMIN_TOKEN_MISSING'
  } else {
    try {
      $state = Invoke-RestMethod -Method Get -Uri "$($cfg.SourceUrl)/api/state" -Headers @{ 'X-Bot-Admin-Token' = $adminToken } -TimeoutSec $TimeoutSec
      if ($state.research_lane_enabled) {
        $toggles = [ordered]@{}
        foreach ($p in $state.research_lane_enabled.PSObject.Properties) { $toggles[[string]$p.Name] = ($p.Value -eq $true) }
        $runtime.research_lane_enabled = $toggles
      } else {
        $runtime.toggles_error = 'TOGGLES_NOT_IN_STATE'
      }
    } catch {
      $runtime.toggles_error = 'FLY_STATE_HTTP_FAILED'
    }
  }
}
Write-JsonAtomic -Path (Join-Path $cfg.StateDir 'fly_runtime_snapshot_v1.json') -Value $runtime -Depth 6

# Recent guarded deploy workflow runs (read-only `gh run list`) so the proof can
# attribute a DEPLOY_MAINTENANCE pause to an actual guarded deploy.
$deploys = New-Snapshot 'fly_deploy_runs_snapshot_v1'
$deployRepo = if ($env:DOXXED_DEPLOY_REPO) { $env:DOXXED_DEPLOY_REPO } else { 'danishhaiderau-maker/doxed-founders-website' }
$deploys.repo = $deployRepo
$deploys.workflow = 'fly-bot-deploy.yml'
if ($offline) {
  $deploys.error = 'OFFLINE'
} elseif (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
  $deploys.error = 'GH_CLI_MISSING'
} else {
  $previousPreference = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  # gh writes UTF-8; Windows PowerShell 5 decodes native output with the console code page (cp437),
  # which turned a title's U+2026 ellipsis into cp437 mojibake in every downstream receipt.
  $previousEncoding = $null
  try { $previousEncoding = [Console]::OutputEncoding; [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }
  try {
    $raw = & gh run list --repo $deployRepo --workflow fly-bot-deploy.yml --limit 8 `
      --json databaseId,status,conclusion,createdAt,updatedAt,event,headSha,displayTitle 2>$null | Out-String
    if ($LASTEXITCODE -eq 0 -and $raw.Trim()) {
      # Windows PowerShell 5 emits the parsed JSON array as one object; enumerate it so
      # ConvertTo-Json writes a flat list instead of a {"value": [...], "Count": n} wrapper.
      $deploys.runs = [object[]]@($raw | ConvertFrom-Json | ForEach-Object { $_ })
      $deploys.ok = $true
    } else {
      $deploys.error = 'GH_RUN_LIST_FAILED'
    }
  } catch {
    $deploys.error = 'GH_RUN_LIST_FAILED'
  } finally {
    $ErrorActionPreference = $previousPreference
    if ($null -ne $previousEncoding) { try { [Console]::OutputEncoding = $previousEncoding } catch { } }
  }
}
Write-JsonAtomic -Path (Join-Path $cfg.StateDir 'fly_deploy_runs_snapshot_v1.json') -Value $deploys -Depth 5
Write-Output ("SNAPSHOTS fly_head_ok={0} relay_ok={1} runtime_ok={2} deploy_runs_ok={3}" -f $head.ok, $relay.ok, $runtime.ok, $deploys.ok)
