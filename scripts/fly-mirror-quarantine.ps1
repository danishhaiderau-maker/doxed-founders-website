function Get-FlyMirrorSha256 {
  param([Parameter(Mandatory=$true)][string]$LiteralPath)
  $stream = [IO.File]::Open($LiteralPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
  try {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
  } finally { $stream.Dispose() }
}

function Invoke-FlyMirrorEpochQuarantine {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory=$true)][string]$MirrorPath,
    [Parameter(Mandatory=$true)][string]$QuarantineRoot,
    [Parameter(Mandatory=$true)][double]$FreshCollectionSignalTs,
    [int]$RetryCount = 60,
    [int]$RetryDelayMs = 250
  )

  $mirror = [IO.Path]::GetFullPath($MirrorPath).TrimEnd('\', '/')
  $quarantineRootFull = [IO.Path]::GetFullPath($QuarantineRoot).TrimEnd('\', '/')
  if ($mirror -eq $quarantineRootFull -or $quarantineRootFull.StartsWith($mirror + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Quarantine root must be outside the active mirror.'
  }
  New-Item -ItemType Directory -Path $mirror -Force | Out-Null
  New-Item -ItemType Directory -Path $quarantineRootFull -Force | Out-Null

  # Stable per-signal destination makes an interrupted attempt safely resumable.
  $signalKey = ([BitConverter]::ToString([BitConverter]::GetBytes($FreshCollectionSignalTs))).Replace('-', '').ToLowerInvariant()
  $destination = Join-Path $quarantineRootFull ("fresh_signal_" + $signalKey)
  New-Item -ItemType Directory -Path $destination -Force | Out-Null
  $manifestPath = Join-Path $destination 'quarantine_manifest.json'
  $temporaryManifest = $manifestPath + '.tmp'
  # Never expose an earlier completion receipt while this signal is being
  # retried or while additional old-mirror files are still present.
  Remove-Item -LiteralPath $manifestPath, $temporaryManifest -Force -ErrorAction SilentlyContinue

  $sourceFiles = @(Get-ChildItem -LiteralPath $mirror -Recurse -File -Force -ErrorAction Stop | Sort-Object FullName)
  foreach ($file in $sourceFiles) {
    $relative = $file.FullName.Substring($mirror.Length + 1).Replace('\', '/')
    if (-not $relative -or $relative -match '(^|/)\.\.(/|$)') { throw "Unsafe mirror-relative path: $relative" }
    $target = Join-Path $destination ($relative.Replace('/', [IO.Path]::DirectorySeparatorChar))
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null

    $copied = $false
    for ($attempt = 0; $attempt -lt [Math]::Max(1, $RetryCount); $attempt++) {
      try {
        $before = Get-Item -LiteralPath $file.FullName -Force -ErrorAction Stop
        $sourceHash = Get-FlyMirrorSha256 -LiteralPath $file.FullName
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force -ErrorAction Stop
        $targetItem = Get-Item -LiteralPath $target -Force -ErrorAction Stop
        $targetHash = Get-FlyMirrorSha256 -LiteralPath $target
        $after = Get-Item -LiteralPath $file.FullName -Force -ErrorAction Stop
        if ($before.Length -ne $after.Length -or $sourceHash -ne (Get-FlyMirrorSha256 -LiteralPath $file.FullName)) {
          throw 'Source changed while it was being preserved.'
        }
        if ($targetItem.Length -ne $before.Length -or $targetHash -ne $sourceHash) {
          throw 'Quarantine copy verification failed.'
        }
        Remove-Item -LiteralPath $file.FullName -Force -ErrorAction Stop
        $copied = $true
        break
      } catch {
        if ($attempt + 1 -ge [Math]::Max(1, $RetryCount)) { throw "Unable to quarantine '$relative' without data loss: $($_.Exception.Message)" }
        Start-Sleep -Milliseconds ([Math]::Max(10, $RetryDelayMs))
      }
    }
    if (-not $copied) { throw "Unable to quarantine '$relative'." }
  }

  # Empty directories are safe to remove only after every file is preserved.
  Get-ChildItem -LiteralPath $mirror -Recurse -Directory -Force -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Remove-Item -Force -ErrorAction SilentlyContinue
  $remaining = @(Get-ChildItem -LiteralPath $mirror -Recurse -File -Force -ErrorAction Stop)
  if ($remaining.Count -ne 0) { throw 'Active mirror is not empty after quarantine; refusing fresh sync.' }

  # Inventory the whole resumable destination, including files successfully
  # preserved by an earlier interrupted attempt.
  $preserved = @()
  foreach ($file in @(Get-ChildItem -LiteralPath $destination -Recurse -File -Force -ErrorAction Stop | Sort-Object FullName)) {
    if ($file.FullName -eq $manifestPath -or $file.FullName -eq $temporaryManifest) { continue }
    $relative = $file.FullName.Substring($destination.Length + 1).Replace('\', '/')
    $preserved += [ordered]@{
      path = $relative
      size_bytes = [int64]$file.Length
      sha256 = (Get-FlyMirrorSha256 -LiteralPath $file.FullName)
    }
  }
  $preservedTotalBytes = [int64]0
  foreach ($row in $preserved) {
    # Ordered dictionaries do not expose size_bytes as a PowerShell object
    # property to Measure-Object on every Windows PowerShell host.
    $preservedTotalBytes += [int64]$row['size_bytes']
  }

  $manifest = [ordered]@{
    schema = 'fly_mirror_epoch_quarantine_v2'
    complete = $true
    completed_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    fresh_collection_signal_ts = $FreshCollectionSignalTs
    source_mirror = $mirror
    file_count = $preserved.Count
    total_bytes = $preservedTotalBytes
    files = $preserved
  }
  $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $temporaryManifest -Encoding UTF8
  Move-Item -LiteralPath $temporaryManifest -Destination $manifestPath -Force
  return [pscustomobject]@{ Destination = $destination; ManifestPath = $manifestPath; FileCount = $preserved.Count }
}
