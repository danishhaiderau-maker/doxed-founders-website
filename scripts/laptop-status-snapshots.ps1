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

# /health and /ready answer 503 with a full JSON body while the bot boots or a
# heartbeat is late; Invoke-RestMethod throws on that status and the snapshot
# lost git_rev/live_armed/lanes (nulls during and after every deploy). Read the
# body of an error response too; $script:LastHttpStatus carries the code.
function Invoke-JsonGet([string]$Uri, [hashtable]$Headers = @{}) {
  $script:LastHttpStatus = $null
  try {
    $r = Invoke-RestMethod -Method Get -Uri $Uri -Headers $Headers -TimeoutSec $TimeoutSec
    $script:LastHttpStatus = 200
    return $r
  } catch {
    $resp = $_.Exception.Response
    if ($null -eq $resp) { throw }
    $script:LastHttpStatus = [int]$resp.StatusCode
    $body = $null
    try {
      $reader = New-Object System.IO.StreamReader($resp.GetResponseStream())
      $body = $reader.ReadToEnd() | ConvertFrom-Json
    } catch { $body = $null }
    if ($null -eq $body) { throw }
    return $body
  }
}

$head = New-Snapshot 'fly_segment_head_snapshot_v1'
$health = $null
$healthHttp = $null
if ($offline) {
  $head.error = 'OFFLINE'
} else {
  try {
    $health = Invoke-JsonGet "$($cfg.SourceUrl)/health"
    $healthHttp = $script:LastHttpStatus
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
    $ready = Invoke-JsonGet "$($cfg.SourceUrl)/ready"
    $runtime.ready_http = $script:LastHttpStatus
    $progress = $ready.strategy_progress
    $cycle = $progress.scheduled_ai_cycle
    $runtime.ready_status = $ready.status
    $runtime.ws_age = $ready.ws_age
    $runtime.ws_transport_connected = $ready.ws_transport_connected
    $wsConn = $ready.ws_connection
    if ($wsConn) {
      $runtime.ws_connection = [ordered]@{
        reconnect_count = $wsConn.reconnect_count; generation = $wsConn.generation
        connected_age_sec = $wsConn.connected_age_sec; last_disconnect_reason = $wsConn.last_disconnect_reason
      }
    }
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
      ws_heartbeat_age_sec = $progress.ws_heartbeat_age_sec
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
# Keep the last good value of the identity fields instead of publishing nulls.
# Carried values are labelled (carried_fields + last_good_at), never silent.
$runtimePath = Join-Path $cfg.StateDir 'fly_runtime_snapshot_v1.json'
$previousRuntime = Read-JsonFile $runtimePath
$lastGood = if ($previousRuntime -and $previousRuntime.last_good) { $previousRuntime.last_good } else { $null }
$identity = @('git_rev', 'live_armed', 'bitfinex_live_enabled', 'tile_registry_signature', 'active_tile_lanes')
$carried = @()
$fresh = [ordered]@{}
foreach ($name in $identity) {
  $value = $runtime[$name]
  $empty = ($null -eq $value) -or ($value -is [string] -and [string]::IsNullOrWhiteSpace($value)) -or
           ($name -eq 'active_tile_lanes' -and @($value).Count -eq 0)
  if (-not $empty) { $fresh[$name] = $value }
  elseif ($lastGood -and $null -ne $lastGood.$name) { $runtime[$name] = $lastGood.$name; $carried += $name }
}
if ($fresh.Count -eq $identity.Count) {
  $fresh.observedAt = $runtime.observedAt
  $runtime.last_good = $fresh
} elseif ($lastGood) {
  $merged = [ordered]@{}
  foreach ($p in $lastGood.PSObject.Properties) { $merged[$p.Name] = $p.Value }
  foreach ($k in $fresh.Keys) { $merged[$k] = $fresh[$k] }
  $runtime.last_good = $merged
}
if ($carried.Count -gt 0) {
  $runtime.carried_fields = $carried
  $runtime.last_good_at = if ($lastGood) { $lastGood.observedAt } else { $null }
}
if ($healthHttp) { $runtime.health_http = $healthHttp }
Write-JsonAtomic -Path $runtimePath -Value $runtime -Depth 6

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
      # Serving tip's deploy run + job steps for system-health fly.revision
      # (deploy_run_id / conclusion / failed_steps / paper_resumed_by). One read-only
      # gh api call; never workflow run / rerun / cancel / merge.
      $servingRev = [string]$runtime.git_rev
      if ([string]::IsNullOrWhiteSpace($servingRev) -and $runtime.last_good) {
        $servingRev = [string]$runtime.last_good.git_rev
      }
      if (-not [string]::IsNullOrWhiteSpace($servingRev)) {
        $servingRev = $servingRev.Trim().ToLowerInvariant()
        $match = $null
        foreach ($run in $deploys.runs) {
          if ($null -eq $run) { continue }
          if ([string]$run.status -ne 'completed') { continue }
          $sha = ([string]$run.headSha).Trim().ToLowerInvariant()
          if ([string]::IsNullOrWhiteSpace($sha)) { continue }
          if ($sha.StartsWith($servingRev) -or $servingRev.StartsWith($sha.Substring(0, [Math]::Min(12, $sha.Length)))) {
            $match = $run
            break
          }
        }
        if ($null -ne $match -and $null -ne $match.databaseId) {
          try {
            $jobsRaw = & gh api "repos/$deployRepo/actions/runs/$($match.databaseId)/jobs?per_page=100" 2>$null | Out-String
            if ($LASTEXITCODE -eq 0 -and $jobsRaw.Trim()) {
              $jobsDoc = $jobsRaw | ConvertFrom-Json
              $compact = @()
              foreach ($job in @($jobsDoc.jobs)) {
                $jName = [string]$job.name
                if ($jName -ne 'test-and-deploy' -and $jName -ne 'resume-paper') { continue }
                $steps = @()
                foreach ($step in @($job.steps)) {
                  $steps += [ordered]@{ name = [string]$step.name; conclusion = [string]$step.conclusion }
                }
                $compact += [ordered]@{
                  name = $jName
                  conclusion = [string]$job.conclusion
                  steps = $steps
                }
              }
              $deploys.serving_deploy = [ordered]@{
                databaseId = $match.databaseId
                status = [string]$match.status
                conclusion = [string]$match.conclusion
                createdAt = [string]$match.createdAt
                event = [string]$match.event
                headSha = [string]$match.headSha
                jobs = $compact
              }
            }
          } catch {
            # Keep runs list; fly.revision falls back to run match without steps.
          }
        }
      }
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
Write-JsonAtomic -Path (Join-Path $cfg.StateDir 'fly_deploy_runs_snapshot_v1.json') -Value $deploys -Depth 8
Write-Output ("SNAPSHOTS fly_head_ok={0} relay_ok={1} runtime_ok={2} deploy_runs_ok={3}" -f $head.ok, $relay.ok, $runtime.ok, $deploys.ok)
