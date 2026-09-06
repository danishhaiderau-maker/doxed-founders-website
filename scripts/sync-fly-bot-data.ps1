param(
  [string]$SourceUrl = "https://doxed-btc-bot.fly.dev",
  [string]$AdminToken = "",
  [string]$TargetDir = "",
  [string]$PublishAnalyzerReport = "",
  [string[]]$IncludePath = @(),
  [int]$MaxLocalMirrorGiB = 30,
  [string]$ProgressHeartbeatFile = "",
  [string]$ProgressRelayEvidenceJson = "",
  [switch]$ForceFullRefresh,
  [string]$MirroredSourceRevision = "",
  [object]$InitialManifest = $null
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "fly-canonical-lock.ps1")
. (Join-Path $scriptDir "fly-data-paths.ps1")
. (Join-Path $scriptDir "fly-mirror-atomic.ps1")
. (Join-Path $scriptDir "fly-sync-backoff.ps1")
. (Join-Path $scriptDir "fly-sync-file-pacing.ps1")
. (Join-Path $scriptDir "fly-lifecycle-bundle-copy.ps1")
$SourceUrl = Get-CanonicalFlyBotUrl -RequestedUrl $SourceUrl
if (-not $TargetDir) {
  $TargetDir = Get-DoxxedFlyMirrorDir
}
. (Join-Path $scriptDir "home-bot-vault-env.ps1")
if (-not $AdminToken) {
  $AdminToken = Import-CanonicalBotAdminToken
}
if (-not $AdminToken) {
  throw "AdminToken is required (parameter, vault home-bot.env, or BOT_ADMIN_TOKEN)."
}

. (Join-Path $scriptDir "fly-sync-bundles.ps1")
. (Join-Path $scriptDir "fly-forensic-group-verify.ps1")
$targetRoot = [System.IO.Path]::GetFullPath($TargetDir)
New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
if ([string]::IsNullOrWhiteSpace($ProgressHeartbeatFile)) {
  $ProgressHeartbeatFile = Join-Path $targetRoot ".fly-data-sync-loop.heartbeat.json"
}
$statePath = Join-Path $targetRoot ".fly-sync-state.json"
$headers = @{ "X-Bot-Admin-Token" = $AdminToken }
Add-Type -AssemblyName System.Net.Http
$transportAttempts = 5
$resourcePressureCircuitThreshold = 2
$sqliteSnapshotBuildingMaxAttempts = 35
$consecutiveChunkPressureFailures = 0
$manifestTimeoutSec = 90
# A busy paper-runtime can take a little over two minutes to begin streaming a
# checksum-fenced 4 MiB chunk.  The prior 120-second client deadline discarded
# valid HTTP 200 responses observed at ~129 seconds and retried the same offset
# indefinitely.  Keep the request bounded, but leave enough headroom for the
# runtime's bounded worker queue to drain without weakening generation checks.
$chunkTimeoutSec = 240
$ackTimeoutSec = 60
$manifestPageSize = 250
$downloadClient = [System.Net.Http.HttpClient]::new()
$downloadClient.Timeout = [TimeSpan]::FromSeconds($chunkTimeoutSec)
$downloadClient.DefaultRequestHeaders.Add("X-Bot-Admin-Token", $AdminToken)

function Test-DataSyncResourcePressureError {
  param([string]$Message = "")
  return Test-FlySyncResourcePressureMessage -Message $Message
}

function Get-DataSyncRetryDelaySec {
  param(
    [int]$Attempt,
    [bool]$ResourcePressure
  )
  if ($ResourcePressure) {
    # A booting or CPU-starved one-core Fly machine needs a real quiet window;
    # rapid retries compound watchdog starvation and can provoke rc=137.
    return [Math]::Min(60, 15 * [Math]::Max(1, $Attempt))
  }
  return [Math]::Min(15, 2 * [Math]::Max(1, $Attempt))
}

function Invoke-DataSyncJsonRequest {
  param(
    [Parameter(Mandatory = $true)][string]$Stage,
    [Parameter(Mandatory = $true)][string]$Uri,
    [ValidateSet("Get", "Post")][string]$Method = "Get",
    [int]$TimeoutSec = $manifestTimeoutSec,
    [int]$MaxAttempts = $transportAttempts,
    [ValidateRange(1, 900)][int]$MaxElapsedSec = 300,
    [string]$Body = ""
  )
  $requestWatch = [System.Diagnostics.Stopwatch]::StartNew()
  $consecutiveNonBuildingPressureFailures = 0
  for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    $remainingSeconds = [Math]::Floor($MaxElapsedSec - $requestWatch.Elapsed.TotalSeconds)
    if ($remainingSeconds -lt 1) {
      throw "Fly data-sync stage=$Stage failed: REQUEST_DEADLINE_EXCEEDED."
    }
    try {
      $parameters = @{
        Uri = $Uri
        Method = $Method
        Headers = $headers
        TimeoutSec = [int][Math]::Min($TimeoutSec, $remainingSeconds)
      }
      if ($Method -eq "Post") {
        $parameters.ContentType = "application/json"
        $parameters.Body = $Body
      }
      $result = Invoke-RestMethod @parameters
      if ($requestWatch.Elapsed.TotalSeconds -ge $MaxElapsedSec) {
        throw "Fly data-sync stage=$Stage failed: REQUEST_DEADLINE_EXCEEDED."
      }
      Write-Host (
        "[FLY SYNC] stage=$Stage attempt=$attempt/$MaxAttempts " +
        "elapsed_ms=$([Math]::Round($requestWatch.Elapsed.TotalMilliseconds)) status=success"
      )
      $consecutiveNonBuildingPressureFailures = 0
      return $result
    } catch {
      $remainingSeconds = [Math]::Floor($MaxElapsedSec - $requestWatch.Elapsed.TotalSeconds)
      if ($remainingSeconds -lt 1 -or $_.Exception.Message -match 'REQUEST_DEADLINE_EXCEEDED') {
        throw "Fly data-sync stage=$Stage failed: REQUEST_DEADLINE_EXCEEDED."
      }
      $structuredSnapshotBuilding = (
        $Stage -eq "sqlite_snapshot_lease" -and
        [string]$_.ErrorDetails.Message -match '"snapshot_status"\s*:\s*"BUILDING"'
      )
      $structuredSnapshotTerminal = (
        $Stage -eq "sqlite_snapshot_lease" -and
        [string]$_.ErrorDetails.Message -match '"snapshot_status"\s*:\s*"(FAILED|EXPIRED)"'
      )
      if ($Stage -eq "sqlite_snapshot_lease") {
        if ($structuredSnapshotBuilding) {
          $consecutiveNonBuildingPressureFailures = 0
        } else {
          $consecutiveNonBuildingPressureFailures += 1
        }
      }
      $effectiveMaxAttempts = $MaxAttempts
      $snapshotPressureCircuitOpen = (
        $Stage -eq "sqlite_snapshot_lease" -and
        -not $structuredSnapshotBuilding -and
        $consecutiveNonBuildingPressureFailures -ge $resourcePressureCircuitThreshold
      )
      Write-Warning (
        "[FLY SYNC] stage=$Stage attempt=$attempt/$effectiveMaxAttempts " +
        "elapsed_ms=$([Math]::Round($requestWatch.Elapsed.TotalMilliseconds)) " +
        "status=$(if ($structuredSnapshotBuilding) { 'building' } else { 'failed' }) " +
        "error=$($_.Exception.Message)"
      )
      if ($structuredSnapshotTerminal -or $snapshotPressureCircuitOpen -or $attempt -ge $effectiveMaxAttempts) {
        $failureProgress = if ($snapshotPressureCircuitOpen) {
          "$consecutiveNonBuildingPressureFailures/$resourcePressureCircuitThreshold consecutive pressure"
        } elseif ($structuredSnapshotTerminal) {
          "terminal snapshot lease"
        } else {
          "$attempt/$effectiveMaxAttempts"
        }
        throw (
          "Fly data-sync stage=$Stage failed after " +
          "$failureProgress attempt(s): $($_.Exception.Message)"
        )
      }
      if ($structuredSnapshotBuilding) {
        # The server proves that this is the same single-flight background
        # build, so joining it cannot start duplicate SQLite work. Keep raw or
        # proxy 503s on the two-strike pressure circuit below.
        Start-Sleep -Seconds ([Math]::Min(2, $remainingSeconds))
        continue
      }
      $resourcePressure = Test-DataSyncResourcePressureError -Message $_.Exception.Message
      $retryDelaySec = Get-DataSyncRetryDelaySec `
        -Attempt $attempt `
        -ResourcePressure $resourcePressure
      Start-Sleep -Seconds ([Math]::Min($retryDelaySec, $remainingSeconds))
    }
  }
}

function New-DataSyncManifestUri {
  param(
    [switch]$IdentityOnly,
    [string]$Path = "",
    [string]$RefreshNonce = "",
    [string]$GenerationId = "",
    [string]$Cursor = "",
    [int]$PageSize = $manifestPageSize
  )
  # Initial generation refreshes request a fresh full inventory. A hot file is
  # refreshed by exact path, while the final fence compares authority
  # identities only. Neither bounded check rebuilds unrelated metadata.
  $identityQuery = if ($IdentityOnly) { "&identity_only=1" } else { "" }
  $pathQuery = if ($Path) {
    "&path=" + [uri]::EscapeDataString($Path)
  } else { "" }
  $generationQuery = if ($GenerationId) {
    "&generation_id=" + [uri]::EscapeDataString($GenerationId)
  } else { "" }
  $cursorQuery = if ($Cursor) {
    "&cursor=" + [uri]::EscapeDataString($Cursor)
  } else { "" }
  $pageQuery = if ($GenerationId -or (-not $IdentityOnly -and -not $Path)) {
    "&page_size=$PageSize"
  } else { "" }
  if ($GenerationId -and -not $IdentityOnly) {
    return "$base/api/data-sync/manifest?paged=1$generationQuery$cursorQuery$pageQuery"
  }
  if (-not $RefreshNonce) { $RefreshNonce = [guid]::NewGuid().ToString("N") }
  return (
    "$base/api/data-sync/manifest?fresh=1$identityQuery$pathQuery$generationQuery$pageQuery&nonce=" +
    [uri]::EscapeDataString($RefreshNonce)
  )
}

function Get-CompleteDataSyncManifest {
  param(
    [Parameter(Mandatory = $true)]$FirstPage
  )
  if ([string]$FirstPage.schema -ne "fly_runtime_incremental_sync_v1") {
    throw "Unexpected Fly sync manifest schema."
  }
  if ([string]$FirstPage.inventory_status -ne "CURRENT") {
    throw (
      "Fly sync manifest inventory is not CURRENT " +
      "(status=$([string]$FirstPage.inventory_status) " +
      "build=$([string]$FirstPage.inventory_build_status) " +
      "error=$([string]$FirstPage.inventory_error))."
    )
  }
  $generationId = [string]$FirstPage.inventory_generation_id
  $inventorySha256 = [string]$FirstPage.inventory_sha256
  $inventoryGeneratedAt = [string]$FirstPage.inventory_generated_at
  if (
    $generationId -notmatch '^[0-9a-f]{64}$' -or
    $inventorySha256 -notmatch '^[0-9a-f]{64}$' -or
    $generationId -cne $inventorySha256 -or
    -not $inventoryGeneratedAt
  ) {
    throw "Fly manifest is missing its validated immutable generation identity."
  }

  $expectedFileCount = [int64]$FirstPage.file_count
  $expectedTotalBytes = [int64]$FirstPage.total_bytes
  $expectedPageCount = [int]$FirstPage.manifest_page_count
  if ($expectedFileCount -lt 0 -or $expectedTotalBytes -lt 0 -or $expectedPageCount -lt 1) {
    throw "Fly manifest generation totals are invalid."
  }
  $rows = [System.Collections.Generic.List[object]]::new()
  $pageReceipts = [System.Collections.Generic.List[object]]::new()
  $paths = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::Ordinal
  )
  $cursors = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::Ordinal
  )
  $page = $FirstPage
  $expectedIndex = 0
  $expectedCursor = ""
  while ($true) {
    if (
      [string]$page.schema -ne "fly_runtime_incremental_sync_v1" -or
      [string]$page.inventory_status -ne "CURRENT" -or
      [string]$page.inventory_generation_id -cne $generationId -or
      [string]$page.inventory_sha256 -cne $inventorySha256 -or
      [string]$page.inventory_generated_at -cne $inventoryGeneratedAt -or
      [int64]$page.file_count -ne $expectedFileCount -or
      [int64]$page.total_bytes -ne $expectedTotalBytes -or
      [int]$page.manifest_page_count -ne $expectedPageCount -or
      [int]$page.manifest_page_index -ne $expectedIndex -or
      [string]$page.manifest_page_cursor -cne $expectedCursor -or
      [string]$page.manifest_page_sha256 -notmatch '^[0-9a-f]{64}$'
    ) {
      throw "Fly manifest page identity or sequence changed during aggregation."
    }
    $pageRows = @($page.files)
    if ([int]$page.manifest_page_file_count -ne $pageRows.Count) {
      throw "Fly manifest page file count does not match its rows."
    }
    $pageBytes = [int64]0
    foreach ($row in $pageRows) {
      $path = [string]$row.path
      if (-not $path -or -not $paths.Add($path)) {
        throw "Fly manifest contains a duplicate or empty path across pages."
      }
      $pageBytes += [int64]$row.size
      $rows.Add($row)
    }
    if ($pageBytes -ne [int64]$page.manifest_page_total_bytes) {
      throw "Fly manifest page byte total does not match its rows."
    }
    $pageReceipts.Add([pscustomobject]@{
      page_index = $expectedIndex
      page_sha256 = [string]$page.manifest_page_sha256
      file_count = $pageRows.Count
      paths = @($pageRows | ForEach-Object { [string]$_.path })
    })

    $nextCursor = [string]$page.manifest_next_cursor
    $isLastPage = [bool]$page.manifest_is_last_page
    if ($isLastPage) {
      if ($nextCursor) { throw "Fly manifest final page unexpectedly has a cursor." }
      break
    }
    if (-not $nextCursor -or -not $cursors.Add($nextCursor)) {
      throw "Fly manifest next cursor is missing or duplicated."
    }
    $expectedIndex += 1
    if ($expectedIndex -ge $expectedPageCount) {
      throw "Fly manifest emitted more pages than declared."
    }
    $expectedCursor = $nextCursor
    $page = Invoke-DataSyncJsonRequest `
      -Stage "manifest_page_$expectedIndex" `
      -Uri (New-DataSyncManifestUri `
        -GenerationId $generationId `
        -Cursor $nextCursor `
        -PageSize $manifestPageSize) `
      -TimeoutSec $manifestTimeoutSec
  }
  if (
    ($expectedIndex + 1) -ne $expectedPageCount -or
    [int64]$rows.Count -ne $expectedFileCount -or
    [int64](($rows | Measure-Object -Property size -Sum).Sum) -ne $expectedTotalBytes
  ) {
    throw "Fly manifest aggregation is incomplete."
  }

  $complete = [ordered]@{}
  foreach ($property in $FirstPage.PSObject.Properties) {
    $complete[$property.Name] = $property.Value
  }
  $complete.files = @($rows)
  $complete.manifest_pages_complete = $true
  $complete.manifest_pages_aggregated = $expectedPageCount
  $complete.manifest_page_receipts = @($pageReceipts)
  return [pscustomobject]$complete
}

function Get-DataSyncManifestIdentityValue {
  param(
    [Parameter(Mandatory = $true)]$Manifest,
    [Parameter(Mandatory = $true)][string[]]$Names
  )
  foreach ($name in $Names) {
    if ($Manifest.PSObject.Properties.Name -contains $name) {
      $value = $Manifest.$name
      if ($null -ne $value -and -not [string]::IsNullOrWhiteSpace([string]$value)) {
        return [pscustomobject]@{ Name = $name; Value = $value; Present = $true }
      }
    }
  }
  return [pscustomobject]@{ Name = $null; Value = $null; Present = $false }
}

function Assert-DataSyncManifestIdentity {
  param(
    [Parameter(Mandatory = $true)]$Initial,
    [Parameter(Mandatory = $true)]$Final
  )
  if ([string]$Final.schema -ne "fly_runtime_incremental_sync_v1") {
    throw "Unexpected Fly sync manifest schema during final identity fence."
  }
  $identityFields = @(
    @{ Label = "source_git_rev"; Names = @("source_git_rev"); Numeric = $false; Required = $true },
    @{ Label = "tile_registry_signature"; Names = @("tile_registry_signature"); Numeric = $false; Required = $true },
    @{ Label = "fresh_collection_signal_ts"; Names = @("fresh_collection_signal_ts"); Numeric = $true; Required = $true },
    # Servers may publish the collection identity under one of these explicit
    # epoch names. Compare the logical value whenever either manifest exposes
    # it; absence from both remains backwards compatible.
    @{ Label = "dataset_epoch"; Names = @("dataset_epoch", "collection_epoch_id", "epoch_id", "generation_epoch"); Numeric = $false; Required = $false }
  )
  foreach ($field in $identityFields) {
    $before = Get-DataSyncManifestIdentityValue -Manifest $Initial -Names $field.Names
    $after = Get-DataSyncManifestIdentityValue -Manifest $Final -Names $field.Names
    if ($field.Required -and (-not $before.Present -or -not $after.Present)) {
      throw "Fly sync final identity fence is missing required field $($field.Label)."
    }
    if ($before.Present -ne $after.Present) {
      throw "Fly sync final identity fence changed field availability for $($field.Label)."
    }
    if (-not $before.Present) { continue }
    $matches = if ($field.Numeric) {
      [double]$before.Value -eq [double]$after.Value
    } else {
      [string]$before.Value -ceq [string]$after.Value
    }
    if (-not $matches) {
      throw "Fly sync final identity fence mismatch for $($field.Label)."
    }
  }
  $generationId = [string]$Initial.inventory_generation_id
  if ($generationId) {
    if (
      $generationId -notmatch '^[0-9a-f]{64}$' -or
      [string]$Final.inventory_generation_id -cne $generationId -or
      $Final.inventory_generation_available -ne $true
    ) {
      throw "Fly sync final identity fence lost the immutable inventory generation."
    }
  }
}

function Write-SyncProgressHeartbeat {
  param(
    [Parameter(Mandatory = $true)][string]$Phase,
    [string]$RelativePath = "",
    [int]$FileIndex = 0,
    [int]$FileCount = 0,
    [int64]$FileBytes = 0,
    [int64]$RemoteBytes = 0,
    [switch]$Completed
  )
  if ([string]::IsNullOrWhiteSpace($ProgressHeartbeatFile)) { return }
  $target = [System.IO.Path]::GetFullPath($ProgressHeartbeatFile)
  $relayEvidence = $null
  if (-not [string]::IsNullOrWhiteSpace($ProgressRelayEvidenceJson)) {
    try { $relayEvidence = $ProgressRelayEvidenceJson | ConvertFrom-Json }
    catch { $relayEvidence = $null }
  } elseif (Test-Path -LiteralPath $target -PathType Leaf) {
    try {
      $previousHeartbeat = Get-Content -LiteralPath $target -Raw | ConvertFrom-Json
      if ($previousHeartbeat.PSObject.Properties.Name -contains "relayEvidence") {
        $relayEvidence = $previousHeartbeat.relayEvidence
      }
    } catch { $relayEvidence = $null }
  }
  $observedRevision = $(if ($manifest -and $manifest.PSObject.Properties.Name -contains "source_git_rev") { [string]$manifest.source_git_rev } else { "" })
  $requestedRevision = [string]$MirroredSourceRevision
  $revisionMatches = [bool](
    $requestedRevision -and
    $observedRevision -and
    (
      $requestedRevision.Equals($observedRevision, [StringComparison]::OrdinalIgnoreCase) -or
      $requestedRevision.StartsWith($observedRevision, [StringComparison]::OrdinalIgnoreCase) -or
      $observedRevision.StartsWith($requestedRevision, [StringComparison]::OrdinalIgnoreCase)
    )
  )
  $progress = [ordered]@{
    ok = $true
    inProgress = -not [bool]$Completed
    phase = $Phase
    updatedAt = [DateTimeOffset]::UtcNow.ToString("o")
    # Keep the canonical heartbeat timestamp populated while a long atomic
    # sync is in progress.  Supervisors written against the completed
    # heartbeat contract read syncedAt; dropping it made an active download
    # look infinitely stale until the final heartbeat replaced this record.
    syncedAt = [DateTimeOffset]::UtcNow.ToString("o")
    source = $SourceUrl
    sourceRevision = $(if ($MirroredSourceRevision) { $MirroredSourceRevision } else { $null })
    observedSourceRevision = $(if ($observedRevision) { $observedRevision } else { $null })
    # Independent prospective provenance. The authenticated Fly manifest
    # describes the code actually deployed with the authoritative collector.
    # Older receipts intentionally have no such field and remain UNKNOWN.
    deployedRevision = $(if ($observedRevision) { $observedRevision } else { $null })
    mirroredSourceRevision = $(if ($MirroredSourceRevision) { $MirroredSourceRevision } else { $null })
    revisionParity = $(
      if (-not $MirroredSourceRevision -or -not $manifest -or -not $manifest.source_git_rev) { "UNKNOWN" }
      elseif ($revisionMatches) { "MATCH" }
      else { "MISMATCH" }
    )
    tileRegistrySignature = $(if ($manifest -and $manifest.PSObject.Properties.Name -contains "tile_registry_signature") { [string]$manifest.tile_registry_signature } else { $null })
    currentFile = $RelativePath
    fileIndex = $FileIndex
    fileCount = $FileCount
    fileBytes = $FileBytes
    remoteBytes = $RemoteBytes
    relayEvidence = $relayEvidence
  }
  $temporary = "$target.progress-$PID-$([Guid]::NewGuid().ToString('N'))"
  $backup = "$temporary.replace-backup"
  $encoding = New-Object System.Text.UTF8Encoding($false)
  try {
    [System.IO.File]::WriteAllText(
      $temporary,
      (($progress | ConvertTo-Json -Depth 8) + [Environment]::NewLine),
      $encoding
    )
    if (Test-Path -LiteralPath $target -PathType Leaf) {
      Invoke-MirrorAtomicReplace `
        -Candidate $temporary `
        -Destination $target `
        -Backup $backup `
        -Attempts 12
    } else {
      [System.IO.File]::Move($temporary, $target)
    }
  } finally {
    if (Test-Path -LiteralPath $temporary) {
      Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $backup) {
      Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    }
  }
}

$syncState = @{}
if (Test-Path -LiteralPath $statePath) {
  try {
    $loaded = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    foreach ($property in $loaded.PSObject.Properties) {
      $syncState[$property.Name] = $property.Value
    }
  } catch {
    throw "Existing sync state is unreadable: $statePath"
  }
}

function Save-SyncState {
  # A unique same-directory temporary file makes replacement atomic even if a
  # stale worker is still unwinding. A fixed `.tmp` name let one worker move a
  # different worker's file and caused the desktop analyzer sync to go dark.
  $stateTmp = "$statePath.$PID.$([guid]::NewGuid().ToString('N')).tmp"
  $stateBackup = "$stateTmp.bak"
  try {
    $syncState | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $stateTmp -Encoding UTF8
    if (Test-Path -LiteralPath $statePath) {
      # Move-Item -Force is not an atomic overwrite on Windows and can fail
      # with "Cannot create a file when that file already exists". Replace the
      # complete temporary file in one filesystem operation so the analyzer can
      # never observe a partially-written JSON state document.
      Invoke-MirrorAtomicReplace `
        -Candidate $stateTmp `
        -Destination $statePath `
        -Backup $stateBackup `
        -Attempts 12
    } else {
      [System.IO.File]::Move($stateTmp, $statePath)
    }
  } finally {
    Remove-Item -LiteralPath $stateTmp -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stateBackup -Force -ErrorAction SilentlyContinue
  }
}

$base = $SourceUrl.TrimEnd("/")
function Set-SqliteSnapshotLease {
  param([Parameter(Mandatory = $true)]$Row)
  $rel = [string]$Row.path
  # One identity is reused by every transport retry. This makes a lost HTTP
  # response an idempotent poll of the same immutable snapshot flight.
  $requestId = [guid]::NewGuid().ToString("N")
  $lease = Invoke-DataSyncJsonRequest `
    -Stage "sqlite_snapshot_lease" `
    -Uri ("$base/api/data-sync/sqlite-snapshot?path=$([uri]::EscapeDataString($rel))" +
      "&request_id=$requestId" +
      "&inventory_generation_id=$inventoryGenerationId" +
      "&inventory_sha256=$inventorySha256" +
      "&source_physical_size=$([int64]$Row.physical_size)" +
      "&source_mtime_ns=$([int64]$Row.mtime_ns)" +
      "&source_inode=$([int64]$Row.inode)" +
      "&source_consistency_mode=$([string]$Row.consistency_mode)") `
    -TimeoutSec $manifestTimeoutSec `
    -MaxAttempts $sqliteSnapshotBuildingMaxAttempts
  if (
    $lease.schema -ne "fly_runtime_sqlite_snapshot_lease_v1" -or
    [string]$lease.path -ne $rel -or
    [string]$lease.request_id -cne $requestId -or
    [string]$lease.build_id -notmatch '^[0-9a-f]{32}$' -or
    [string]$lease.inventory_generation_id -cne $inventoryGenerationId -or
    [string]$lease.inventory_sha256 -cne $inventorySha256 -or
    -not [string]$lease.snapshot_id -or
    [int64]$lease.snapshot_size -lt 0 -or
    [string]$lease.snapshot_sha256 -notmatch '^[0-9a-f]{64}$'
  ) {
    throw "Invalid Fly SQLite snapshot lease for $rel."
  }
  $Row | Add-Member -NotePropertyName snapshot_id -NotePropertyValue ([string]$lease.snapshot_id) -Force
  $Row | Add-Member -NotePropertyName snapshot_request_id -NotePropertyValue $requestId -Force
  $Row | Add-Member -NotePropertyName snapshot_build_id -NotePropertyValue ([string]$lease.build_id) -Force
  $Row | Add-Member -NotePropertyName snapshot_inventory_generation_id -NotePropertyValue ([string]$lease.inventory_generation_id) -Force
  $Row | Add-Member -NotePropertyName snapshot_inventory_sha256 -NotePropertyValue ([string]$lease.inventory_sha256) -Force
  $Row | Add-Member -NotePropertyName snapshot_size -NotePropertyValue ([int64]$lease.snapshot_size) -Force
  $Row | Add-Member -NotePropertyName snapshot_sha256 -NotePropertyValue ([string]$lease.snapshot_sha256) -Force
  $Row.size = [int64]$lease.snapshot_size
  $Row.physical_size = [int64]$lease.snapshot_size
}
# The long-running loop already performs an authenticated, retry-bounded
# manifest preflight before deciding whether this expensive child sync is due.
# PowerShell invokes this script in-process, so reuse that exact object instead
# of immediately amplifying load with an identical second request. Standalone
# callers retain the authenticated fetch below. Per-file generation fences and
# the final authenticated acknowledgement remain authoritative for atomicity.
$manifest = $InitialManifest
if ($null -eq $manifest) {
  $manifestRefreshNonce = [guid]::NewGuid().ToString("N")
  $manifest = Invoke-DataSyncJsonRequest `
    -Stage "manifest_initial" `
    -Uri (New-DataSyncManifestUri `
      -RefreshNonce $manifestRefreshNonce `
      -PageSize $manifestPageSize) `
    -TimeoutSec $manifestTimeoutSec
}
$manifest = Get-CompleteDataSyncManifest -FirstPage $manifest
$inventorySha256 = [string]$manifest.inventory_sha256
$inventoryGenerationId = [string]$manifest.inventory_generation_id
$inventoryGeneratedAt = [string]$manifest.inventory_generated_at
if (
  $inventorySha256 -notmatch '^[0-9a-f]{64}$' -or
  $inventoryGenerationId -cne $inventorySha256 -or
  -not $inventoryGeneratedAt -or
  $manifest.manifest_pages_complete -ne $true
) {
  throw "Fly manifest is missing its validated inventory generation identity."
}

$ackRows = [System.Collections.Generic.List[object]]::new()
# Keep each request burst and its cadence bounded for Fly's shared one-core
# paper runtime. The throttle grows after boot/502/503 pressure and recovers
# gradually after successful chunks, so a large resumable mirror pass yields
# CPU to health, trading and watchdog work throughout the copy.
$chunkLimit = 1MB
$baseInterChunkThrottleMs = 1000
$baseInterFileThrottleMs = 1500
$maxAdaptiveThrottleMs = 5000
$adaptiveThrottleMs = $baseInterChunkThrottleMs
$selectedFiles = @($manifest.files)
$selectedFiles = @(
  $selectedFiles | Sort-Object `
    @{ Expression = { if ([string]$_.consistency_mode -eq "sqlite_snapshot_v1") { 0 } else { 1 } } }, `
    @{ Expression = { [string]$_.path } }
)
# SQLite snapshot rows are short-lived authenticated leases.  Download them
# before ordinary files so a large revision refresh cannot consume the lease
# while validating hundreds of unrelated hot documents.

# Local retirement is separate from downloading. Default to retaining every
# existing raw file; only explicit laptop opt-in enables archive-first removal.
# This flag never grants Fly source-cleanup authority.
$localRetirementEnabled = $env:FLY_SYNC_LOCAL_RETIREMENT_ENABLED -ceq '1'
$localRetirementStatus = if ($localRetirementEnabled) { 'ENABLED_NO_ELIGIBLE_FILES' } else { 'DISABLED_SOURCE_RETAINED' }
$staleRotationFiles = 0
$staleRotationBytes = [int64]0
if ($localRetirementEnabled) {
# Fly is the authoritative owner of raw research streams. A top-level raw file
# absent from its authenticated manifest may be retired locally only after
# archive-first verification below.
$manifestPaths = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($manifestRow in @($manifest.files)) {
  [void]$manifestPaths.Add(([string]$manifestRow.path).Replace("\", "/"))
}
$staleArchiveRoot = Join-Path $targetRoot "archive\sync-retired"
$canonicalLocalFiles = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($canonicalLocalName in @("canonical_dataset_manifest.jsonl")) {
  [void]$canonicalLocalFiles.Add($canonicalLocalName)
}
foreach ($candidate in @(Get-ChildItem -LiteralPath $targetRoot -File -Force -ErrorAction SilentlyContinue)) {
  if ($candidate.Name -notmatch '\.(jsonl|log|csv)(?:\.\d+)?$') { continue }
  $resolvedCandidate = [System.IO.Path]::GetFullPath($candidate.FullName)
  if (-not $resolvedCandidate.StartsWith($targetRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Local raw research file escaped the mirror root: $resolvedCandidate"
  }
  if ($canonicalLocalFiles.Contains($candidate.Name)) { continue }
  if ($manifestPaths.Contains($candidate.Name)) { continue }
  $staleRotationBytes += [int64]$candidate.Length
  $stamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssfffZ")
  $archiveDir = Join-Path $staleArchiveRoot $stamp
  New-Item -ItemType Directory -Path $archiveDir -Force | Out-Null
  $archivePath = Join-Path $archiveDir $candidate.Name
  $resolvedArchive = [System.IO.Path]::GetFullPath($archivePath)
  if (-not $resolvedArchive.StartsWith(($targetRoot.TrimEnd('\') + '\'), [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Local archive path escaped the mirror root: $resolvedArchive"
  }
  if (Test-Path -LiteralPath $archivePath) { throw "Archive collision: $archivePath" }
  # Archive-first means copy, verify both sides, then remove the local mirror
  # source. A rename alone cannot prove recoverability after storage faults or
  # concurrent mutation.
  $sourceSize = [int64]$candidate.Length
  $sourceSha256 = (Get-FileHash -LiteralPath $resolvedCandidate -Algorithm SHA256).Hash.ToLowerInvariant()
  $temporaryArchive = "$resolvedArchive.$([guid]::NewGuid().ToString('N')).tmp"
  [System.IO.File]::Copy($resolvedCandidate, $temporaryArchive, $false)
  $copiedSize = [int64](Get-Item -LiteralPath $temporaryArchive).Length
  $copiedSha256 = (Get-FileHash -LiteralPath $temporaryArchive -Algorithm SHA256).Hash.ToLowerInvariant()
  $stableSourceSize = [int64](Get-Item -LiteralPath $resolvedCandidate).Length
  $stableSourceSha256 = (Get-FileHash -LiteralPath $resolvedCandidate -Algorithm SHA256).Hash.ToLowerInvariant()
  if (
    $copiedSize -ne $sourceSize -or $copiedSha256 -cne $sourceSha256 -or
    $stableSourceSize -ne $sourceSize -or $stableSourceSha256 -cne $sourceSha256
  ) {
    Remove-Item -LiteralPath $temporaryArchive -Force -ErrorAction SilentlyContinue
    throw "Archive verification failed; source retained: $($candidate.Name)"
  }
  [System.IO.File]::Move($temporaryArchive, $resolvedArchive)
  $promotedSha256 = (Get-FileHash -LiteralPath $resolvedArchive -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($promotedSha256 -cne $sourceSha256) {
    throw "Promoted archive verification failed; source retained: $($candidate.Name)"
  }
  Remove-Item -LiteralPath $resolvedCandidate -Force -ErrorAction Stop
  $receipt = [ordered]@{
    schema = "canonical_research_cleanup_receipt_v1"
    archived_at = [DateTimeOffset]::UtcNow.ToString("o")
    reason = "ABSENT_FROM_AUTHENTICATED_FLY_MANIFEST"
    source_relative = $candidate.Name
    archive_relative = $archivePath.Substring($targetRoot.Length).TrimStart('\').Replace('\', '/')
    archive_sha256 = $sourceSha256
    archive_bytes = $sourceSize
    verification = "COPY_AND_SOURCE_STABILITY_SHA256_VERIFIED_BEFORE_REMOVAL"
    recoverable = $true
  }
  $receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$archivePath.receipt.json" -Encoding UTF8
  [void]$syncState.Remove($candidate.Name)
  $staleRotationFiles += 1
}
if ($staleRotationFiles -gt 0) {
  $localRetirementStatus = 'ARCHIVED_AND_VERIFIED'
  Write-Host (
    "Archived $staleRotationFiles stale local Fly research file(s), " +
    "$staleRotationBytes byte(s), absent from the authenticated manifest."
  )
  Save-SyncState
}
}
Write-Host "[FLY SYNC] stage=local_retirement status=$localRetirementStatus files=$staleRotationFiles bytes=$staleRotationBytes"
if ($IncludePath.Count -gt 0) {
  $selectedFiles = @(
    $selectedFiles | Where-Object { [string]$_.path -in $IncludePath }
  )
  if ($selectedFiles.Count -ne $IncludePath.Count) {
    throw "One or more IncludePath entries were not present in the Fly manifest."
  }
}
# Hard admission guard. Retention may remove only analyzer-acknowledged,
# fingerprinted closed rotations; this downloader never deletes data to make
# room and refuses a sync whose projected growth crosses the local cap.
$capBytes = [int64]$MaxLocalMirrorGiB * 1GB
$currentMirrorBytes = [int64](
  (Get-ChildItem -LiteralPath $targetRoot -Recurse -File -Force -ErrorAction SilentlyContinue |
    Measure-Object -Property Length -Sum).Sum
)
$incomingGrowth = [int64]0
foreach ($row in $selectedFiles) {
  $candidate = Join-Path $targetRoot (([string]$row.path) -replace "/", "\")
  $existingBytes = if (Test-Path -LiteralPath $candidate) {
    [int64](Get-Item -LiteralPath $candidate).Length
  } else { 0 }
  $incomingGrowth += [Math]::Max([int64]0, ([int64]$row.size - $existingBytes))
}
if (($currentMirrorBytes + $incomingGrowth) -gt $capBytes) {
  throw (
    "Local Fly mirror hard cap would be exceeded: current=$currentMirrorBytes " +
    "incoming=$incomingGrowth cap=$capBytes. Analyzer retention must produce " +
    "fingerprinted receipts and free eligible closed rotations before sync resumes."
  )
}
$selectedFileCount = @($selectedFiles).Count
$selectedFileIndex = 0
$pendingStateWrites = 0
if ($env:FLY_SYNC_TRANSPORT_BUNDLES -eq '1') {
  # Explicit canary opt-in. Failure is surfaced to the normal loop/backoff;
  # never silently restart an hours-long serial transfer on a broken batch.
  $bundleResult = Receive-FlyTransportBundles -Manifest $manifest -SourceUrl $SourceUrl `
    -AdminToken $AdminToken -TargetRoot $targetRoot `
    -ClientScript (Join-Path $scriptDir 'fly-sync-bundle-client.py') `
    -SyncState $syncState -SaveCheckpoint { Save-SyncState } `
    -Progress { param($files, $phase) Write-Host "[FLY SYNC] stage=$phase files=$files ack=pending" }
  Write-Host "[FLY SYNC] stage=bundle_complete files=$($bundleResult.Files) original_manifest_ack=pending"
}
foreach ($row in $selectedFiles) {
  $selectedFileIndex += 1
  $rel = [string]$row.path
  if (-not $rel -or $rel.StartsWith(".") -or $rel.Split("/") -contains "..") {
    throw "Unsafe relative path from Fly manifest: $rel"
  }
  $local = [System.IO.Path]::GetFullPath((Join-Path $targetRoot ($rel -replace "/", "\")))
  if (-not $local.StartsWith($targetRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Fly manifest path escaped the mirror root: $rel"
  }
  $parent = Split-Path -Parent $local
  New-Item -ItemType Directory -Path $parent -Force | Out-Null

  $previous = $syncState[$rel]
  $remoteSize = [int64]$row.size
  $remoteInode = [int64]$row.inode
  $localSize = if (Test-Path -LiteralPath $local) {
    [int64](Get-Item -LiteralPath $local).Length
  } else { 0 }
  $extension = [System.IO.Path]::GetExtension($local).ToLowerInvariant()
  $opaqueQuarantineEvidence = $rel.Replace("\", "/").ToLowerInvariant().StartsWith(
    "corrupt_evidence_quarantine/",
    [System.StringComparison]::Ordinal
  )
  $appendOnly = $extension -in @(".jsonl", ".csv", ".log", ".txt")
  $consistencyMode = [string]$(if ($row.consistency_mode) { $row.consistency_mode } else { "strict_generation_v1" })
  $forensicOriginal = $null -ne $row.forensic_component
  if ($rel -cmatch '^v3/lifecycle_bundle_index/recovery-quarantine/[0-9a-f]{16}/lifecycle_index\.sqlite3(?:-wal|-shm)?$' -and -not $forensicOriginal) {
    throw "FORENSIC_BINDING_MISSING: $rel"
  }
  if ($forensicOriginal -and $consistencyMode -ne "strict_generation_v1") {
    throw "FORENSIC_SNAPSHOT_SUBSTITUTION_FORBIDDEN: $rel"
  }
  if ($consistencyMode -eq "sqlite_snapshot_v1") {
    # Acquire exactly one short-lived lease immediately before this DB is
    # compared/downloaded; ordinary manifest polling remains metadata-only.
    Set-SqliteSnapshotLease -Row $row
    $remoteSize = [int64]$row.size
  }
  # A revision refresh must walk and revalidate the entire manifest, but it
  # must remain resumable.  Inode/mtime/size (or append-prefix inode/size)
  # identifies the exact Fly volume generation independently of application
  # code revision.  Discarding that proof when ForceFullRefresh was set made a
  # restarted 300+ file refresh download every completed file again.  Reuse an
  # already-verified generation; any changed generation still fails this test
  # and is downloaded and atomically replaced below.
  $sameGeneration = if ($appendOnly) {
    (
      $previous -and
      [int64]$previous.inode -eq $remoteInode -and
      $localSize -le $remoteSize
    )
  } else {
    (
      $previous -and
      [int64]$previous.inode -eq $remoteInode -and
      [int64]$previous.mtime_ns -eq [int64]$row.mtime_ns -and
      [int64]$previous.size -eq $remoteSize -and
      $localSize -eq $remoteSize
    )
  }
  $verifiedFullSha256 = $null
  if ($opaqueQuarantineEvidence -and $sameGeneration -and $localSize -eq $remoteSize) {
    $storedFullSha256 = [string]$previous.full_sha256
    if ($storedFullSha256 -notmatch '^[0-9a-fA-F]{64}$') {
      $sameGeneration = $false
    } else {
      $verifiedFullSha256 = (Get-FileHash -LiteralPath $local -Algorithm SHA256).Hash.ToLowerInvariant()
      if ($verifiedFullSha256 -ne $storedFullSha256.ToLowerInvariant()) {
        $sameGeneration = $false
        $verifiedFullSha256 = $null
      }
    }
  }
  $downloadedGeneration = $false
  if ($forensicOriginal -and $sameGeneration -and $localSize -eq $remoteSize) {
    try { Assert-FlyForensicPayload -Row $row -Path $local }
    catch { $sameGeneration = $false }
  }
  if (-not ($sameGeneration -and $localSize -eq $remoteSize)) {
  # Assemble and validate a complete same-directory candidate. Never append
  # directly to a file that the analyzer can read: doing so exposed a partial
  # JSONL record between chunk writes. The existing mirror remains untouched
  # until the candidate is complete and atomically replaces it.
  $candidate = "$local.$PID.$([guid]::NewGuid().ToString('N')).download"
  $candidateBackup = "$candidate.replace-backup"
  try {
    Write-SyncProgressHeartbeat `
      -Phase "file_start" `
      -RelativePath $rel `
      -FileIndex $selectedFileIndex `
      -FileCount $selectedFileCount `
      -FileBytes $localSize `
      -RemoteBytes $remoteSize
    $fullReplaceRetry = $false
    $generationRefreshCount = 0
    # Revision refreshes frequently encounter small atomically-replaced JSON
    # reports whose manifest generation is obsolete before the first request.
    # The no-fence endpoint path already proves one exact before/after
    # generation and returns its identity. Use that verified one-read path
    # immediately for small strict files instead of spending three manifest
    # refreshes discovering that the report is hot.
    $atomicSnapshotFallback = (
      $ForceFullRefresh -and
      -not $opaqueQuarantineEvidence -and
      $consistencyMode -eq "strict_generation_v1" -and
      $remoteSize -le $chunkLimit
    )
    while ($true) {
      $refreshGeneration = $false
      $chunkReceipts = [System.Collections.Generic.List[object]]::new()
      if (
        $sameGeneration -and
        -not $opaqueQuarantineEvidence -and
        -not $fullReplaceRetry -and
        -not $atomicSnapshotFallback -and
        (Test-Path -LiteralPath $local)
      ) {
        [System.IO.File]::Copy($local, $candidate, $true)
        $offset = $localSize
      } else {
        [System.IO.File]::WriteAllBytes($candidate, [byte[]]::new(0))
        $offset = 0
      }
      while ($offset -lt $remoteSize) {
      $limit = if ($atomicSnapshotFallback) {
        $chunkLimit
      } else {
        [Math]::Min($chunkLimit, $remoteSize - $offset)
      }
      $chunkComplete = $false
      for ($attempt = 1; $attempt -le $transportAttempts -and -not $chunkComplete; $attempt++) {
        $tmp = Join-Path $env:TEMP ("fly-sync-" + [guid]::NewGuid().ToString("N") + ".part")
        try {
          $encoded = [uri]::EscapeDataString($rel)
          $expectedPhysicalSize = [int64]$(if ($null -ne $row.physical_size) { $row.physical_size } else { $row.size })
          $expectedMtime = [int64]$row.mtime_ns
          $expectedInode = [int64]$row.inode
          $expectedPublishedSize = [int64]$row.size
          $requestUrl = "$base/api/data-sync/file?path=$encoded&offset=$offset&limit=$limit"
          $requestUrl += "&ack_inventory_sha256=$inventorySha256"
          if (-not $atomicSnapshotFallback) {
            $requestUrl += (
              "&expected_physical_size=$expectedPhysicalSize&expected_published_size=$expectedPublishedSize" +
              "&expected_mtime_ns=$expectedMtime&expected_inode=$expectedInode&consistency_mode=$consistencyMode"
            )
            if ($consistencyMode -eq "sqlite_snapshot_v1") {
              $requestUrl += (
                "&snapshot_id=$([uri]::EscapeDataString([string]$row.snapshot_id))" +
                "&snapshot_request_id=$([string]$row.snapshot_request_id)" +
                "&snapshot_build_id=$([string]$row.snapshot_build_id)" +
                "&inventory_generation_id=$([string]$row.snapshot_inventory_generation_id)" +
                "&inventory_sha256=$([string]$row.snapshot_inventory_sha256)" +
                "&expected_snapshot_size=$([int64]$row.snapshot_size)" +
                "&expected_snapshot_sha256=$([string]$row.snapshot_sha256)"
              )
            }
          }
          $chunkRequestWatch = [System.Diagnostics.Stopwatch]::StartNew()
          $response = $downloadClient.GetAsync($requestUrl).GetAwaiter().GetResult()
          $chunkRequestElapsedMs = [Math]::Round(
            $chunkRequestWatch.Elapsed.TotalMilliseconds
          )
          if (-not $response.IsSuccessStatusCode) {
            $errorBody = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            $statusCode = [int]$response.StatusCode
            $response.Dispose()
            throw "Fly sync HTTP $statusCode for $rel at offset ${offset}: $errorBody"
          }
          try {
            $payload = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
            [System.IO.File]::WriteAllBytes($tmp, $payload)
            $expectedHash = [string](
              $response.Headers.GetValues("X-Chunk-Sha256") | Select-Object -First 1
            )
            if ($consistencyMode -eq "sqlite_snapshot_v1") {
              $returnedSnapshotId = [string](
                $response.Headers.GetValues("X-Data-Snapshot-Id") | Select-Object -First 1
              )
              $returnedSnapshotSha = [string](
                $response.Headers.GetValues("X-Data-Snapshot-Sha256") | Select-Object -First 1
              )
              $returnedRequestId = [string](
                $response.Headers.GetValues("X-Data-Snapshot-Request-Id") | Select-Object -First 1
              )
              $returnedBuildId = [string](
                $response.Headers.GetValues("X-Data-Snapshot-Build-Id") | Select-Object -First 1
              )
              $returnedInventoryGenerationId = [string](
                $response.Headers.GetValues("X-Data-Inventory-Generation-Id") | Select-Object -First 1
              )
              $returnedInventorySha256 = [string](
                $response.Headers.GetValues("X-Data-Inventory-Sha256") | Select-Object -First 1
              )
              if ($returnedSnapshotId -ne [string]$row.snapshot_id -or
                  $returnedSnapshotSha -ne [string]$row.snapshot_sha256 -or
                  $returnedRequestId -ne [string]$row.snapshot_request_id -or
                  $returnedBuildId -ne [string]$row.snapshot_build_id -or
                  $returnedInventoryGenerationId -ne [string]$row.snapshot_inventory_generation_id -or
                  $returnedInventorySha256 -ne [string]$row.snapshot_inventory_sha256) {
                throw "SQLite snapshot identity changed while downloading $rel."
              }
            }
            if ($atomicSnapshotFallback) {
              # The server validates that this complete small-file read stayed
              # on one generation.  Adopt the exact generation returned by
              # that read instead of the already-obsolete manifest row.
              $snapshotSize = [int64]($response.Headers.GetValues("X-Data-Size") | Select-Object -First 1)
              $snapshotMtime = [int64]($response.Headers.GetValues("X-Data-Mtime-Ns") | Select-Object -First 1)
              $snapshotInode = [int64]($response.Headers.GetValues("X-Data-Inode") | Select-Object -First 1)
              if ($snapshotSize -gt $chunkLimit -or $payload.Length -ne $snapshotSize) {
                throw "Atomic snapshot fallback did not return one complete small file for $rel."
              }
              $remoteSize = $snapshotSize
              $remoteInode = $snapshotInode
              $row.size = $snapshotSize
              $row.physical_size = $snapshotSize
              $row.mtime_ns = $snapshotMtime
              $row.inode = $snapshotInode
            }
          } finally {
            $response.Dispose()
          }
          $actualHash = (Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash.ToLowerInvariant()
          if ($actualHash -ne $expectedHash.ToLowerInvariant()) {
            throw "Chunk checksum mismatch for $rel at offset $offset."
          }
          $chunkOffset = $offset
          $input = [System.IO.File]::OpenRead($tmp)
          try {
            $output = [System.IO.File]::Open(
              $candidate,
              [System.IO.FileMode]::Append,
              [System.IO.FileAccess]::Write,
              [System.IO.FileShare]::Read
            )
            try { $input.CopyTo($output) } finally { $output.Dispose() }
          } finally { $input.Dispose() }
          if ($opaqueQuarantineEvidence) {
            $chunkReceipts.Add([ordered]@{
              offset = [int64]$chunkOffset
              length = [int64]$payload.Length
              sha256 = $actualHash
            })
          }
          $offset = [int64](Get-Item -LiteralPath $candidate).Length
          $chunkComplete = $true
          # Any successfully checksum-verified chunk proves the pressure
          # sequence has ended.
          $consecutiveChunkPressureFailures = 0
          Write-SyncProgressHeartbeat `
            -Phase "chunk_complete" `
            -RelativePath $rel `
            -FileIndex $selectedFileIndex `
            -FileCount $selectedFileCount `
            -FileBytes $offset `
            -RemoteBytes $remoteSize
          $slowSuccessfulChunk = $chunkRequestElapsedMs -ge 2000
          if ($slowSuccessfulChunk) {
            # A checksum-valid 200 can still prove resource pressure. During
            # the first production transfer, repeated 2-8 second successful
            # chunks coincided with health/ready timeouts, but the old client
            # immediately reduced its throttle after every success. Yield
            # progressively even without an HTTP error so control traffic can
            # run between disk reads and hashes on the shared one-vCPU VM.
            $adaptiveThrottleMs = [Math]::Min(
              $maxAdaptiveThrottleMs,
              [Math]::Max(2000, $adaptiveThrottleMs * 2)
            )
            Write-Warning (
              "[FLY SYNC] stage=file_chunk status=slow_success " +
              "elapsed_ms=$chunkRequestElapsedMs throttle_ms=$adaptiveThrottleMs"
            )
          }
          if ($offset -lt $remoteSize) {
            Start-Sleep -Milliseconds $adaptiveThrottleMs
          }
          if (-not $slowSuccessfulChunk -and $adaptiveThrottleMs -gt $baseInterChunkThrottleMs) {
            $adaptiveThrottleMs = [Math]::Max(
              $baseInterChunkThrottleMs,
              $adaptiveThrottleMs - 100
            )
          }
        } catch {
          $generationChanged = (
            $_.Exception.Message -match '^Fly sync HTTP 409 ' -and
            $_.Exception.Message -match 'generation changed'
          )
          $sqliteLeaseRefreshRequired = (
            $consistencyMode -eq "sqlite_snapshot_v1" -and
            $_.Exception.Message -match '^Fly sync HTTP 409 ' -and
            $_.Exception.Message -match (
              'sqlite snapshot (?:is unavailable or expired|flight identity mismatch|' +
              'acknowledgement identity mismatch)'
            )
          )
          if (($generationChanged -or $sqliteLeaseRefreshRequired) -and $generationRefreshCount -lt 3) {
            $refreshGeneration = $true
            break
          }
          $resourcePressure = Test-DataSyncResourcePressureError -Message $_.Exception.Message
          $consecutiveChunkPressureFailures = Get-FlySyncNextPressureFailureCount `
            -CurrentCount $consecutiveChunkPressureFailures `
            -IsResourcePressure $resourcePressure
          if ($resourcePressure) {
            $adaptiveThrottleMs = [Math]::Min(
              $maxAdaptiveThrottleMs,
              [Math]::Max(2000, $adaptiveThrottleMs * 2)
            )
          }
          if ($consecutiveChunkPressureFailures -ge $resourcePressureCircuitThreshold) {
            throw (
              "Fly data-sync stage=file_chunk_resource_pressure_circuit_open " +
              "path=$rel offset=$offset consecutive_pressure_failures=" +
              "$consecutiveChunkPressureFailures threshold=" +
              "${resourcePressureCircuitThreshold}: $($_.Exception.Message)"
            )
          }
          if ($attempt -ge $transportAttempts) {
            throw (
              "Fly data-sync stage=file_chunk failed for path=$rel " +
              "file=$selectedFileIndex/$selectedFileCount offset=$offset " +
              "remote_size=$remoteSize limit=$limit after " +
              "$attempt/$transportAttempts attempt(s): $($_.Exception.Message)"
            )
          }
          Start-Sleep -Seconds (Get-DataSyncRetryDelaySec `
            -Attempt $attempt `
            -ResourcePressure $resourcePressure)
        } finally {
          if (Test-Path -LiteralPath $tmp) {
            Remove-Item -LiteralPath $tmp -Force
          }
        }
      }
      if ($refreshGeneration) { break }
      }
      if ($refreshGeneration) {
        if ($forensicOriginal) {
          throw "FORENSIC_GENERATION_CHANGED: $rel"
        }
        $generationRefreshCount += 1
        if ($consistencyMode -eq "sqlite_snapshot_v1") {
          Set-SqliteSnapshotLease -Row $row
          $remoteSize = [int64]$row.size
          $sameGeneration = $false
          $fullReplaceRetry = $true
          continue
        }
        if (
          $consistencyMode -eq "strict_generation_v1" -and
          $remoteSize -le $chunkLimit
        ) {
          # Small atomically-rewritten documents may never remain equal to a
          # preceding manifest long enough to download. The endpoint can read
          # one complete generation and verify before/after identity itself.
          # Never use this for multi-chunk/raw evidence streams.
          $atomicSnapshotFallback = $true
          $sameGeneration = $false
          $fullReplaceRetry = $true
          Write-Host (
            "Fly generation remained hot for $rel; using one-read verified " +
            "atomic snapshot fallback for a $remoteSize-byte strict document."
          )
          continue
        }
        $freshManifest = Invoke-DataSyncJsonRequest `
          -Stage "manifest_targeted_refresh" `
          -Uri (New-DataSyncManifestUri -Path $rel) `
          -TimeoutSec $manifestTimeoutSec
        if ($freshManifest.schema -ne "fly_runtime_incremental_sync_v1") {
          throw "Unexpected Fly sync manifest schema during generation refresh."
        }
        if (
          [string]$freshManifest.inventory_status -ne "CURRENT" -or
          [string]$freshManifest.targeted_path -ne $rel
        ) {
          throw "Fly targeted generation refresh was not CURRENT for $rel."
        }
        $freshRows = @($freshManifest.files | Where-Object { [string]$_.path -eq $rel })
        if ($freshRows.Count -ne 1) {
          throw "Fly manifest generation refresh did not return exactly one row for $rel."
        }
        $row = $freshRows[0]
        $remoteSize = [int64]$row.size
        $remoteInode = [int64]$row.inode
        if (($currentMirrorBytes + $remoteSize) -gt $capBytes) {
          throw "Refreshed Fly generation would exceed the local mirror hard cap for $rel."
        }
        $sameGeneration = $false
        $fullReplaceRetry = $true
        Write-Host (
          "Fly generation or SQLite snapshot lease changed while downloading $rel; " +
          "refreshed authenticated manifest and restarting the candidate from byte zero " +
          "($generationRefreshCount/3)."
        )
        continue
      }
      if ([int64](Get-Item -LiteralPath $candidate).Length -ne $remoteSize) {
        throw "Incomplete Fly mirror candidate for $rel."
      }
      if ($consistencyMode -eq "sqlite_snapshot_v1") {
        $candidateSha = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($candidateSha -ne ([string]$row.snapshot_sha256).ToLowerInvariant()) {
          throw "SQLite snapshot checksum mismatch for $rel."
        }
      }
      if ($opaqueQuarantineEvidence) {
        $verifiedFullSha256 = Test-OpaqueMirrorChunkReceipts `
          -Path $candidate `
          -ExpectedSize $remoteSize `
          -Receipts @($chunkReceipts)
      }
      try {
        Test-MirrorCandidate `
          -Path $candidate `
          -RelativePath $rel `
          -ExpectedSize $remoteSize
        break
      } catch {
        # Same-inode JSONL/CSV rewrites (signal_snapshot patches) keep st_ino
        # on the Fly volume, so an incremental splice can land mid-record.
        # Never publish that candidate; retry once as a complete-file replace.
        if ($fullReplaceRetry -or -not $sameGeneration) { throw }
        $fullReplaceRetry = $true
        Write-Host (
          "Incremental JSONL/CSV candidate failed validation for $rel; " +
          "retrying as a complete atomic replace without deleting prior valid records."
        )
      }
    }
    if ($forensicOriginal) { Assert-FlyForensicPayload -Row $row -Path $candidate }
    Publish-MirrorCandidate -Candidate $candidate -Destination $local
    $downloadedGeneration = $true
  } finally {
    Remove-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $candidateBackup -Force -ErrorAction SilentlyContinue
  }
  }
  $stateMetadataChanged = [bool](
    $previous -and (
      [int64]$previous.inode -ne $remoteInode -or
      [int64]$previous.size -ne $remoteSize -or
      [int64]$previous.mtime_ns -ne [int64]$row.mtime_ns
    )
  )
  if ($downloadedGeneration -or -not $previous -or $stateMetadataChanged) {
    $syncState[$rel] = [ordered]@{
      inode = $remoteInode
      size = $remoteSize
      mtime_ns = [int64]$row.mtime_ns
      synced_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    if ($opaqueQuarantineEvidence) {
      $syncState[$rel].full_sha256 = $verifiedFullSha256
    }
    $pendingStateWrites += 1
    # Preserve bounded resumability without rewriting the complete state map
    # for every unchanged manifest row. Metadata is still reconciled when an
    # earlier interrupted pass published a file before its state checkpoint.
    # At most nine changed files need replay after interruption; published
    # mirror files remain atomic.
    if ($pendingStateWrites -ge 10) {
      Save-SyncState
      $pendingStateWrites = 0
    }
  }
  $ackRow = [ordered]@{
    path = $rel
    size = $remoteSize
    mtime_ns = [int64]$row.mtime_ns
  }
  if ($opaqueQuarantineEvidence) {
    $ackRow.full_sha256 = $verifiedFullSha256
  }
  $ackRows.Add($ackRow)
  # A final chunk is immediately followed by the next file request otherwise.
  # Yield briefly after every downloaded file as well as between chunks so the
  # shared-CPU Fly machine can schedule health/ready/status handlers.  This
  # changes only copy pacing; resumability, hashes and final acknowledgement
  # remain authoritative.
  if ($downloadedGeneration -and $selectedFileIndex -lt $selectedFileCount) {
    $fileThrottleMs = Get-FlySyncInterFileDelayMs `
      -FileBytes $remoteSize `
      -RequestElapsedMs $chunkRequestElapsedMs `
      -AdaptiveThrottleMs $adaptiveThrottleMs `
      -BaseInterFileThrottleMs $baseInterFileThrottleMs `
      -BaseInterChunkThrottleMs $baseInterChunkThrottleMs
    Start-Sleep -Milliseconds $fileThrottleMs
  }
}

Save-SyncState
$downloadClient.Dispose()
Assert-FlyForensicGroups -Rows @($selectedFiles) -Root $targetRoot

# Do not acknowledge or publish canonical parity from a generation that
# changed while this pass was copying files. This authenticated cache-bypassed
# fence deliberately permits ordinary append growth, but revision, tile
# configuration, Fresh Collection, and any explicit epoch must remain exact.
$finalManifest = Invoke-DataSyncJsonRequest `
  -Stage "manifest_final_identity" `
  -Uri (New-DataSyncManifestUri `
    -IdentityOnly `
    -GenerationId $inventoryGenerationId) `
  -TimeoutSec $manifestTimeoutSec
Assert-DataSyncManifestIdentity -Initial $manifest -Final $finalManifest

$ackSessionId = [guid]::NewGuid().ToString("N")
$ackByPath = [System.Collections.Generic.Dictionary[string, object]]::new(
  [System.StringComparer]::Ordinal
)
foreach ($ackRow in @($ackRows)) {
  $ackPath = [string]$ackRow.path
  if (-not $ackPath -or $ackByPath.ContainsKey($ackPath)) {
    throw "Downloaded acknowledgement rows contain a duplicate or empty path."
  }
  $ackByPath.Add($ackPath, $ackRow)
}
if ($ackByPath.Count -ne [int]$manifest.file_count) {
  throw (
    "Downloaded acknowledgement set does not cover the complete manifest " +
    "(expected=$([int]$manifest.file_count) actual=$($ackByPath.Count))."
  )
}
$ackCommon = [ordered]@{
  schema = "fly_runtime_incremental_ack_v3"
  inventory_sha256 = $inventorySha256
  inventory_generation_id = $inventoryGenerationId
  inventory_generated_at = $inventoryGeneratedAt
  inventory_file_count = [int]$manifest.file_count
  manifest_page_count = [int]$manifest.manifest_page_count
  manifest_pages_complete = $true
  ack_session_id = $ackSessionId
  source_git_rev = [string]$manifest.source_git_rev
  collection_epoch_id = [string]$manifest.collection_epoch_id
  tile_registry_signature = [string]$manifest.tile_registry_signature
}
foreach ($pageReceipt in @($manifest.manifest_page_receipts)) {
  $pageAckRows = [System.Collections.Generic.List[object]]::new()
  foreach ($ackPath in @($pageReceipt.paths)) {
    if (-not $ackByPath.ContainsKey([string]$ackPath)) {
      throw "Downloaded acknowledgement set is missing manifest path $ackPath."
    }
    $pageAckRows.Add($ackByPath[[string]$ackPath])
  }
  $stagePayload = [ordered]@{}
  foreach ($key in $ackCommon.Keys) { $stagePayload[$key] = $ackCommon[$key] }
  $stagePayload.operation = "STAGE_PAGE"
  $stagePayload.page_index = [int]$pageReceipt.page_index
  $stagePayload.page_sha256 = [string]$pageReceipt.page_sha256
  $stagePayload.files = @($pageAckRows)
  $stageAck = Invoke-DataSyncJsonRequest `
    -Stage "acknowledgement_page_$([int]$pageReceipt.page_index)" `
    -Uri "$base/api/data-sync/ack" `
    -Method Post `
    -Body ($stagePayload | ConvertTo-Json -Depth 6 -Compress) `
    -TimeoutSec $ackTimeoutSec
  if (
    [string]$stageAck.inventory_generation_id -cne $inventoryGenerationId -or
    [int]$stageAck.page_index -ne [int]$pageReceipt.page_index -or
    [int]$stageAck.accepted -ne [int]$pageReceipt.file_count -or
    [int]$stageAck.rejected_count -ne 0
  ) {
    throw "Fly sync page acknowledgement was incomplete."
  }
}
$finalizePayload = [ordered]@{}
foreach ($key in $ackCommon.Keys) { $finalizePayload[$key] = $ackCommon[$key] }
$finalizePayload.operation = "FINALIZE"
$ack = Invoke-DataSyncJsonRequest `
  -Stage "acknowledgement_finalize" `
  -Uri "$base/api/data-sync/ack" `
  -Method Post `
  -Body ($finalizePayload | ConvertTo-Json -Depth 5 -Compress) `
  -TimeoutSec $ackTimeoutSec

# A transport-level HTTP success is not sufficient: every exact manifest row
# must have been accepted. Missing v2 result fields and partial acceptance both
# fail closed so an older or overloaded server can never publish false parity.
$ackExpected = [int]$manifest.file_count
$ackAccepted = if ($ack.PSObject.Properties.Name -contains "accepted") {
  [int]$ack.accepted
} else { -1 }
$ackRejected = if ($ack.PSObject.Properties.Name -contains "rejected_count") {
  [int]$ack.rejected_count
} else { -1 }
if ($ackAccepted -ne $ackExpected -or $ackRejected -ne 0) {
  throw (
    "Fly sync acknowledgement was incomplete " +
    "(expected=$ackExpected accepted=$ackAccepted rejected=$ackRejected)."
  )
}
if (
  [string]$ack.inventory_sha256 -ne $inventorySha256 -or
  [string]$ack.inventory_generation_id -ne $inventoryGenerationId -or
  [string]$ack.inventory_generated_at -ne $inventoryGeneratedAt -or
  [int]$ack.inventory_file_count -ne [int]$manifest.file_count -or
  [int]$ack.manifest_page_count -ne [int]$manifest.manifest_page_count -or
  $ack.manifest_pages_complete -ne $true
) {
  throw "Fly sync acknowledgement did not bind to the requested inventory generation."
}

# The acknowledgement itself is remote work and may outlive a deployment or
# Fresh Collection transition. Fence the authority once more after it returns;
# parity is publishable only when the same revision/epoch/tile identities still
# hold on both sides of the acknowledgement.
$postAckManifest = Invoke-DataSyncJsonRequest `
  -Stage "manifest_post_ack_identity" `
  -Uri (New-DataSyncManifestUri `
    -IdentityOnly `
    -GenerationId $inventoryGenerationId) `
  -TimeoutSec $manifestTimeoutSec
Assert-DataSyncManifestIdentity -Initial $manifest -Final $postAckManifest

# Immutable lifecycle bundles need a stronger acknowledgement than the raw
# incremental mirror: retain and re-verify canonical, recoverable archive and
# durable index copies, then post their exact receipt. This never deletes or
# alters the Fly source bundle. The durable index stabilizes receipt timestamps
# so an interrupted/retried pass is byte-for-byte idempotent at the server.
$lifecycleAckCount = 0
$lifecycleManifestPaths = @(
  $selectedFiles |
    ForEach-Object { [string]$_.path } |
    Where-Object { $_ -match '^v3/lifecycle_bundles/[^/]+/lifecycle-[0-9a-f]{64}/manifest\.json$' } |
    Sort-Object -Unique
)
foreach ($lifecycleManifestPath in $lifecycleManifestPaths) {
  [void](Publish-LifecycleBundleCopyAndAck `
    -TargetRoot $targetRoot `
    -BundleManifestRelativePath $lifecycleManifestPath `
    -PostAcknowledgement {
      param($receipt)
      $body = $receipt | ConvertTo-Json -Depth 12 -Compress
      return Invoke-DataSyncJsonRequest `
        -Stage "lifecycle_acknowledgement" `
        -Uri "$base/api/data-sync/lifecycle-ack" `
        -Method Post `
        -Body $body `
        -TimeoutSec $ackTimeoutSec
    })
  $lifecycleAckCount += 1
}

# Transfer-ready bundles are intentionally isolated from qualification ACKs.
# The current Fly lifecycle-ack route accepts only
# lifecycle_bundle_cleanup_ack_v1, so a lifecycle_transfer_bundle_ack_v1 must
# never be posted there. Publish and verify its canonical/archive/index copies
# locally, then return a fail-closed local acknowledgement which cannot confer
# profitability, ranking, or source-cleanup authority. A future remote transfer
# route must be a separate, explicitly schema-aware contract.
$lifecycleTransferAckCount = 0
$lifecycleTransferManifestPaths = @(
  $selectedFiles |
    ForEach-Object { [string]$_.path } |
    Where-Object { $_ -match '^v3/lifecycle_transfer_bundles/[^/]+/transfer-[0-9a-f]{64}/manifest\.json$' } |
    Sort-Object -Unique
)
foreach ($lifecycleTransferManifestPath in $lifecycleTransferManifestPaths) {
  [void](Publish-LifecycleBundleCopyAndAck `
    -TargetRoot $targetRoot `
    -BundleManifestRelativePath $lifecycleTransferManifestPath `
    -PostAcknowledgement {
      param($receipt)
      if (
        [string]$receipt.schema -cne 'lifecycle_transfer_bundle_ack_v1' -or
        $receipt.profitability_supported -ne $false -or
        $receipt.ranking_eligible -ne $false -or
        $receipt.source_cleanup_authorized -ne $false
      ) {
        throw 'Unsafe transfer lifecycle acknowledgement contract.'
      }
      return [pscustomobject]@{
        ok = $true
        status = 'ACKNOWLEDGED_SOURCE_RETAINED'
        bundle_id = [string]$receipt.bundle_id
        profitability_supported = $false
        ranking_eligible = $false
        source_cleanup_authorized = $false
      }
    })
  $lifecycleTransferAckCount += 1
}

$analyzerPublished = $false
$analyzerPublishErrorCode = $null
if ($PublishAnalyzerReport) {
  try {
  $reportPath = [System.IO.Path]::GetFullPath($PublishAnalyzerReport)
  if (-not (Test-Path -LiteralPath $reportPath)) {
    throw "Analyzer report does not exist: $reportPath"
  }
  $reportRoot = Split-Path -Parent $reportPath
  $reportManifestPath = Join-Path $reportRoot "report_manifest.json"
  if (-not (Test-Path -LiteralPath $reportManifestPath)) {
    throw "Analyzer report manifest does not exist: $reportManifestPath"
  }
  $reportManifestRaw = Get-Content -LiteralPath $reportManifestPath -Raw
  $reportManifest = $reportManifestRaw | ConvertFrom-Json
  if ([string]$reportManifest.schema -ne "report_manifest_v1") {
    throw "Unsupported analyzer report manifest schema: $($reportManifest.schema)"
  }
  # PowerShell 7 converts ISO JSON timestamps to System.DateTime. Casting that
  # value to string uses the desktop locale and also loses the exact JSON
  # spelling that the Fly bundle validator binds to the source manifest. Read
  # the first (top-level) unescaped ISO value from the original bytes instead.
  $generatedAtMatch = [regex]::Match(
    $reportManifestRaw,
    '"generated_at"\s*:\s*"(?<value>[^"\\]+)"'
  )
  if (-not $generatedAtMatch.Success) {
    throw "Analyzer report manifest generated_at is missing or escaped."
  }
  $analyzerGeneratedAt = $generatedAtMatch.Groups['value'].Value
  $analyzerGeneratedAtValue = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParse(
    $analyzerGeneratedAt,
    [Globalization.CultureInfo]::InvariantCulture,
    [Globalization.DateTimeStyles]::RoundtripKind,
    [ref]$analyzerGeneratedAtValue
  )) {
    throw "Analyzer report manifest generated_at is invalid."
  }
  # generated_at identifies the beginning of the analyzer cycle; the manifest
  # is its commit marker and may legitimately be written several minutes later
  # after the policy grid and dashboard are rendered. Bound that duration and
  # use the commit marker—not an arbitrary five-minute wall—as the upper file
  # fence.
  $reportManifestItem = Get-Item -LiteralPath $reportManifestPath
  $analyzerCommittedAtValue = [DateTimeOffset]$reportManifestItem.LastWriteTimeUtc
  if ($analyzerCommittedAtValue -lt $analyzerGeneratedAtValue.AddMinutes(-1) -or
      $analyzerCommittedAtValue -gt $analyzerGeneratedAtValue.AddMinutes(30)) {
    throw "Analyzer manifest commit time is outside the bounded run window."
  }
  $analyzerRunId = [string]$reportManifest.analyzer_sync_id
  $analyzerRevision = [string]$reportManifest.analysis_provenance.generation_revision
  $cohortSchema = [string]$reportManifest.analysis_provenance.cohort_schema
  if (-not $analyzerGeneratedAt -or -not $analyzerRunId -or -not $analyzerRevision -or -not $cohortSchema) {
    throw "Analyzer report manifest provenance is incomplete; rerun the canonical analyzer before publishing."
  }
  if ($analyzerRevision -notmatch '^[0-9a-fA-F]{7,64}$') {
    throw "Analyzer generation revision is not a verifiable Git revision: $analyzerRevision"
  }
  if ($cohortSchema -ne "analysis_cohorts_v1") {
    throw "Unsupported analyzer cohort schema: $cohortSchema"
  }
  if (-not [string]$manifest.source_git_rev) {
    throw "Fly source-data manifest does not identify source_git_rev."
  }
  if ([string]$manifest.source_git_rev -notmatch '^[0-9a-fA-F]{7,64}$') {
    throw "Fly source-data revision is not a verifiable Git revision."
  }
  $bundlePath = Join-Path ([System.IO.Path]::GetTempPath()) "doxxed-analyzer-$PID-$([guid]::NewGuid().ToString('N')).zip"
  $snapshotRoot = Join-Path ([System.IO.Path]::GetTempPath()) "doxxed-analyzer-snapshot-$PID-$([guid]::NewGuid().ToString('N'))"
  New-Item -ItemType Directory -Path $snapshotRoot | Out-Null
  # Windows PowerShell 5.1 does not always load the base compression assembly
  # when FileSystem is requested. Load both explicitly before using
  # ZipArchiveMode so publication behaves the same in the desktop supervisor
  # and newer PowerShell runtimes.
  Add-Type -AssemblyName System.IO.Compression
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  try {
    $requiredPaths = [System.Collections.Generic.List[string]]::new()
    $requiredPaths.Add("report_manifest.json")
    foreach ($rawName in @($reportManifest.text_artifacts)) {
      $name = [string]$rawName
      if (-not $name -or $name -ne [System.IO.Path]::GetFileName($name)) {
        throw "Unsafe analyzer text artifact path in manifest: $name"
      }
      $requiredPaths.Add($name)
    }
    foreach ($row in @($reportManifest.reports)) {
      $name = [System.IO.Path]::GetFileName([string]$row.file)
      if (-not $name -or $name -ne [string]$row.file) {
        throw "Unsafe analyzer report path in manifest: $($row.file)"
      }
      $requiredPaths.Add("reports/$name")
    }
    $duplicates = @($requiredPaths | Group-Object { $_.ToLowerInvariant() } | Where-Object Count -gt 1)
    if ($duplicates.Count -gt 0) {
      throw "Analyzer report manifest contains duplicate artifact paths."
    }
    if (-not ($requiredPaths -contains "analysis_dashboard.html")) {
      throw "Analyzer report manifest does not require analysis_dashboard.html."
    }
    if (@($reportManifest.reports).Count -ne [int]$reportManifest.report_count) {
      throw "Analyzer report_count does not match the reports array."
    }

    $bundleFiles = [System.Collections.Generic.List[object]]::new()
    foreach ($relative in $requiredPaths) {
      $source = if ($relative.StartsWith("reports/")) {
        Join-Path (Join-Path $reportRoot "reports") ([System.IO.Path]::GetFileName($relative))
      } else {
        Join-Path $reportRoot $relative
      }
      if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required analyzer artifact is missing: $relative"
      }
      $destination = Join-Path $snapshotRoot ($relative.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
      $destinationDir = Split-Path -Parent $destination
      New-Item -ItemType Directory -Path $destinationDir -Force | Out-Null
      Copy-Item -LiteralPath $source -Destination $destination
      $item = Get-Item -LiteralPath $destination
      $artifactModifiedAt = [DateTimeOffset]$item.LastWriteTimeUtc
      if ($artifactModifiedAt -lt $analyzerGeneratedAtValue.AddMinutes(-15) -or
          $artifactModifiedAt -gt $analyzerCommittedAtValue.AddMinutes(1)) {
        throw "Analyzer artifact is outside the committed run window: $relative"
      }
      if ($relative.StartsWith("reports/")) {
        $reportName = [System.IO.Path]::GetFileName($relative)
        $reportRow = @($reportManifest.reports | Where-Object { [string]$_.file -eq $reportName })
        if ($reportRow.Count -ne 1 -or [int64]$reportRow[0].size_bytes -ne [int64]$item.Length) {
          throw "Analyzer report metadata does not match the snapshotted file: $relative"
        }
      }
      $bundleFiles.Add([ordered]@{
        path = $relative
        size_bytes = [int64]$item.Length
        sha256 = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
      })
    }
    $snapshotId = "analyzer-$($analyzerGeneratedAt.Replace(':','').Replace('-',''))-$([guid]::NewGuid().ToString('N'))"
    $bundleManifest = [ordered]@{
      schema = "analyzer_mirror_bundle_v2"
      snapshot_id = $snapshotId
      analyzer_run_id = $analyzerRunId
      analyzer_version = [string]$reportManifest.analyzer_version
      analyzer_generated_at = $analyzerGeneratedAt
      source_data_revision = [string]$manifest.source_git_rev
      analyzer_generation_revision = $analyzerRevision
      cohort_schema = $cohortSchema
      data_scope = [string]$reportManifest.data_scope
      session_scope = [string]$reportManifest.session_scope
      source_report_manifest_sha256 = (Get-FileHash -LiteralPath (Join-Path $snapshotRoot "report_manifest.json") -Algorithm SHA256).Hash.ToLowerInvariant()
      files = @($bundleFiles)
    }
    $bundleManifestJson = $bundleManifest | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText(
      (Join-Path $snapshotRoot "bundle_manifest.json"),
      $bundleManifestJson,
      [System.Text.UTF8Encoding]::new($false)
    )

    $zip = [System.IO.Compression.ZipFile]::Open(
      $bundlePath,
      [System.IO.Compression.ZipArchiveMode]::Create
    )
    try {
      foreach ($row in $bundleFiles) {
        $relative = [string]$row.path
        $source = Join-Path $snapshotRoot ($relative.Replace('/', [System.IO.Path]::DirectorySeparatorChar))
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
          $zip,
          $source,
          $relative,
          [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
      }
      [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
        $zip,
        (Join-Path $snapshotRoot "bundle_manifest.json"),
        "bundle_manifest.json",
        [System.IO.Compression.CompressionLevel]::Optimal
      ) | Out-Null
    } finally {
      $zip.Dispose()
    }
    if ((Get-Item -LiteralPath $bundlePath).Length -gt 50MB) {
      throw "Analyzer bundle exceeds the 50 MB compressed limit."
    }
    $client = [System.Net.Http.HttpClient]::new()
    $client.DefaultRequestHeaders.Add("X-Bot-Admin-Token", $AdminToken)
    try {
      $form = [System.Net.Http.MultipartFormDataContent]::new()
      $stream = [System.IO.File]::OpenRead($bundlePath)
      try {
        $content = [System.Net.Http.StreamContent]::new($stream)
        $content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::new("application/zip")
        $form.Add($content, "bundle", "analyzer_bundle.zip")
        $publishResponse = $client.PostAsync(
          "$base/api/data-sync/analyzer-report",
          $form
        ).GetAwaiter().GetResult()
        $publishResponse.EnsureSuccessStatusCode() | Out-Null
        $analyzerPublished = $true
      } finally {
        $stream.Dispose()
        $form.Dispose()
      }
    } finally {
      $client.Dispose()
    }
  } finally {
    Remove-Item -LiteralPath $bundlePath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $snapshotRoot -Recurse -Force -ErrorAction SilentlyContinue
  }
  } catch {
    # Analyzer publication is a derived, optional mirror operation. A rejected
    # bundle must preserve the last validated hosted report without changing
    # the success of the canonical Fly evidence download and ACK above.
    $analyzerPublishErrorCode = "ANALYZER_PUBLICATION_FAILED"
    Write-Warning "Optional analyzer publication failed; canonical evidence sync remains valid."
  }
}

# The child sync owns the atomic data download and ACK, so it must also commit
# the progress receipt. Without this final marker a successful standalone sync
# leaves the analyzer permanently fail-closed behind `inProgress: true`.
$MirroredSourceRevision = [string]$manifest.source_git_rev
Write-SyncProgressHeartbeat `
  -Phase "complete" `
  -FileIndex $selectedFiles.Count `
  -FileCount $selectedFiles.Count `
  -FileBytes ([int64](($selectedFiles | Measure-Object -Property size -Sum).Sum)) `
  -RemoteBytes ([int64](($selectedFiles | Measure-Object -Property size -Sum).Sum)) `
  -Completed

# Commit an append-first, hash-chained dataset identity only after the complete
# authenticated generation and its heartbeat are durable. Analyzer admission
# can therefore fail closed on epoch/revision/tile parity.
if (-not [string]::IsNullOrWhiteSpace($ProgressHeartbeatFile)) {
  $migrationScript = Join-Path $repoRoot "scripts\migrate_canonical_research_store.py"
  $canonicalManifestReceipt = & python $migrationScript --record-existing --destination $targetRoot --heartbeat $ProgressHeartbeatFile
  if ($LASTEXITCODE -ne 0) { throw "Canonical manifest commit failed with exit code $LASTEXITCODE." }
  if (-not $canonicalManifestReceipt) { throw "Canonical manifest commit returned no receipt." }
}

[pscustomobject]@{
  Source = $base
  Target = $targetRoot
  Files = $selectedFiles.Count
  Bytes = [int64](($selectedFiles | Measure-Object -Property size -Sum).Sum)
  SourceRevision = $manifest.source_git_rev
  AckAccepted = $ack.accepted
  LifecycleAcknowledged = $lifecycleAckCount
  LifecycleTransferAcknowledged = $lifecycleTransferAckCount
  PrunedRotations = @($ack.removed_acknowledged_rotations).Count
  LocalRetirementStatus = $localRetirementStatus
  LocalRetirementFiles = $staleRotationFiles
  LocalRetirementBytes = $staleRotationBytes
  AnalyzerPublished = [bool]$analyzerPublished
  AnalyzerPublishErrorCode = $analyzerPublishErrorCode
}
