param(
  [string]$SourceUrl = "https://doxed-btc-bot.fly.dev",
  [string]$AdminToken = "",
  [string]$TargetDir = "",
  [string]$PublishAnalyzerReport = "",
  [string[]]$IncludePath = @(),
  [int]$MaxLocalMirrorGiB = 30
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
if (-not $AdminToken) {
  $AdminToken = [Environment]::GetEnvironmentVariable(
    "BOT_ADMIN_TOKEN",
    [EnvironmentVariableTarget]::Process
  )
}
if (-not $AdminToken) {
  throw "AdminToken is required (parameter or BOT_ADMIN_TOKEN environment variable)."
}

$targetRoot = [System.IO.Path]::GetFullPath($TargetDir)
New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
$statePath = Join-Path $targetRoot ".fly-sync-state.json"
$headers = @{ "X-Bot-Admin-Token" = $AdminToken }
Add-Type -AssemblyName System.Net.Http
$downloadClient = [System.Net.Http.HttpClient]::new()
$downloadClient.Timeout = [TimeSpan]::FromSeconds(45)
$downloadClient.DefaultRequestHeaders.Add("X-Bot-Admin-Token", $AdminToken)

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
      [System.IO.File]::Replace($stateTmp, $statePath, $stateBackup, $true)
    } else {
      [System.IO.File]::Move($stateTmp, $statePath)
    }
  } finally {
    Remove-Item -LiteralPath $stateTmp -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stateBackup -Force -ErrorAction SilentlyContinue
  }
}

$base = $SourceUrl.TrimEnd("/")
$manifest = Invoke-RestMethod -Uri "$base/api/data-sync/manifest" -Headers $headers -TimeoutSec 30
if ($manifest.schema -ne "fly_runtime_incremental_sync_v1") {
  throw "Unexpected Fly sync manifest schema."
}

$ackRows = [System.Collections.Generic.List[object]]::new()
$chunkLimit = 4MB
$selectedFiles = @($manifest.files)

# Fly is the authoritative owner of raw research streams. Once a top-level raw
# stream or closed rotation is no longer declared by its authenticated
# manifest, keeping an extra local copy only wastes disk and can make the
# analyzer rediscover a retired epoch. Reports, archives, JSON configuration
# and subdirectories are never candidates.
$manifestPaths = [System.Collections.Generic.HashSet[string]]::new(
  [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($manifestRow in @($manifest.files)) {
  [void]$manifestPaths.Add(([string]$manifestRow.path).Replace("\", "/"))
}
$staleRotationFiles = 0
$staleRotationBytes = [int64]0
foreach ($candidate in @(Get-ChildItem -LiteralPath $targetRoot -File -Force -ErrorAction SilentlyContinue)) {
  if ($candidate.Name -notmatch '\.(jsonl|log|csv)(?:\.\d+)?$') { continue }
  $resolvedCandidate = [System.IO.Path]::GetFullPath($candidate.FullName)
  if (-not $resolvedCandidate.StartsWith($targetRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Local raw research file escaped the mirror root: $resolvedCandidate"
  }
  if ($manifestPaths.Contains($candidate.Name)) { continue }
  $staleRotationBytes += [int64]$candidate.Length
  [System.IO.File]::Delete($resolvedCandidate)
  if (Test-Path -LiteralPath $resolvedCandidate) {
    throw "Failed to remove stale local Fly research file: $resolvedCandidate"
  }
  [void]$syncState.Remove($candidate.Name)
  $staleRotationFiles += 1
}
if ($staleRotationFiles -gt 0) {
  Write-Host (
    "Removed $staleRotationFiles stale local Fly research file(s), " +
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
foreach ($row in $selectedFiles) {
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
  if (-not ($sameGeneration -and $localSize -eq $remoteSize)) {
  # Assemble and validate a complete same-directory candidate. Never append
  # directly to a file that the analyzer can read: doing so exposed a partial
  # JSONL record between chunk writes. The existing mirror remains untouched
  # until the candidate is complete and atomically replaces it.
  $candidate = "$local.$PID.$([guid]::NewGuid().ToString('N')).download"
  $candidateBackup = "$candidate.replace-backup"
  try {
    if ($sameGeneration -and (Test-Path -LiteralPath $local)) {
      [System.IO.File]::Copy($local, $candidate, $true)
      $offset = $localSize
    } else {
      [System.IO.File]::WriteAllBytes($candidate, [byte[]]::new(0))
      $offset = 0
    }
    while ($offset -lt $remoteSize) {
    $limit = [Math]::Min($chunkLimit, $remoteSize - $offset)
    $chunkComplete = $false
    for ($attempt = 1; $attempt -le 3 -and -not $chunkComplete; $attempt++) {
      $tmp = Join-Path $env:TEMP ("fly-sync-" + [guid]::NewGuid().ToString("N") + ".part")
      try {
        $encoded = [uri]::EscapeDataString($rel)
        $response = $downloadClient.GetAsync(
          "$base/api/data-sync/file?path=$encoded&offset=$offset&limit=$limit"
        ).GetAwaiter().GetResult()
        $response.EnsureSuccessStatusCode() | Out-Null
        try {
          $payload = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
          [System.IO.File]::WriteAllBytes($tmp, $payload)
          $expectedHash = [string](
            $response.Headers.GetValues("X-Chunk-Sha256") | Select-Object -First 1
          )
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
      } catch {
        if ($attempt -ge 3) { throw }
        Start-Sleep -Seconds (2 * $attempt)
      } finally {
        if (Test-Path -LiteralPath $tmp) {
          Remove-Item -LiteralPath $tmp -Force
        }
      }
    }
    }
    if ([int64](Get-Item -LiteralPath $candidate).Length -ne $remoteSize) {
      throw "Incomplete Fly mirror candidate for $rel."
    }
    Test-MirrorCandidate -Path $candidate -RelativePath $rel
    Publish-MirrorCandidate -Candidate $candidate -Destination $local
  } finally {
    Remove-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $candidateBackup -Force -ErrorAction SilentlyContinue
  }
  }
  $syncState[$rel] = [ordered]@{
    inode = $remoteInode
    size = $remoteSize
    mtime_ns = [int64]$row.mtime_ns
    synced_at = (Get-Date).ToUniversalTime().ToString("o")
  }
  Save-SyncState
  $ackRows.Add([ordered]@{
    path = $rel
    size = $remoteSize
    mtime_ns = [int64]$row.mtime_ns
  })
}

Save-SyncState
$downloadClient.Dispose()

$ackBody = @{ files = @($ackRows) } | ConvertTo-Json -Depth 5
$ack = Invoke-RestMethod `
  -Uri "$base/api/data-sync/ack" `
  -Method Post `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $ackBody `
  -TimeoutSec 30

if ($PublishAnalyzerReport) {
  $reportPath = [System.IO.Path]::GetFullPath($PublishAnalyzerReport)
  if (-not (Test-Path -LiteralPath $reportPath)) {
    throw "Analyzer report does not exist: $reportPath"
  }
  $reportRoot = Split-Path -Parent $reportPath
  $reportManifestPath = Join-Path $reportRoot "report_manifest.json"
  if (-not (Test-Path -LiteralPath $reportManifestPath)) {
    throw "Analyzer report manifest does not exist: $reportManifestPath"
  }
  $reportManifest = Get-Content -LiteralPath $reportManifestPath -Raw | ConvertFrom-Json
  if ([string]$reportManifest.schema -ne "report_manifest_v1") {
    throw "Unsupported analyzer report manifest schema: $($reportManifest.schema)"
  }
  $analyzerGeneratedAt = [string]$reportManifest.generated_at
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
  try { $analyzerGeneratedAtValue = [DateTimeOffset]::Parse($analyzerGeneratedAt) } catch {
    throw "Analyzer report manifest generated_at is invalid: $analyzerGeneratedAt"
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
      if ($artifactModifiedAt -lt $analyzerGeneratedAtValue.AddMinutes(-15) -or $artifactModifiedAt -gt $analyzerGeneratedAtValue.AddMinutes(5)) {
        throw "Analyzer artifact is outside the current run window: $relative ($artifactModifiedAt vs $analyzerGeneratedAtValue)"
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
}

[pscustomobject]@{
  Source = $base
  Target = $targetRoot
  Files = $selectedFiles.Count
  Bytes = [int64](($selectedFiles | Measure-Object -Property size -Sum).Sum)
  SourceRevision = $manifest.source_git_rev
  AckAccepted = $ack.accepted
  PrunedRotations = @($ack.removed_acknowledged_rotations).Count
  AnalyzerPublished = [bool]$PublishAnalyzerReport
}
