param(
  [string]$SourceUrl = "https://doxed-btc-bot.fly.dev",
  [string]$AdminToken = "",
  [string]$TargetDir = "",
  [string]$PublishAnalyzerReport = "",
  [string[]]$IncludePath = @()
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "fly-canonical-lock.ps1")
$SourceUrl = Get-CanonicalFlyBotUrl -RequestedUrl $SourceUrl
if (-not $TargetDir) {
  $TargetDir = Join-Path $repoRoot "services\btc-conservative-agent\fly-data-mirror"
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
  $stateTmp = "$statePath.tmp"
  $syncState | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $stateTmp -Encoding UTF8
  Move-Item -LiteralPath $stateTmp -Destination $statePath -Force
}

$base = $SourceUrl.TrimEnd("/")
$manifest = Invoke-RestMethod -Uri "$base/api/data-sync/manifest" -Headers $headers -TimeoutSec 30
if ($manifest.schema -ne "fly_runtime_incremental_sync_v1") {
  throw "Unexpected Fly sync manifest schema."
}

$ackRows = [System.Collections.Generic.List[object]]::new()
$chunkLimit = 1MB
$selectedFiles = @($manifest.files)
if ($IncludePath.Count -gt 0) {
  $selectedFiles = @(
    $selectedFiles | Where-Object { [string]$_.path -in $IncludePath }
  )
  if ($selectedFiles.Count -ne $IncludePath.Count) {
    throw "One or more IncludePath entries were not present in the Fly manifest."
  }
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
  if (-not $sameGeneration -and (Test-Path -LiteralPath $local)) {
    # A Fly rotation/redeploy replaced or truncated the active file. Reset only
    # this mirror file; the previous generation remains available as a numbered
    # rotation in the manifest until acknowledged retention removes it.
    Remove-Item -LiteralPath $local -Force
    $localSize = 0
  }

  $offset = $localSize
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
            $local,
            [System.IO.FileMode]::Append,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::Read
          )
          try { $input.CopyTo($output) } finally { $output.Dispose() }
        } finally { $input.Dispose() }
        $offset = [int64](Get-Item -LiteralPath $local).Length
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

  if (-not (Test-Path -LiteralPath $local)) {
    [System.IO.File]::WriteAllBytes($local, [byte[]]::new(0))
  }
  if ([int64](Get-Item -LiteralPath $local).Length -ne $remoteSize) {
    throw "Incomplete Fly mirror for $rel."
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
  $client = [System.Net.Http.HttpClient]::new()
  try {
    $client.DefaultRequestHeaders.Add("X-Bot-Admin-Token", $AdminToken)
    $form = [System.Net.Http.MultipartFormDataContent]::new()
    $stream = [System.IO.File]::OpenRead($reportPath)
    try {
      $content = [System.Net.Http.StreamContent]::new($stream)
      $content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::new("text/html")
      $form.Add($content, "report", "analysis_dashboard.html")
      $form.Add(
        [System.Net.Http.StringContent]::new([string]$manifest.source_git_rev),
        "source_git_rev"
      )
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
