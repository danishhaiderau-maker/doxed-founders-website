# Sole laptop owner of Fly inventory ACKs.
#
# Polls Fly, waits for an inventory generation that is CURRENT, authoritative
# and ack-eligible and not yet proven ACKed on disk, pulls and ACKs it through
# the canonical sync client, then refreshes the analyzer. Expected revision and
# generation always come from Fly /health and the manifest; the last ACK comes
# from the on-disk terminal membership receipts. It never arms, pauses or
# resumes anything and never deploys.
param(
  [string]$RepoRoot = '',
  [string]$CanonicalRoot = '',
  [string]$StateDir = '',
  [string]$SourceUrl = '',
  [string]$SyncScript = '',
  [int]$PollSec = 60,
  [int]$MaxBackoffSec = 1800,
  [int]$SoftCapMiB = 550,
  [int]$SyncTimeoutMin = 240,
  [int]$StaleInProgressMin = 15,
  [int]$MaxIterations = 0,
  [int]$InventoryRefreshMinIntervalSec = 600,
  [int]$RevalidatingPollSec = 15,
  [int]$SupersessionCheckSec = 180,
  [switch]$SkipAnalyzerRefresh,
  [switch]$DisableTransportBundles
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'laptop-chain-common.ps1')

$cfg = Get-LaptopChainConfig -RepoRoot $RepoRoot -CanonicalRoot $CanonicalRoot -StateDir $StateDir -SourceUrl $SourceUrl
if (-not $SyncScript) { $SyncScript = Join-Path $cfg.CanonicalRoot 'scripts\sync-fly-bot-data.ps1' }
$logName = 'laptop-ack-watcher'
$mutexName = (Get-ChainMutexName 'LaptopAckWatcher')

$instance = Enter-SingleInstance -Name $mutexName
if (-not $instance) {
  Write-ChainLog -Config $cfg -Name $logName -Message 'WATCHER_ALREADY_RUNNING exit=4'
  exit 4
}

$selfStart = (Get-ProcessStartUtc $PID).ToString('o')
$previousLock = Read-JsonFile $cfg.WatcherLock
Write-JsonAtomic -Path $cfg.WatcherLock -Value ([ordered]@{
  schema = 'laptop_ack_watcher_lock_v1'
  pid = $PID
  startedAt = $selfStart
  host = $env:COMPUTERNAME
  mutex = $mutexName
  released = $false
  takeover = [bool]($instance.Abandoned -or ($previousLock -and $previousLock.released -ne $true))
  previousOwnerPid = $(if ($previousLock) { $previousLock.pid } else { $null })
})
Write-ChainLog -Config $cfg -Name $logName -Message ("WATCH_START repo={0} canonical={1} sync={2} abandonedMutex={3}" -f $cfg.RepoRoot, $cfg.CanonicalRoot, $SyncScript, $instance.Abandoned)

$status = [ordered]@{
  schema = 'laptop_ack_watcher_status_v1'
  pid = $PID
  startedAt = $selfStart
  state = 'STARTING'
  detail = $null
  lastPollAt = $null
  nextPollAt = $null
  consecutiveFailures = 0
  consecutiveSyncFailures = 0
  lastSyncResult = $null
  lastAckGeneration = $null
  lastAckAt = $null
  expected = $null
  inventory = $null
  syncChildPid = $null
}
$script:child = $null

function Save-WatcherStatus { Write-JsonAtomic -Path $cfg.WatcherStatus -Value $status }

function Get-ManifestPage($Token, [string]$Cursor = '', [int]$PageSize = 250) {
  $headers = @{ 'X-Bot-Admin-Token' = $Token; Accept = 'application/json' }
  $uri = "$($cfg.SourceUrl)/api/data-sync/manifest"
  if ($Cursor) { $uri += "?page_size=$PageSize&cursor=$([uri]::EscapeDataString($Cursor))" }
  return Invoke-RestMethod -Uri $uri -Headers $headers -TimeoutSec 180 -UseBasicParsing
}

# A retained generation answers by id as CURRENT/ack-eligible even after the
# live inventory TTL marks it STALE. 410 means Fly no longer retains it.
function Get-GenerationManifest($Token, [string]$GenerationId) {
  $headers = @{ 'X-Bot-Admin-Token' = $Token; Accept = 'application/json' }
  $uri = "$($cfg.SourceUrl)/api/data-sync/manifest?paged=1&generation_id=$GenerationId&page_size=250"
  try {
    return Invoke-RestMethod -Uri $uri -Headers $headers -TimeoutSec 180 -UseBasicParsing
  } catch {
    $response = $_.Exception.Response
    if ($response -and [int]$response.StatusCode -eq 410) { return $null }
    throw
  }
}

# A partially downloaded, unacknowledged generation stays in custody so a
# killed or timed-out child resumes it instead of waiting for a new CURRENT.
function Get-CustodyGeneration($AckedSet) {
  $heartbeat = Read-JsonFile $cfg.HeartbeatFile
  if ($null -eq $heartbeat -or $heartbeat.inProgress -eq $true -or $heartbeat.ackFinalized -eq $true) { return $null }
  $generation = [string]$heartbeat.inventoryGenerationId
  if ($generation -notmatch '^[0-9a-f]{64}$' -or $AckedSet.Contains($generation)) { return $null }
  return $generation
}

# Plain manifest GETs are read-only on Fly; once the CURRENT cache ages out
# nothing rebuilds it unless a client asks (single-flight on Fly).
function Request-InventoryRefresh($Token) {
  $headers = @{ 'X-Bot-Admin-Token' = $Token; Accept = 'application/json' }
  $body = @{ nonce = [guid]::NewGuid().ToString('N') } | ConvertTo-Json -Compress
  return Invoke-RestMethod -Method Post -Uri "$($cfg.SourceUrl)/api/data-sync/manifest/refresh" `
    -Headers $headers -ContentType 'application/json' -Body $body -TimeoutSec 60 -UseBasicParsing
}

function Test-InventoryRefreshNeeded($Manifest, [string]$InventoryStatus) {
  if ($InventoryStatus -notin @('STALE', 'EMPTY')) { return $false }
  if ([string]$Manifest.inventory_build_status -eq 'BUILDING') { return $false }
  return ([datetime]::UtcNow - $script:lastInventoryRefreshRequest).TotalSeconds -ge $InventoryRefreshMinIntervalSec
}
$script:lastInventoryRefreshRequest = [datetime]::MinValue

# Bytes the local mirror still lacks; the soft cap bounds transfer, not the
# inventory size.
function Get-MissingTransferBytes($Manifest, $Token) {
  $pageSize = [int]$Manifest.manifest_page_size
  if ($pageSize -le 0) { $pageSize = 250 }
  $missing = [int64]0
  $pages = 0
  $page = $Manifest
  while ($null -ne $page) {
    $pages++
    foreach ($file in @($page.files)) {
      $relative = [string]$file.path
      if (-not $relative) { continue }
      $remoteSize = [int64]$file.size
      $localSize = [int64]-1
      foreach ($candidate in @((Join-Path $cfg.DataRoot $relative), (Join-Path $cfg.DataRoot (Join-Path 'runtime' $relative)))) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $localSize = (Get-Item -LiteralPath $candidate).Length; break }
      }
      if ($localSize -lt 0) { $missing += $remoteSize } elseif ($localSize -lt $remoteSize) { $missing += ($remoteSize - $localSize) }
    }
    $next = [string]$page.manifest_next_cursor
    if (-not $next) { break }
    if ($pages -gt 200) { throw 'MANIFEST_PAGE_CAP_EXCEEDED' }
    $page = Get-ManifestPage -Token $Token -Cursor $next -PageSize $pageSize
  }
  return $missing
}

function Test-GenerationAckProven([string]$GenerationId) {
  $heartbeat = Read-JsonFile $cfg.HeartbeatFile
  $acked = Get-AckedGenerations $cfg.DataRoot
  return [bool](
    $acked.Set.Contains($GenerationId) -and
    $heartbeat -and
    [string]$heartbeat.inventoryGenerationId -eq $GenerationId -and
    $heartbeat.inProgress -ne $true -and
    $heartbeat.ok -eq $true -and
    $heartbeat.ackFinalized -eq $true -and
    [string]$heartbeat.phase -eq 'complete' -and
    [string]$heartbeat.completionAuthority -eq 'REMOTE_ACK_FINALIZED'
  )
}

# Verified TAR packages cut one request per small file to one per package.
# The client treats a missing index as a failure (never a silent serial
# fallback), so opt in only when Fly already publishes one for this generation.
function Test-BundleTransportOffered($Token, [string]$GenerationId) {
  if ($DisableTransportBundles) { return $false }
  $headers = @{ 'X-Bot-Admin-Token' = $Token; Accept = 'application/json' }
  try {
    $index = Invoke-RestMethod -Uri "$($cfg.SourceUrl)/api/data-sync/bundles?generation_id=$GenerationId" `
      -Headers $headers -TimeoutSec 120 -UseBasicParsing
  } catch {
    return $false
  }
  return (
    [string]$index.schema -eq 'fly_runtime_transport_bundle_index_v1' -and
    [string]$index.generation_id -eq $GenerationId -and
    [string]$index.status -in @('BUILDING', 'COMPLETE')
  )
}

# A deploy changes the runtime revision, and Fly refuses to ACK a generation
# frozen under another revision, so a child still copying it can never finish.
# A serial child is also superseded once Fly publishes bundles for the live
# CURRENT generation: restarting on that path resumes from the sync state.
function Get-SyncSupersession([string]$GenerationId, [string]$ManifestRevision, [bool]$TransportBundles) {
  try {
    $flyRevision = [string](Get-FlyHealth $cfg.SourceUrl).source_git_rev
    if ($flyRevision -and -not (Test-RevisionPrefixMatch $ManifestRevision $flyRevision)) { return 'REVISION_CHANGED' }
    if (-not $TransportBundles) {
      $token = Get-AdminToken $cfg.VaultEnv
      $live = Get-ManifestPage -Token $token
      $liveGeneration = [string]$live.inventory_generation_id
      if ([string]$live.inventory_status -eq 'CURRENT' -and $live.inventory_ack_eligible -eq $true -and
          $live.inventory_authoritative -eq $true -and $liveGeneration -match '^[0-9a-f]{64}$' -and
          (Test-BundleTransportOffered -Token $token -GenerationId $liveGeneration)) {
        return 'BUNDLES_OFFERED'
      }
    }
  } catch {
    return $null
  }
  return $null
}

function Invoke-GenerationSync($Manifest, [string]$FullRevision, [bool]$TransportBundles = $false) {
  $generation = [string]$Manifest.inventory_generation_id
  $stamp = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
  $bundleFlag = if ($TransportBundles) { '1' } else { '0' }
  $pinned = Join-Path $cfg.RunDir "pinned-manifest-$($generation.Substring(0, 16))-$stamp.json"
  ($Manifest | ConvertTo-Json -Depth 60) | Set-Content -LiteralPath $pinned -Encoding UTF8
  $wrapper = Join-Path $cfg.RunDir "sync-child-$stamp.ps1"
  $stdout = Join-Path $cfg.LogDir "sync-child-$stamp.out.log"
  $stderr = Join-Path $cfg.LogDir "sync-child-$stamp.err.log"
  @"
`$ErrorActionPreference = 'Stop'
try {
  `$raw = Get-Content -LiteralPath '$($cfg.VaultEnv)' -Raw
  if (-not `$env:BOT_ADMIN_TOKEN) {
    if (`$raw -notmatch '(?m)^BOT_ADMIN_TOKEN=(.+)`$') { throw 'BOT_ADMIN_TOKEN_UNAVAILABLE' }
    `$env:BOT_ADMIN_TOKEN = `$Matches[1].Trim().Trim('"').Trim("'")
  }
  `$manifest = Get-Content -LiteralPath '$pinned' -Raw | ConvertFrom-Json
  `$env:FLY_SYNC_TRANSPORT_BUNDLES = '$bundleFlag'
  & '$SyncScript' -SourceUrl '$($cfg.SourceUrl)' -AdminToken `$env:BOT_ADMIN_TOKEN -InitialManifest `$manifest ``
    -MirroredSourceRevision '$FullRevision' -ProgressHeartbeatFile '$($cfg.HeartbeatFile)' -MaxLocalMirrorGiB 30 | Out-Null
  exit 0
} catch {
  [Console]::Error.WriteLine(`$_.Exception.Message)
  exit 1
}
"@ | Set-Content -LiteralPath $wrapper -Encoding UTF8

  $lease = $null
  try {
    $lease = [System.IO.File]::Open($cfg.GenerationLeaseFile, 'OpenOrCreate', 'ReadWrite', 'None')
  } catch {
    return [pscustomobject]@{ Ok = $false; Deferred = $true; Code = $null; Detail = 'ANALYZER_OWNS_GENERATION_LEASE' }
  }
  try {
    Write-ChainLog -Config $cfg -Name $logName -Message ("SYNC_START gen={0} rev={1} files={2} bytes={3} bundles={4}" -f $generation.Substring(0, 16), $FullRevision.Substring(0, 12), $Manifest.file_count, $Manifest.total_bytes, $bundleFlag)
    $script:child = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') `
      -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $wrapper) `
      -WorkingDirectory $cfg.CanonicalRoot -WindowStyle Hidden -PassThru `
      -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $null = $script:child.Handle  # keeps ExitCode readable after exit
    $status.syncChildPid = $script:child.Id
    Save-WatcherStatus
    $deadline = [datetime]::UtcNow.AddMinutes($SyncTimeoutMin)
    $superseded = $null
    while (-not ($finished = $script:child.WaitForExit($SupersessionCheckSec * 1000))) {
      if ([datetime]::UtcNow -ge $deadline) { break }
      $superseded = Get-SyncSupersession -GenerationId $generation -ManifestRevision ([string]$Manifest.source_git_rev) -TransportBundles $TransportBundles
      if ($superseded) { break }
    }
    if (-not $finished) {
      if ($superseded) {
        Write-ChainLog -Config $cfg -Name $logName -Message ("SYNC_SUPERSEDED child={0} gen={1} reason={2}; stopping own child" -f $script:child.Id, $generation.Substring(0, 16), $superseded)
      } else {
        Write-ChainLog -Config $cfg -Name $logName -Message ("SYNC_TIMEOUT child={0} after {1} min; stopping own child" -f $script:child.Id, $SyncTimeoutMin)
      }
      Invoke-NativeQuiet { & taskkill.exe /PID $script:child.Id /T /F } | Out-Null
      $script:child.WaitForExit(30000) | Out-Null
    }
    $script:child.WaitForExit()
    $code = $script:child.ExitCode
    $errorText = (Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue)
    $proven = Test-GenerationAckProven $generation
    if ($superseded -and -not $proven) {
      return [pscustomobject]@{ Ok = $false; Deferred = $false; Superseded = $true; Code = $code; Detail = "SUPERSEDED_$superseded" }
    }
    $detail = if ($proven) { 'REMOTE_ACK_FINALIZED' } elseif (-not $finished) { 'SYNC_TIMEOUT' } elseif ($errorText) { ($errorText.Trim() -split "`n")[-1] } else { "EXIT_$code" }
    return [pscustomobject]@{ Ok = ($proven -and $code -eq 0); Deferred = $false; Superseded = $false; Code = $code; Detail = $detail }
  } finally {
    $script:child = $null
    $status.syncChildPid = $null
    $lease.Dispose()
  }
}

function Invoke-AnalyzerRefresh([string]$GenerationShort) {
  if ($SkipAnalyzerRefresh) { return }
  $runner = Join-Path $PSScriptRoot 'run-analyzer-once.ps1'
  Write-ChainLog -Config $cfg -Name $logName -Message "ANALYZER_REFRESH_START gen=$GenerationShort"
  $process = Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner, '-Reason', "ack-$GenerationShort") `
    -WorkingDirectory $cfg.RepoRoot -WindowStyle Hidden -PassThru
  $null = $process.Handle
  $process.WaitForExit()
  Write-ChainLog -Config $cfg -Name $logName -Message ("ANALYZER_REFRESH_EXIT code={0} gen={1}" -f $process.ExitCode, $GenerationShort)
}

$iteration = 0
try {
  while ($true) {
    $iteration++
    $sleepSec = $PollSec
    $status.lastPollAt = Get-UtcNowIso
    try {
      Invoke-ChainLogRotation -Config $cfg
      $health = Get-FlyHealth $cfg.SourceUrl
      $expected = Publish-UpstreamIdentity -Config $cfg -Health $health
      $status.expected = $expected

      if (Test-SyncLoopGuardHeld) {
        $status.state = 'DEFER_SYNC_LOOP_OWNER'
        $status.detail = 'sync-fly-bot-data-loop.ps1 holds the mirror guard'
      } else {
        if (Test-InProgressHeartbeatAbandoned -Config $cfg -StaleMinutes $StaleInProgressMin) {
          if (Set-SyncHeartbeatTerminalFailure -Config $cfg -Reason 'MIRROR_SYNC_STALE_IN_PROGRESS_TAKEOVER') {
            Write-ChainLog -Config $cfg -Name $logName -Message 'TAKEOVER stale in-progress heartbeat preserved in quarantine and closed'
          }
        }
        $token = Get-AdminToken $cfg.VaultEnv
        $manifest = Get-ManifestPage -Token $token
        $generation = [string]$manifest.inventory_generation_id
        $inventoryStatus = [string]$manifest.inventory_status
        $ackEligible = $manifest.inventory_ack_eligible -eq $true
        $authoritative = $manifest.inventory_authoritative -eq $true
        $manifestRevision = [string]$manifest.source_git_rev
        $acked = Get-AckedGenerations $cfg.DataRoot
        if ($acked.Last) {
          $status.lastAckGeneration = $acked.Last.generation
          $status.lastAckAt = $acked.Last.writtenAt
        }
        $liveManifest = $manifest
        $liveStatus = $inventoryStatus
        $custodyGeneration = if ($inventoryStatus -eq 'CURRENT' -and $ackEligible -and $authoritative) { $null } else { Get-CustodyGeneration $acked.Set }
        if ($custodyGeneration) {
          $retained = Get-GenerationManifest -Token $token -GenerationId $custodyGeneration
          if ($null -eq $retained) {
            Write-ChainLog -Config $cfg -Name $logName -Message ("CUSTODY_EXPIRED gen={0}" -f $custodyGeneration.Substring(0, 16))
          } elseif (-not (Test-RevisionPrefixMatch ([string]$retained.source_git_rev) ([string]$expected.source_git_rev))) {
            Write-ChainLog -Config $cfg -Name $logName -Message ("CUSTODY_SUPERSEDED gen={0} rev={1} fly={2}" -f $custodyGeneration.Substring(0, 16), $retained.source_git_rev, $expected.source_git_rev)
          } elseif ([string]$retained.inventory_generation_id -eq $custodyGeneration) {
            Write-ChainLog -Config $cfg -Name $logName -Message ("CUSTODY_RESUME gen={0} live={1}" -f $custodyGeneration.Substring(0, 16), $liveStatus)
            $manifest = $retained
            $generation = $custodyGeneration
            $inventoryStatus = [string]$manifest.inventory_status
            $ackEligible = $manifest.inventory_ack_eligible -eq $true
            $authoritative = $manifest.inventory_authoritative -eq $true
            $manifestRevision = [string]$manifest.source_git_rev
          }
        }
        $status.inventory = [ordered]@{
          generation = $generation; status = $inventoryStatus; ackEligible = $ackEligible
          authoritative = $authoritative; sourceGitRev = $manifestRevision
          fileCount = $manifest.file_count; totalBytes = $manifest.total_bytes
          failureCode = $(if ($manifest.PSObject.Properties.Name -contains 'inventory_error_class') { $manifest.inventory_error_class } else { $null })
        }
        Write-ChainLog -Config $cfg -Name $logName -Message ("POLL status={0} ack={1} auth={2} gen={3} rev={4} fly={5} lastAck={6}" -f $inventoryStatus, $ackEligible, $authoritative, $(if ($generation.Length -ge 12) { $generation.Substring(0, 12) } else { $generation }), $manifestRevision, $expected.source_git_rev, $(if ($acked.Last) { $acked.Last.generation.Substring(0, 12) } else { 'none' }))

        if ($generation -and $acked.Set.Contains($generation)) {
          $status.state = 'IDLE_ALREADY_ACKED'
          $status.detail = $generation
        } elseif ($inventoryStatus -ne 'CURRENT' -or -not $ackEligible -or -not $authoritative -or $generation -notmatch '^[0-9a-f]{64}$') {
          $status.state = 'WAIT_INVENTORY_NOT_ACK_ELIGIBLE'
          $status.detail = "status=$inventoryStatus ackEligible=$ackEligible authoritative=$authoritative"
          if ($liveStatus -in @('STALE_REVALIDATING', 'BUILDING')) {
            $sleepSec = [Math]::Min($PollSec, $RevalidatingPollSec)
          } elseif (Test-InventoryRefreshNeeded -Manifest $liveManifest -InventoryStatus $liveStatus) {
            $script:lastInventoryRefreshRequest = [datetime]::UtcNow
            $refresh = Request-InventoryRefresh $token
            $status.detail += " refresh=$($refresh.status)"
            Write-ChainLog -Config $cfg -Name $logName -Message ("INVENTORY_REFRESH_REQUESTED live={0} result={1}" -f $liveStatus, $refresh.status)
            $sleepSec = [Math]::Min($PollSec, $RevalidatingPollSec)
          }
        } elseif (-not (Test-RevisionPrefixMatch $manifestRevision ([string]$expected.source_git_rev))) {
          $status.state = 'WAIT_REVISION_DRIFT'
          $status.detail = "manifest=$manifestRevision health=$($expected.source_git_rev)"
        } else {
          $fullRevision = Resolve-FullRevision -RepoRoot $cfg.CanonicalRoot -Revision $manifestRevision
          if (-not $fullRevision) {
            $status.state = 'WAIT_REVISION_UNRESOLVED'
            $status.detail = $manifestRevision
          } else {
            $softCap = [int64]$SoftCapMiB * 1MB
            $missing = if ([int64]$manifest.total_bytes -gt $softCap) { Get-MissingTransferBytes -Manifest $manifest -Token $token } else { [int64]$manifest.total_bytes }
            if ($missing -gt $softCap) {
              $status.state = 'WAIT_SOFT_CAP'
              $status.detail = "missingMiB=$([Math]::Round($missing / 1MB, 1)) softCapMiB=$SoftCapMiB"
            } else {
              $status.state = 'SYNCING'
              $status.detail = $generation
              Save-WatcherStatus
              $bundles = Test-BundleTransportOffered -Token $token -GenerationId $generation
              $result = Invoke-GenerationSync -Manifest $manifest -FullRevision $fullRevision -TransportBundles $bundles
              $status.lastSyncResult = [ordered]@{ at = Get-UtcNowIso; generation = $generation; ok = $result.Ok; deferred = $result.Deferred; exitCode = $result.Code; detail = $result.Detail }
              if ($result.Deferred) {
                $status.state = 'DEFER_ANALYZER_LEASE'
                $status.detail = $result.Detail
              } elseif ($result.Superseded) {
                # Not a sync failure: close the child's receipt without backoff and
                # re-poll promptly so the successor generation starts at once.
                [void](Set-SyncHeartbeatTerminalFailure -Config $cfg -Reason 'MIRROR_SYNC_SUPERSEDED' -BackoffSec 60)
                $status.state = 'SUPERSEDED'
                $status.detail = $result.Detail
                $sleepSec = [Math]::Min($PollSec, $RevalidatingPollSec)
              } elseif ($result.Ok) {
                $status.consecutiveSyncFailures = 0
                $status.state = 'ACKED'
                $status.lastAckGeneration = $generation
                $status.lastAckAt = Get-UtcNowIso
                Write-ChainLog -Config $cfg -Name $logName -Message "ACK_OK gen=$generation"
                Save-WatcherStatus
                Invoke-AnalyzerRefresh $generation.Substring(0, 12)
              } else {
                $status.consecutiveSyncFailures++
                $backoff = [int][Math]::Min($MaxBackoffSec, $PollSec * [Math]::Pow(2, [Math]::Min(10, $status.consecutiveSyncFailures)))
                [void](Set-SyncHeartbeatTerminalFailure -Config $cfg -Reason 'LAPTOP_SYNC_FAILED' -ConsecutiveFailures $status.consecutiveSyncFailures -BackoffSec $backoff)
                throw ("SYNC_FAIL gen={0} code={1} detail={2}" -f $generation.Substring(0, 16), $result.Code, $result.Detail)
              }
            }
          }
        }
      }
      $status.consecutiveFailures = 0
    } catch {
      $status.consecutiveFailures++
      $sleepSec = [int][Math]::Min($MaxBackoffSec, $PollSec * [Math]::Pow(2, [Math]::Min(10, $status.consecutiveFailures - 1)))
      $message = [string]$_.Exception.Message
      if ($message.Length -gt 400) { $message = $message.Substring(0, 400) }
      if ($message -notlike 'SYNC_FAIL*') { $status.state = 'ERROR' }
      else { $status.state = 'SYNC_FAIL' }
      $status.detail = $message
      Write-ChainLog -Config $cfg -Name $logName -Message ("{0} failures={1} backoff={2}s {3}" -f $status.state, $status.consecutiveFailures, $sleepSec, $message)
      try { [void](Set-SyncHeartbeatTerminalFailure -Config $cfg -Reason 'LAPTOP_ACK_WATCHER_ERROR' -ConsecutiveFailures $status.consecutiveFailures -BackoffSec $sleepSec) } catch { }
    }
    $status.nextPollAt = [datetime]::UtcNow.AddSeconds($sleepSec).ToString('o')
    Save-WatcherStatus
    if ($MaxIterations -gt 0 -and $iteration -ge $MaxIterations) { break }
    Start-Sleep -Seconds $sleepSec
  }
} finally {
  if ($script:child -and -not $script:child.HasExited) {
    Invoke-NativeQuiet { & taskkill.exe /PID $script:child.Id /T /F } | Out-Null
  }
  try { [void](Set-SyncHeartbeatTerminalFailure -Config $cfg -Reason 'LAPTOP_ACK_WATCHER_EXITED') } catch { }
  $status.state = 'STOPPED'
  Save-WatcherStatus
  $lock = Read-JsonFile $cfg.WatcherLock
  if ($lock -and [string]$lock.pid -eq [string]$PID) {
    Write-JsonAtomic -Path $cfg.WatcherLock -Value ([ordered]@{
      schema = 'laptop_ack_watcher_lock_v1'; pid = $PID; startedAt = $selfStart; host = $env:COMPUTERNAME
      mutex = $mutexName; released = $true; releasedAt = Get-UtcNowIso
    })
  }
  Write-ChainLog -Config $cfg -Name $logName -Message 'WATCH_STOP'
  Exit-SingleInstance $instance
}
