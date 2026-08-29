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
  [string]$MirroredSourceRevision = ""
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "fly-canonical-lock.ps1")
. (Join-Path $scriptDir "fly-data-paths.ps1")
. (Join-Path $scriptDir "fly-mirror-atomic.ps1")
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

$targetRoot = [System.IO.Path]::GetFullPath($TargetDir)
New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
if ([string]::IsNullOrWhiteSpace($ProgressHeartbeatFile)) {
  $ProgressHeartbeatFile = Join-Path $targetRoot ".fly-data-sync-loop.heartbeat.json"
}
$statePath = Join-Path $targetRoot ".fly-sync-state.json"
$headers = @{ "X-Bot-Admin-Token" = $AdminToken }
Add-Type -AssemblyName System.Net.Http
$transportAttempts = 5
$manifestTimeoutSec = 90
# A busy paper-runtime can take a little over two minutes to begin streaming a
# checksum-fenced 4 MiB chunk.  The prior 120-second client deadline discarded
# valid HTTP 200 responses observed at ~129 seconds and retried the same offset
# indefinitely.  Keep the request bounded, but leave enough headroom for the
# runtime's bounded worker queue to drain without weakening generation checks.
$chunkTimeoutSec = 240
$ackTimeoutSec = 60
$downloadClient = [System.Net.Http.HttpClient]::new()
$downloadClient.Timeout = [TimeSpan]::FromSeconds($chunkTimeoutSec)
$downloadClient.DefaultRequestHeaders.Add("X-Bot-Admin-Token", $AdminToken)

function Invoke-DataSyncJsonRequest {
  param(
    [Parameter(Mandatory = $true)][string]$Stage,
    [Parameter(Mandatory = $true)][string]$Uri,
    [ValidateSet("Get", "Post")][string]$Method = "Get",
    [int]$TimeoutSec = $manifestTimeoutSec,
    [string]$Body = ""
  )
  for ($attempt = 1; $attempt -le $transportAttempts; $attempt++) {
    try {
      $parameters = @{
        Uri = $Uri
        Method = $Method
        Headers = $headers
        TimeoutSec = $TimeoutSec
      }
      if ($Method -eq "Post") {
        $parameters.ContentType = "application/json"
        $parameters.Body = $Body
      }
      return Invoke-RestMethod @parameters
    } catch {
      if ($attempt -ge $transportAttempts) {
        throw (
          "Fly data-sync stage=$Stage failed after " +
          "$attempt/$transportAttempts attempt(s): $($_.Exception.Message)"
        )
      }
      Start-Sleep -Seconds ([Math]::Min(15, 2 * $attempt))
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
  $lease = Invoke-DataSyncJsonRequest `
    -Stage "sqlite_snapshot_lease" `
    -Uri "$base/api/data-sync/sqlite-snapshot?path=$([uri]::EscapeDataString($rel))" `
    -TimeoutSec $manifestTimeoutSec
  if (
    $lease.schema -ne "fly_runtime_sqlite_snapshot_lease_v1" -or
    [string]$lease.path -ne $rel -or
    -not [string]$lease.snapshot_id -or
    [int64]$lease.snapshot_size -lt 0 -or
    [string]$lease.snapshot_sha256 -notmatch '^[0-9a-f]{64}$'
  ) {
    throw "Invalid Fly SQLite snapshot lease for $rel."
  }
  $Row | Add-Member -NotePropertyName snapshot_id -NotePropertyValue ([string]$lease.snapshot_id) -Force
  $Row | Add-Member -NotePropertyName snapshot_size -NotePropertyValue ([int64]$lease.snapshot_size) -Force
  $Row | Add-Member -NotePropertyName snapshot_sha256 -NotePropertyValue ([string]$lease.snapshot_sha256) -Force
  $Row.size = [int64]$lease.snapshot_size
  $Row.physical_size = [int64]$lease.snapshot_size
}
$manifest = Invoke-DataSyncJsonRequest `
  -Stage "manifest_initial" `
  -Uri "$base/api/data-sync/manifest" `
  -TimeoutSec $manifestTimeoutSec
if ($manifest.schema -ne "fly_runtime_incremental_sync_v1") {
  throw "Unexpected Fly sync manifest schema."
}

$ackRows = [System.Collections.Generic.List[object]]::new()
$chunkLimit = 1MB
$interChunkThrottleMs = 50
$selectedFiles = @($manifest.files)
$selectedFiles = @(
  $selectedFiles | Sort-Object `
    @{ Expression = { if ([string]$_.consistency_mode -eq "sqlite_snapshot_v1") { 0 } else { 1 } } }, `
    @{ Expression = { [string]$_.path } }
)
# SQLite snapshot rows are short-lived authenticated leases.  Download them
# before ordinary files so a large revision refresh cannot consume the lease
# while validating hundreds of unrelated hot documents.

# Fly is the authoritative owner of raw research streams. A top-level raw file
# absent from its authenticated manifest is retired locally, but cleanup is
# archive-first and recoverable. Nothing is directly deleted here.
$manifestPaths = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($manifestRow in @($manifest.files)) {
  [void]$manifestPaths.Add(([string]$manifestRow.path).Replace("\", "/"))
}
$staleRotationFiles = 0
$staleRotationBytes = [int64]0
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
  if (Test-Path -LiteralPath $archivePath) { throw "Archive collision: $archivePath" }
  [System.IO.File]::Move($resolvedCandidate, $archivePath)
  $receipt = [ordered]@{
    schema = "canonical_research_cleanup_receipt_v1"
    archived_at = [DateTimeOffset]::UtcNow.ToString("o")
    reason = "ABSENT_FROM_AUTHENTICATED_FLY_MANIFEST"
    source_relative = $candidate.Name
    archive_relative = $archivePath.Substring($targetRoot.Length).TrimStart('\').Replace('\', '/')
    recoverable = $true
  }
  $receipt | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$archivePath.receipt.json" -Encoding UTF8
  [void]$syncState.Remove($candidate.Name)
  $staleRotationFiles += 1
}
if ($staleRotationFiles -gt 0) {
  Write-Host (
    "Archived $staleRotationFiles stale local Fly research file(s), " +
    "$staleRotationBytes byte(s), absent from the authenticated manifest."
  )
  Save-SyncState
}
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
  $appendOnly = $extension -in @(".jsonl", ".csv", ".log", ".txt")
  $consistencyMode = [string]$(if ($row.consistency_mode) { $row.consistency_mode } else { "strict_generation_v1" })
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
  $downloadedGeneration = $false
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
      $consistencyMode -eq "strict_generation_v1" -and
      $remoteSize -le $chunkLimit
    )
    while ($true) {
      $refreshGeneration = $false
      if (
        $sameGeneration -and
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
          if (-not $atomicSnapshotFallback) {
            $requestUrl += (
              "&expected_physical_size=$expectedPhysicalSize&expected_published_size=$expectedPublishedSize" +
              "&expected_mtime_ns=$expectedMtime&expected_inode=$expectedInode&consistency_mode=$consistencyMode"
            )
            if ($consistencyMode -eq "sqlite_snapshot_v1") {
              $requestUrl += (
                "&snapshot_id=$([uri]::EscapeDataString([string]$row.snapshot_id))" +
                "&expected_snapshot_size=$([int64]$row.snapshot_size)" +
                "&expected_snapshot_sha256=$([string]$row.snapshot_sha256)"
              )
            }
          }
          $response = $downloadClient.GetAsync($requestUrl).GetAwaiter().GetResult()
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
              if ($returnedSnapshotId -ne [string]$row.snapshot_id -or
                  $returnedSnapshotSha -ne [string]$row.snapshot_sha256) {
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
          $offset = [int64](Get-Item -LiteralPath $candidate).Length
          $chunkComplete = $true
          Write-SyncProgressHeartbeat `
            -Phase "chunk_complete" `
            -RelativePath $rel `
            -FileIndex $selectedFileIndex `
            -FileCount $selectedFileCount `
            -FileBytes $offset `
            -RemoteBytes $remoteSize
          if ($offset -lt $remoteSize) {
            Start-Sleep -Milliseconds $interChunkThrottleMs
          }
        } catch {
          $generationChanged = (
            $_.Exception.Message -match '^Fly sync HTTP 409 ' -and
            $_.Exception.Message -match 'generation changed'
          )
          $sqliteLeaseExpired = (
            $consistencyMode -eq "sqlite_snapshot_v1" -and
            $_.Exception.Message -match '^Fly sync HTTP 400 ' -and
            $_.Exception.Message -match 'sqlite snapshot is unavailable or expired'
          )
          if (($generationChanged -or $sqliteLeaseExpired) -and $generationRefreshCount -lt 3) {
            $refreshGeneration = $true
            break
          }
          if ($attempt -ge $transportAttempts) {
            throw (
              "Fly data-sync stage=file_chunk failed for path=$rel " +
              "file=$selectedFileIndex/$selectedFileCount offset=$offset " +
              "remote_size=$remoteSize limit=$limit after " +
              "$attempt/$transportAttempts attempt(s): $($_.Exception.Message)"
            )
          }
          Start-Sleep -Seconds (2 * $attempt)
        } finally {
          if (Test-Path -LiteralPath $tmp) {
            Remove-Item -LiteralPath $tmp -Force
          }
        }
      }
      if ($refreshGeneration) { break }
      }
      if ($refreshGeneration) {
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
          -Stage "manifest_refresh" `
          -Uri "$base/api/data-sync/manifest" `
          -TimeoutSec $manifestTimeoutSec
        if ($freshManifest.schema -ne "fly_runtime_incremental_sync_v1") {
          throw "Unexpected Fly sync manifest schema during generation refresh."
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
      try {
        Test-MirrorCandidate -Path $candidate -RelativePath $rel
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
  $ackRows.Add([ordered]@{
    path = $rel
    size = $remoteSize
    mtime_ns = [int64]$row.mtime_ns
  })
}

Save-SyncState
$downloadClient.Dispose()

$ackBody = @{ files = @($ackRows) } | ConvertTo-Json -Depth 5
$ack = Invoke-DataSyncJsonRequest `
  -Stage "acknowledgement" `
  -Uri "$base/api/data-sync/ack" `
  -Method Post `
  -Body $ackBody `
  -TimeoutSec $ackTimeoutSec

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
  PrunedRotations = @($ack.removed_acknowledged_rotations).Count
  AnalyzerPublished = [bool]$analyzerPublished
  AnalyzerPublishErrorCode = $analyzerPublishErrorCode
}
