function Test-MirrorCandidate {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$RelativePath,
    [Nullable[Int64]]$ExpectedSize = $null
  )
  $normalizedRelativePath = $RelativePath.Replace("\", "/").Trim("/")
  $relativeParts = @($normalizedRelativePath.Split("/"))
  if (
    [string]::IsNullOrWhiteSpace($normalizedRelativePath) -or
    @($relativeParts | Where-Object { $_ -in @("", ".", "..") }).Count -gt 0
  ) {
    throw "Downloaded candidate has an invalid relative path: $RelativePath."
  }
  $name = $normalizedRelativePath.ToLowerInvariant()
  $opaqueCorruptEvidence = $name.StartsWith(
    "corrupt_evidence_quarantine/",
    [System.StringComparison]::Ordinal
  )
  if ($opaqueCorruptEvidence) {
    if ($relativeParts.Count -lt 2) {
      throw "Downloaded quarantine candidate has an invalid relative path: $RelativePath."
    }
    if ($null -eq $ExpectedSize -or [int64]$ExpectedSize -lt 0) {
      throw "Quarantine evidence manifest size is unavailable for $RelativePath."
    }
    $candidateSize = [int64](Get-Item -LiteralPath $Path).Length
    if ($candidateSize -ne [int64]$ExpectedSize) {
      throw "Quarantine evidence manifest size mismatch for $RelativePath."
    }
    # These are immutable forensic bytes. A corrupt or truncated JSONL payload
    # is the evidence being preserved, so semantic parsing would destroy the
    # quarantine contract. Authenticated contiguous chunk receipts are checked
    # separately before this semantic admission gate.
    return
  }
  # This legacy filename is an append-only newline-delimited crash journal,
  # not one JSON document. Validating the whole file as JSON stalls the mirror
  # as soon as a second crash record is appended.
  if ($name -eq "crash_dump.json") {
    $name = "crash_dump.jsonl"
  }
  if ($name -match '\.json$') {
    try {
      $null = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
      throw "Downloaded JSON candidate is invalid for ${RelativePath}: $($_.Exception.Message)"
    }
    return
  }
  if ($name -match '\.jsonl(?:\.\d+)?$') {
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    try {
      if ($stream.Length -eq 0) { return }
      $stream.Seek(-1, [System.IO.SeekOrigin]::End) | Out-Null
      if ($stream.ReadByte() -ne 10) {
        throw "Downloaded JSONL candidate has an incomplete final record for $RelativePath."
      }
      $stream.Seek(0, [System.IO.SeekOrigin]::Begin) | Out-Null
      $reader = [System.IO.StreamReader]::new($stream, [System.Text.UTF8Encoding]::new($false, $true), $true, 65536, $true)
      try {
        $lineNumber = 0
        while (($line = $reader.ReadLine()) -ne $null) {
          $lineNumber += 1
          if ([string]::IsNullOrWhiteSpace($line)) { continue }
          try { $null = $line | ConvertFrom-Json } catch {
            throw "Downloaded JSONL candidate has invalid JSON at ${RelativePath}:${lineNumber}."
          }
        }
      } finally { $reader.Dispose() }
    } finally { $stream.Dispose() }
    return
  }
  if ($name -match '\.csv(?:\.\d+)?$') {
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    try {
      if ($stream.Length -eq 0) { return }
      $stream.Seek(-1, [System.IO.SeekOrigin]::End) | Out-Null
      if ($stream.ReadByte() -ne 10) {
        throw "Downloaded CSV candidate has an incomplete final record for $RelativePath."
      }
      $stream.Seek(0, [System.IO.SeekOrigin]::Begin) | Out-Null
      $reader = [System.IO.StreamReader]::new($stream, [System.Text.UTF8Encoding]::new($false, $true), $true, 65536, $true)
      try {
        $header = $reader.ReadLine()
        if ([string]::IsNullOrWhiteSpace($header) -or $header.IndexOf(',') -lt 0) {
          throw "Downloaded CSV candidate has no usable header for $RelativePath."
        }
      } finally { $reader.Dispose() }
    } finally { $stream.Dispose() }
  }
}

function Test-OpaqueMirrorChunkReceipts {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][Int64]$ExpectedSize,
    [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Receipts
  )
  if ($ExpectedSize -lt 0) { throw "Opaque mirror expected size is invalid." }
  $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
  try {
    if ($stream.Length -ne $ExpectedSize) { throw "Opaque mirror candidate size mismatch." }
    $cursor = [int64]0
    foreach ($receipt in @($Receipts)) {
      $offset = [int64]$receipt.offset
      $length = [int64]$receipt.length
      $expectedHash = [string]$receipt.sha256
      if ($offset -ne $cursor -or $length -le 0 -or ($offset + $length) -gt $ExpectedSize) {
        throw "Opaque mirror chunk receipts contain a gap, overlap, or invalid range at offset $offset."
      }
      if ($expectedHash -notmatch '^[0-9a-fA-F]{64}$') {
        throw "Opaque mirror chunk receipt checksum is missing or invalid at offset $offset."
      }
      $stream.Position = $offset
      $remaining = $length
      $sha = [System.Security.Cryptography.SHA256]::Create()
      try {
        $buffer = [byte[]]::new([Math]::Min(1048576, [int]$length))
        while ($remaining -gt 0) {
          $wanted = [int][Math]::Min([int64]$buffer.Length, $remaining)
          $read = $stream.Read($buffer, 0, $wanted)
          if ($read -le 0) { throw "Opaque mirror chunk receipt range is incomplete at offset $offset." }
          [void]$sha.TransformBlock($buffer, 0, $read, $null, 0)
          $remaining -= $read
        }
        [void]$sha.TransformFinalBlock([byte[]]::new(0), 0, 0)
        $actualHash = [System.BitConverter]::ToString($sha.Hash).Replace("-", "").ToLowerInvariant()
      } finally { $sha.Dispose() }
      if ($actualHash -ne $expectedHash.ToLowerInvariant()) {
        throw "Opaque mirror chunk receipt checksum mismatch at offset $offset."
      }
      $cursor += $length
    }
    if ($cursor -ne $ExpectedSize) {
      throw "Opaque mirror chunk receipts do not cover the complete candidate."
    }
  } finally { $stream.Dispose() }
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Publish-MirrorCandidate {
  param(
    [Parameter(Mandatory = $true)][string]$Candidate,
    [Parameter(Mandatory = $true)][string]$Destination,
    [int]$ReplaceAttempts = 12
  )
  $backup = "$Candidate.replace-backup"
  try {
    if (Test-Path -LiteralPath $Destination) {
      Invoke-MirrorAtomicReplace `
        -Candidate $Candidate `
        -Destination $Destination `
        -Backup $backup `
        -Attempts $ReplaceAttempts
    } else {
      [System.IO.File]::Move($Candidate, $Destination)
    }
  } finally {
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
  }
}

function Invoke-MirrorAtomicReplace {
  param(
    [Parameter(Mandatory = $true)][string]$Candidate,
    [Parameter(Mandatory = $true)][string]$Destination,
    [Parameter(Mandatory = $true)][string]$Backup,
    [int]$Attempts = 12
  )
  $boundedAttempts = [Math]::Max(1, [Math]::Min(20, $Attempts))
  for ($attempt = 1; $attempt -le $boundedAttempts; $attempt++) {
    try {
      [System.IO.File]::Replace($Candidate, $Destination, $Backup, $true)
      return
    } catch [System.IO.IOException] {
      if ($attempt -ge $boundedAttempts) {
        throw [System.IO.IOException]::new(
          "Atomic mirror publish failed after $attempt attempt(s): destination=$Destination candidate=$Candidate error=$($_.Exception.Message)",
          $_.Exception
        )
      }
      # Readers of a large immutable snapshot can briefly hold a Windows file
      # handle without FILE_SHARE_DELETE. Keep the validated candidate and the
      # prior mirror intact, then retry only the atomic publish operation.
      Start-Sleep -Milliseconds ([Math]::Min(2000, 100 * [Math]::Pow(2, $attempt - 1)))
    }
  }
}
